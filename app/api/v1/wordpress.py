from fastapi import APIRouter, HTTPException, status, Depends, Request
from fastapi.responses import JSONResponse
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import json
import time
import hmac
import hashlib
import logging
import uuid

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
                "url": config["livekit_url"] or settings.livekit_url
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
    """Fetch Supabase auth user metadata by email via Admin API."""
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users"
    logger.warning(f"DEBUG _fetch_supabase_user_by_email: settings.supabase_url = {settings.supabase_url}")
    logger.warning(f"DEBUG _fetch_supabase_user_by_email: Full URL = {url}")
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }
    params = {"email": email}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
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
        if isinstance(users, list) and users:
            return users[0]
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch Supabase user by email: %s", exc)
    return None


def _derive_password(payload: Dict[str, Any]) -> str:
    """Create deterministic password used solely for service-side sign-ins."""
    raw = f"{settings.wordpress_bridge_secret}:{payload.get('site','')}:{payload.get('wp_user_id')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_user(user_obj: Any) -> Dict[str, Any]:
    if not user_obj:
        return {}
    if isinstance(user_obj, dict):
        return user_obj
    return {
        "id": getattr(user_obj, "id", None),
        "aud": getattr(user_obj, "aud", None),
        "email": getattr(user_obj, "email", None),
        "phone": getattr(user_obj, "phone", None),
        "role": getattr(user_obj, "role", None),
        "last_sign_in_at": getattr(user_obj, "last_sign_in_at", None),
        "app_metadata": getattr(user_obj, "app_metadata", None),
        "user_metadata": getattr(user_obj, "user_metadata", None),
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


async def _ensure_supabase_user(payload: Dict[str, Any]) -> str:
    """Ensure a Supabase Auth user exists for the WordPress identity."""
    email = payload.get("email")
    display_name = payload.get("display_name") or email
    roles = payload.get("roles") or []
    derived_password = _derive_password(payload)
    metadata = {
        "wordpress_user_id": payload.get("wp_user_id"),
        "wordpress_site": payload.get("site"),
        "wordpress_roles": roles,
        "display_name": display_name,
    }

    # Create a fresh Supabase client for this operation to avoid any global state issues
    from supabase import create_client
    platform_admin_client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    logger.warning(f"DEBUG _ensure_supabase_user: Platform URL = {settings.supabase_url}")
    logger.warning(f"DEBUG _ensure_supabase_user: platform_admin_client.supabase_url = {getattr(platform_admin_client, 'supabase_url', 'N/A')}")
    admin_auth = platform_admin_client.auth.admin
    user_record = await _fetch_supabase_user_by_email(email)
    user_id: Optional[str] = None

    if user_record and user_record.get("id"):
        user_id = user_record["id"]
        try:
            admin_auth.update_user_by_id(
                user_id,
                {
                    "password": derived_password,
                    "email_confirm": True,
                    "user_metadata": metadata,
                },
            )
        except Exception as exc:
            logger.warning("Failed to update Supabase user %s: %s", user_id, exc)
    else:
        try:
            create_response = admin_auth.create_user(
                {
                    "email": email,
                    "password": derived_password,
                    "email_confirm": True,
                    "user_metadata": metadata,
                }
            )
            created_user = getattr(create_response, "user", None)
            if created_user:
                user_id = getattr(created_user, "id", None)
        except Exception as exc:
            logger.error("Failed to create Supabase user for %s: %s", email, exc)
            # Attempt to refetch in case of race
            refreshed = await _fetch_supabase_user_by_email(email)
            user_id = refreshed.get("id") if refreshed else None

    if not user_id:
        raise HTTPException(status_code=500, detail="Unable to provision Supabase user.")

    await _upsert_profile(user_id, email, display_name)
    await _upsert_wp_mapping(user_id, payload)
    return user_id


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

    now = int(time.time())
    if abs(now - timestamp) > BRIDGE_MAX_SKEW_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Payload timestamp is outside the allowed window.",
        )

    # Look up the WordPress site by domain to get the per-site api_secret
    bridge_secret = None
    wordpress_site = None

    if site_url:
        # Normalize the site URL to match how we store domains
        import re
        normalized_domain = re.sub(r'^https?://', '', site_url).lower().strip('/').split('/')[0]
        logger.info(f"WordPress session: Looking up site by domain: {normalized_domain}")

        # Query the wordpress_sites table
        try:
            from supabase import create_client
            platform_client = create_client(settings.supabase_url, settings.supabase_service_role_key)
            result = platform_client.table("wordpress_sites").select("*").eq("site_url", normalized_domain).execute()
            if result.data and len(result.data) > 0:
                wordpress_site = result.data[0]
                bridge_secret = wordpress_site.get("api_secret")
                logger.info(f"WordPress session: Found site {normalized_domain} with client_id {wordpress_site.get('client_id')}")
        except Exception as e:
            logger.error(f"WordPress session: Error looking up site: {e}")

    # Fall back to global secret if no per-site secret found
    if not bridge_secret:
        bridge_secret = settings.wordpress_bridge_secret
        logger.info("WordPress session: Using global WORDPRESS_BRIDGE_SECRET")

    if not bridge_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WordPress session bridge is not configured.",
        )

    encoded_payload = _encode_payload_for_signature(payload)
    expected_signature = hmac.new(
        bridge_secret.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid payload signature.",
        )

    # We need to create a session in the CLIENT's Supabase, not the platform's
    # The embed authenticates against the client's Supabase project
    from supabase import create_client

    client_supabase_url = None
    client_supabase_anon_key = None
    client_supabase_service_key = None

    if wordpress_site and wordpress_site.get("client_id"):
        try:
            client_result = platform_client.table("clients").select("supabase_url, supabase_anon_key, supabase_service_role_key").eq("id", wordpress_site.get("client_id")).execute()
            if client_result.data and len(client_result.data) > 0:
                client_config = client_result.data[0]
                client_supabase_url = client_config.get("supabase_url")
                client_supabase_anon_key = client_config.get("supabase_anon_key")
                client_supabase_service_key = client_config.get("supabase_service_role_key")
        except Exception as e:
            logger.error(f"WordPress session: Error fetching client config: {e}")

    if not client_supabase_url or not client_supabase_service_key:
        # Fallback to platform Supabase if client config not available
        logger.warning("WordPress session: No client Supabase config, falling back to platform")
        client_supabase_url = settings.supabase_url
        client_supabase_anon_key = settings.supabase_anon_key
        client_supabase_service_key = settings.supabase_service_role_key

    logger.info(f"WordPress session: Creating session in Supabase: {client_supabase_url}")

    # Create/find user in the CLIENT's Supabase using service role
    client_admin = create_client(client_supabase_url, client_supabase_service_key)

    # Generate a shadow email for the WordPress user
    shadow_email = f"wp-{payload.get('wp_user_id')}@{normalized_domain or 'wordpress.bridge'}"

    # Try to find existing user or create new one
    user_id = None
    try:
        # Look up by shadow email first
        users = client_admin.auth.admin.list_users()
        for u in users:
            u_email = u.email if hasattr(u, 'email') else u.get('email')
            u_metadata = (u.user_metadata if hasattr(u, 'user_metadata') else u.get('user_metadata')) or {}
            if u_email == shadow_email or (isinstance(u_metadata, dict) and u_metadata.get("wp_user_id") == str(payload.get("wp_user_id")) and u_metadata.get("wp_site") == payload.get("site")):
                user_id = u.id if hasattr(u, 'id') else u.get('id')
                logger.info(f"WordPress session: Found existing user {user_id} for {shadow_email}")
                break
    except Exception as e:
        logger.warning(f"WordPress session: Error looking up user: {e}")

    if not user_id:
        # Create new user
        try:
            create_response = client_admin.auth.admin.create_user({
                "email": shadow_email,
                "email_confirm": True,
                "user_metadata": {
                    "wp_user_id": str(payload.get("wp_user_id")),
                    "wp_site": payload.get("site"),
                    "wp_email": email,
                    "wp_display_name": payload.get("display_name"),
                    "wp_roles": payload.get("roles"),
                    "is_wordpress_user": True
                }
            })
            if create_response and getattr(create_response, "user", None):
                user_id = create_response.user.id
                logger.info(f"WordPress session: Created new user {user_id}")
        except Exception as e:
            logger.error(f"WordPress session: Error creating user: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create user in client Supabase: {str(e)}",
            )

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not find or create user in client Supabase.",
        )

    # Generate a proper session using Supabase admin API
    # We set a random password and then sign in with it to get valid tokens
    import secrets

    random_password = secrets.token_urlsafe(32)

    # Update user's password (this works for both new and existing users)
    try:
        client_admin.auth.admin.update_user_by_id(
            user_id,
            {"password": random_password}
        )
        logger.info(f"WordPress session: Set random password for user {user_id}")
    except Exception as e:
        logger.error(f"WordPress session: Error setting password: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to set user password: {str(e)}",
        )

    # Now sign in with the password to get a valid session
    # We need to use the anon key client for sign in
    anon_client = create_client(client_supabase_url, client_supabase_anon_key)
    try:
        auth_response = anon_client.auth.sign_in_with_password({
            "email": shadow_email,
            "password": random_password
        })
        if not auth_response.session:
            raise Exception("No session returned from sign_in_with_password")

        access_token = auth_response.session.access_token
        refresh_token = auth_response.session.refresh_token
        logger.info(f"WordPress session: Successfully signed in user {user_id}")
    except Exception as e:
        logger.error(f"WordPress session: Error signing in: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sign in user: {str(e)}",
        )

    # Build session payload manually since we're not using sign_in_with_password
    session_payload = {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 3600,
        "refresh_token": refresh_token,
        "provider_token": None,
        "provider_refresh_token": None,
        "user": {
            "id": user_id,
            "email": shadow_email,
            "user_metadata": {
                "wp_display_name": payload.get("display_name"),
                "wp_email": email,
                "wp_user_id": payload.get("wp_user_id"),
                "wp_site": payload.get("site")
            }
        },
        # Include client Supabase credentials so the embed can authenticate properly
        "supabase_url": client_supabase_url,
        "supabase_anon_key": client_supabase_anon_key
    }

    # Upsert profile into the CLIENT's Supabase so the agent can read user info
    # We already have client_admin from earlier
    try:
        logger.info(f"WordPress session: Upserting profile for user {user_id}")

        # Upsert profile with WordPress user info (use minimal fields that work with most schemas)
        profile_data = {
            "user_id": user_id,
            "email": email,
            "full_name": payload.get("display_name") or email,
            "updated_at": datetime.utcnow().isoformat(),
        }

        # Check if profile exists first
        existing = client_admin.table("profiles").select("user_id").eq("user_id", user_id).execute()
        if existing.data and len(existing.data) > 0:
            # Update existing profile
            client_admin.table("profiles").update(profile_data).eq("user_id", user_id).execute()
            logger.info(f"WordPress session: Updated existing profile for user {user_id}")
        else:
            # Insert new profile
            profile_data["created_at"] = datetime.utcnow().isoformat()
            client_admin.table("profiles").insert(profile_data).execute()
            logger.info(f"WordPress session: Created new profile for user {user_id}")
    except Exception as e:
        logger.error(f"WordPress session: Error upserting profile: {e}")
        # Continue even if profile upsert fails - the session is still valid

    debug = {}
    if settings.debug:
        debug = {
            "wp_user_id": payload.get("wp_user_id"),
            "wp_site": payload.get("site"),
            "roles": payload.get("roles"),
        }

    response_body = session_payload

    if debug:
        response_body = {**response_body, "bridge_debug": debug}

    logger.info(f"WordPress session: Returning session for user {user_id} with Supabase URL {client_supabase_url}")
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


