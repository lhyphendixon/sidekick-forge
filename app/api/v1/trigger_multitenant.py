"""
Multi-tenant Agent trigger endpoint for Sidekick Forge Platform

This endpoint handles agent triggering with proper tenant isolation.
"""
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from enum import Enum
from uuid import UUID
import logging
import asyncio
import json
import time
from datetime import datetime
import uuid
import traceback

from app.services.agent_service_multitenant import AgentService
from app.services.client_service_multitenant import ClientService as PlatformClientService
from app.services.client_connection_manager import ClientConfigurationError
from app.integrations.livekit_client import LiveKitManager
from app.services.client_service_supabase import ClientService as SingleTenantClientService
from app.services.tools_service_supabase import ToolsService as SharedToolsService
from app.api.v1 import trigger as shared_trigger
from app.models.platform_client import PlatformClient
from livekit import api
import os
from app.config import settings
from app.utils.tool_prompts import apply_tool_prompt_instructions

logger = logging.getLogger(__name__)

router = APIRouter(tags=["trigger"])


class TriggerMode(str, Enum):
    """Agent trigger modes"""
    VOICE = "voice"
    TEXT = "text"


class TriggerAgentRequest(BaseModel):
    """Request model for triggering an agent"""
    # Agent identification
    agent_slug: str = Field(..., description="Slug of the agent to trigger")
    client_id: Optional[str] = Field(None, description="Client ID (auto-detected if not provided)")
    
    # Mode and content
    mode: TriggerMode = Field(..., description="Trigger mode: voice or text")
    message: Optional[str] = Field(None, description="Text message (required for text mode)")
    
    # Voice mode parameters
    room_name: Optional[str] = Field(None, description="LiveKit room name (required for voice mode)")
    platform: Optional[str] = Field("livekit", description="Voice platform (default: livekit)")
    
    # Session and user info
    user_id: str = Field(..., description="User identifier")
    session_id: Optional[str] = Field(None, description="Session identifier")
    conversation_id: Optional[str] = Field(None, description="Conversation identifier")
    
    # Optional context
    context: Optional[Dict[str, Any]] = Field(None, description="Additional context data")


