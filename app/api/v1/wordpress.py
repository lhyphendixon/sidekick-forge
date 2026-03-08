from fastapi import APIRouter, HTTPException, status, Depends, Request
from fastapi.responses import JSONResponse
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import json
import time
import hmac
import hashlib
import logging

import httpx

from app.config import settings
from app.models.common import APIResponse, SuccessResponse
from app.models.agent import Agent
from app.middleware.auth import get_current_auth, require_site_auth
from app.integrations.supabase_client import supabase_manager
from app.utils.exceptions import NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter()
BRIDGE_MAX_SKEW_SECONDS = getattr(settings, "wordpress_bridge_max_skew", 300)

@router.get("/agent-settings/{agent_slug}", response_model=APIResponse)
async def get_agent_settings(
    agent_slug: str,
    auth=Depends(require_site_auth)
):
    """
    Get agent settings for WordPress plugin (compatibility endpoint)
    """
    try:
        # Get agent configuration
        config = await supabase_manager.get_agent_configuration(agent_slug)
        
        if not config:
            raise NotFoundError(f"Agent '{agent_slug}' not found")
        
        # Format response for WordPress plugin compatibility
        settings = {
            "agent_id": config["agent_id"],
            "agent_slug": config["agent_slug"],
            "agent_name": config["agent_name"],
            "system_prompt": config["system_prompt"],
            "voice_id": config["voice_id"],
            "temperature": config["temperature"],
            "max_tokens": config["max_tokens"],
            "model": config["model"],
            "provider_config": config["provider_config"],
            "voice_settings": config["voice_settings"],
            "stt": {
                "provider": config["stt_provider"],
                "model": config["stt_model"],
                "language": config.get("stt_language", "en")
            },
            "tts": {
                "provider": config["tts_provider"],
                "model": config["tts_model"],
                "voice": config["tts_voice"]
            },
            "livekit": {
                "url": config["livekit_url"] or settings.livekit_url,
                "api_key": config["livekit_api_key"] or settings.livekit_api_key,
                "api_secret": config["livekit_api_secret"] or settings.livekit_api_secret
            },
            "api_keys": {
                "openai": config["openai_api_key"],
                "anthropic": config["anthropic_api_key"],
                "groq": config["groq_api_key"]
            },
            "webhooks": {
                "voice_context": config["voice_context_webhook_url"],
                "text_context": config["text_context_webhook_url"]
            }
        }
        
        return APIResponse(
            success=True,
            data=settings
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_slug}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/sync-user", response_model=APIResponse[SuccessResponse])
async def sync_wordpress_user(
    user_data: Dict[str, Any],
    auth=Depends(require_site_auth)
):
    """
    Sync WordPress user data with SaaS backend
    """
    try:
        # Extract user information
        wp_user_id = user_data.get("id")
        email = user_data.get("email")
        username = user_data.get("username")
        display_name = user_data.get("display_name")
        
        if not wp_user_id or not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User ID and email are required"
            )
        
        # Create unique identifier for WordPress user
        unique_id = f"{auth.site_domain}:wp_user_{wp_user_id}"
        
        # Check if user mapping exists
        query = supabase_manager.admin_client.table("wordpress_user_mappings").select("*").eq("wp_identifier", unique_id)
        result = await supabase_manager.execute_query(query)
        
        if not result:
            # Create new mapping
            mapping_data = {
                "wp_identifier": unique_id,
                "site_id": auth.site_id,
                "wp_user_id": wp_user_id,
                "email": email,
                "username": username,
                "display_name": display_name,
                "user_metadata": user_data,
                "created_at": datetime.utcnow().isoformat()
            }
            
            await supabase_manager.execute_query(
                supabase_manager.admin_client.table("wordpress_user_mappings").insert(mapping_data)
            )
        else:
            # Update existing mapping
            update_data = {
                "email": email,
                "username": username,
                "display_name": display_name,
                "user_metadata": user_data,
                "last_seen_at": datetime.utcnow().isoformat()
            }
            
            await supabase_manager.execute_query(
                supabase_manager.admin_client.table("wordpress_user_mappings")
                .update(update_data)
                .eq("wp_identifier", unique_id)
            )
        
        return APIResponse(
            success=True,
            data=SuccessResponse(
                message=f"User {email} synced successfully"
            )
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/site-info", response_model=APIResponse)
async def get_site_info(auth=Depends(require_site_auth)):
    """
    Get information about the authenticated WordPress site
    """
    try:
        # Get site information
        query = supabase_manager.admin_client.table("wordpress_sites").select("*").eq("id", auth.site_id)
        result = await supabase_manager.execute_query(query)
        
        if not result:
            raise NotFoundError("Site information not found")
        
        site = result[0]
        
        # Remove sensitive information
        site.pop("api_key_hash", None)
        
        return APIResponse(
            success=True,
            data=site
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Site not found"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/report-usage", response_model=APIResponse[SuccessResponse])
async def report_usage(
    usage_data: Dict[str, Any],
    auth=Depends(require_site_auth)
):
    """
    Report usage statistics from WordPress plugin
    """
    try:
        # Update site usage statistics
        update_data = {
            "total_conversations": usage_data.get("total_conversations", 0),
            "total_messages": usage_data.get("total_messages", 0),
            "total_agents": usage_data.get("total_agents", 0),
            "last_seen_at": datetime.utcnow().isoformat()
        }
        
        # Update site metadata
        if "metadata" in usage_data:
            update_data["site_metadata"] = usage_data["metadata"]
        
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("wordpress_sites")
            .update(update_data)
            .eq("id", auth.site_id)
        )
        
        # Log usage event
        usage_event = {
            "site_id": auth.site_id,
            "event_type": "usage_report",
            "usage_data": usage_data,
            "created_at": datetime.utcnow().isoformat()
        }
        
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("site_usage_events").insert(usage_event)
        )
        
        return APIResponse(
            success=True,
            data=SuccessResponse(
                message="Usage data recorded successfully"
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

def _encode_payload_for_signature(payload: Dict[str, Any]) -> str:
    """Encode payload deterministically to match WordPress signature generation."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


async def _fetch_supabase_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Fetch Supabase auth user metadata by email via Admin API.

    Note: Supabase Admin API's email param doesn't filter - it returns all users.
    We must filter client-side to find the exact email match.
    """
    normalized_email = email.lower().strip()
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users"
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }
    # Note: The 'email' param doesn't actually filter in Supabase Admin API
    # We need to paginate through all users or use a different approach
    try:
        page = 1
        per_page = 100
        while True:
            params = {"page": page, "per_page": per_page}
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=params, headers=headers)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()

            users = []
            if isinstance(data, dict):
                users = data.get("users") or data.get("data") or []
            elif isinstance(data, list):
                users = data

            # Find exact email match (case-insensitive)
            for user in users:
                user_email = (user.get("email") or "").lower().strip()
                if user_email == normalized_email:
                    logger.info("Found exact match for email %s: id=%s", email, user.get("id"))
                    return user

            # If we got fewer users than per_page, we've reached the end
            if len(users) < per_page:
                logger.info("No user found for email %s after checking all pages", email)
                return None

            page += 1
            # Safety limit to prevent infinite loops
            if page > 50:
                logger.warning("Reached page limit (50) searching for email %s", email)
                return None

    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch Supabase user by email %s: %s", email, exc)
    return None


