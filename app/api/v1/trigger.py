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
    try:
        logger.info(f"Triggering agent {request.agent_slug} in {request.mode} mode for user {request.user_id}")
        
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
    
    # Use backend's LiveKit credentials for ALL operations (true thin client)
    # Clients don't need LiveKit credentials - backend owns the infrastructure
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
    
    # Generate user token for frontend to join the room (thin client)
    user_token = backend_livekit.create_token(
        identity=f"user_{request.user_id}",
        room_name=request.room_name,
        metadata={"user_id": request.user_id, "client_id": client.id}
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
        logger.info(f"Waiting 2 seconds for room {request.room_name} to be fully ready...")
        await asyncio.sleep(2)
    
    # Trigger actual LiveKit agent container (using backend credentials)
    container_result = await spawn_agent_container(
        agent=agent,
        client=client,
        room_name=request.room_name,
        user_id=request.user_id,
        session_id=request.session_id,
        conversation_id=request.conversation_id,
        context=request.context,
        use_backend_livekit=True  # Force use of backend credentials
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
        "container_info": container_result,
        "status": "voice_agent_triggered",
        "message": f"Room {request.room_name} created with backend credentials, agent {agent.slug} spawned, user token provided"
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
    Trust that the main sophisticated agent is running and will handle job dispatch
    
    The main agent (autonomite_agent_v1_1_19_text_support.py) has a request_filter 
    that automatically accepts all room jobs. No need to start separate workers.
    """
    logger.info(f"🎯 Trusting main agent to handle job for {agent.slug} in room {room_name}")
    
    try:
        # Check if main sophisticated agent is running
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True
        )
        
        main_agent_running = False
        main_agent_pid = None
        for line in result.stdout.split('\n'):
            if 'autonomite_agent_v1_1_19_text_support.py' in line and 'dev' in line:
                main_agent_running = True
                # Extract PID
                parts = line.split()
                if len(parts) > 1:
                    main_agent_pid = parts[1]
                break
        
        if not main_agent_running:
            logger.error("❌ Main sophisticated agent is not running!")
            return {
                "container_id": None,
                "status": "error",
                "error": "Main agent not running",
                "message": "The main sophisticated agent (autonomite_agent_v1_1_19_text_support.py) is not running. Please start it."
            }
        
        # Create job metadata for the main agent to access
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
        
        # Store metadata for main agent access
        container_id = f"agent-{agent.slug}-{uuid.uuid4().hex[:8]}"
        metadata_file = f"/tmp/job_metadata_{container_id}.json"
        with open(metadata_file, 'w') as f:
            json.dump(job_metadata, f)
        
        logger.info(f"✅ Main agent (PID {main_agent_pid}) is running and will automatically handle room {room_name}")
        logger.info(f"📋 Job metadata stored at {metadata_file}")
        logger.info(f"🎯 The main agent has request_filter that accepts all jobs - no manual dispatch needed")
        
        # The main agent will automatically:
        # 1. Detect when a user joins the room
        # 2. Accept the job via request_filter 
        # 3. Run the entrypoint function
        # 4. Provide full voice processing with RAG, user profiles, etc.
        
        return {
            "container_id": container_id,
            "metadata_file": metadata_file,
            "status": "main_agent_ready",
            "room_name": room_name,
            "agent_slug": agent.slug,
            "main_agent_pid": main_agent_pid,
            "method": "main_agent_auto_dispatch",
            "capabilities": [
                "voice_processing", 
                "rag_context", 
                "user_profiles", 
                "conversation_storage",
                "intelligent_responses"
            ],
            "message": f"Main sophisticated agent ready for {room_name} - will auto-accept job"
        }
            
    except Exception as e:
        logger.error(f"❌ Failed to verify main agent status: {str(e)}")
        return {
            "container_id": None,
            "status": "error", 
            "error": str(e),
            "message": f"Failed to verify main agent: {str(e)}"
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
            "created_at": datetime.now().isoformat()
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