# ============================================================================
# WordPress Knowledge Base Sync Endpoints
# ============================================================================

@router.post("/wordpress/kb-sync/init")
async def init_kb_sync(request: Request):
    """
    Initialize a knowledge base sync session.

    Called by WordPress plugin when starting a bulk sync operation.
    Returns a session ID and validates the site configuration.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be valid JSON."
        )

    site_url = body.get("site_url")
    api_secret = body.get("api_secret")
    content_count = body.get("content_count", 0)
    post_types = body.get("post_types", ["post", "page"])

    if not site_url or not api_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing site_url or api_secret"
        )

    # Normalize the site URL
    import re
    normalized_domain = re.sub(r'^https?://', '', site_url).lower().strip('/').split('/')[0]

    # Look up the WordPress site
    from supabase import create_client
    platform_client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    try:
        result = platform_client.table("wordpress_sites").select("*").eq("site_url", normalized_domain).execute()
        if not result.data or len(result.data) == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"WordPress site not found: {normalized_domain}"
            )
        wordpress_site = result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"KB Sync: Error looking up site: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error looking up WordPress site: {str(e)}"
        )

    # Validate the API secret
    stored_secret = wordpress_site.get("api_secret")
    if not stored_secret or stored_secret != api_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API secret"
        )

    # Get client information
    client_id = wordpress_site.get("client_id")
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="WordPress site is not associated with a client"
        )

    # Generate a sync session ID
    sync_session_id = str(uuid.uuid4())

    logger.info(f"KB Sync: Initialized sync session {sync_session_id} for site {normalized_domain} with {content_count} items")

    return JSONResponse({
        "success": True,
        "sync_session_id": sync_session_id,
        "wordpress_site_id": str(wordpress_site.get("id")),
        "client_id": str(client_id),
        "message": f"Ready to sync {content_count} items"
    })


@router.post("/wordpress/kb-sync/batch")
async def sync_kb_batch(request: Request):
    """
    Sync a batch of WordPress content to the knowledge base.

    Called by WordPress plugin with batches of content (up to 20 items).
    Each item includes the rendered HTML content, title, URL, etc.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be valid JSON."
        )

    site_url = body.get("site_url")
    api_secret = body.get("api_secret")
    sync_session_id = body.get("sync_session_id")
    items = body.get("items", [])

    if not site_url or not api_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing site_url or api_secret"
        )

    if not items:
        return JSONResponse({
            "success": True,
            "processed": 0,
            "results": []
        })

    # Normalize and validate site
    import re
    normalized_domain = re.sub(r'^https?://', '', site_url).lower().strip('/').split('/')[0]

    from supabase import create_client
    platform_client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    try:
        result = platform_client.table("wordpress_sites").select("*").eq("site_url", normalized_domain).execute()
        if not result.data or len(result.data) == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WordPress site not found")
        wordpress_site = result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    # Validate secret
    if wordpress_site.get("api_secret") != api_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API secret")

    client_id = wordpress_site.get("client_id")
    wordpress_site_id = str(wordpress_site.get("id"))

    if not client_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Site not associated with client")

    # Import document processor
    from app.services.document_processor import document_processor

    results = []
    for item in items:
        wp_post_id = item.get("post_id")
        title = item.get("title", "Untitled")
        content = item.get("content", "")
        url = item.get("url", "")
        post_type = item.get("post_type", "post")
        modified = item.get("modified")

        if not content or not content.strip():
            results.append({
                "post_id": wp_post_id,
                "success": False,
                "error": "No content provided"
            })
            continue

        # Calculate content hash for change detection
        content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

        # Check if content already synced and unchanged
        try:
            existing = platform_client.table("wordpress_content_sync").select("*").eq(
                "wordpress_site_id", wordpress_site_id
            ).eq("wp_post_id", wp_post_id).execute()

            if existing.data and len(existing.data) > 0:
                existing_record = existing.data[0]
                if existing_record.get("content_hash") == content_hash and existing_record.get("sync_status") == "synced":
                    # Content unchanged, skip
                    results.append({
                        "post_id": wp_post_id,
                        "success": True,
                        "skipped": True,
                        "message": "Content unchanged"
                    })
                    continue
        except Exception as e:
            logger.warning(f"KB Sync: Error checking existing content: {e}")

        # Process the content through document processor
        try:
            # Update or create sync record to 'processing' status
            sync_record = {
                "wordpress_site_id": wordpress_site_id,
                "client_id": str(client_id),
                "wp_post_id": wp_post_id,
                "wp_post_type": post_type,
                "wp_post_title": title,
                "wp_post_url": url,
                "wp_post_modified": modified,
                "sync_status": "processing",
                "content_hash": content_hash,
                "metadata": {
                    "sync_session_id": sync_session_id,
                    "synced_at": datetime.utcnow().isoformat()
                }
            }

            platform_client.table("wordpress_content_sync").upsert(
                sync_record,
                on_conflict="wordpress_site_id,wp_post_id"
            ).execute()

            # Process the content (this creates document and chunks)
            process_result = await document_processor.process_web_content(
                content=content,
                title=title,
                source_url=url,
                description=f"WordPress {post_type}: {title}",
                user_id=None,  # System-level import
                agent_ids=[],  # Will be scoped to all agents for client
                client_id=str(client_id),
                metadata={
                    "wordpress_site_id": wordpress_site_id,
                    "wp_post_id": wp_post_id,
                    "wp_post_type": post_type,
                    "source": "wordpress_kb_sync"
                }
            )

            if process_result.get("success"):
                document_id = process_result.get("document_id")

                # Update sync record with document ID
                platform_client.table("wordpress_content_sync").update({
                    "document_id": str(document_id),
                    "sync_status": "synced",
                    "last_sync_at": datetime.utcnow().isoformat(),
                    "last_error": None
                }).eq("wordpress_site_id", wordpress_site_id).eq("wp_post_id", wp_post_id).execute()

                results.append({
                    "post_id": wp_post_id,
                    "success": True,
                    "document_id": document_id,
                    "status": process_result.get("status", "processing")
                })
            else:
                error_msg = process_result.get("error", "Unknown processing error")
                platform_client.table("wordpress_content_sync").update({
                    "sync_status": "error",
                    "last_error": error_msg
                }).eq("wordpress_site_id", wordpress_site_id).eq("wp_post_id", wp_post_id).execute()

                results.append({
                    "post_id": wp_post_id,
                    "success": False,
                    "error": error_msg
                })

        except Exception as e:
            logger.error(f"KB Sync: Error processing content for post {wp_post_id}: {e}")
            results.append({
                "post_id": wp_post_id,
                "success": False,
                "error": str(e)
            })

    successful = len([r for r in results if r.get("success")])
    logger.info(f"KB Sync: Processed batch of {len(items)} items, {successful} successful")

    return JSONResponse({
        "success": True,
        "processed": len(items),
        "successful": successful,
        "results": results
    })