# DEPRECATED: Password derivation is no longer used.
# We now use admin API to generate sessions directly without passwords.
# Keeping for reference only.
def _derive_password_deprecated(payload: Dict[str, Any], site_secret: str) -> str:
    """DEPRECATED: Create deterministic password used solely for service-side sign-ins."""
    raw = f"{site_secret}:{payload.get('site','')}:{payload.get('wp_user_id')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _get_wordpress_site_by_url(site_url: str) -> Optional[Dict[str, Any]]:
    """Fetch WordPress site configuration by site URL."""
    if not site_url:
        return None

    # Ensure supabase_manager is initialized
    if not getattr(supabase_manager, "_initialized", False):
        await supabase_manager.initialize()

    # Normalize the site URL (remove protocol, trailing slashes)
    normalized_url = site_url.lower().strip()
    for prefix in ["https://", "http://", "www."]:
        if normalized_url.startswith(prefix):
            normalized_url = normalized_url[len(prefix):]
    normalized_url = normalized_url.rstrip("/")

    try:
        # Try exact match first
        result = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("wordpress_sites")
            .select("*")
            .eq("site_url", normalized_url)
            .eq("is_active", True)
            .limit(1)
        )
        if result and len(result) > 0:
            return result[0]

        # Try with original URL (in case it was stored differently)
        result = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("wordpress_sites")
            .select("*")
            .ilike("site_url", f"%{normalized_url}%")
            .eq("is_active", True)
            .limit(1)
        )
        if result and len(result) > 0:
            return result[0]

    except Exception as exc:
        logger.warning("Failed to fetch WordPress site by URL %s: %s", site_url, exc)

    return None


