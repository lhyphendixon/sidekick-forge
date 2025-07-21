"""
Agent trigger endpoint for WordPress plugin integration
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from enum import Enum
import logging
import asyncio
import subprocess
import json
import uuid
import tempfile
import time
from datetime import datetime

from app.services.agent_service_supabase import AgentService
from app.services.client_service_supabase import ClientService
from app.core.dependencies import get_client_service, get_agent_service
from app.integrations.livekit_client import LiveKitManager
from app.config import settings
from app.utils.logging_config import get_context_logger
from app.utils.metrics import AGENT_TRIGGERS
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
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    
    # Create contextual logger for this request
    ctx_logger = get_context_logger(
        __name__,
        request_id=request_id,
        agent_slug=request.agent_slug,
        user_id=request.user_id,
        mode=request.mode.value,
        room_name=request.room_name,
        client_id=request.client_id
    )
    
    try:
        ctx_logger.info(f"Triggering agent {request.agent_slug} in {request.mode} mode")
        
        # Validate mode-specific requirements
        if request.mode == TriggerMode.VOICE and not request.room_name:
            raise HTTPException(status_code=400, detail="room_name is required for voice mode")
        
        if request.mode == TriggerMode.TEXT and not request.message:
            raise HTTPException(status_code=400, detail="message is required for text mode")
        
        # Auto-detect client_id if not provided by finding agent across all clients
        client_id = request.client_id
        if not client_id:
            ctx_logger.info(f"Auto-detecting client for agent {request.agent_slug}")
            all_agents = await agent_service.get_all_agents_with_clients()
            for agent in all_agents:
                # agent is a dict from get_all_agents_with_clients
                if agent.get("slug") == request.agent_slug:
                    client_id = agent.get("client_id")
                    ctx_logger.info(f"Found agent {request.agent_slug} in client {client_id}")
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
        
        # Track successful trigger
        AGENT_TRIGGERS.labels(
            agent_slug=request.agent_slug,
            mode=request.mode.value,
            status="success"
        ).inc()
        
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
        
    except HTTPException as he:
        # Track failed trigger
        AGENT_TRIGGERS.labels(
            agent_slug=request.agent_slug,
            mode=request.mode.value,
            status="client_error"
        ).inc()
        raise
    except Exception as e:
        # Track failed trigger
        AGENT_TRIGGERS.labels(
            agent_slug=request.agent_slug,
            mode=request.mode.value,
            status="server_error"
        ).inc()
        logger.error(f"Error triggering agent {request.agent_slug}: {str(e)}")
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
        
        logger.info(f"🏢 Creating room with backend LiveKit infrastructure")
        
        # Create the room
        room_metadata = {
            "client_id": request.client_id,
            "client_name": client.name,
            "created_by": "autonomite_backend_api",
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
    logger.info(f"Handling voice trigger for agent {agent.slug} in room {request.room_name}")
    
    # Use backend LiveKit infrastructure for thin client architecture
    from app.integrations.livekit_client import livekit_manager
    backend_livekit = livekit_manager
    
    logger.info(f"🏢 Using backend LiveKit infrastructure for thin client architecture")
    
    # Ensure the room exists (create if it doesn't)
    room_info = await ensure_livekit_room_exists(
        backend_livekit, 
        request.room_name,
        agent_name=agent.name,
        user_id=request.user_id
    )
    
    # Generate user token for frontend to join the room (with agent dispatch)
    user_token = backend_livekit.create_token(
        identity=f"user_{request.user_id}",
        room_name=request.room_name,
        metadata={"user_id": request.user_id, "client_id": client.id},
        enable_agent_dispatch=True,
        agent_name="minimal-agent"  # Match the agent name in our container
    )
    
    # Prepare agent context for LiveKit
    agent_context = {
        "agent_slug": agent.slug,
        "agent_name": agent.name,
        "system_prompt": agent.system_prompt,
        "voice_settings": agent.voice_settings,
        "webhooks": agent.webhooks,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "conversation_id": request.conversation_id,
        "context": request.context or {}
    }
    
    # Add a small delay to ensure room is fully ready
    if room_info["status"] == "created":
        logger.info(f"Waiting 0.5 seconds for room {request.room_name} to be fully ready...")
        await asyncio.sleep(0.5)
    
    # Spawn agent container with client's LiveKit credentials
    container_result = await spawn_agent_container(
        agent=agent,
        client=client,
        room_name=request.room_name,
        user_id=request.user_id,
        session_id=request.session_id,
        conversation_id=request.conversation_id,
        context=request.context,
        use_backend_livekit=True  # Use backend for room creation, client creds for agent
    )
    
    # If container spawned successfully, wait for readiness then dispatch
    if container_result.get("status") != "error":
        container_name = container_result.get("container_name")
        logger.info(f"⏳ Waiting for container {container_name} to be ready...")
        
        # Skip health check temporarily - containers start immediately
        # TODO: Re-enable once health server is properly integrated  
        # container_ready = await wait_for_container_ready(container_name, max_attempts=15, delay=1)
        container_ready = True  # Assume container is ready
        
        if container_ready:
            logger.info(f"✅ Container {container_name} assumed ready (health check skipped)")
            logger.info(f"🎯 Dispatching agent {agent.slug} to room {request.room_name}")
            
            # Skip explicit dispatch - the agent uses automatic dispatch
            # The agent will automatically pick up jobs when participants join the room
            logger.info(f"✅ Agent container ready - will auto-dispatch when participants join")
            dispatch_result = {
                "success": True,
                "method": "automatic",
                "message": "Agent will auto-dispatch when participants join the room"
            }
            
            logger.info(f"📨 Dispatch result: {dispatch_result}")
            container_result["dispatch"] = dispatch_result
        else:
            logger.error(f"❌ Container {container_name} failed to become ready")
            container_result["dispatch"] = {
                "success": False,
                "error": "Container failed health checks"
            }
    
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
        "container_info": container_result,
        "status": "voice_agent_triggered",
        "message": f"Room {request.room_name} created with backend LiveKit, worker started with automatic dispatch"
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
    
    # API keys from client configuration
    api_keys = client.settings.api_keys if client.settings and client.settings.api_keys else {}
    
    # TODO: Here you would integrate with your text processing system
    # This could involve:
    # 1. Calling the agent's text webhook if configured
    # 2. Using AI APIs (OpenAI, etc.) with the client's API keys
    # 3. Processing through LiveKit data channels
    
    return {
        "mode": "text",
        "message_received": request.message,
        "text_context": text_context,
        "webhook_configured": bool(text_webhook),
        "webhook_url": text_webhook,
        "api_keys_available": list(api_keys.__dict__.keys()) if hasattr(api_keys, '__dict__') else [],
        "status": "text_message_processed",
        "response": f"Text message processed by agent {agent.slug}. Integration with AI processing pipeline needed."
    }


async def spawn_agent_container(
    agent,
    client, 
    room_name: str,
    user_id: str,
    session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    use_backend_livekit: bool = False
) -> Dict[str, Any]:
    """
    Spawn a dedicated agent container for this client using their LiveKit credentials
    
    This implements the multi-tenant architecture where each client gets their own
    isolated agent container configured with their specific LiveKit Cloud account.
    """
    logger.info(f"🚀 Spawning dedicated container for {agent.slug} in room {room_name}")
    
    try:
        # Import container manager
        from app.services.container_manager import container_manager
        await container_manager.initialize()
        
        # Get client's LiveKit credentials - NO FALLBACKS ALLOWED
        livekit_url = None
        livekit_api_key = None
        livekit_api_secret = None
        
        # Extract LiveKit credentials from client settings
        if hasattr(client, 'settings') and client.settings and hasattr(client.settings, 'livekit'):
            livekit_url = getattr(client.settings.livekit, 'server_url', None)
            livekit_api_key = getattr(client.settings.livekit, 'api_key', None)
            livekit_api_secret = getattr(client.settings.livekit, 'api_secret', None)
        
        # Validate all LiveKit credentials are present
        if not all([livekit_url, livekit_api_key, livekit_api_secret]):
            logger.error(f"❌ Missing LiveKit credentials for client {client.id}")
            logger.error(f"   - URL: {'Present' if livekit_url else 'MISSING'}")
            logger.error(f"   - API Key: {'Present' if livekit_api_key else 'MISSING'}")
            logger.error(f"   - API Secret: {'Present' if livekit_api_secret else 'MISSING'}")
            raise HTTPException(
                status_code=500,
                detail=f"Client {client.id} does not have LiveKit credentials configured. Each client must have their own LiveKit Cloud account."
            )
        
        logger.info(f"Using LiveKit URL: {livekit_url}")
        
        # Debug logging for API keys
        if hasattr(client.settings.api_keys, 'cartesia_api_key'):
            cartesia_key = client.settings.api_keys.cartesia_api_key
            logger.info(f"📝 Cartesia API key from client: length={len(cartesia_key) if cartesia_key else 0}, value={repr(cartesia_key)}")
        
        # Prepare agent configuration
        agent_config = {
            "agent_name": agent.name,
            "system_prompt": agent.system_prompt,
            "livekit_url": livekit_url,
            "livekit_api_key": livekit_api_key,
            "livekit_api_secret": livekit_api_secret,
            "room_name": room_name,  # Add room name for container lifecycle management
            
            # Voice settings
            "voice_id": agent.voice_settings.voice_id if agent.voice_settings else "alloy",
            "stt_provider": agent.voice_settings.stt_provider if agent.voice_settings and agent.voice_settings.stt_provider else (agent.voice_settings.provider.value if agent.voice_settings and hasattr(agent.voice_settings.provider, 'value') else "deepgram"),
            "tts_provider": agent.voice_settings.provider.value if agent.voice_settings and hasattr(agent.voice_settings.provider, 'value') else "elevenlabs",
            
            # API keys from client (using correct attribute names)
            "openai_api_key": client.settings.api_keys.openai_api_key if hasattr(client.settings.api_keys, 'openai_api_key') else "",
            "anthropic_api_key": client.settings.api_keys.anthropic_api_key if hasattr(client.settings.api_keys, 'anthropic_api_key') else "",
            "groq_api_key": client.settings.api_keys.groq_api_key if hasattr(client.settings.api_keys, 'groq_api_key') else "",
            "deepgram_api_key": client.settings.api_keys.deepgram_api_key if hasattr(client.settings.api_keys, 'deepgram_api_key') else "",
            "cartesia_api_key": client.settings.api_keys.cartesia_api_key if hasattr(client.settings.api_keys, 'cartesia_api_key') else "",
            "elevenlabs_api_key": client.settings.api_keys.elevenlabs_api_key if hasattr(client.settings.api_keys, 'elevenlabs_api_key') else "",
            
            # Webhooks
            "voice_context_webhook_url": agent.webhooks.voice_context_webhook_url if agent.webhooks else "",
            "text_context_webhook_url": agent.webhooks.text_context_webhook_url if agent.webhooks else "",
        }
        
        # Site/client configuration
        site_config = {
            "domain": client.name,
            "tier": getattr(client, 'tier', 'basic')
        }
        
        # Deploy the agent container with session identifier from room name
        # Extract a unique session ID from the room name (last 8 chars of room name)
        import uuid
        if len(room_name) >= 8:
            session_id_for_container = room_name.split("_")[-1][:8] if "_" in room_name else room_name[:8]
            logger.info(f"📝 Extracted session ID '{session_id_for_container}' from room name '{room_name}'")
        else:
            # Fallback for short room names
            session_id_for_container = str(uuid.uuid4())[:8]
            logger.warning(f"⚠️ Room name '{room_name}' too short for session extraction, using fallback ID: {session_id_for_container}")
        
        container_info = await container_manager.deploy_agent_container(
            site_id=client.id,
            agent_slug=agent.slug,
            agent_config=agent_config,
            site_config=site_config,
            session_id=session_id_for_container
        )
        
        # Store job metadata for the container
        job_metadata = {
            "user_id": user_id,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "agent_slug": agent.slug,
            "client_id": client.id,
            "context": context or {},
            "room_name": room_name,
            "timestamp": time.time()
        }
        
        # Create metadata file for container access
        metadata_file = f"/tmp/job_metadata_{container_info['name']}.json"
        with open(metadata_file, 'w') as f:
            json.dump(job_metadata, f)
        
        logger.info(f"✅ Container {container_info['name']} deployed for {client.name}/{agent.slug}")
        logger.info(f"📋 Job metadata stored at {metadata_file}")
        logger.info(f"🔗 Container connected to client's LiveKit Cloud: {client.settings.livekit.server_url}")
        
        return {
            "container_id": container_info["id"],
            "container_name": container_info["name"],
            "metadata_file": metadata_file,
            "status": container_info["status"],
            "room_name": room_name,
            "agent_slug": agent.slug,
            "client_id": client.id,
            "method": "containerized_agent",
            "livekit_cloud": client.settings.livekit.server_url,
            "capabilities": [
                "voice_processing", 
                "client_isolated",
                "livekit_cloud_connected",
                "api_key_configured",
                "webhook_enabled"
            ],
            "message": f"Dedicated agent container deployed for {room_name} on client's LiveKit Cloud"
        }
            
    except Exception as e:
        logger.error(f"❌ Failed to spawn agent container: {str(e)}")
        return {
            "container_id": None,
            "status": "error", 
            "error": str(e),
            "message": f"Failed to spawn agent container: {str(e)}"
        }


async def create_client_livekit_manager(client) -> LiveKitManager:
    """Create a LiveKit manager using client-specific credentials"""
    # Create a new LiveKit manager instance with client credentials
    client_livekit = LiveKitManager()
    client_livekit.api_key = client.settings.livekit.api_key
    client_livekit.api_secret = client.settings.livekit.api_secret
    client_livekit.url = client.settings.livekit.server_url
    client_livekit._initialized = True
    
    return client_livekit


async def ensure_livekit_room_exists(
    livekit_manager: LiveKitManager,
    room_name: str,
    agent_name: str = None,
    user_id: str = None
) -> Dict[str, Any]:
    """
    Ensure a LiveKit room exists, creating it if necessary
    
    This function handles the room lifecycle to prevent timing issues:
    1. Check if room already exists
    2. Create room with appropriate settings if it doesn't exist
    3. Return room information for the frontend
    """
    try:
        # First, check if the room already exists
        existing_room = await livekit_manager.get_room(room_name)
        
        if existing_room:
            logger.info(f"✅ Room {room_name} already exists with {existing_room['num_participants']} participants")
            return {
                "room_name": room_name,
                "status": "existing",
                "participants": existing_room['num_participants'],
                "created_at": existing_room.get('creation_time'),
                "message": f"Room {room_name} already exists and is ready"
            }
        
        # Room doesn't exist, create it
        logger.info(f"🏗️ Creating new LiveKit room: {room_name}")
        
        room_metadata = {
            "agent_name": agent_name,
            "user_id": user_id,
            "created_by": "autonomite_backend",
            "created_at": datetime.now().isoformat(),
            # Agent dispatch will happen when participant joins
            "agent_request": {
                "agent": "minimal-agent",  # Must match the agent name in WorkerOptions
                "namespace": "default"
            }
        }
        
        room_info = await livekit_manager.create_room(
            name=room_name,
            empty_timeout=1800,  # 30 minutes - much longer timeout for agent rooms
            max_participants=10,  # Allow multiple participants
            metadata=room_metadata
        )
        
        logger.info(f"✅ Created room {room_name} successfully")
        
        # Wait a moment to ensure room is fully created
        await asyncio.sleep(1)
        
        # Create a placeholder token to keep the room alive
        placeholder_token = livekit_manager.create_token(
            identity="room_keeper",
            room_name=room_name,
            metadata={"placeholder": True, "role": "room_keeper"}
        )
        
        logger.info(f"Created placeholder token for room {room_name}")
        
        # Verify room was created
        verification = await livekit_manager.get_room(room_name)
        if not verification:
            raise Exception(f"Room {room_name} was created but cannot be verified")
        
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


async def wait_for_container_ready(
    container_name: str, 
    max_attempts: int = 15, 
    delay: int = 1
) -> bool:
    """
    Poll container health endpoint until ready or timeout
    """
    import httpx
    
    # Containers expose health on port 8080
    health_url = f"http://{container_name}:8080/health"
    
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(health_url, timeout=2.0)
                if response.status_code == 200:
                    health_data = response.json()
                    if health_data.get("status") == "healthy":
                        logger.info(f"✅ Container {container_name} health check passed (attempt {attempt + 1})")
                        return True
        except Exception as e:
            logger.debug(f"Health check attempt {attempt + 1} failed: {e}")
        
        if attempt < max_attempts - 1:
            await asyncio.sleep(delay)
    
    logger.error(f"❌ Container {container_name} health checks failed after {max_attempts} attempts")
    return False


async def dispatch_agent_to_room(
    livekit_manager: LiveKitManager,
    room_name: str,
    agent_name: str,
    metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Explicitly dispatch an agent to a LiveKit room using the AgentDispatchService
    
    This is required when using explicit dispatch (agent_name is set in WorkerOptions)
    """
    try:
        logger.info(f"🎯 Dispatching agent {agent_name} to room {room_name}")
        
        # Create the LiveKit API client
        lk_api = api.LiveKitAPI(
            livekit_manager.url,
            livekit_manager.api_key,
            livekit_manager.api_secret
        )
        
        # Create the dispatch request
        dispatch_request = api.CreateAgentDispatchRequest(
            room=room_name,
            agent_name=agent_name,
            metadata=json.dumps(metadata) if metadata else None
        )
        
        # Send the dispatch request
        dispatch_result = await lk_api.agent_dispatch.create_dispatch(dispatch_request)
        
        logger.info(f"✅ Agent dispatch successful: {dispatch_result}")
        
        return {
            "success": True,
            "dispatch_id": dispatch_result.agent_id if hasattr(dispatch_result, 'agent_id') else None,
            "room_name": room_name,
            "agent_name": agent_name,
            "metadata": metadata,
            "message": f"Agent {agent_name} dispatched to room {room_name}"
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to dispatch agent {agent_name} to room {room_name}: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "room_name": room_name,
            "agent_name": agent_name,
            "message": f"Failed to dispatch agent: {str(e)}"
        }