async def _get_kb_sync_status_impl(site_url: str, api_secret: str):
    """
    Internal implementation for getting sync status.
    Now queries the client's Supabase to get real document processing status.
    """
    import re
    normalized_domain = re.sub(r'^https?://', '', site_url).lower().strip('/').split('/')[0]

    logger.info(f"KB Sync Status: site_url={site_url}, normalized={normalized_domain}")
    logger.info(f"KB Sync Status: received api_secret length={len(api_secret)}")

    from supabase import create_client
    platform_client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    # Validate site and secret
    try:
        result = platform_client.table("wordpress_sites").select("id, api_secret, client_id").eq("site_url", normalized_domain).execute()
        if not result.data or len(result.data) == 0:
            logger.warning(f"KB Sync Status: Site not found for domain: {normalized_domain}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WordPress site not found")
        wordpress_site = result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    stored_secret = wordpress_site.get("api_secret") or ""

    if stored_secret != api_secret:
        logger.warning(f"KB Sync Status: Secret mismatch! received length={len(api_secret)} vs stored length={len(stored_secret)}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API secret")

    wordpress_site_id = str(wordpress_site.get("id"))
    client_id = wordpress_site.get("client_id")

    # Get sync records from platform
    try:
        sync_records = platform_client.table("wordpress_content_sync").select(
            "wp_post_id, wp_post_type, wp_post_title, sync_status, last_sync_at, last_error, document_id"
        ).eq("wordpress_site_id", wordpress_site_id).execute()

        records = sync_records.data or []

        # Get client's Supabase credentials to check real document status
        client_result = platform_client.table("clients").select(
            "supabase_url, supabase_service_role_key"
        ).eq("id", client_id).execute()

        document_statuses = {}
        if client_result.data and len(client_result.data) > 0:
            client_data = client_result.data[0]
            client_supabase_url = client_data.get("supabase_url")
            client_supabase_key = client_data.get("supabase_service_role_key")

            if client_supabase_url and client_supabase_key:
                try:
                    client_supabase = create_client(client_supabase_url, client_supabase_key)

                    # Get all document IDs from sync records
                    doc_ids = [r.get("document_id") for r in records if r.get("document_id")]

                    if doc_ids:
                        # Query client's documents table for real status
                        # Note: document_id is stored as string but documents.id might be int
                        docs_result = client_supabase.table("documents").select("id, status").execute()

                        for doc in (docs_result.data or []):
                            doc_id = str(doc.get("id"))
                            document_statuses[doc_id] = doc.get("status", "unknown")

                except Exception as e:
                    logger.warning(f"KB Sync Status: Could not query client Supabase: {e}")

        # Build enhanced records with real document status
        enhanced_records = []
        for r in records:
            doc_id = r.get("document_id")
            real_status = document_statuses.get(str(doc_id)) if doc_id else None

            # Determine effective status:
            # - If we have real status from client DB, use it
            # - If document exists and is "ready", it's truly synced
            # - If document is "processing", show as processing
            # - If document is "error" or other, show appropriately
            if real_status:
                if real_status == "ready":
                    effective_status = "ready"
                elif real_status == "processing":
                    effective_status = "processing"
                elif real_status == "error":
                    effective_status = "error"
                else:
                    effective_status = real_status
            else:
                # Fallback to sync record status if we couldn't get real status
                effective_status = r.get("sync_status", "unknown")

            enhanced_records.append({
                **r,
                "document_status": real_status,
                "effective_status": effective_status
            })

        # Calculate stats based on effective status (real document status)
        stats = {
            "total": len(enhanced_records),
            "ready": len([r for r in enhanced_records if r.get("effective_status") == "ready"]),
            "processing": len([r for r in enhanced_records if r.get("effective_status") == "processing"]),
            "synced": len([r for r in enhanced_records if r.get("effective_status") == "synced"]),
            "pending": len([r for r in enhanced_records if r.get("effective_status") == "pending"]),
            "error": len([r for r in enhanced_records if r.get("effective_status") == "error"])
        }

        # Get list of post IDs by status for the WordPress plugin UI
        ready_post_ids = [r.get("wp_post_id") for r in enhanced_records if r.get("effective_status") == "ready"]
        processing_post_ids = [r.get("wp_post_id") for r in enhanced_records if r.get("effective_status") == "processing"]
        error_post_ids = [r.get("wp_post_id") for r in enhanced_records if r.get("effective_status") == "error"]

        # For backwards compatibility, also include "synced" in synced_post_ids (old behavior)
        synced_post_ids = ready_post_ids + [r.get("wp_post_id") for r in enhanced_records if r.get("effective_status") == "synced"]

        return JSONResponse({
            "success": True,
            "stats": stats,
            "synced_post_ids": synced_post_ids,
            "ready_post_ids": ready_post_ids,
            "processing_post_ids": processing_post_ids,
            "error_post_ids": error_post_ids,
            "records": enhanced_records
        })

    except Exception as e:
        logger.error(f"KB Sync: Error getting status: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/wordpress/kb-sync/status")