class TriggerAgentResponse(BaseModel):
    """Response model for agent trigger"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    agent_info: Optional[Dict[str, Any]] = None


# Create service instances
agent_service = AgentService()
platform_client_service = PlatformClientService()

try:
    _shared_client_service = SingleTenantClientService(
        settings.supabase_url,
        settings.supabase_service_role_key,
    )
    shared_tools_service = SharedToolsService(_shared_client_service)
except Exception as shared_tools_error:
    logger.error("Failed to initialize shared ToolsService for multi-tenant text handler: %s", shared_tools_error)
    shared_tools_service = None


@router.post("/trigger-agent", response_model=TriggerAgentResponse)
async def trigger_agent(request: TriggerAgentRequest) -> TriggerAgentResponse:
    """
    Trigger an AI agent for voice or text interaction
    
    This endpoint handles multi-tenant agent triggering with proper isolation.
    """
    request_start = time.time()
    try:
        logger.info(f"🚀 STARTING trigger-agent request: agent={request.agent_slug}, mode={request.mode}, user={request.user_id}")
        
        # Validate mode-specific requirements
        if request.mode == TriggerMode.VOICE and not request.room_name:
            raise HTTPException(status_code=400, detail="room_name is required for voice mode")
        
        if request.mode == TriggerMode.TEXT and not request.message:
            raise HTTPException(status_code=400, detail="message is required for text mode")
        
        # Auto-detect client_id if not provided
        client_id = None
        if request.client_id:
            client_id = UUID(request.client_id)
        else:
            logger.info(f"Auto-detecting client for agent {request.agent_slug}")
            client_id = await agent_service.find_agent_client(request.agent_slug)
            
            if not client_id:
                raise HTTPException(
                    status_code=404, 
                    detail=f"Agent '{request.agent_slug}' not found in any client"
                )
        
        # Get agent configuration from client's database
        agent = await agent_service.get_agent(client_id, request.agent_slug)
        if not agent:
            raise HTTPException(
                status_code=404, 
                detail=f"Agent '{request.agent_slug}' not found in client '{client_id}'"
            )
        
        if not agent.enabled:
            raise HTTPException(
                status_code=400, 
                detail=f"Agent '{request.agent_slug}' is currently disabled"
            )
        
        # Get client info and API keys from platform database
        client_info = await agent_service.get_client_info(client_id)
        platform_client = await platform_client_service.get_client(str(client_id))
        if not platform_client:
            raise HTTPException(
                status_code=404,
                detail=f"Client '{client_id}' not found"
            )

        api_keys = await agent_service.get_client_api_keys(client_id)
        
        # Process based on mode
        if request.mode == TriggerMode.VOICE:
            result = await handle_voice_trigger(request, agent, client_info, api_keys, platform_client)
        else:  # TEXT mode
            result = await handle_text_trigger(request, agent, platform_client, api_keys)
        
        request_total = time.time() - request_start
        logger.info(f"✅ COMPLETED trigger-agent request in {request_total:.2f}s")
        
        return TriggerAgentResponse(
            success=True,
            message=f"Agent {request.agent_slug} triggered successfully in {request.mode} mode",
            data=result,
            agent_info={
                "slug": agent.slug,
                "name": agent.name,
                "client_id": str(client_id),
                "client_name": platform_client.name,
                "voice_provider": agent.voice_settings.provider if agent.voice_settings else "livekit",
                "voice_id": agent.voice_settings.voice_id if agent.voice_settings else "alloy"
            }
        )
        
    except HTTPException:
        raise
    except ClientConfigurationError as e:
        logger.error(f"Client configuration error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Error triggering agent {request.agent_slug}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Internal error triggering agent: {str(e)}"
        )


async def handle_voice_trigger(
    request: TriggerAgentRequest, 
    agent,
    client_info: Dict[str, Any],
    api_keys: Dict[str, Optional[str]],
    platform_client: PlatformClient
) -> Dict[str, Any]:
    """
    Handle voice mode agent triggering with multi-tenant support
    """
    logger.info(f"Handling voice trigger for agent {agent.slug} in room {request.room_name}")
    
    # Use backend's LiveKit credentials (platform owns the infrastructure)
    from app.integrations.livekit_client import livekit_manager
    backend_livekit = livekit_manager
    
    # Generate conversation_id if not provided (no-fallback policy)
    conversation_id = request.conversation_id or str(uuid.uuid4())

    # Prepare agent context with all necessary configuration
    agent_context = {
        "client_id": client_info['id'],
        "agent_slug": agent.slug,
        "agent_name": agent.name,
        "system_prompt": agent.system_prompt,
        "voice_settings": {
            **(agent.voice_settings.dict() if agent.voice_settings else {}),
            "tts_provider": agent.voice_settings.provider if agent.voice_settings else "cartesia"
        },
        "webhooks": agent.webhooks.dict() if agent.webhooks else {},
        "user_id": request.user_id,
        "session_id": request.session_id,
        "conversation_id": conversation_id,
        "context": request.context or {},
        "api_keys": {k: v for k, v in api_keys.items() if v}  # Include all available API keys
    }

    agent_id_value = getattr(agent, "id", None)
    if agent_id_value:
        agent_context["agent_id"] = str(agent_id_value)

    # Provide Supabase credentials to the worker so transcripts can be stored
    supabase_url = getattr(platform_client, "supabase_project_url", None) or getattr(platform_client, "supabase_url", None)
    supabase_service_key = getattr(platform_client, "supabase_service_role_key", None)
    if supabase_url and supabase_service_key:
        agent_context["supabase_url"] = supabase_url
        agent_context["supabase_service_role_key"] = supabase_service_key
        # Backwards compatibility
        agent_context["supabase_service_key"] = supabase_service_key
        logger.info("Voice trigger: attached client Supabase credentials (url=%s, key=%s)",
                    bool(supabase_url), bool(supabase_service_key))
    else:
        logger.warning("Voice trigger: missing Supabase credentials for client %s (url=%s, key=%s)",
                       client_info['id'], bool(supabase_url), bool(supabase_service_key))

    # Include assigned tools (Abilities) so the worker can register them
    tools_payload: List[Dict[str, Any]] = []
    tools_service = shared_tools_service
    if tools_service is None:
        try:
            temp_client_service = SingleTenantClientService(settings.supabase_url, settings.supabase_service_role_key)
            tools_service = SharedToolsService(temp_client_service)
        except Exception as reinit_error:
            logger.warning("Voice trigger: unable to initialize shared ToolsService: %s", reinit_error)
            tools_service = None

    if tools_service:
        try:
            assigned_tools = await tools_service.list_agent_tools(str(client_info["id"]), str(agent_id_value or agent.id))
            for tool in assigned_tools:
                tool_dict = tool.dict()
                for ts_field in ("created_at", "updated_at"):
                    value = tool_dict.get(ts_field)
                    if hasattr(value, "isoformat"):
                        tool_dict[ts_field] = value.isoformat()
                tools_payload.append(tool_dict)
            if tools_payload:
                agent_context["tools"] = tools_payload
                logger.info("Voice trigger: including %d abilities for agent %s", len(tools_payload), agent.slug)
        except Exception as tool_error:
            logger.warning("Voice trigger: failed to load abilities for agent %s: %s", agent.slug, tool_error)
    else:
        logger.warning("Voice trigger: ToolsService unavailable; abilities will be skipped")

    if tools_payload:
        try:
            updated_prompt, appended_sections = apply_tool_prompt_instructions(
                agent_context.get("system_prompt"),
                tools_payload,
            )
            agent_context["system_prompt"] = updated_prompt
            if appended_sections:
                agent_context["tool_prompt_sections"] = appended_sections
                logger.info("Voice trigger: appended %d hidden ability instructions", len(appended_sections))
        except Exception as tp_error:
            logger.warning("Voice trigger: failed to apply ability instructions: %s", tp_error)

    # Ensure the room exists
    room_info = await ensure_livekit_room_exists(
        backend_livekit, 
        request.room_name,
        agent_name=settings.livekit_agent_name,
        agent_slug=agent.slug,
        user_id=request.user_id,
        agent_config=agent_context
    )
    
    # Generate user token for frontend
    user_token = backend_livekit.create_token(
        identity=f"user_{request.user_id}",
        room_name=request.room_name,
        metadata={"user_id": request.user_id, "client_id": client_info['id']}
    )
    
    # Ensure room_info carries conversation_id in metadata if possible
    try:
        meta = room_info.get("metadata") if isinstance(room_info, dict) else None
        if isinstance(meta, dict):
            meta["conversation_id"] = conversation_id
    except Exception:
        pass

    return {
        "mode": "voice",
        "room_name": request.room_name,
        "platform": request.platform,
        "conversation_id": conversation_id,
        "agent_context": agent_context,
        "livekit_config": {
            "server_url": backend_livekit.url,
            "user_token": user_token,
            "configured": True
        },
        "room_info": room_info,
        "dispatch_info": {
            "status": "automatic",
            "message": "Agent will be automatically dispatched when participant joins"
        },
        "status": "voice_agent_triggered"
    }


async def handle_text_trigger(
    request: TriggerAgentRequest,
    agent,
    platform_client: PlatformClient,
    api_keys: Dict[str, Optional[str]]
) -> Dict[str, Any]:
    """Delegate multi-tenant text handling to shared single-tenant implementation."""
    logger.info(
        "Handling text trigger for agent %s with message: %s",
        agent.slug,
        (request.message[:50] + "...") if request.message else None,
    )

    # Merge API keys from the connection manager into the platform client model so downstream logic sees them.
    if api_keys and getattr(platform_client, "settings", None) and getattr(platform_client.settings, "api_keys", None):
        for key, value in api_keys.items():
            if hasattr(platform_client.settings.api_keys, key) and value is not None:
                setattr(platform_client.settings.api_keys, key, value)

    # Ensure compatibility attributes expected by the shared handler are present.
    if getattr(platform_client, "supabase_project_url", None) and not getattr(platform_client, "supabase_url", None):
        setattr(platform_client, "supabase_url", platform_client.supabase_project_url)

    shared_request = shared_trigger.TriggerAgentRequest(
        agent_slug=request.agent_slug,
        client_id=str(platform_client.id) if getattr(platform_client, "id", None) else request.client_id,
        mode=shared_trigger.TriggerMode.TEXT,
        message=request.message,
        room_name=None,
        platform=request.platform,
        user_id=request.user_id,
        session_id=request.session_id,
        conversation_id=request.conversation_id,
        context=request.context,
    )

    if shared_tools_service is None:
        logger.warning("Shared ToolsService unavailable; text abilities will be skipped for multi-tenant request.")

    return await shared_trigger.handle_text_trigger(
        shared_request,
        agent,
        platform_client,
        shared_tools_service,
    )


async def ensure_livekit_room_exists(
    livekit_manager: LiveKitManager,
    room_name: str,
    agent_name: str = None,
    agent_slug: str = None,
    user_id: str = None,
    agent_config: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Ensure a LiveKit room exists with proper metadata for multi-tenant agents
    """
    try:
        def _json_safe(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, list):
                return [_json_safe(v) for v in value]
            if isinstance(value, dict):
                return {k: _json_safe(v) for k, v in value.items()}
            return value

        await livekit_manager.initialize()

        # Check if room already exists
        existing_room = await livekit_manager.get_room(room_name)
        
        if existing_room:
            logger.info(f"✅ Room {room_name} already exists")
            merged_metadata: Dict[str, Any] = {}
            raw_metadata = existing_room.get("metadata")
            if raw_metadata:
                try:
                    merged_metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else dict(raw_metadata)
                except Exception as parse_err:
                    logger.warning("Unable to parse existing room metadata: %s", parse_err, exc_info=True)
                    merged_metadata = {}

            if agent_config:
                merged_metadata.update(agent_config)

            merged_metadata.update(
                {
                    "agent_name": agent_name or merged_metadata.get("agent_name"),
                    "agent_slug": agent_slug or merged_metadata.get("agent_slug"),
                    "user_id": user_id or merged_metadata.get("user_id"),
                    "updated_at": datetime.now().isoformat(),
                }
            )

            safe_metadata = _json_safe(merged_metadata)
            try:
                if await livekit_manager.update_room_metadata(room_name, safe_metadata):
                    logger.info(
                        "🛠️ Refreshed room metadata with latest agent context",
                        extra={
                            "has_tools": bool(safe_metadata.get("tools")),
                            "tool_count": len(safe_metadata.get("tools", [])) if isinstance(safe_metadata.get("tools"), list) else 0,
                        },
                    )
                else:
                    logger.warning("LiveKit did not accept metadata update for room %s", room_name)
            except Exception as update_err:
                logger.warning("Failed to update room metadata for %s: %s", room_name, update_err, exc_info=True)

            dispatch_status = "skipped"
            try:
                dispatch_request = api.CreateAgentDispatchRequest(
                    room=room_name,
                    metadata=json.dumps(safe_metadata),
                    agent_name=agent_name or settings.livekit_agent_name,
                )
                await livekit_manager.livekit_api.agent_dispatch.create_dispatch(dispatch_request)
                dispatch_status = "dispatched"
                logger.info("🔄 Re-dispatched agent for existing room %s", room_name)
            except Exception as dispatch_err:
                logger.warning("Failed to re-dispatch agent for room %s: %s", room_name, dispatch_err, exc_info=True)

            return {
                "room_name": room_name,
                "status": "existing",
                "participants": existing_room["num_participants"],
                "dispatch_status": dispatch_status,
            }
        
        # Create new room with full agent configuration
        # Start with the agent configuration as the base (flattened metadata)
        room_metadata = agent_config if agent_config is not None else {}
        
        # Add or overwrite general room information
        room_metadata.update({
            "agent_name": agent_name,
            "user_id": user_id,
            "created_by": "sidekick_forge_backend",
            "created_at": datetime.now().isoformat()
        })
        
        # Create room (without auto-dispatch to prevent dual dispatch)
        room_info = await livekit_manager.create_room(
            name=room_name,
            empty_timeout=1800,
            max_participants=10,
            metadata=json.dumps(room_metadata),
            enable_agent_dispatch=False  # Disable auto-dispatch to prevent dual dispatch
        )
        
        # Explicit agent dispatch (prevents dual dispatch issue)
        dispatch_request = api.CreateAgentDispatchRequest(
            room=room_name,
            metadata=json.dumps(room_metadata),
            agent_name=agent_name or settings.livekit_agent_name
        )
        dispatch_response = await livekit_manager.livekit_api.agent_dispatch.create_dispatch(dispatch_request)
        
        logger.info(f"✅ Created room {room_name} successfully")
        
        return {
            "room_name": room_name,
            "status": "created",
            "participants": 0,
            "created_at": room_info["created_at"].isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to ensure room {room_name} exists: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create/verify LiveKit room: {str(e)}"
        )
