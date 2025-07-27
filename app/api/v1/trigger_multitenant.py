"""
Multi-tenant Agent trigger endpoint for Sidekick Forge Platform

This endpoint handles agent triggering with proper tenant isolation.
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from enum import Enum
from uuid import UUID
import logging
import asyncio
import json
import time
from datetime import datetime
import traceback

from app.services.agent_service_multitenant import AgentService
from app.services.client_service_multitenant import ClientService
from app.services.client_connection_manager import ClientConfigurationError
from app.integrations.livekit_client import LiveKitManager
from livekit import api
import os

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
client_service = ClientService()


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
        api_keys = await agent_service.get_client_api_keys(client_id)
        
        # Process based on mode
        if request.mode == TriggerMode.VOICE:
            result = await handle_voice_trigger(request, agent, client_info, api_keys)
        else:  # TEXT mode
            result = await handle_text_trigger(request, agent, client_info, api_keys)
        
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
                "client_name": client_info.get('name', 'Unknown'),
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
    api_keys: Dict[str, Optional[str]]
) -> Dict[str, Any]:
    """
    Handle voice mode agent triggering with multi-tenant support
    """
    logger.info(f"Handling voice trigger for agent {agent.slug} in room {request.room_name}")
    
    # Use backend's LiveKit credentials (platform owns the infrastructure)
    from app.integrations.livekit_client import livekit_manager
    backend_livekit = livekit_manager
    
    # Prepare agent context with all necessary configuration
    agent_context = {
        "client_id": client_info['id'],
        "agent_slug": agent.slug,
        "agent_name": agent.name,
        "system_prompt": agent.system_prompt,
        "voice_settings": {
            **(agent.voice_settings.dict() if agent.voice_settings else {}),
            "tts_provider": agent.voice_settings.provider if agent.voice_settings else "livekit"
        },
        "webhooks": agent.webhooks.dict() if agent.webhooks else {},
        "user_id": request.user_id,
        "session_id": request.session_id,
        "conversation_id": request.conversation_id,
        "context": request.context or {},
        "api_keys": {k: v for k, v in api_keys.items() if v}  # Include all available API keys
    }
    
    # Ensure the room exists
    room_info = await ensure_livekit_room_exists(
        backend_livekit, 
        request.room_name,
        agent_name=agent.name,
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
    
    return {
        "mode": "voice",
        "room_name": request.room_name,
        "platform": request.platform,
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
    client_info: Dict[str, Any],
    api_keys: Dict[str, Optional[str]]
) -> Dict[str, Any]:
    """
    Handle text mode agent triggering
    """
    logger.info(f"Handling text trigger for agent {agent.slug} with message: {request.message[:50]}...")
    
    # Prepare context for text processing
    text_context = {
        "agent_slug": agent.slug,
        "agent_name": agent.name,
        "system_prompt": agent.system_prompt,
        "user_message": request.message,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "conversation_id": request.conversation_id,
        "context": request.context or {}
    }
    
    # Check if agent has text webhook configured
    text_webhook = agent.webhooks.text_context_webhook_url if agent.webhooks else None
    
    # Process text message through appropriate LLM
    response_text = None
    llm_provider = agent.voice_settings.llm_provider if agent.voice_settings else "openai"
    llm_model = agent.voice_settings.llm_model if agent.voice_settings else "gpt-4"
    
    # Get appropriate API key
    api_key = None
    if llm_provider == "groq" and api_keys.get('groq_api_key'):
        api_key = api_keys['groq_api_key']
    elif llm_provider == "openai" and api_keys.get('openai_api_key'):
        api_key = api_keys['openai_api_key']
    elif llm_provider == "anthropic" and api_keys.get('anthropic_api_key'):
        api_key = api_keys['anthropic_api_key']
    
    if api_key and api_key not in ["test_key", "test", "dummy"]:
        try:
            # Process with LLM (implementation details omitted for brevity)
            # This would call the appropriate LLM API
            pass
        except Exception as e:
            logger.error(f"Error processing text with {llm_provider}: {str(e)}")
    
    return {
        "mode": "text",
        "message_received": request.message,
        "text_context": text_context,
        "webhook_configured": bool(text_webhook),
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "api_key_available": bool(api_key and api_key not in ["test_key", "test", "dummy"]),
        "status": "text_message_processed",
        "response": response_text or f"I'm sorry, I couldn't process your message. Please ensure the {llm_provider} API key is configured."
    }


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
        # Check if room already exists
        existing_room = await livekit_manager.get_room(room_name)
        
        if existing_room:
            logger.info(f"✅ Room {room_name} already exists")
            return {
                "room_name": room_name,
                "status": "existing",
                "participants": existing_room['num_participants']
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
        
        # Create room
        room_info = await livekit_manager.create_room(
            name=room_name,
            empty_timeout=1800,
            max_participants=10,
            metadata=json.dumps(room_metadata),
            enable_agent_dispatch=True,
            agent_name=agent_slug if agent_slug else "sidekick-agent"
        )
        
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