async def get_kb_sync_status_get(
    site_url: str,
    api_secret: str
):
    """
    Get the current sync status for a WordPress site (GET method).
    Note: GET may have issues with special characters in api_secret. Prefer POST.
    """
    return await _get_kb_sync_status_impl(site_url, api_secret)


@router.post("/wordpress/kb-sync/status")
async def get_kb_sync_status_post(request: Request):
    """
    Get the current sync status for a WordPress site (POST method).
    Preferred method as it handles special characters in api_secret correctly.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be valid JSON."
        )

    site_url = body.get("site_url")
    api_secret = body.get("api_secret")

    if not site_url or not api_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing site_url or api_secret"
        )

    return await _get_kb_sync_status_impl(site_url, api_secret)


@router.post("/wordpress/kb-sync/delete")
async def delete_synced_content(request: Request):
    """
    Delete synced content from the knowledge base.

    Called when WordPress content is deleted or unpublished.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be valid JSON."
        )

    site_url = body.get("site_url")
    api_secret = body.get("api_secret")
    post_ids = body.get("post_ids", [])

    if not site_url or not api_secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing credentials")

    if not post_ids:
        return JSONResponse({"success": True, "deleted": 0})

    import re
    normalized_domain = re.sub(r'^https?://', '', site_url).lower().strip('/').split('/')[0]

    from supabase import create_client
    platform_client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    # Validate site
    try:
        result = platform_client.table("wordpress_sites").select("*").eq("site_url", normalized_domain).execute()
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")
        wordpress_site = result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    if wordpress_site.get("api_secret") != api_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API secret")

    wordpress_site_id = str(wordpress_site.get("id"))
    client_id = str(wordpress_site.get("client_id"))

    # Import document processor for deletion
    from app.services.document_processor import document_processor

    deleted_count = 0
    for post_id in post_ids:
        try:
            # Get the sync record to find the document ID
            sync_result = platform_client.table("wordpress_content_sync").select("document_id").eq(
                "wordpress_site_id", wordpress_site_id
            ).eq("wp_post_id", post_id).execute()

            if sync_result.data and len(sync_result.data) > 0:
                document_id = sync_result.data[0].get("document_id")

                if document_id:
                    # Delete the document from client's knowledge base
                    await document_processor.delete_document(
                        document_id=document_id,
                        client_id=client_id
                    )

                # Update sync record to deleted status
                platform_client.table("wordpress_content_sync").update({
                    "sync_status": "deleted",
                    "document_id": None
                }).eq("wordpress_site_id", wordpress_site_id).eq("wp_post_id", post_id).execute()

                deleted_count += 1

        except Exception as e:
            logger.error(f"KB Sync: Error deleting post {post_id}: {e}")

    logger.info(f"KB Sync: Deleted {deleted_count} items from knowledge base")

    return JSONResponse({
        "success": True,
        "deleted": deleted_count
    })
