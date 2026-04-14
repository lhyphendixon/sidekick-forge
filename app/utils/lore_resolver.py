"""
Lore target resolver — shared between trigger endpoints and admin routes.

Figures out where a given user's personal Lore lives:
  - Real platform user_id (resolves shadow users to their owner)
  - Home client id (the client whose Supabase holds this user's Lore)
  - Target Supabase URL/key (for dedicated instances; None means platform DB)

The resolution handles four cases:
  1. Session user_id is a shadow user → look up platform_user_id in
     platform_client_user_mappings and use that real user.
  2. Platform user has tenant_assignments.admin_client_ids → first one is home.
  3. Platform user is a superadmin (no home client) → fall back to Leandrew Dixon.
  4. Platform user is a regular end-user with no home → falls back to the
     sidekick's client (so their context is preserved locally).
"""

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


async def resolve_lore_target_for_session(
    session_user_id: str,
    sidekick_client_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve the Lore target for a sidekick session.

    Args:
        session_user_id: The user_id in the incoming request. May be a shadow
                         user if this is a cross-client embed session.
        sidekick_client_id: The client_id of the sidekick being triggered.
                            Used as the fallback home when the user has no
                            explicit one.

    Returns:
        {
            "user_id":         real platform user id (may equal session_user_id),
            "session_user_id": the original session user id (for reference),
            "home_client_id":  the client whose Supabase holds this user's Lore,
            "target_url":      Supabase URL if dedicated (else None),
            "target_key":      Supabase service role key if dedicated (else None),
        }
    """
    result = {
        "user_id": session_user_id,
        "session_user_id": session_user_id,
        "home_client_id": None,
        "target_url": None,
        "target_key": None,
    }

    try:
        from app.integrations.supabase_client import supabase_manager
        from app.utils.supabase_credentials import SupabaseCredentialManager

        # Step 1: If this user is a shadow user, resolve to their real platform user.
        try:
            mapping = (
                supabase_manager.admin_client
                .table("platform_client_user_mappings")
                .select("platform_user_id,client_id")
                .eq("client_user_id", session_user_id)
                .execute()
            )
            if mapping.data:
                matched = next(
                    (m for m in mapping.data if m.get("client_id") == sidekick_client_id),
                    mapping.data[0],
                )
                real_user_id = matched["platform_user_id"]
                if real_user_id and real_user_id != session_user_id:
                    result["user_id"] = real_user_id
                    logger.info(
                        f"🔮 Lore: resolved shadow {session_user_id[:8]} → "
                        f"platform {real_user_id[:8]}"
                    )
        except Exception as exc:
            logger.debug(f"Lore: no shadow mapping for {session_user_id[:8]}: {exc}")

        real_user_id = result["user_id"]

        # Step 2: Look up the real user's tenant assignments
        lore_home_client_id: Optional[str] = None
        is_super_admin = False
        try:
            user_row = supabase_manager.admin_client.auth.admin.get_user_by_id(real_user_id)
            user_meta = getattr(user_row.user, "user_metadata", {}) or {}
            app_meta = getattr(user_row.user, "app_metadata", {}) or {}

            admin_client_ids = (user_meta.get("tenant_assignments") or {}).get("admin_client_ids") or []
            if admin_client_ids:
                lore_home_client_id = admin_client_ids[0]

            platform_role = (
                user_meta.get("platform_role")
                or app_meta.get("platform_role")
                or ""
            ).lower()
            is_super_admin = platform_role in ("super_admin", "superadmin")
        except Exception as exc:
            logger.debug(f"Lore: user metadata lookup failed for {real_user_id[:8]}: {exc}")

        # Step 3: Superadmin fallback → Leandrew Dixon
        if not lore_home_client_id and is_super_admin:
            try:
                fb = (
                    supabase_manager.admin_client
                    .table("clients")
                    .select("id")
                    .eq("name", "Leandrew Dixon")
                    .maybe_single()
                    .execute()
                )
                if fb and fb.data:
                    lore_home_client_id = fb.data["id"]
            except Exception:
                pass

        # Step 4: Final fallback — sidekick's own client
        if not lore_home_client_id and sidekick_client_id:
            lore_home_client_id = sidekick_client_id

        result["home_client_id"] = lore_home_client_id

        # Step 5: Fetch the client's Supabase credentials
        if lore_home_client_id:
            try:
                t_url, _anon, t_key = await SupabaseCredentialManager.get_client_supabase_credentials(lore_home_client_id)
                platform_url = os.getenv("SUPABASE_URL", "")
                if t_url and t_key and t_url != platform_url:
                    result["target_url"] = t_url
                    result["target_key"] = t_key
            except Exception as exc:
                logger.debug(f"Lore: could not fetch creds for {lore_home_client_id}: {exc}")

        logger.info(
            f"🔮 Lore target: session_user={session_user_id[:8]} "
            f"lore_user={real_user_id[:8]} "
            f"home={lore_home_client_id[:8] if lore_home_client_id else 'none'} "
            f"target={'dedicated' if result['target_url'] else 'platform'}"
        )
    except Exception as exc:
        logger.warning(f"Lore target resolution failed: {exc}")

    return result
