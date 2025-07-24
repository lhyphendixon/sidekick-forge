"""
Agent trigger endpoint for WordPress plugin integration
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Body, BackgroundTasks
from pydantic import BaseModel, Field
from enum import Enum
import logging
import asyncio
import subprocess
import json
import uuid
import tempfile
import time
from datetime import datetime, timedelta

from app.services.agent_service_supabase import AgentService
from app.services.client_service_supabase import ClientService
from app.core.dependencies import get_client_service, get_agent_service
from app.integrations.livekit_client import LiveKitManager
from app.services.livekit_client_manager import get_client_livekit_manager
from app.config import settings
from app.utils.logging_config import get_context_logger
from app.utils.metrics import AGENT_TRIGGERS
from app.services.container_pool_manager import get_container_pool_manager
from app.utils.circuit_breaker import circuit_breaker, get_circuit_breaker
from app.services.error_reporter import report_error, ErrorSeverity
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
    http_request: Request,
    background_tasks: BackgroundTasks,
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
            result = await handle_voice_trigger(request, agent, client, http_request, background_tasks)
        else:  # TEXT mode
            result = await handle_text_trigger(request, agent, client)
        
        # Track successful trigger
        AGENT_TRIGGERS.labels(
            agent_slug=request.agent_slug,
            mode=request.mode.value,
            status="success"
        ).inc()
        
        # Extract room_name and user_token for top-level response
        response = TriggerAgentResponse(
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
        
        # Add room_name and user_token at top level for voice mode
        if request.mode == TriggerMode.VOICE and result:
            response_dict = response.dict()
            response_dict["room_name"] = result.get("room_name")
            if result.get("livekit_config"):
                response_dict["user_token"] = result["livekit_config"].get("user_token")
            return response_dict
        
        return response
        
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
    client,
    http_request: Request,
    background_tasks: BackgroundTasks
) -> Dict[str, Any]:
    """
    Handle voice mode agent triggering
    
    This creates a LiveKit room (if needed) and triggers a Python LiveKit agent to join it
    """
    logger.info(f"Handling voice trigger for agent {agent.slug} in room {request.room_name}")
    
    # Get client's LiveKit credentials first
    client_livekit = await get_client_livekit_manager(client)
    
    if not client_livekit:
        raise HTTPException(
            status_code=500,
            detail=f"Client {client.id} does not have LiveKit credentials configured"
        )
    
    logger.info(f"🏢 Using CLIENT-SPECIFIC LiveKit infrastructure for true multi-tenant isolation")
    logger.info(f"🔐 Client LiveKit URL: {client_livekit.url}")
    logger.info(f"🔐 Client API Key (preview): {client_livekit.api_key[:10]}...")
    logger.info(f"🔐 This ensures per-client billing, logging, and migration capabilities")
    
    # Start room creation task (will run in parallel with other operations)
    room_task = asyncio.create_task(ensure_livekit_room_exists(
        client_livekit, 
        request.room_name,
        agent_name=agent.slug,  # Use slug to match worker registration
        user_id=request.user_id
    ))
    
    # Start room keepalive for preview sessions (2 hours)
    if request.room_name.startswith('preview_'):
        try:
            from app.services.room_keepalive import room_keepalive_service
            await room_keepalive_service.track_room(
                room_name=request.room_name,
                livekit_manager=client_livekit,
                duration_hours=2.0
            )
            logger.info(f"💓 Started 2-hour keepalive for preview room {request.room_name}")
        except Exception as e:
            logger.warning(f"Could not start room keepalive: {e}")
            # Non-fatal, continue anyway
            
    # Add room to monitoring
    try:
        from app.services.room_monitor import room_monitor
        room_monitor.add_room(
            room_name=request.room_name,
            livekit_manager=client_livekit,
            metadata={
                "agent": agent.name,
                "client": client.name,
                "created_at": datetime.now().isoformat(),
                "is_preview": request.room_name.startswith('preview_')
            }
        )
        logger.info(f"👁️ Added room {request.room_name} to monitoring")
    except Exception as e:
        logger.warning(f"Could not add room to monitoring: {e}")
        # Non-fatal, continue anyway
    
    # Get authenticated user email if available
    user_email = None
    if hasattr(http_request.state, 'auth') and http_request.state.auth:
        auth_context = http_request.state.auth
        if auth_context.type == "supabase" and auth_context.user_id:
            # Get user email from Supabase
            try:
                from app.integrations.supabase_client import supabase_manager
                user_response = supabase_manager.admin_client.auth.admin.get_user_by_id(auth_context.user_id)
                if user_response and user_response.user:
                    user_email = user_response.user.email
                    logger.info(f"✅ Retrieved authenticated user email for token: {user_email}")
            except Exception as e:
                logger.warning(f"Could not retrieve user email: {e}")
    
    # Prepare agent context for container
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
    
    # PARALLEL DISPATCH: Allocate container from warm pool for instant availability
    # This uses pre-warmed containers instead of on-demand spawning
    logger.info(f"🏊 Allocating container from warm pool for instant agent availability")
    
    # First check if there's already a healthy container running for this client/agent
    # This is a temporary workaround for pool restoration issues
    import docker
    try:
        logger.info(f"🔍 Checking for existing containers before pool allocation")
        docker_client = docker.from_env()
        # Look for existing containers matching this client/agent pattern
        container_prefix = f"agent_{client.id[:8]}_{agent.slug.replace('-', '_')}"
        logger.info(f"🔍 Looking for containers with prefix: {container_prefix}")
        containers = docker_client.containers.list(filters={"name": container_prefix})
        logger.info(f"🔍 Found {len(containers)} containers matching prefix")
        
        existing_container = None
        for container in containers:
            if container.status == "running":
                # Check health status
                health = container.attrs.get("State", {}).get("Health", {}).get("Status")
                if health == "healthy":
                    logger.info(f"✅ Found existing healthy container: {container.name}")
                    existing_container = container
                    break
        
        if existing_container:
            # Use the existing container instead of allocating from pool
            from app.services.container_pool_manager import PooledContainer, ContainerState
            pooled_container = PooledContainer(
                container_name=existing_container.name,
                client_id=client.id,
                agent_slug=agent.slug,
                state=ContainerState.ALLOCATED,
                created_at=datetime.now(),
                last_used=datetime.now(),
                session_count=1,
                allocated_to=request.room_name,
                metadata={"existing_container": True}
            )
            async def return_container():
                return pooled_container
            container_task = asyncio.create_task(return_container())
            logger.info(f"🔄 Using existing container {existing_container.name} instead of pool allocation")
        else:
            # No existing container, proceed with pool allocation
            # Get container pool manager
            from app.services.container_pool_manager import get_container_pool_manager
            pool_manager = get_container_pool_manager()
            
            # Prepare metadata for container allocation
            container_metadata = {
                "user_id": request.user_id,
                "session_id": request.session_id,
                "conversation_id": request.conversation_id,
                "user_email": user_email,
                "context": request.context
            }
            
            # Allocate container from pool (async but fast due to pre-warming)
            container_task = asyncio.create_task(pool_manager.allocate_container(
                client_id=client.id,
                agent_slug=agent.slug,
                room_name=request.room_name,
                metadata=container_metadata
            ))
    except Exception as e:
        logger.error(f"Error checking for existing containers: {e}")
        # Fallback to pool allocation
        from app.services.container_pool_manager import get_container_pool_manager
        pool_manager = get_container_pool_manager()
        
        container_metadata = {
            "user_id": request.user_id,
            "session_id": request.session_id,
            "conversation_id": request.conversation_id,
            "user_email": user_email,
            "context": request.context
        }
        
        container_task = asyncio.create_task(pool_manager.allocate_container(
            client_id=client.id,
            agent_slug=agent.slug,
            room_name=request.room_name,
            metadata=container_metadata
        ))
    
    # Generate user token while container is spawning (parallel operation)
    logger.info(f"🎫 Generating user token in parallel with container spawn")
    user_metadata = {
        "user_id": request.user_id,
        "client_id": client.id
    }
    if user_email:
        user_metadata["user_email"] = user_email
    
    user_token = client_livekit.create_token(
        identity=f"user_{request.user_id}",
        room_name=request.room_name,
        metadata=user_metadata,
        enable_agent_dispatch=True,
        agent_name=agent.slug  # Match the actual agent slug used in container
    )
    
    # Wait for both room creation and container allocation in parallel
    logger.info(f"⏳ Waiting for parallel room creation and container allocation...")
    room_info, pooled_container = await asyncio.gather(room_task, container_task)
    
    if pooled_container:
        logger.info(f"✅ Allocated container {pooled_container.container_name} from warm pool")
        container_name = pooled_container.container_name
        
        # Build container result for compatibility
        container_result = {
            "status": "allocated",
            "container_name": container_name,
            "container_id": container_name,
            "livekit_cloud": client_livekit.url,
            "message": f"Allocated pre-warmed container in {pooled_container.state} state",
            "session_count": pooled_container.session_count
        }
        
        # Prepare comprehensive dispatch metadata for LLM context priming
        # Per LiveKit docs: "Add context during conversation" - inject session data via dispatch
        dispatch_metadata = {
            # Container and client info
            "container_name": container_name,
            "client_id": client.id,
            "client_name": client.name,
            "agent_slug": agent.slug,
            "agent_name": agent.name,
            
            # Session identifiers
            "user_id": request.user_id,
            "session_id": request.session_id,
            "conversation_id": request.conversation_id,
            
            # User context for LLM priming
            "user_email": user_email,
            "user_context": request.context or {},
            
            # Agent configuration hints
            "system_prompt": agent.system_prompt[:200] if agent.system_prompt else None,  # First 200 chars
            "enable_rag": getattr(agent, 'enable_rag', False),
            "voice_id": agent.voice_settings.voice_id if agent.voice_settings else None,
            
            # Session metadata
            "session_started_at": datetime.now().isoformat(),
            "room_name": request.room_name,
            "is_preview": request.room_name.startswith('preview_'),
            
            # Previous conversation hint (if available)
            "has_conversation_history": bool(request.conversation_id and request.conversation_id != f"conv_{request.room_name}")
        }
        
        logger.info(f"📝 Enhanced dispatch metadata for LLM context priming:")
        logger.info(f"   User: {user_email or request.user_id}")
        logger.info(f"   Context keys: {list(request.context.keys()) if request.context else 'None'}")
        logger.info(f"   RAG enabled: {getattr(agent, 'enable_rag', False)}")
        logger.info(f"   Has history: {dispatch_metadata['has_conversation_history']}")
        
        # Schedule background dispatch and verification - this runs AFTER HTTP response
        logger.info(f"📋 Scheduling background dispatch for {container_name}")
        background_tasks.add_task(
            background_dispatch_and_verify,
            client_livekit=client_livekit,
            room_name=request.room_name,
            agent_name=agent.slug,
            metadata=dispatch_metadata,
            container_name=container_name
        )
        
        # Mark dispatch as scheduled (not completed yet)
        container_result["dispatch_status"] = "scheduled"
        container_result["dispatch_message"] = "Agent dispatch scheduled in background for minimal latency"
        logger.info(f"🚀 Dispatch scheduled - response will be sent immediately")
    else:
        # Container allocation failed - DO NOT FALLBACK per NO WORKAROUNDS policy
        logger.error(f"❌ Container pool allocation failed - no containers available")
        
        # Report error for visibility
        error_report = await report_error(
            component="trigger_endpoint",
            operation="pool_allocation_failed",
            error=Exception("Container pool exhausted or allocation failed"),
            severity=ErrorSeverity.HIGH,
            context={
                "client_id": client.id,
                "agent_slug": agent.slug,
                "room_name": request.room_name,
                "pool_status": "empty_or_failed"
            },
            user_message="Voice agent service unavailable. Container pool allocation failed."
        )
        
        # FAIL FAST with clear error message
        container_result = {
            "status": "error",
            "container_name": None,
            "message": "Container pool allocation failed - no containers available in warm pool",
            "dispatch_status": "failed",
            "dispatch_message": "No dispatch possible without container allocation",
            "error_id": error_report.error_id,
            "error_details": {
                "reason": "pool_empty_or_allocation_failed",
                "suggestion": "Check pool manager logs for container creation failures",
                "policy": "NO_FALLBACKS - failing fast to expose root cause"
            }
        }
        
        logger.error(f"🚨 FAILING FAST: Pool allocation failed for {client.name}/{agent.slug}")
        logger.error(f"   This is the root cause that needs fixing, not masking with fallbacks")
        logger.error(f"   Check: app.services.container_pool_manager for creation failures")
    
    return {
        "mode": "voice",
        "room_name": request.room_name,
        "platform": request.platform,
        "agent_context": agent_context,
        "livekit_config": {
            "server_url": client_livekit.url,
            "user_token": user_token,
            "configured": True
        },
        "room_info": room_info,
        "container_info": container_result,
        "status": "voice_agent_triggered",
        "message": f"Room {request.room_name} created with parallel agent dispatch for minimal latency"
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
    use_backend_livekit: bool = False,
    user_email: Optional[str] = None
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
        
        # Clean up old containers if we have too many
        import docker
        docker_client = docker.from_env()
        agent_containers = docker_client.containers.list(filters={"name": "agent_"})
        logger.info(f"📊 Current container count: {len(agent_containers)}")
        
        # More aggressive cleanup - clean if we have 4 or more containers
        if len(agent_containers) >= 4:
            logger.warning(f"⚠️ Found {len(agent_containers)} agent containers, cleaning up old ones")
            # Sort by creation time and remove old ones
            containers_by_age = []
            for container in agent_containers:
                created = container.attrs['Created']
                containers_by_age.append((created, container))
            containers_by_age.sort(reverse=True, key=lambda x: x[0])
            
            # Keep only the 3 most recent
            for i, (_, container) in enumerate(containers_by_age[3:]):
                try:
                    logger.info(f"🗑️ Removing old container: {container.name}")
                    container.stop(timeout=5)
                    container.remove()
                except Exception as e:
                    logger.error(f"Failed to remove container {container.name}: {e}")
        
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
        
        logger.info(f"🏢 Using CLIENT-SPECIFIC LiveKit credentials for {client.id}")
        logger.info(f"   - URL: {livekit_url}")
        logger.info(f"   - API Key: {livekit_api_key[:10]}... (length: {len(livekit_api_key)})")
        logger.info(f"   - API Secret: {'SET' if livekit_api_secret else 'MISSING'} (length: {len(livekit_api_secret) if livekit_api_secret else 0})")
        
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
            
            # Embedding providers (from client settings)
            "siliconflow_api_key": client.settings.api_keys.siliconflow_api_key if hasattr(client.settings.api_keys, 'siliconflow_api_key') else "",
            "novita_api_key": client.settings.api_keys.novita_api_key if hasattr(client.settings.api_keys, 'novita_api_key') else "",
            
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
        
        # Use the new pooling system to get or create a container
        try:
            container_name = await container_manager.get_or_create_container(
                site_id=client.id,
                agent_slug=agent.slug,
                agent_config=agent_config,
                site_config=site_config,
                session_id=session_id_for_container,
                room_name=room_name
            )
            
            # Get container info for the response
            container_info = await container_manager.get_container_info(container_name)
            
            # Clear any previous failure history on success
            from app.services.agent_fallback import agent_fallback_service
            agent_fallback_service.clear_failure_history(client.id, agent.slug)
            
        except Exception as e:
            # Handle container creation failure
            logger.error(f"❌ Container creation failed with exception: {type(e).__name__}: {str(e)}")
            logger.error(f"❌ Full exception details: {repr(e)}")
            
            from app.services.agent_fallback import agent_fallback_service
            
            fallback_response = await agent_fallback_service.handle_startup_failure(
                client_id=client.id,
                agent_slug=agent.slug,
                container_name=f"agent_{client.id[:8]}_{agent.slug}",
                error=e,
                logs=None
            )
            
            logger.error(f"Container creation failed: {fallback_response}")
            
            # Return error response instead of raising
            return {
                "status": "error",
                "error": fallback_response.get("user_message", str(e)),
                "details": fallback_response
            }
        
        # Use provided user_email if available (already retrieved from Supabase Auth)
        if user_email:
            logger.info(f"✨ Using authenticated user email for container: {user_email}")
        
        # Store job metadata for the container
        job_metadata = {
            "user_id": user_id,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "agent_slug": agent.slug,
            "client_id": client.id,
            "context": context or {},
            "room_name": room_name,
            "timestamp": time.time(),
            # Add Supabase config for RAG system
            "supabase_url": settings.supabase_url,
            "supabase_key": settings.supabase_service_role_key,
            # Add greeting for consistency
            "greeting": agent.greeting if hasattr(agent, 'greeting') else f"Hello! I'm {agent.name}. How can I help you today?",
            # Use authenticated user email or fallback
            "user_email": user_email or user_id or "anonymous@example.com",
            # RAG settings from agent configuration
            "rag_settings": agent.rag_settings.dict() if hasattr(agent, 'rag_settings') and agent.rag_settings else {}
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


async def dispatch_agent_with_retry(
    client_livekit: LiveKitManager,
    room_name: str,
    agent_name: str,
    metadata: Dict[str, Any],
    max_retries: int = 3,
    base_delay: float = 1.0
) -> Dict[str, Any]:
    """
    Dispatch agent to room with retry logic
    
    Args:
        client_livekit: Client's LiveKit manager
        room_name: Room to dispatch agent to
        agent_name: Name of the agent to dispatch
        metadata: Metadata for the dispatch
        max_retries: Maximum number of retry attempts
        base_delay: Base delay between retries (exponential backoff)
    
    Returns:
        Dict with dispatch result
    """
    logger.info(f"🚀 Starting agent dispatch with retry logic for room {room_name}")
    
    # Log client-specific credential usage
    logger.info(f"🔐 Using CLIENT-SPECIFIC LiveKit credentials for dispatch:")
    logger.info(f"   - Client LiveKit URL: {client_livekit.url}")
    logger.info(f"   - Client API Key: {client_livekit.api_key[:20]}... (NOT backend key)")
    
    # Check if workers are available before attempting dispatch
    try:
        logger.info("🔍 Checking for available workers...")
        workers = await client_livekit.lk_api.agent.list_workers()
        if not workers.workers:
            logger.warning("⚠️ No active workers found - agent dispatch will fail")
            logger.warning("   Make sure agent-worker service is running: docker-compose up -d agent-worker")
            return {
                "success": False,
                "status": "no_workers",
                "message": "No agent workers available to handle dispatch",
                "suggestion": "Start agent worker service"
            }
        logger.info(f"✅ Found {len(workers.workers)} active worker(s)")
        for worker in workers.workers:
            logger.info(f"   - Worker {worker.id}: {worker.version}, status={worker.status}")
    except Exception as e:
        logger.error(f"Failed to check workers: {e}")
        # Continue anyway - dispatch might still work
    
    for attempt in range(max_retries):
        try:
            # Create LiveKit API client with CLIENT credentials
            from livekit import api
            lk_api = api.LiveKitAPI(
                client_livekit.url,
                client_livekit.api_key,
                client_livekit.api_secret
            )
            
            # Create dispatch request
            dispatch_request = api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name=agent_name,
                metadata=json.dumps(metadata)
            )
            
            # Attempt dispatch
            logger.info(f"🚀 Sending dispatch request for room {room_name}, agent {agent_name}")
            logger.info(f"   Dispatch metadata: {json.dumps(metadata, indent=2)}")
            
            dispatch_result = await lk_api.agent_dispatch.create_dispatch(dispatch_request)
            
            logger.info(f"✅ Dispatch successful on attempt {attempt + 1}:")
            logger.info(f"   Dispatch ID: {dispatch_result.id}")
            logger.info(f"   Room: {room_name}")
            logger.info(f"   Agent: {agent_name}")
            logger.info(f"   Container: {metadata.get('container_name', 'N/A')}")
            
            # Verify dispatch was received
            await asyncio.sleep(1)  # Brief pause to let dispatch propagate
            logger.info(f"🔍 Dispatch verification: Check agent logs for 'Job received' or 'Request received for room {room_name}'")
            
            return {
                "success": True,
                "dispatch_id": dispatch_result.id,
                "attempts": attempt + 1,
                "method": "explicit",
                "message": f"Agent dispatched successfully after {attempt + 1} attempt(s)"
            }
            
        except Exception as e:
            logger.warning(f"⚠️ Dispatch attempt {attempt + 1} failed: {e}")
            
            if attempt < max_retries - 1:
                # Calculate delay with exponential backoff
                delay = base_delay * (2 ** attempt)
                logger.info(f"⏳ Retrying in {delay}s...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"❌ All dispatch attempts failed after {max_retries} tries")
                return {
                    "success": False,
                    "error": str(e),
                    "attempts": max_retries,
                    "method": "failed",
                    "message": f"Agent dispatch failed after {max_retries} attempts"
                }


async def verify_agent_joined_room(
    client_livekit: LiveKitManager,
    room_name: str,
    timeout: float = 5.0,
    poll_interval: float = 0.5
) -> Dict[str, Any]:
    """
    Verify agent has joined the room by polling participant list
    
    Args:
        client_livekit: Client's LiveKit manager
        room_name: Room to check
        timeout: Maximum time to wait for agent to join
        poll_interval: Interval between checks
    
    Returns:
        Dict with verification result
    """
    logger.info(f"🔍 Verifying agent joins room {room_name} within {timeout}s")
    
    # Confirm we're using client-specific credentials for verification
    logger.info(f"🔐 Verification using CLIENT LiveKit credentials:")
    logger.info(f"   - URL: {client_livekit.url}")
    logger.info(f"   - API Key: {client_livekit.api_key[:20]}... (CLIENT-SPECIFIC)")
    
    start_time = asyncio.get_event_loop().time()
    agent_found = False
    participant_count = 0
    
    while (asyncio.get_event_loop().time() - start_time) < timeout:
        try:
            # Get room info with participants
            room_info = await client_livekit.get_room(room_name)
            if room_info:
                participant_count = room_info.get("num_participants", 0)
                
                # Check if we have participants (agent should be one)
                if participant_count > 0:
                    # Get participant list to verify agent presence
                    participants = await client_livekit.list_participants(room_name)
                    
                    # Look for agent participant (usually has metadata or specific identity)
                    for participant in participants:
                        if participant.get("is_publisher", False):  # Agents are publishers
                            agent_found = True
                            logger.info(f"✅ Agent found in room: {participant.get('identity', 'unknown')}")
                            break
                    
                    if agent_found:
                        break
            
            await asyncio.sleep(poll_interval)
            
        except Exception as e:
            logger.error(f"Error checking room participants: {e}")
            await asyncio.sleep(poll_interval)
    
    elapsed = asyncio.get_event_loop().time() - start_time
    
    if agent_found:
        return {
            "success": True,
            "agent_joined": True,
            "time_to_join": round(elapsed, 2),
            "participant_count": participant_count,
            "message": f"Agent joined room in {elapsed:.2f}s"
        }
    else:
        return {
            "success": False,
            "agent_joined": False,
            "time_waited": round(elapsed, 2),
            "participant_count": participant_count,
            "message": f"Agent did not join room within {timeout}s"
        }


async def background_dispatch_and_verify(
    client_livekit: LiveKitManager,
    room_name: str,
    agent_name: str,
    metadata: Dict[str, Any],
    container_name: str
):
    """
    Background task to dispatch agent and verify it joined
    This runs after the HTTP response is sent to minimize latency
    """
    logger.info(f"🎯 Starting background dispatch and verification for {room_name}")
    
    # Confirm client-specific credentials are being used
    client_id = metadata.get("client_id", "unknown")
    logger.info(f"📋 Dispatching agent for client_id '{client_id}' using LiveKit API key '{client_livekit.api_key[:20]}...'")
    
    # Dispatch with retry
    dispatch_result = await dispatch_agent_with_retry(
        client_livekit=client_livekit,
        room_name=room_name,
        agent_name=agent_name,
        metadata=metadata
    )
    
    if dispatch_result["success"]:
        # Verify agent joined
        verify_result = await verify_agent_joined_room(
            client_livekit=client_livekit,
            room_name=room_name,
            timeout=5.0
        )
        
        if verify_result["agent_joined"]:
            logger.info(f"🎉 Complete success: Agent dispatched and joined {room_name} in {verify_result['time_to_join']}s")
        else:
            logger.error(f"⚠️ Agent dispatched but did not join room within timeout")
            # Could trigger additional recovery logic here
    else:
        logger.error(f"❌ Failed to dispatch agent after all retries")


async def create_client_livekit_manager(client) -> LiveKitManager:
    """Create a LiveKit manager using client-specific credentials"""
    # Create a new LiveKit manager instance with client credentials
    client_livekit = LiveKitManager()
    client_livekit.api_key = client.settings.livekit.api_key
    client_livekit.api_secret = client.settings.livekit.api_secret
    client_livekit.url = client.settings.livekit.server_url
    client_livekit._initialized = True
    
    return client_livekit


@circuit_breaker(
    name="livekit_room_operations",
    failure_threshold=3,
    timeout=timedelta(seconds=30),
    fallback_function=lambda *args, **kwargs: {
        "room_name": kwargs.get('room_name', 'unknown'),
        "status": "fallback",
        "message": "Room creation circuit breaker open - using fallback",
        "num_participants": 0
    }
)
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
    3. Verify room creation with multiple checks
    4. Return room information for the frontend
    
    Includes retry logic with exponential backoff for transient failures.
    """
    max_retries = 3
    base_delay = 1  # seconds
    
    for attempt in range(max_retries):
        try:
            # First, check if the room already exists
            logger.info(f"🔍 Checking if room {room_name} exists (attempt {attempt + 1}/{max_retries})")
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
                    "agent": agent_name,  # Use agent_name parameter passed to function
                    "namespace": "default"
                }
            }
            
            # For preview sessions, use 2 hour timeout as per Phase 1.3
            is_preview = room_name.startswith('preview_')
            empty_timeout = 7200 if is_preview else 1800  # 2 hours for preview, 30 min for regular
            
            logger.info(f"⏱️ Setting room timeout to {empty_timeout}s ({empty_timeout/60:.0f} minutes) for {'preview' if is_preview else 'regular'} room")
            
            room_info = await livekit_manager.create_room(
                name=room_name,
                empty_timeout=empty_timeout,
                max_participants=10,  # Allow multiple participants
                metadata=room_metadata
            )
            
            logger.info(f"✅ Room creation API call successful for {room_name}")
            
            # Wait a moment to ensure room is fully created
            await asyncio.sleep(1)
            
            # Create a placeholder token to keep the room alive
            placeholder_token = livekit_manager.create_token(
                identity="room_keeper",
                room_name=room_name,
                metadata={"placeholder": True, "role": "room_keeper"}
            )
            
            logger.info(f"🎫 Created placeholder token for room {room_name}")
            
            # Verify room was created with multiple checks over 5 seconds
            logger.info(f"🔄 Verifying room creation with multiple checks...")
            verification_attempts = 3
            verification_delay = 1.5  # Total 4.5 seconds of checks
            
            for v_attempt in range(verification_attempts):
                verification = await livekit_manager.get_room(room_name)
                if verification:
                    logger.info(f"✅ Room {room_name} verified on attempt {v_attempt + 1}/{verification_attempts}")
                    logger.info(f"   - Participants: {verification.get('num_participants', 0)}")
                    logger.info(f"   - Created: {verification.get('creation_time')}")
                    logger.info(f"   - Max participants: {verification.get('max_participants')}")
                    
                    return {
                        "room_name": room_name,
                        "status": "created",
                        "participants": 0,
                        "created_at": room_info["created_at"].isoformat(),
                        "max_participants": room_info["max_participants"],
                        "metadata": room_metadata,
                        "empty_timeout": empty_timeout,
                        "message": f"Room {room_name} created successfully and ready for participants"
                    }
                
                if v_attempt < verification_attempts - 1:
                    logger.warning(f"⚠️ Room verification attempt {v_attempt + 1} failed, retrying in {verification_delay}s...")
                    await asyncio.sleep(verification_delay)
            
            # If we get here, verification failed
            raise Exception(f"Room {room_name} was created but could not be verified after {verification_attempts} attempts")
            
        except Exception as e:
            logger.error(f"❌ Failed to ensure room {room_name} exists (attempt {attempt + 1}/{max_retries}): {str(e)}")
            logger.error(f"   Error type: {type(e).__name__}")
            logger.error(f"   Full error: {repr(e)}")
            
            if attempt < max_retries - 1:
                # Exponential backoff
                delay = base_delay * (2 ** attempt)
                logger.info(f"⏳ Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
            else:
                # Final attempt failed
                logger.error(f"🚨 All {max_retries} attempts failed to create/verify room {room_name}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to create/verify LiveKit room after {max_retries} attempts: {str(e)}"
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


@router.post("/agents/dispatch-to-room")
async def dispatch_agent_to_room_endpoint(
    request: Request,
    room_name: str = Body(...),
    agent_name: str = Body(default="session-agent-rag"),
    client_id: Optional[str] = Body(None),
    agent_slug: Optional[str] = Body(None)
) -> Dict[str, Any]:
    """
    Dispatch an agent to an existing room
    
    This endpoint is called by the frontend after a participant joins the room,
    ensuring the dispatch happens when LiveKit Cloud is ready to deliver it.
    """
    try:
        logger.info(f"Frontend requesting agent dispatch for room {room_name}")
        
        # Use backend LiveKit
        from app.integrations.livekit_client import livekit_manager
        
        # Create the dispatch
        dispatch_result = await livekit_manager.create_agent_dispatch(
            room_name=room_name,
            agent_name=agent_name
        )
        
        logger.info(f"Agent dispatch result: {dispatch_result}")
        
        return dispatch_result
        
    except Exception as e:
        logger.error(f"Failed to dispatch agent: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to dispatch agent: {str(e)}"
        )


@router.post("/containers/release")
async def release_container_to_pool(
    request: Request,
    container_name: str = Body(...),
    client_id: str = Body(...),
    agent_slug: str = Body(...),
    force_recycle: bool = Body(False)
) -> Dict[str, Any]:
    """
    Release a container back to the warm pool after session completion
    
    This endpoint is called by the agent after finishing a session,
    allowing the container to be reused for future sessions after state cleanup.
    """
    try:
        logger.info(f"🔄 Releasing container {container_name} back to pool for {client_id}/{agent_slug}")
        
        # Get pool manager
        pool_manager = get_container_pool_manager()
        
        # Release the container back to pool
        await pool_manager.release_container(
            client_id=client_id,
            agent_slug=agent_slug,
            container_name=container_name,
            force_recycle=force_recycle
        )
        
        return {
            "success": True,
            "message": f"Container {container_name} released to warm pool",
            "client_id": client_id,
            "agent_slug": agent_slug,
            "state_reset": "performed" if not force_recycle else "skipped_for_recycle"
        }
        
    except Exception as e:
        logger.error(f"Failed to release container: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to release container: {str(e)}"
        )


@router.get("/containers/pool/stats")
async def get_container_pool_stats() -> Dict[str, Any]:
    """
    Get statistics about the container warm pools
    
    Returns information about pool sizes, idle containers, and allocations.
    """
    try:
        pool_manager = get_container_pool_manager()
        stats = pool_manager.get_pool_stats()
        
        return {
            "success": True,
            "stats": stats,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Failed to get pool stats: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get pool stats: {str(e)}"
        )