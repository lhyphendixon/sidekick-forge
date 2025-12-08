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
    """Fetch Supabase auth user metadata by email via Admin API."""
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users"
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

    admin_auth = supabase_manager.admin_client.auth.admin
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


@router.post("/wordpress/session")
async def issue_wordpress_session(request: Request):
    """Exchange a signed WordPress session payload for Supabase Auth tokens."""
    if not settings.wordpress_bridge_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WordPress session bridge is not configured.",
        )

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

    encoded_payload = _encode_payload_for_signature(payload)
    expected_signature = hmac.new(
        settings.wordpress_bridge_secret.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid payload signature.",
        )

    user_id = await _ensure_supabase_user(payload)

    derived_password = _derive_password(payload)
    try:
        login_response = supabase_manager.auth_client.auth.sign_in_with_password(
            {"email": email, "password": derived_password}
        )
    except Exception as exc:
        logger.error("Failed to create Supabase session for %s: %s", payload["email"], exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create Supabase session.",
        )

    if not getattr(login_response, "session", None):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase did not return a session.",
        )

    session_payload = _normalize_session(login_response.session, login_response.user)
    session_payload["user"]["id"] = session_payload["user"].get("id") or user_id

    debug = {}
    if settings.debug:
        debug = {
            "wp_user_id": payload.get("wp_user_id"),
            "wp_site": payload.get("site"),
            "roles": payload.get("roles"),
        }

    response_body = session_payload
    if debug:
        response_body = {**session_payload, "bridge_debug": debug}

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