def _serialize_value(val: Any) -> Any:
    """Convert datetime objects to ISO strings for JSON serialization."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_serialize_value(item) for item in val]
    return val


def _normalize_user(user_obj: Any) -> Dict[str, Any]:
    if not user_obj:
        return {}
    if isinstance(user_obj, dict):
        return {k: _serialize_value(v) for k, v in user_obj.items()}
    return {
        "id": getattr(user_obj, "id", None),
        "aud": getattr(user_obj, "aud", None),
        "email": getattr(user_obj, "email", None),
        "phone": getattr(user_obj, "phone", None),
        "role": getattr(user_obj, "role", None),
        "last_sign_in_at": _serialize_value(getattr(user_obj, "last_sign_in_at", None)),
        "app_metadata": _serialize_value(getattr(user_obj, "app_metadata", None)),
        "user_metadata": _serialize_value(getattr(user_obj, "user_metadata", None)),
    }


def _normalize_session(session_obj: Any, user_obj: Any) -> Dict[str, Any]:
    if not session_obj:
        return {}
    return {
        "access_token": getattr(session_obj, "access_token", None),
        "token_type": getattr(session_obj, "token_type", None),
        "expires_in": getattr(session_obj, "expires_in", None),
        "refresh_token": getattr(session_obj, "refresh_token", None),
        "provider_token": getattr(session_obj, "provider_token", None),
        "provider_refresh_token": getattr(session_obj, "provider_refresh_token", None),
        "user": _normalize_user(user_obj),
    }


async def _upsert_profile(user_id: str, email: str, display_name: Optional[str]):
    profile_data = {
        "user_id": user_id,
        "email": email,
        "full_name": display_name or email,
        "updated_at": datetime.utcnow().isoformat(),
        "created_at": datetime.utcnow().isoformat(),
    }
    try:
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("profiles").upsert(profile_data, on_conflict="user_id")
        )
    except Exception as exc:
        logger.warning("Failed to upsert profile for %s: %s", user_id, exc)


async def _upsert_wp_mapping(user_id: str, payload: Dict[str, Any]):
    identifier = f"{payload.get('site','unknown')}::wp_user_{payload.get('wp_user_id')}"
    mapping = {
        "wp_identifier": identifier,
        "site_url": payload.get("site"),
        "wp_user_id": payload.get("wp_user_id"),
        "email": payload.get("email"),
        "display_name": payload.get("display_name"),
        "roles": payload.get("roles"),
        "user_id": user_id,
        "updated_at": datetime.utcnow().isoformat(),
    }
    try:
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("wordpress_user_mappings").upsert(mapping, on_conflict="wp_identifier")
        )
    except Exception as exc:
        logger.debug("wordpress_user_mappings upsert skipped or failed: %s", exc)


async def _ensure_supabase_user_no_password(payload: Dict[str, Any]) -> str:
    """Ensure a Supabase Auth user exists for the WordPress identity.

    This function NEVER modifies passwords. It either:
    1. Returns an existing user's ID (preserving their password)
    2. Creates a new user with a random password (they can reset it if needed)

    The WordPress bridge uses admin API to generate sessions directly,
    so no password synchronization is needed.
    """
    email = payload.get("email")
    display_name = payload.get("display_name") or email
    roles = payload.get("roles") or []

    # WordPress-specific metadata (stored separately from platform user data)
    wp_metadata = {
        "wordpress_user_id": payload.get("wp_user_id"),
        "wordpress_site": payload.get("site"),
        "wordpress_roles": roles,
        "display_name": display_name,
    }

    admin_auth = supabase_manager.admin_client.auth.admin
    user_record = await _fetch_supabase_user_by_email(email)
    user_id: Optional[str] = None

    if user_record and user_record.get("id"):
        # User exists - just return their ID, DO NOT modify their password
        user_id = user_record["id"]
        logger.info(
            "WordPress bridge: Found existing user %s (%s) - using their account without password modification",
            user_id, email
        )

        # Optionally update WordPress-related metadata (but NOT password)
        # Only update if this is already a WordPress-linked user or has no platform_role
        user_metadata = user_record.get("user_metadata") or {}
        if not user_metadata.get("platform_role"):
            # Safe to add/update WordPress metadata for non-admin users
            try:
                # Merge existing metadata with WordPress metadata
                merged_metadata = {**user_metadata, **wp_metadata}
                admin_auth.update_user_by_id(
                    user_id,
                    {"user_metadata": merged_metadata}
                )
                logger.info("Updated WordPress metadata for user %s", user_id)
            except Exception as exc:
                # Non-fatal - user can still authenticate
                logger.warning("Failed to update WordPress metadata for user %s: %s", user_id, exc)
    else:
        # No existing user - create a new one with a secure random password
        # They won't need this password since we use admin API for session generation
        import secrets
        random_password = secrets.token_urlsafe(32)

        try:
            create_response = admin_auth.create_user(
                {
                    "email": email,
                    "password": random_password,  # Random, never used
                    "email_confirm": True,
                    "user_metadata": wp_metadata,
                }
            )
            created_user = getattr(create_response, "user", None)
            if created_user:
                user_id = getattr(created_user, "id", None)
            logger.info("Created new WordPress-bridged user %s for %s (password NOT synced)", user_id, email)
        except Exception as exc:
            logger.error("Failed to create Supabase user for %s: %s", email, exc)
            # Attempt to refetch in case of race condition
            refreshed = await _fetch_supabase_user_by_email(email)
            user_id = refreshed.get("id") if refreshed else None

    if not user_id:
        raise HTTPException(status_code=500, detail="Unable to provision Supabase user.")

    await _upsert_profile(user_id, email, display_name)
    await _upsert_wp_mapping(user_id, payload)
    return user_id


async def _generate_admin_session(user_id: str, email: str) -> Dict[str, Any]:
    """Generate a Supabase session for a user using the Admin API.

    This bypasses password authentication entirely - the WordPress payload
    signature verification serves as the authentication proof.

    Uses the generateLink + verifyOtp pattern recommended by Supabase.
    Reference: https://github.com/orgs/supabase/discussions/11854
    """
    admin_headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }

    # Step 1: Generate a magic link (server-side, never sent to user)
    generate_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/generate_link"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                generate_url,
                headers=admin_headers,
                json={
                    "type": "magiclink",
                    "email": email,
                }
            )
            response.raise_for_status()
            link_data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Failed to generate magic link for %s: %s - %s", email, exc.response.status_code, exc.response.text)
        raise HTTPException(status_code=500, detail="Failed to generate session link.")
    except Exception as exc:
        logger.error("Failed to generate magic link for %s: %s", email, exc)
        raise HTTPException(status_code=500, detail="Failed to generate session link.")

    hashed_token = link_data.get("hashed_token")
    if not hashed_token:
        logger.error("No hashed_token in generate_link response for %s: %s", email, link_data)
        raise HTTPException(status_code=500, detail="Failed to generate authentication token.")

    logger.info("Generated magic link token for %s, verifying...", email)

    # Step 2: Verify the token using verifyOtp to get actual session tokens
    # Use the anon key for this endpoint (it's the public verification endpoint)
    verify_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/verify"
    anon_headers = {
        "apikey": settings.supabase_anon_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                verify_url,
                headers=anon_headers,
                json={
                    "type": "magiclink",
                    "token_hash": hashed_token,
                }
            )

            if response.status_code == 200:
                session_data = response.json()
                logger.info("Successfully verified token and got session for %s", email)
                return session_data

            # Try alternative endpoint format
            logger.warning("First verify attempt failed (%d), trying alternative...", response.status_code)

            # Some Supabase versions use /verify with GET
            verify_get_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/verify?token_hash={hashed_token}&type=magiclink"
            response = await client.get(verify_get_url, headers=anon_headers, follow_redirects=True)

            if response.status_code == 200:
                session_data = response.json()
                logger.info("Successfully verified token (GET) and got session for %s", email)
                return session_data

            # Try with 'token' instead of 'token_hash'
            response = await client.post(
                verify_url,
                headers=anon_headers,
                json={
                    "type": "magiclink",
                    "token": hashed_token,
                }
            )

            if response.status_code == 200:
                session_data = response.json()
                logger.info("Successfully verified token (token param) for %s", email)
                return session_data

            logger.error("All verify attempts failed for %s: %d - %s", email, response.status_code, response.text)

    except Exception as exc:
        logger.error("Failed to verify token for %s: %s", email, exc)

    # If verification fails, we need a fallback
    # Return user info and let the embed handle auth differently
    logger.warning("Could not generate full session for %s, returning user info for embed token auth", email)

    # Fetch user record for the response
    try:
        user_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(user_url, headers=admin_headers)
            response.raise_for_status()
            user_record = response.json()
    except Exception:
        user_record = {"id": user_id, "email": email}

    return {
        "user": _normalize_user(user_record),
        "user_id": user_id,
        "email": email,
        "auth_method": "wordpress_bridge_fallback",
        "requires_embed_token": True,  # Signal that embed should use its own token auth
    }


@router.post("/wordpress/session/exchange")
@router.post("/wordpress/session")
async def issue_wordpress_session(request: Request):
    """Exchange a signed WordPress session payload for Supabase Auth tokens."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be valid JSON.",
        )

    payload = body.get("payload")
    signature = body.get("signature") or request.headers.get("x-skf-signature")

    if not isinstance(payload, dict) or not signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing payload or signature.",
        )

    timestamp = payload.get("timestamp")
    if not isinstance(timestamp, int):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload missing timestamp.",
        )

    email = payload.get("email")
    wp_user_id = payload.get("wp_user_id")
    site_url = payload.get("site")
    if not email or wp_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload missing required fields.",
        )

    # Look up the WordPress site to get the shared secret
    wp_site = await _get_wordpress_site_by_url(site_url)
    if not wp_site:
        logger.warning("WordPress site not found for URL: %s", site_url)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"WordPress site not registered: {site_url}",
        )

    site_secret = wp_site.get("api_secret")
    if not site_secret:
        logger.error("WordPress site %s has no api_secret configured", site_url)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WordPress site has no shared secret configured.",
        )

    now = int(time.time())
    if abs(now - timestamp) > BRIDGE_MAX_SKEW_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Payload timestamp is outside the allowed window.",
        )

    encoded_payload = _encode_payload_for_signature(payload)
    expected_signature = hmac.new(
        site_secret.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Debug logging for signature mismatch
    if not hmac.compare_digest(expected_signature, signature):
        logger.warning(
            "Signature mismatch for site %s:\n  received_sig=%s\n  expected_sig=%s\n  payload_encoded_full=%s\n  secret_len=%d",
            site_url,
            signature,
            expected_signature,
            encoded_payload,
            len(site_secret) if site_secret else 0
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid payload signature.",
        )

    # Ensure user exists (without modifying any passwords)
    user_id = await _ensure_supabase_user_no_password(payload)

    # Generate session using admin API (no password needed)
    logger.info("Generating admin session for WordPress user %s (%s)", user_id, email)

    try:
        session_data = await _generate_admin_session(user_id, email)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to generate admin session for %s: %s", email, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create Supabase session.",
        )

    # Build response
    debug = {}
    if settings.debug:
        debug = {
            "wp_user_id": payload.get("wp_user_id"),
            "wp_site": payload.get("site"),
            "roles": payload.get("roles"),
            "auth_method": "admin_api_no_password",
        }

    response_body = session_data
    if debug:
        response_body = {**session_data, "bridge_debug": debug}

    return JSONResponse(response_body)

@router.post("/webhook/test", response_model=APIResponse[SuccessResponse])
async def test_webhook(
    request: Request,
    auth=Depends(require_site_auth)
):
    """
    Test webhook endpoint for WordPress plugin connectivity
    """
    try:
        # Get request data
        body = await request.body()
        headers = dict(request.headers)
        
        # Log test webhook
        test_data = {
            "site_id": auth.site_id,
            "headers": headers,
            "body_size": len(body),
            "timestamp": datetime.utcnow().isoformat()
        }
        
        return APIResponse(
            success=True,
            data=SuccessResponse(
                message="Webhook test successful"
            ),
            meta=test_data
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
