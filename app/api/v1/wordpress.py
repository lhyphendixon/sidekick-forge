from fastapi import APIRouter, HTTPException, status, Depends, Request
from typing import Dict, Any
from datetime import datetime

from app.models.common import APIResponse, SuccessResponse
from app.models.agent import Agent
from app.middleware.auth import get_current_auth, require_site_auth
from app.integrations.supabase_client import supabase_manager
from app.utils.exceptions import NotFoundError

router = APIRouter()

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