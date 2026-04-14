"""
Lore MCP authentication — Bearer token middleware.

External MCP clients (Claude.ai, Cursor, etc.) authenticate with a static
Bearer token:

    Authorization: Bearer slf_lore_<random>

The server hashes the token, looks it up in `lore_api_keys` on the platform
Supabase, and resolves the user's home client + Supabase target server-side.
The resolved context is then injected into every tool call — external clients
cannot pass user_id, target_url, or target_key themselves.

Internal callers (admin routes, voice interview, agent worker) bypass this
flow by passing the platform service role key in the `X-Lore-Internal`
header, along with explicit user_id/target_url/target_key query params.
This preserves the existing internal flow while locking down external access.
"""

import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import httpx
from fastapi import Header, HTTPException, Query, Request
from supabase import Client as SupabaseClient, create_client

logger = logging.getLogger("lore_mcp.auth")

TOKEN_PREFIX = "slf_lore_"
TOKEN_BODY_BYTES = 32  # 256 bits of entropy
INTERNAL_SECRET_ENV = "SUPABASE_SERVICE_ROLE_KEY"


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def generate_token() -> str:
    """Generate a new raw token. Returned once to the user, never stored."""
    body = secrets.token_urlsafe(TOKEN_BODY_BYTES)
    return f"{TOKEN_PREFIX}{body}"


