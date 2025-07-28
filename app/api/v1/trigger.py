"""
Agent trigger endpoint for WordPress plugin integration
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from enum import Enum
import logging
import asyncio
import json
import uuid
import time
from datetime import datetime
import traceback  # For detailed errors

from app.services.agent_service_supabase import AgentService
from app.services.client_service_supabase import ClientService
from app.core.dependencies import get_client_service, get_agent_service
from app.integrations.livekit_client import LiveKitManager
from livekit import api
import os

logger = logging.getLogger(__name__)

router = APIRouter(tags=["trigger"])


class CreateRoomRequest(BaseModel):
    """Request model for creating a LiveKit room"""
    room_name: str = Field(..., description="Name of the room to create")
    client_id: str = Field(..., description="Client ID for LiveKit credentials")
    max_participants: int = Field(10, description="Maximum participants allowed")
    empty_timeout: int = Field(600, description="Timeout in seconds for empty rooms")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Room metadata")


class CreateRoomResponse(BaseModel):
    """Response model for room creation"""
    success: bool
    room_name: str
    room_info: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


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


# Using dependencies from core module - no need to redefine here


@router.post("/trigger-agent", response_model=TriggerAgentResponse)
async def trigger_agent(
    request: TriggerAgentRequest,
    agent_service: AgentService = Depends(get_agent_service)
) -> TriggerAgentResponse:
    """
    Trigger an AI agent for voice or text interaction
    
    This endpoint handles:
    - Voice mode: Triggers Python LiveKit agent to join a room
    - Text mode: Processes text messages through the agent
    """
    request_start = time.time()
    try:
        logger.debug(f"Received trigger request", extra={'agent_slug': request.agent_slug, 'mode': request.mode, 'user_id': request.user_id})
        logger.info(f"🚀 STARTING trigger-agent request: agent={request.agent_slug}, mode={request.mode}, user={request.user_id}")
        logger.info(f"Room name requested: {request.room_name}")
        
        # Validate mode-specific requirements
        if request.mode == TriggerMode.VOICE and not request.room_name:
            raise HTTPException(status_code=400, detail="room_name is required for voice mode")
        
        if request.mode == TriggerMode.TEXT and not request.message:
            raise HTTPException(status_code=400, detail="message is required for text mode")
        
        # Auto-detect client_id if not provided by finding agent across all clients
        client_id = request.client_id
        if not client_id:
            logger.info(f"Auto-detecting client for agent {request.agent_slug}")
            all_agents = await agent_service.get_all_agents_with_clients()
            for agent in all_agents:
                # agent is a dict from get_all_agents_with_clients
                if agent.get("slug") == request.agent_slug:
                    client_id = agent.get("client_id")
                    logger.info(f"Found agent {request.agent_slug} in client {client_id}")
                    break
            
            if not client_id:
                raise HTTPException(
                    status_code=404, 
                    detail=f"Agent '{request.agent_slug}' not found in any client"
                )
        
        # Get agent configuration
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
        
        # Get client configuration for LiveKit/API keys
        client = await agent_service.client_service.get_client(client_id)
        if not client:
            raise HTTPException(
                status_code=404, 
                detail=f"Client '{client_id}' not found"
            )
        
        # Process based on mode
        if request.mode == TriggerMode.VOICE:
            result = await handle_voice_trigger(request, agent, client)
        else:  # TEXT mode
            result = await handle_text_trigger(request, agent, client)
        
        request_total = time.time() - request_start
        logger.info(f"✅ COMPLETED trigger-agent request in {request_total:.2f}s")
        
        return TriggerAgentResponse(
            success=True,
            message=f"Agent {request.agent_slug} triggered successfully in {request.mode} mode",
            data=result,
            agent_info={
                "slug": agent.slug,
                "name": agent.name,
                "client_id": client_id,
                "client_name": client.name,
                "voice_provider": agent.voice_settings.provider if agent.voice_settings else "livekit",
                "voice_id": agent.voice_settings.voice_id if agent.voice_settings else "alloy"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering agent {request.agent_slug}: {str(e)}", exc_info=True, extra={'traceback': traceback.format_exc()})
        raise HTTPException(
            status_code=500, 
            detail=f"Internal error triggering agent: {str(e)}"
        )


@router.post("/create-room", response_model=CreateRoomResponse)
async def create_livekit_room(
    request: CreateRoomRequest,
    agent_service: AgentService = Depends(get_agent_service)
) -> CreateRoomResponse:
    """
    Create a LiveKit room for voice interactions
    
    This endpoint allows frontends to pre-create rooms before triggering agents,
    ensuring rooms are ready and avoiding timing issues.
    """
    try:
        logger.info(f"Creating LiveKit room {request.room_name} for client {request.client_id}")
        
        # Get client configuration for LiveKit credentials
        client = await agent_service.client_service.get_client(request.client_id)
        if not client:
            raise HTTPException(
                status_code=404, 
                detail=f"Client '{request.client_id}' not found"
            )
        
        # Use backend LiveKit infrastructure (thin client - no client credentials needed)
        from app.integrations.livekit_client import livekit_manager
        backend_livekit = livekit_manager
        
        # Ensure LiveKit manager is initialized
        if not backend_livekit._initialized:
            await backend_livekit.initialize()
        
        logger.info(f"🏢 Creating room with backend LiveKit infrastructure")
        
        # Create the room
        room_metadata = {
            "client_id": request.client_id,
            "client_name": client.name,
            "created_by": "sidekick_backend_api",
            "created_at": datetime.now().isoformat(),
            **(request.metadata or {})
        }
        
        room_info = await backend_livekit.create_room(
            name=request.room_name,
            empty_timeout=request.empty_timeout,
            max_participants=request.max_participants,
            metadata=room_metadata
        )
        
        logger.info(f"✅ Successfully created room {request.room_name}")
        
        return CreateRoomResponse(
            success=True,
            room_name=request.room_name,
            room_info={
                "name": room_info["name"],
                "created_at": room_info["created_at"].isoformat(),
                "max_participants": room_info["max_participants"],
                "metadata": room_metadata,
                "server_url": backend_livekit.url
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating room {request.room_name}: {str(e)}")
        return CreateRoomResponse(
            success=False,
            room_name=request.room_name,
            error=str(e)
        )


async def handle_voice_trigger(
    request: TriggerAgentRequest, 
    agent, 
    client
) -> Dict[str, Any]:
    """
    Handle voice mode agent triggering
    
    This creates a LiveKit room (if needed) and triggers a Python LiveKit agent to join it
    """
    logger.debug(f"Starting voice trigger handling", extra={'agent_slug': agent.slug, 'room_name': request.room_name})
    logger.info(f"Handling voice trigger for agent {agent.slug} in room {request.room_name}")
    
    voice_trigger_start = time.time()
    
    # Use backend's LiveKit credentials for ALL operations (true thin client)
    # Clients don't need LiveKit credentials - backend owns the infrastructure
    from app.integrations.livekit_client import livekit_manager
    backend_livekit = livekit_manager
    
    # Ensure LiveKit manager is initialized
    if not backend_livekit._initialized:
        await backend_livekit.initialize()
    
    logger.info(f"🏢 Using backend LiveKit infrastructure for thin client architecture")
    
    # Prepare agent context first so we can pass it to room creation
    # Build voice settings with proper defaults
    voice_settings = agent.voice_settings.dict() if agent.voice_settings else {}
    
    # Apply defaults for null values
    if not voice_settings.get('llm_provider'):
        voice_settings['llm_provider'] = 'groq'  # Use Groq as default since we have the API key
    if not voice_settings.get('stt_provider'):
        voice_settings['stt_provider'] = 'deepgram'
    if not voice_settings.get('tts_provider'):
        voice_settings['tts_provider'] = 'elevenlabs'  # Use ElevenLabs since we have the API key
        
    agent_context = {
        "client_id": client.id,  # Add client_id for API key lookup
        "agent_slug": agent.slug,
        "agent_name": agent.name,
        "system_prompt": agent.system_prompt,
        "voice_settings": voice_settings,
        "webhooks": agent.webhooks.dict() if agent.webhooks else {},
        "user_id": request.user_id,
        "session_id": request.session_id,
        "conversation_id": request.conversation_id,
        "context": request.context or {},
        "api_keys": {
            # LLM Providers
            "openai_api_key": client.settings.api_keys.openai_api_key if client.settings and client.settings.api_keys else None,
            "groq_api_key": client.settings.api_keys.groq_api_key if client.settings and client.settings.api_keys else None,
            "deepinfra_api_key": client.settings.api_keys.deepinfra_api_key if client.settings and client.settings.api_keys else None,
            "replicate_api_key": client.settings.api_keys.replicate_api_key if client.settings and client.settings.api_keys else None,
            # Voice/Speech Providers
            "deepgram_api_key": client.settings.api_keys.deepgram_api_key if client.settings and client.settings.api_keys else None,
            "elevenlabs_api_key": client.settings.api_keys.elevenlabs_api_key if client.settings and client.settings.api_keys else None,
            "cartesia_api_key": client.settings.api_keys.cartesia_api_key if client.settings and client.settings.api_keys else None,
            "speechify_api_key": client.settings.api_keys.speechify_api_key if client.settings and client.settings.api_keys else None,
            # Embedding/Reranking Providers
            "novita_api_key": client.settings.api_keys.novita_api_key if client.settings and client.settings.api_keys else None,
            "cohere_api_key": client.settings.api_keys.cohere_api_key if client.settings and client.settings.api_keys else None,
            "siliconflow_api_key": client.settings.api_keys.siliconflow_api_key if client.settings and client.settings.api_keys else None,
            "jina_api_key": client.settings.api_keys.jina_api_key if client.settings and client.settings.api_keys else None,
            # Additional providers
            "anthropic_api_key": getattr(client.settings.api_keys, 'anthropic_api_key', None) if client.settings and client.settings.api_keys else None,
        } if client.settings and client.settings.api_keys else {}
    }
    
    # Ensure the room exists (create if it doesn't)
    room_start = time.time()
    room_info = await ensure_livekit_room_exists(
        backend_livekit, 
        request.room_name,
        agent_name=agent.name,
        agent_slug=agent.slug,  # Pass the actual agent slug
        user_id=request.user_id,
        agent_config=agent_context
    )
    room_duration = time.time() - room_start
    logger.info(f"⏱️ Room ensure process took {room_duration:.2f}s")
    logger.info(f"Room ensured: {room_info['status']} for room {room_info.get('room_name', request.room_name)}")
    
    # Generate user token for frontend to join the room (thin client)
    token_start = time.time()
    user_token = backend_livekit.create_token(
        identity=f"user_{request.user_id}",
        room_name=request.room_name,
        metadata={"user_id": request.user_id, "client_id": client.id}
    )
    token_duration = time.time() - token_start
    logger.info(f"⏱️ User token generation took {token_duration:.2f}s")
    logger.debug(f"Generated user token", extra={'token_length': len(user_token)})
    
    # EXPLICITLY DISPATCH THE AGENT
    # Check if there are already participants in the room (which might indicate an agent)
    try:
        participants = await backend_livekit.list_participants(request.room_name)
        agent_count = sum(1 for p in participants if 'agent' in p.get('identity', '').lower())
        if agent_count > 0:
            logger.warning(f"⚠️ Room {request.room_name} already has {agent_count} agent(s). Skipping dispatch.")
            dispatch_info = {
                "status": "skipped",
                "message": f"Agent already present in room (found {agent_count} agents)",
                "mode": "skipped_dispatch"
            }
        else:
            dispatch_start = time.time()
            dispatch_info = await dispatch_agent_job(
                livekit_manager=backend_livekit,
                room_name=request.room_name,
                agent=agent,
                client=client
            )
            dispatch_duration = time.time() - dispatch_start
            logger.info(f"⏱️ Agent dispatch took {dispatch_duration:.2f}s")
            logger.info(f"Agent dispatch completed with status: {dispatch_info.get('status')}")
    except Exception as e:
        logger.error(f"Error checking participants: {e}")
        # If we can't check, proceed with dispatch
        dispatch_start = time.time()
        dispatch_info = await dispatch_agent_job(
            livekit_manager=backend_livekit,
            room_name=request.room_name,
            agent=agent,
            client=client
        )
        dispatch_duration = time.time() - dispatch_start
        logger.info(f"⏱️ Agent dispatch took {dispatch_duration:.2f}s")
        logger.info(f"Agent dispatch completed with status: {dispatch_info.get('status')}")
    
    # Add a small delay to ensure room is fully ready
    if room_info["status"] == "created":
        logger.info(f"Waiting for room {request.room_name} to be fully ready...")
        await asyncio.sleep(0.5)  # Small delay for room initialization
        
        # Verify the room exists in LiveKit after creation
        verify_start = time.time()
        room_check = await backend_livekit.get_room(request.room_name)
        if not room_check:
            logger.error(f"❌ Room {request.room_name} not found immediately after creation!")
        else:
            logger.info(f"✅ Room {request.room_name} verified in LiveKit with agent dispatch enabled")
        verify_duration = time.time() - verify_start
        logger.info(f"⏱️ Post-creation verification took {verify_duration:.2f}s")
    
    # Room has been created and agent has been explicitly dispatched
    logger.info(f"🎯 Room {request.room_name} created and agent explicitly dispatched")
    
    voice_trigger_total = time.time() - voice_trigger_start
    logger.info(f"⏱️ TOTAL voice trigger process took {voice_trigger_total:.2f}s")
    
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
        "dispatch_info": dispatch_info,  # Use the actual dispatch_info from explicit dispatch
        "status": "voice_agent_triggered",
        "message": f"Room {request.room_name} ready with explicit agent dispatch to 'sidekick-agent', user token provided.",
        "total_duration_ms": int(voice_trigger_total * 1000)
    }


async def handle_text_trigger(
    request: TriggerAgentRequest, 
    agent, 
    client
) -> Dict[str, Any]:
    """
    Handle text mode agent triggering
    
    This should process text messages through the agent
    """
    logger.debug(f"Starting text trigger handling", extra={'agent_slug': agent.slug, 'message_length': len(request.message) if request.message else 0})
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
    logger.info(f"Text context prepared", extra=text_context)
    
    # Check if agent has text webhook configured
    text_webhook = agent.webhooks.text_context_webhook_url if agent.webhooks else None
    
    # API keys from client configuration
    api_keys = client.settings.api_keys if client.settings and client.settings.api_keys else {}
    
    # Process text message through appropriate LLM
    response_text = None
    llm_provider = agent.voice_settings.llm_provider if agent.voice_settings else "openai"
    llm_model = agent.voice_settings.llm_model if agent.voice_settings else "gpt-4"
    
    # Get appropriate API key
    api_key = None
    if llm_provider == "groq" and api_keys.groq_api_key:
        api_key = api_keys.groq_api_key
    elif llm_provider == "openai" and api_keys.openai_api_key:
        api_key = api_keys.openai_api_key
    elif llm_provider == "anthropic" and hasattr(api_keys, 'anthropic_api_key') and api_keys.anthropic_api_key:
        api_key = api_keys.anthropic_api_key
    
    if api_key and api_key not in ["test_key", "test", "dummy"]:
        try:
            if llm_provider == "groq":
                # Use Groq API
                import httpx
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": llm_model or "llama3-70b-8192",
                            "messages": [
                                {"role": "system", "content": agent.system_prompt},
                                {"role": "user", "content": request.message}
                            ],
                            "temperature": 0.7,
                            "max_tokens": 1000
                        },
                        timeout=30.0
                    )
                    if response.status_code == 200:
                        result = response.json()
                        response_text = result["choices"][0]["message"]["content"]
                    else:
                        logger.error(f"Groq API error: {response.status_code} - {response.text}")
                        
            elif llm_provider == "openai":
                # Use OpenAI API
                import httpx
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": llm_model or "gpt-4",
                            "messages": [
                                {"role": "system", "content": agent.system_prompt},
                                {"role": "user", "content": request.message}
                            ],
                            "temperature": 0.7,
                            "max_tokens": 1000
                        },
                        timeout=30.0
                    )
                    if response.status_code == 200:
                        result = response.json()
                        response_text = result["choices"][0]["message"]["content"]
                    else:
                        logger.error(f"OpenAI API error: {response.status_code} - {response.text}")
                        
        except Exception as e:
            logger.error(f"Error processing text with {llm_provider}: {str(e)}")
    else:
        logger.warning(f"No valid API key available for {llm_provider}")
    
    # If we have a text webhook and no response yet, try calling it
    if not response_text and text_webhook:
        try:
            import httpx
            async with httpx.AsyncClient() as http_client:
                webhook_response = await http_client.post(
                    text_webhook,
                    json={
                        "message": request.message,
                        "agent": agent.slug,
                        "user_id": request.user_id,
                        "session_id": request.session_id,
                        "conversation_id": request.conversation_id,
                        "context": text_context
                    },
                    timeout=30.0
                )
                if webhook_response.status_code == 200:
                    webhook_data = webhook_response.json()
                    response_text = webhook_data.get("response", webhook_data.get("message", ""))
        except Exception as e:
            logger.error(f"Error calling text webhook: {str(e)}")
    
    return {
        "mode": "text",
        "message_received": request.message,
        "text_context": text_context,
        "webhook_configured": bool(text_webhook),
        "webhook_url": text_webhook,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "api_key_available": bool(api_key and api_key not in ["test_key", "test", "dummy"]),
        "status": "text_message_processed",
        "response": response_text or f"I'm sorry, I couldn't process your message. Please ensure the {llm_provider} API key is configured.",
        "agent_response": response_text
    }


async def dispatch_agent_job(
    livekit_manager: LiveKitManager,
    room_name: str,
    agent,
    client
) -> Dict[str, Any]:
    """
    Explicit dispatch mode - Directly dispatch agent to room with full configuration.
    
    This ensures the agent receives all necessary configuration and API keys
    through job metadata, following LiveKit's recommended pattern.
    """
    logger.info(f"🚀 Starting agent dispatch: agent={agent.slug}, room={room_name}, client={client.id}")
    dispatch_start = time.time()
    
    try:
        # Prepare full agent configuration for job metadata
        # Build voice settings with proper defaults
        voice_settings = agent.voice_settings.dict() if agent.voice_settings else {}
        
        # Apply defaults for null values
        if not voice_settings.get('llm_provider'):
            voice_settings['llm_provider'] = 'groq'  # Use Groq as default since we have the API key
        if not voice_settings.get('stt_provider'):
            voice_settings['stt_provider'] = 'deepgram'
        if not voice_settings.get('tts_provider'):
            voice_settings['tts_provider'] = 'elevenlabs'  # Use ElevenLabs since we have the API key
            
        job_metadata = {
            "client_id": client.id,
            "agent_slug": agent.slug,
            "agent_name": agent.name,
            "system_prompt": agent.system_prompt,
            "voice_settings": voice_settings,
            "webhooks": agent.webhooks.dict() if agent.webhooks else {},
            "api_keys": {
                # LLM Providers
                "openai_api_key": client.settings.api_keys.openai_api_key if client.settings and client.settings.api_keys else None,
                "groq_api_key": client.settings.api_keys.groq_api_key if client.settings and client.settings.api_keys else None,
                "deepinfra_api_key": client.settings.api_keys.deepinfra_api_key if client.settings and client.settings.api_keys else None,
                "replicate_api_key": client.settings.api_keys.replicate_api_key if client.settings and client.settings.api_keys else None,
                # Voice/Speech Providers
                "deepgram_api_key": client.settings.api_keys.deepgram_api_key if client.settings and client.settings.api_keys else None,
                "elevenlabs_api_key": client.settings.api_keys.elevenlabs_api_key if client.settings and client.settings.api_keys else None,
                "cartesia_api_key": client.settings.api_keys.cartesia_api_key if client.settings and client.settings.api_keys else None,
                "speechify_api_key": client.settings.api_keys.speechify_api_key if client.settings and client.settings.api_keys else None,
                # Embedding/Reranking Providers
                "novita_api_key": client.settings.api_keys.novita_api_key if client.settings and client.settings.api_keys else None,
                "cohere_api_key": client.settings.api_keys.cohere_api_key if client.settings and client.settings.api_keys else None,
                "siliconflow_api_key": client.settings.api_keys.siliconflow_api_key if client.settings and client.settings.api_keys else None,
                "jina_api_key": client.settings.api_keys.jina_api_key if client.settings and client.settings.api_keys else None,
                # Additional providers
                "anthropic_api_key": getattr(client.settings.api_keys, 'anthropic_api_key', None) if client.settings and client.settings.api_keys else None,
            } if client.settings and client.settings.api_keys else {}
        }
        
        # Create LiveKit API client for explicit dispatch
        api_start = time.time()
        livekit_api = api.LiveKitAPI(
            url=livekit_manager.url,
            api_key=livekit_manager.api_key,
            api_secret=livekit_manager.api_secret
        )
        api_duration = time.time() - api_start
        logger.info(f"⏱️ LiveKit API client creation took {api_duration:.2f}s")
        
        # Dispatch the agent with job metadata using the correct API method
        dispatch_request = api.CreateAgentDispatchRequest(
            room=room_name,
            metadata=json.dumps(job_metadata),  # Pass full config as job metadata
            agent_name="sidekick-agent"  # Match the agent name the worker accepts
        )
        
        logger.info(f"📤 Sending dispatch request:")
        logger.info(f"   - Room: {room_name}")
        logger.info(f"   - Agent name: sidekick-agent")
        logger.info(f"   - Metadata fields: {len(job_metadata)}")
        logger.info(f"   - Metadata size: {len(json.dumps(job_metadata))} bytes")
        
        dispatch_api_start = time.time()
        dispatch_response = await livekit_api.agent_dispatch.create_dispatch(dispatch_request)
        dispatch_api_duration = time.time() - dispatch_api_start
        logger.info(f"⏱️ Dispatch API call took {dispatch_api_duration:.2f}s")
        
        # Log the actual response to understand structure
        logger.info(f"Dispatch response type: {type(dispatch_response)}")
        logger.info(f"Dispatch response dir: {dir(dispatch_response)}")
        
        # Try different attribute names
        dispatch_id = None
        if hasattr(dispatch_response, 'dispatch_id'):
            dispatch_id = dispatch_response.dispatch_id
        elif hasattr(dispatch_response, 'agent_dispatch_id'):
            dispatch_id = dispatch_response.agent_dispatch_id
        elif hasattr(dispatch_response, 'id'):
            dispatch_id = dispatch_response.id
        
        logger.info(f"✅ Agent dispatched successfully with dispatch_id: {dispatch_id}")
        
        dispatch_total_duration = time.time() - dispatch_start
        logger.info(f"⏱️ Total dispatch process took {dispatch_total_duration:.2f}s")
        
        return {
            "status": "dispatched",
            "dispatch_id": dispatch_id,
            "message": "Agent job dispatched to worker pool.",
            "mode": "explicit_dispatch",
            "agent": agent.slug,
            "metadata_size": len(json.dumps(job_metadata)),
            "duration_ms": int(dispatch_total_duration * 1000)
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to dispatch agent: {str(e)}", exc_info=True)
        logger.error(f"   - Error type: {type(e).__name__}")
        logger.error(f"   - Room: {room_name}")
        logger.error(f"   - Agent: {agent.slug}")
        
        # Fallback to automatic mode if explicit dispatch fails
        return {
            "status": "automatic",
            "message": f"Explicit dispatch failed ({str(e)}), falling back to automatic mode",
            "mode": "automatic_dispatch",
            "agent": agent.slug,
            "error": str(e)
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
    Ensure a LiveKit room exists, creating it if necessary
    
    This function handles the room lifecycle to prevent timing issues:
    1. Check if room already exists
    2. Create room with appropriate settings if it doesn't exist
    3. Return room information for the frontend
    """
    import time
    start_time = time.time()
    
    logger.debug(f"Checking if room exists", extra={'room_name': room_name})
    try:
        # First, check if the room already exists
        check_start = time.time()
        existing_room = await livekit_manager.get_room(room_name)
        check_duration = time.time() - check_start
        logger.info(f"⏱️ Room existence check took {check_duration:.2f}s")
        
        if existing_room:
            logger.info(f"✅ Room {room_name} already exists with {existing_room['num_participants']} participants")
            total_duration = time.time() - start_time
            return {
                "room_name": room_name,
                "status": "existing",
                "participants": existing_room['num_participants'],
                "created_at": existing_room.get('creation_time'),
                "message": f"Room {room_name} already exists and is ready",
                "duration_ms": int(total_duration * 1000)
            }
        
        # Room doesn't exist, create it
        logger.info(f"🏗️ Creating new LiveKit room: {room_name}")
        
        # Start with the full agent configuration as the base for the metadata
        room_metadata = agent_config if agent_config is not None else {}
        
        # Add or overwrite general room information
        room_metadata.update({
            "agent_name": agent_name,
            "agent_slug": agent_slug,
            "user_id": user_id,
            "created_by": "sidekick_backend",
            "created_at": datetime.now().isoformat()
        })
        
        # Convert metadata to JSON string (LiveKit expects JSON string)
        import json
        metadata_json = json.dumps(room_metadata)
        
        create_start = time.time()
        room_info = await livekit_manager.create_room(
            name=room_name,
            empty_timeout=1800,  # 30 minutes - much longer timeout for agent rooms
            max_participants=10,  # Allow multiple participants
            metadata=metadata_json
            # Agent dispatch flags are removed. This function ONLY creates a room.
        )
        create_duration = time.time() - create_start
        logger.info(f"⏱️ Room creation took {create_duration:.2f}s")
        
        logger.info(f"✅ Created room {room_name} successfully")
        
        # Quick wait to ensure room is fully created
        wait_start = time.time()
        await asyncio.sleep(0.2)  # Reduced from 1s to 0.2s
        wait_duration = time.time() - wait_start
        logger.info(f"⏱️ Room ready wait took {wait_duration:.2f}s")
        
        # Create a placeholder token to keep the room alive
        token_start = time.time()
        placeholder_token = livekit_manager.create_token(
            identity="room_keeper",
            room_name=room_name,
            metadata={"placeholder": True, "role": "room_keeper"}
        )
        token_duration = time.time() - token_start
        logger.info(f"⏱️ Token creation took {token_duration:.2f}s")
        
        logger.info(f"Created placeholder token for room {room_name}")
        
        # Verify room was created
        verify_start = time.time()
        verification = await livekit_manager.get_room(room_name)
        verify_duration = time.time() - verify_start
        logger.info(f"⏱️ Room verification took {verify_duration:.2f}s")
        
        if not verification:
            raise Exception(f"Room {room_name} was created but cannot be verified")
        
        logger.debug(f"Room verified", extra={'status': 'success'})
        return {
            "room_name": room_name,
            "status": "created",
            "participants": 0,
            "created_at": room_info["created_at"].isoformat(),
            "max_participants": room_info["max_participants"],
            "metadata": room_metadata,
            "message": f"Room {room_name} created successfully and ready for participants"
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to ensure room {room_name} exists: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create/verify LiveKit room: {str(e)}"
        )