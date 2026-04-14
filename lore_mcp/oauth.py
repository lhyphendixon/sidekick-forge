"""
OAuth 2.1 shim for Lore MCP.

Exposes the endpoints Claude.ai web and other compliant MCP clients expect for
dynamic client registration (RFC 7591) + PKCE authorization_code grant, so
non-developer users can connect to their Lore MCP via a browser-based consent
flow instead of copy/pasting a static API key.

Endpoint split across two hosts:

    lore-staging.sidekickforge.com (this module):
        GET  /.well-known/oauth-authorization-server
        GET  /.well-known/oauth-protected-resource
        POST /register          (dynamic client registration)
        POST /token             (code + refresh exchange)

    staging.sidekickforge.com (app/api/v1/lore_oauth.py):
        GET  /lore/oauth/authorize   (login-gated consent page)
        POST /lore/oauth/consent     (creates authorization code)

Both hosts share the `lore_oauth_*` tables on the platform Supabase so the
authorization code written by the main app can be redeemed here. The token
tables are also shared — the MCP bearer middleware checks lore_oauth_tokens
alongside the static lore_api_keys table.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from auth import _get_platform_client, hash_token

LOGGER = logging.getLogger("lore_mcp.oauth")

# Where users go to log in + approve — served by the main Sidekick Forge app.
PLATFORM_BASE_URL = os.getenv(
    "SIDEKICK_FORGE_BASE_URL", "https://staging.sidekickforge.com"
).rstrip("/")

# Where the MCP itself lives — used in discovery docs + to build self-referential
# URLs. Can be overridden for local dev.
LORE_MCP_BASE_URL = os.getenv(
    "LORE_MCP_BASE_URL", "https://lore-staging.sidekickforge.com"
).rstrip("/")

ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=30)
DEFAULT_SCOPE = "lore:read lore:write"
OAUTH_TOKEN_PREFIX = "slf_oauth_"
OAUTH_REFRESH_PREFIX = "slf_oauth_rt_"


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _verify_pkce(code_verifier: str, code_challenge: str, method: str) -> bool:
    """Verify a PKCE code_verifier against the stored code_challenge.

    Only S256 is allowed — plain is explicitly rejected to comply with OAuth 2.1.
    """
    if method != "S256":
        return False
    if not code_verifier or not code_challenge:
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge)


# ---------------------------------------------------------------------------
# Token generation
# ---------------------------------------------------------------------------

def _new_access_token() -> str:
    return f"{OAUTH_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def _new_refresh_token() -> str:
    return f"{OAUTH_REFRESH_PREFIX}{secrets.token_urlsafe(32)}"


def _new_client_id() -> str:
    # RFC 7591 allows any unique string. Prefix makes logs readable.
    return f"lore_client_{secrets.token_urlsafe(16)}"


def _new_authorization_code() -> str:
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Redirect-URI validation
# ---------------------------------------------------------------------------

# Claude.ai publishes rotating callback URLs under a handful of hosts; rather
# than hard-code them we allow any https redirect and the well-known localhost
# loopback pattern for CLIs. Strict equality with whatever the client
# registered is still enforced at authorize + token time.
def _is_valid_redirect_uri(uri: str) -> bool:
    try:
        parsed = urlparse(uri)
    except Exception:
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost"):
        return True
    return False


# ---------------------------------------------------------------------------
# OAuth token lookup — used by the bearer auth middleware
# ---------------------------------------------------------------------------

def resolve_oauth_token(raw: str) -> Optional[tuple[str, str]]:
    """Look up an OAuth-issued access token in lore_oauth_tokens.
    Returns (user_id, token_row_id) or None if invalid/expired/revoked."""
    if not raw or not raw.startswith(OAUTH_TOKEN_PREFIX):
        return None
    client = _get_platform_client()
    if client is None:
        LOGGER.error("Platform Supabase client not configured — cannot validate OAuth tokens")
        return None

    token_hash = hash_token(raw)
    try:
        resp = (
            client
            .table("lore_oauth_tokens")
            .select("id,user_id,expires_at,revoked_at")
            .eq("access_token_hash", token_hash)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        LOGGER.warning(f"OAuth token lookup failed: {exc}")
        return None

    if not resp or not resp.data:
        return None
    row = resp.data
    if row.get("revoked_at"):
        return None
    expires_at = row.get("expires_at")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp_dt < datetime.now(timezone.utc):
                return None
        except Exception:
            pass

    # Best-effort last_used_at bump
    try:
        client.table("lore_oauth_tokens").update(
            {"last_used_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", row["id"]).execute()
    except Exception:
        pass

    return row["user_id"], row["id"]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    # RFC 7591 — all fields optional per spec, server fills defaults.
    client_name: Optional[str] = None
    redirect_uris: List[str] = Field(default_factory=list)
    grant_types: Optional[List[str]] = None
    response_types: Optional[List[str]] = None
    token_endpoint_auth_method: Optional[str] = None
    scope: Optional[str] = None

    class Config:
        extra = "allow"  # ignore unknown fields rather than 422


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server_metadata() -> JSONResponse:
    """RFC 8414 discovery doc. Claude.ai reads this to find the authorization +
    token endpoints. authorization_endpoint lives on the main platform app
    (which has the login session); everything else lives here on lore-mcp."""
    return JSONResponse(
        {
            "issuer": LORE_MCP_BASE_URL,
            "authorization_endpoint": f"{PLATFORM_BASE_URL}/admin/lore/oauth/authorize",
            "token_endpoint": f"{LORE_MCP_BASE_URL}/token",
            "registration_endpoint": f"{LORE_MCP_BASE_URL}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["lore:read", "lore:write"],
            "service_documentation": f"{PLATFORM_BASE_URL}/admin/lore",
        }
    )


@router.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource_metadata() -> JSONResponse:
    """RFC 9728 — advertises which authorization server protects this resource.
    Claude.ai fetches this after an unauthenticated request to the MCP to
    discover where it should go to get a token."""
    return JSONResponse(
        {
            "resource": LORE_MCP_BASE_URL,
            "authorization_servers": [LORE_MCP_BASE_URL],
            "scopes_supported": ["lore:read", "lore:write"],
            "bearer_methods_supported": ["header"],
        }
    )


@router.post("/register")
async def register_client(body: RegisterRequest) -> JSONResponse:
    """RFC 7591 dynamic client registration. Public clients only — no secret
    is issued because PKCE is mandatory. Any valid redirect URIs are accepted;
    the actual check happens at authorize + token time via strict equality."""
    client = _get_platform_client()
    if client is None:
        raise HTTPException(status_code=500, detail="Platform database not configured.")

    if not body.redirect_uris:
        raise HTTPException(status_code=400, detail="redirect_uris is required.")
    for uri in body.redirect_uris:
        if not _is_valid_redirect_uri(uri):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid redirect_uri: {uri}. Must be https or http loopback.",
            )

    client_id = _new_client_id()
    grant_types = body.grant_types or ["authorization_code", "refresh_token"]
    response_types = body.response_types or ["code"]
    scope = body.scope or DEFAULT_SCOPE

    row = {
        "client_id": client_id,
        "client_name": (body.client_name or "MCP Client")[:200],
        "redirect_uris": body.redirect_uris,
        "grant_types": grant_types,
        "response_types": response_types,
        "token_endpoint_auth_method": "none",
        "scope": scope,
    }
    try:
        client.table("lore_oauth_clients").insert(row).execute()
    except Exception as exc:
        LOGGER.error(f"Failed to register OAuth client: {exc}")
        raise HTTPException(status_code=500, detail="Registration failed.")

    LOGGER.info(f"OAuth client registered: {client_id} ({row['client_name']})")

    return JSONResponse(
        status_code=201,
        content={
            "client_id": client_id,
            "client_id_issued_at": int(datetime.now(timezone.utc).timestamp()),
            "client_name": row["client_name"],
            "redirect_uris": body.redirect_uris,
            "grant_types": grant_types,
            "response_types": response_types,
            "token_endpoint_auth_method": "none",
            "scope": scope,
        },
    )


@router.post("/token")
async def token_endpoint(
    request: Request,
    grant_type: str = Form(...),
    code: Optional[str] = Form(None),
    redirect_uri: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    code_verifier: Optional[str] = Form(None),
    refresh_token: Optional[str] = Form(None),
) -> JSONResponse:
    """Exchanges an authorization code or refresh token for an access token.
    Public client only — no client_secret, PKCE is mandatory for the auth code
    path. All errors are returned as the OAuth 2.0 JSON error shape."""
    client = _get_platform_client()
    if client is None:
        return _oauth_error("server_error", "Platform database not configured.", 500)

    if grant_type == "authorization_code":
        return await _exchange_authorization_code(
            client, code, redirect_uri, client_id, code_verifier
        )
    if grant_type == "refresh_token":
        return await _exchange_refresh_token(client, refresh_token, client_id)

    return _oauth_error("unsupported_grant_type", f"grant_type '{grant_type}' not supported")


# ---------------------------------------------------------------------------
# Grant handlers
# ---------------------------------------------------------------------------

async def _exchange_authorization_code(
    client: SupabaseClient,
    code: Optional[str],
    redirect_uri: Optional[str],
    client_id: Optional[str],
    code_verifier: Optional[str],
) -> JSONResponse:
    if not code or not redirect_uri or not client_id or not code_verifier:
        return _oauth_error(
            "invalid_request",
            "Missing one of: code, redirect_uri, client_id, code_verifier",
        )

    try:
        resp = (
            client.table("lore_oauth_authorization_codes")
            .select("*")
            .eq("code", code)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        LOGGER.warning(f"Auth code lookup failed: {exc}")
        return _oauth_error("server_error", "Auth code lookup failed", 500)

    if not resp or not resp.data:
        return _oauth_error("invalid_grant", "Authorization code not found")
    row = resp.data

    if row.get("used_at"):
        # Replay — revoke every token we've ever issued for this client_id
        # per OAuth 2.1 replay-detection guidance.
        LOGGER.warning(f"OAuth code replay detected: {code[:8]}...")
        try:
            client.table("lore_oauth_tokens").update(
                {"revoked_at": datetime.now(timezone.utc).isoformat()}
            ).eq("client_id", row["client_id"]).execute()
        except Exception:
            pass
        return _oauth_error("invalid_grant", "Authorization code already used")

    expires_at = row.get("expires_at")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp_dt < datetime.now(timezone.utc):
                return _oauth_error("invalid_grant", "Authorization code expired")
        except Exception:
            pass

    if row["client_id"] != client_id:
        return _oauth_error("invalid_grant", "Client ID mismatch")
    if row["redirect_uri"] != redirect_uri:
        return _oauth_error("invalid_grant", "redirect_uri mismatch")

    if not _verify_pkce(
        code_verifier,
        row["code_challenge"],
        row.get("code_challenge_method", "S256"),
    ):
        return _oauth_error("invalid_grant", "PKCE verification failed")

    # Mark single-use
    try:
        client.table("lore_oauth_authorization_codes").update(
            {"used_at": datetime.now(timezone.utc).isoformat()}
        ).eq("code", code).execute()
    except Exception:
        pass

    return _issue_tokens(client, client_id=client_id, user_id=row["user_id"], scope=row["scope"])


async def _exchange_refresh_token(
    client: SupabaseClient,
    refresh_token: Optional[str],
    client_id: Optional[str],
) -> JSONResponse:
    if not refresh_token or not client_id:
        return _oauth_error("invalid_request", "Missing refresh_token or client_id")

    token_hash = hash_token(refresh_token)
    try:
        resp = (
            client.table("lore_oauth_tokens")
            .select("*")
            .eq("refresh_token_hash", token_hash)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        LOGGER.warning(f"Refresh token lookup failed: {exc}")
        return _oauth_error("server_error", "Refresh token lookup failed", 500)

    if not resp or not resp.data:
        return _oauth_error("invalid_grant", "Unknown refresh token")
    row = resp.data
    if row.get("revoked_at"):
        return _oauth_error("invalid_grant", "Refresh token revoked")
    if row["client_id"] != client_id:
        return _oauth_error("invalid_grant", "Client ID mismatch")
    if row.get("refresh_expires_at"):
        try:
            exp_dt = datetime.fromisoformat(row["refresh_expires_at"].replace("Z", "+00:00"))
            if exp_dt < datetime.now(timezone.utc):
                return _oauth_error("invalid_grant", "Refresh token expired")
        except Exception:
            pass

    # Revoke the old token (rotate — OAuth 2.1 recommends)
    try:
        client.table("lore_oauth_tokens").update(
            {"revoked_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", row["id"]).execute()
    except Exception:
        pass

    return _issue_tokens(client, client_id=client_id, user_id=row["user_id"], scope=row["scope"])


def _issue_tokens(
    client: SupabaseClient,
    *,
    client_id: str,
    user_id: str,
    scope: str,
) -> JSONResponse:
    access_raw = _new_access_token()
    refresh_raw = _new_refresh_token()
    now = datetime.now(timezone.utc)
    expires_at = now + ACCESS_TOKEN_TTL
    refresh_expires_at = now + REFRESH_TOKEN_TTL

    try:
        client.table("lore_oauth_tokens").insert(
            {
                "access_token_hash": hash_token(access_raw),
                "refresh_token_hash": hash_token(refresh_raw),
                "client_id": client_id,
                "user_id": user_id,
                "scope": scope,
                "expires_at": expires_at.isoformat(),
                "refresh_expires_at": refresh_expires_at.isoformat(),
            }
        ).execute()
        client.table("lore_oauth_clients").update(
            {"last_used_at": now.isoformat()}
        ).eq("client_id", client_id).execute()
    except Exception as exc:
        LOGGER.error(f"Failed to persist issued tokens: {exc}")
        return _oauth_error("server_error", "Failed to issue token", 500)

    return JSONResponse(
        {
            "access_token": access_raw,
            "token_type": "Bearer",
            "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
            "refresh_token": refresh_raw,
            "scope": scope,
        }
    )


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "error_description": description},
    )