def hash_token(raw: str) -> str:
    """Deterministic sha256 of the raw token (hex). Stored in lore_api_keys.key_hash."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_prefix(raw: str) -> str:
    """First ~12 chars of the raw token for safe display in admin UIs."""
    return raw[: len(TOKEN_PREFIX) + 4]  # e.g. "slf_lore_A1b2"


# ---------------------------------------------------------------------------
# LoreContext — the resolved auth/routing for a tool call
# ---------------------------------------------------------------------------

@dataclass
class LoreContext:
    user_id: str                 # REAL platform user_id (never a shadow)
    target_url: Optional[str]    # Dedicated Supabase URL if applicable
    target_key: Optional[str]    # Service role key for target_url
    source: str                  # "bearer" | "internal"
    api_key_id: Optional[str] = None  # lore_api_keys.id if source=bearer


# ---------------------------------------------------------------------------
# Platform Supabase client — single instance shared across requests
# ---------------------------------------------------------------------------

_platform_client: Optional[SupabaseClient] = None


def _get_platform_client() -> Optional[SupabaseClient]:
    global _platform_client
    if _platform_client is not None:
        return _platform_client
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        return None
    _platform_client = create_client(url, key)
    return _platform_client


# ---------------------------------------------------------------------------
# Token → user_id resolution
# ---------------------------------------------------------------------------

def resolve_token(raw: str) -> Optional[Tuple[str, str]]:
    """Look up a raw token in lore_api_keys. Returns (user_id, api_key_id)
    or None if the token is invalid, revoked, or the platform DB is unreachable.
    """
    if not raw:
        return None

    # OAuth-issued tokens skip the lore_api_keys lookup entirely.
    if raw.startswith("slf_oauth_"):
        try:
            from oauth import resolve_oauth_token
            return resolve_oauth_token(raw)
        except Exception as exc:
            logger.warning(f"OAuth token resolve failed: {exc}")
            return None

    if not raw.startswith(TOKEN_PREFIX):
        return None

    client = _get_platform_client()
    if client is None:
        logger.error("Platform Supabase client not configured — cannot validate tokens")
        return None

    key_hash = hash_token(raw)
    try:
        resp = (
            client
            .table("lore_api_keys")
            .select("id,user_id,revoked_at")
            .eq("key_hash", key_hash)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        logger.warning(f"Token lookup failed: {exc}")
        return None

    if not resp or not resp.data:
        # Fall back to OAuth-issued tokens (slf_oauth_ prefix or anything not
        # found in lore_api_keys). Imported lazily to avoid a circular import.
        try:
            from oauth import resolve_oauth_token
            return resolve_oauth_token(raw)
        except Exception:
            return None
    row = resp.data
    if row.get("revoked_at"):
        return None

    # Update last_used_at (fire and forget — best effort)
    try:
        from datetime import datetime, timezone
        client.table("lore_api_keys").update(
            {"last_used_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", row["id"]).execute()
    except Exception:
        pass

    return row["user_id"], row["id"]


def resolve_user_home_target(user_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Given a real platform user_id, resolve their home client's Supabase
    credentials. Returns (target_url, target_key) — both None means the
    platform Supabase is used (Adventurer tier)."""
    client = _get_platform_client()
    if client is None:
        return None, None

    home_client_id: Optional[str] = None
    is_super = False

    # 1. Try user_metadata.tenant_assignments.admin_client_ids[0]
    try:
        u = client.auth.admin.get_user_by_id(user_id)
        if u and u.user:
            meta = getattr(u.user, "user_metadata", {}) or {}
            app_meta = getattr(u.user, "app_metadata", {}) or {}
            admin_ids = (meta.get("tenant_assignments") or {}).get("admin_client_ids") or []
            if admin_ids:
                home_client_id = admin_ids[0]
            role = (meta.get("platform_role") or app_meta.get("platform_role") or "").lower()
            is_super = role in ("super_admin", "superadmin")
    except Exception as exc:
        logger.debug(f"auth resolver: user lookup failed for {user_id[:8]}: {exc}")

    # 2. Superadmin fallback → Leandrew Dixon
    if not home_client_id and is_super:
        try:
            fb = (
                client.table("clients")
                .select("id")
                .eq("name", "Leandrew Dixon")
                .maybe_single()
                .execute()
            )
            if fb and fb.data:
                home_client_id = fb.data["id"]
        except Exception:
            pass

    if not home_client_id:
        return None, None

    # 3. Fetch dedicated Supabase credentials for the home client
    try:
        row = (
            client.table("clients")
            .select("supabase_url,supabase_service_role_key")
            .eq("id", home_client_id)
            .single()
            .execute()
        )
        if not row.data:
            return None, None
        t_url = row.data.get("supabase_url")
        t_key = row.data.get("supabase_service_role_key")
        platform_url = os.getenv("SUPABASE_URL", "")
        if t_url and t_key and t_url != platform_url:
            return t_url, t_key
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def require_lore_context(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_lore_internal: Optional[str] = Header(None),
    user_id_q: Optional[str] = Query(None, alias="user_id"),
    target_url_q: Optional[str] = Query(None, alias="target_url"),
    target_key_q: Optional[str] = Query(None, alias="target_key"),
) -> LoreContext:
    """FastAPI dependency that produces a LoreContext for the current request.

    Two paths:

    1. **External** — caller sends `Authorization: Bearer slf_lore_<token>`.
       The token is hashed, looked up, and the user_id + home target are
       resolved server-side. Query params user_id/target_url/target_key are
       IGNORED in this path — external clients cannot impersonate.

    2. **Internal** — caller sends `X-Lore-Internal: <service_role_key>`
       along with explicit user_id, target_url, target_key query params.
       Used by admin routes, voice interview, and agent worker where the
       user context is already trusted and resolved upstream.

    Raises 401 if neither path is satisfied.
    """
    # Path 1: Bearer token
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
        resolved = resolve_token(raw)
        if resolved is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key.")
        user_id, api_key_id = resolved
        target_url, target_key = resolve_user_home_target(user_id)
        return LoreContext(
            user_id=user_id,
            target_url=target_url,
            target_key=target_key,
            source="bearer",
            api_key_id=api_key_id,
        )

    # Path 2: Internal service role
    internal_secret = os.getenv(INTERNAL_SECRET_ENV, "")
    if x_lore_internal and internal_secret and hmac.compare_digest(x_lore_internal, internal_secret):
        if not user_id_q:
            raise HTTPException(status_code=400, detail="Internal call requires user_id query param.")
        return LoreContext(
            user_id=user_id_q,
            target_url=target_url_q,
            target_key=target_key_q,
            source="internal",
        )

    raise HTTPException(
        status_code=401,
        detail="Missing or invalid credentials. Provide Authorization: Bearer slf_lore_<token>.",
    )


async def optional_lore_context(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_lore_internal: Optional[str] = Header(None),
    user_id_q: Optional[str] = Query(None, alias="user_id"),
    target_url_q: Optional[str] = Query(None, alias="target_url"),
    target_key_q: Optional[str] = Query(None, alias="target_key"),
) -> Optional[LoreContext]:
    """Same as require_lore_context but returns None instead of raising.
    Used for endpoints that have both authenticated and unauthenticated modes."""
    try:
        return await require_lore_context(
            request, authorization, x_lore_internal, user_id_q, target_url_q, target_key_q
        )
    except HTTPException:
        return None
