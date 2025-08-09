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
from supabase import create_client, Client as SupabaseClient

# --- Performance Logging Helper ---
def log_perf(event: str, room_name: str, details: Dict[str, Any]):
    log_entry = {
        "event": event,
        "room_name": room_name,
        "details": details
    }
    logger.info(f"PERF: {json.dumps(log_entry)}")

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
    # Build voice settings with NO DEFAULTS (enforce no-fallback policy)
    voice_settings = agent.voice_settings.dict() if agent.voice_settings else {}
    
    # Normalize provider fields (admin may store TTS as 'provider')
    normalized_llm = voice_settings.get("llm_provider")
    normalized_stt = voice_settings.get("stt_provider")
    normalized_tts = voice_settings.get("tts_provider") or voice_settings.get("provider")

    # Validate required providers are configured
    missing_vs = []
    if not normalized_llm:
        missing_vs.append("llm_provider")
    if not normalized_stt:
        missing_vs.append("stt_provider")
    if not normalized_tts:
        missing_vs.append("tts_provider")
    if missing_vs:
        raise HTTPException(status_code=400, detail=f"Missing voice settings: {', '.join(missing_vs)}")
        
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
        # Include embedding configuration from client's additional_settings
        "embedding": client.additional_settings.get("embedding", {}) if client.additional_settings else {},
        # Include client's Supabase credentials for context system
        "supabase_url": client.settings.supabase.url if client.settings and client.settings.supabase else None,
        "supabase_anon_key": client.settings.supabase.anon_key if client.settings and client.settings.supabase else None,
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
        metadata={"user_id": request.user_id, "client_id": client.id},
        dispatch_agent_name=None,  # Disable token dispatch
        dispatch_metadata=None
    )
    token_duration = time.time() - token_start
    logger.info(f"⏱️ User token generation took {token_duration:.2f}s")
    logger.debug(f"Generated user token", extra={'token_length': len(user_token)})
    
    # Validate API keys for selected providers before dispatch (fail fast)
    try:
        selected_llm = normalized_llm
        selected_stt = normalized_stt
        selected_tts = normalized_tts
        provider_to_key = {
            "openai": "openai_api_key",
            "groq": "groq_api_key",
            "deepgram": "deepgram_api_key",
            "elevenlabs": "elevenlabs_api_key",
            "cartesia": "cartesia_api_key",
        }
        api_keys_map = agent_context.get("api_keys", {})
        missing_keys = []
        for provider in (selected_llm, selected_stt, selected_tts):
            key_name = provider_to_key.get(provider)
            if key_name and not api_keys_map.get(key_name):
                missing_keys.append(f"{provider}:{key_name}")
        if missing_keys:
            raise HTTPException(status_code=400, detail=f"Missing API keys for providers: {', '.join(missing_keys)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Provider key validation failed: {e}")

    # EXPLICITLY DISPATCH THE AGENT
    # Remove participant check and always dispatch
    dispatch_start = time.time()
    dispatch_info = await dispatch_agent_job(
        livekit_manager=backend_livekit,
        room_name=request.room_name,
        agent=agent,
        client=client,
        user_id=request.user_id
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
    logger.info(f"🎯 Room {request.room_name} ready; agent dispatched explicitly via API")
    
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


async def _store_conversation_turn(
    supabase_client: SupabaseClient,
    user_id: str,
    agent_id: str,
    conversation_id: str,
    user_message: str,
    agent_response: str,
    session_id: Optional[str] = None,
    context_manager=None  # Optional context manager for embeddings
) -> None:
    """
    Store a conversation turn immediately in the client's Supabase.
    This implements transactional turn-based storage for unified voice/text conversations.
    """
    try:
        # Get current timestamp for both messages
        ts = datetime.utcnow().isoformat()
        
        # Store user message row
        user_row = {
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "session_id": session_id,
            "role": "user",
            "content": user_message,
            "transcript": user_message,  # Required field, same as content for text
            "created_at": ts
        }
        user_result = supabase_client.table("conversation_transcripts").insert(user_row).execute()
        
        # Store assistant message row
        assistant_row = {
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "session_id": session_id,
            "role": "assistant",
            "content": agent_response,
            "transcript": agent_response,  # Required field, same as content for text
            "created_at": ts
        }
        assistant_result = supabase_client.table("conversation_transcripts").insert(assistant_row).execute()
        
        logger.info(f"✅ Stored conversation turn: conversation_id={conversation_id} (2 rows: user + assistant)")
        
        # Best-effort embedding generation
        if context_manager and hasattr(context_manager, 'embedder'):
            try:
                # Get row IDs from insert results
                user_row_id = user_result.data[0]['id'] if user_result.data else None
                assistant_row_id = assistant_result.data[0]['id'] if assistant_result.data else None
                
                # List of trivial messages to skip
                trivial_messages = {'hey', 'hi', 'hello', 'ok', 'okay', 'thanks', 'thank you', 'bye', 'goodbye'}
                
                # Generate and update user message embedding
                if user_row_id and len(user_message) >= 8 and user_message.lower() not in trivial_messages:
                    try:
                        user_embedding = await context_manager.embedder.create_embedding(user_message)
                        supabase_client.table("conversation_transcripts").update({
                            "embeddings": user_embedding
                        }).eq("id", user_row_id).execute()
                        logger.info(f"✅ Generated embedding for user message (id={user_row_id})")
                    except Exception as embed_error:
                        logger.warning(f"Failed to generate user message embedding: {embed_error}")
                
                # Generate and update assistant response embedding
                if assistant_row_id and len(agent_response) >= 8 and agent_response.lower() not in trivial_messages:
                    try:
                        assistant_embedding = await context_manager.embedder.create_embedding(agent_response)
                        supabase_client.table("conversation_transcripts").update({
                            "embeddings": assistant_embedding
                        }).eq("id", assistant_row_id).execute()
                        logger.info(f"✅ Generated embedding for assistant response (id={assistant_row_id})")
                    except Exception as embed_error:
                        logger.warning(f"Failed to generate assistant response embedding: {embed_error}")
                        
            except Exception as e:
                logger.warning(f"Failed during embedding generation process: {e}")
                # Continue - embedding failure shouldn't break the conversation
        
    except Exception as e:
        logger.error(f"❌ Failed to store conversation turn: {e}")
        logger.error(f"User message: {user_message[:100]}...")
        logger.error(f"Assistant response: {agent_response[:100]}...")


async def handle_text_trigger(
    request: TriggerAgentRequest, 
    agent, 
    client
) -> Dict[str, Any]:
    """
    Handle text mode agent triggering with full RAG support
    
    This processes text messages through the agent using the same
    context-aware system as voice conversations.
    """
    logger.info(f"🚀 Starting RAG-powered text trigger for agent {agent.slug}")
    logger.info(f"💬 User message: {request.message[:100]}...")
    
    # Initialize variables
    response_text = None
    user_id = request.user_id or "anonymous"
    conversation_id = request.conversation_id or f"text_{request.session_id or uuid.uuid4().hex}"
    
    # Get LLM provider and model from agent voice settings - NO DEFAULTS
    if not agent.voice_settings or not agent.voice_settings.llm_provider:
        raise ValueError("Agent does not have voice settings configured with an LLM provider")
    
    llm_provider = agent.voice_settings.llm_provider
    llm_model = agent.voice_settings.llm_model
    
    # Log the agent's voice settings for debugging
    logger.info(f"Agent voice_settings: {agent.voice_settings}")
    logger.info(f"Using LLM provider: {llm_provider}, model: {llm_model}")
    
    # Prepare metadata for context
    # Check for embedding config in both locations (additional_settings first, then settings)
    embedding_cfg = {}
    if client.additional_settings and client.additional_settings.get("embedding"):
        embedding_cfg = client.additional_settings.get("embedding", {})
    elif client.settings and hasattr(client.settings, 'embedding') and client.settings.embedding:
        embedding_cfg = client.settings.embedding.dict()
    
    metadata = {
        "agent_slug": agent.slug,
        "agent_name": agent.name,
        "agent_id": agent.id,
        "system_prompt": agent.system_prompt,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "client_id": client.id,
        "voice_settings": agent.voice_settings.dict() if agent.voice_settings else {},
        "embedding": embedding_cfg
    }
    
    # Get API keys from client configuration
    api_keys = {}
    if client.settings and client.settings.api_keys:
        api_keys = {
            "openai_api_key": client.settings.api_keys.openai_api_key,
            "groq_api_key": client.settings.api_keys.groq_api_key,
            "deepgram_api_key": client.settings.api_keys.deepgram_api_key,
            "elevenlabs_api_key": client.settings.api_keys.elevenlabs_api_key,
            "cartesia_api_key": client.settings.api_keys.cartesia_api_key,
            "anthropic_api_key": getattr(client.settings.api_keys, 'anthropic_api_key', None),
            "novita_api_key": client.settings.api_keys.novita_api_key,
            "cohere_api_key": client.settings.api_keys.cohere_api_key,
            "siliconflow_api_key": client.settings.api_keys.siliconflow_api_key,
            "jina_api_key": client.settings.api_keys.jina_api_key,
        }
    
    try:
        # Initialize context manager if we have Supabase credentials
        context_manager = None
        client_supabase = None
        
        if client.settings and client.settings.supabase:
            logger.info("🔍 Initializing context manager for RAG...")
            from supabase import create_client
            
            # Create Supabase client for the client's database
            # Use service_role_key for server-side operations to bypass RLS
            client_supabase = create_client(
                client.settings.supabase.url,
                client.settings.supabase.service_role_key or client.settings.supabase.anon_key
            )
            
            # Import the context manager from agent_modules
            from app.agent_modules.context import AgentContextManager
            from app.agent_modules.llm_wrapper import ContextAwareLLM
            
            # Create context manager
            context_manager = AgentContextManager(
                supabase_client=client_supabase,
                agent_config=metadata,
                user_id=user_id,
                client_id=client.id,
                api_keys=api_keys
            )
            logger.info("✅ Context manager initialized")
        
        # Configure LLM based on provider
        from livekit.plugins import openai as lk_openai, groq as lk_groq
        from livekit.agents import llm as lk_llm
        
        llm_plugin = None
        if llm_provider == "groq":
            groq_key = api_keys.get("groq_api_key")
            if groq_key and groq_key not in ["test_key", "test", "dummy"]:
                # Map old model names to new ones
                if llm_model == "llama3-70b-8192" or llm_model == "llama-3.1-70b-versatile":
                    llm_model = "llama-3.3-70b-versatile"
                llm_plugin = lk_groq.LLM(
                    model=llm_model or "llama-3.3-70b-versatile",
                    api_key=groq_key
                )
                logger.info(f"✅ Initialized Groq LLM with model: {llm_model}")
        else:  # openai
            openai_key = api_keys.get("openai_api_key")
            if openai_key and openai_key not in ["test_key", "test", "dummy"]:
                llm_plugin = lk_openai.LLM(
                    model=llm_model or "gpt-4",
                    api_key=openai_key
                )
                logger.info(f"✅ Initialized OpenAI LLM with model: {llm_model}")
        
        if not llm_plugin:
            raise ValueError(f"No valid API key for {llm_provider}")
        
        # Wrap LLM with context awareness if we have context manager
        if context_manager:
            logger.info("🧠 Wrapping LLM with RAG context...")
            context_aware_llm = ContextAwareLLM(
                base_llm=llm_plugin,
                context_manager=context_manager,
                user_id=user_id
            )
            
            # Build complete context including conversation history and documents
            logger.info("🔍 Building complete context with RAG...")
            context_result = await context_manager.build_complete_context(
                user_message=request.message,
                user_id=user_id
            )
            enhanced_prompt = context_result.get("enhanced_system_prompt", agent.system_prompt)
            
            # Create chat context using the SDK's abstraction
            ctx = lk_llm.ChatContext()
            ctx.add_message(role="system", content=enhanced_prompt)
            
            # Short-term buffer memory: fetch last 20 messages from this conversation
            recent_rows = []
            try:
                recent_q = (
                    client_supabase
                    .table("conversation_transcripts")
                    .select("role,content")
                    .eq("conversation_id", conversation_id)
                    .order("created_at", desc=True)
                    .limit(20)
                    .execute()
                )
                # Reverse so oldest messages come first
                recent_rows = list(reversed(recent_q.data or []))
                if recent_rows:
                    logger.info(f"📚 Loaded {len(recent_rows)} recent messages for buffer memory")
            except Exception as e:
                logger.warning(f"Couldn't load recent turns for buffer memory: {e}")
            
            # Inject recent history into context
            for row in recent_rows:
                role = row["role"] if row["role"] in ("user", "assistant") else "user"
                text = row["content"] or ""
                ctx.add_message(role=role, content=text)
            
            # Finally add the new user message
            ctx.add_message(role="user", content=request.message)
            
            # Get response using LLM directly (wrapper has async issues)
            logger.info("🤖 Generating RAG-enhanced response...")
            stream = llm_plugin.chat(chat_ctx=ctx)
            
            # Collect the stream response
            response_text = ""
            async for chunk in stream:
                # Handle different chunk formats from LiveKit SDK
                if hasattr(chunk, 'choices') and chunk.choices:
                    for choice in chunk.choices:
                        if hasattr(choice, 'delta') and choice.delta and hasattr(choice.delta, 'content') and choice.delta.content:
                            response_text += choice.delta.content
                elif hasattr(chunk, 'delta') and chunk.delta and hasattr(chunk.delta, 'content') and chunk.delta.content:
                    response_text += chunk.delta.content
                elif hasattr(chunk, 'content') and chunk.content:
                    response_text += chunk.content
                
            logger.info(f"✅ Generated response: {response_text[:100]}...")
            
        else:
            # Fallback to basic LLM without RAG (no context manager)
            logger.warning("⚠️ No context manager available, using basic LLM")
            # Use ChatContext for consistency
            ctx = lk_llm.ChatContext()
            ctx.add_message(role="system", content=agent.system_prompt)
            
            # Short-term buffer memory even without RAG
            if client_supabase:
                recent_rows = []
                try:
                    recent_q = (
                        client_supabase
                        .table("conversation_transcripts")
                        .select("role,content")
                        .eq("conversation_id", conversation_id)
                        .order("created_at", desc=True)
                        .limit(20)
                        .execute()
                    )
                    # Reverse so oldest messages come first
                    recent_rows = list(reversed(recent_q.data or []))
                    if recent_rows:
                        logger.info(f"📚 Loaded {len(recent_rows)} recent messages for buffer memory (non-RAG)")
                except Exception as e:
                    logger.warning(f"Couldn't load recent turns for buffer memory: {e}")
                
                # Inject recent history into context
                for row in recent_rows:
                    role = row["role"] if row["role"] in ("user", "assistant") else "user"
                    text = row["content"] or ""
                    ctx.add_message(role=role, content=text)
            
            # Finally add the new user message
            ctx.add_message(role="user", content=request.message)
            stream = llm_plugin.chat(chat_ctx=ctx)
            
            # Collect the stream response
            response_text = ""
            async for chunk in stream:
                # Handle different chunk formats from LiveKit SDK
                if hasattr(chunk, 'choices') and chunk.choices:
                    for choice in chunk.choices:
                        if hasattr(choice, 'delta') and choice.delta and hasattr(choice.delta, 'content') and choice.delta.content:
                            response_text += choice.delta.content
                elif hasattr(chunk, 'delta') and chunk.delta and hasattr(chunk.delta, 'content') and chunk.delta.content:
                    response_text += chunk.delta.content
                elif hasattr(chunk, 'content') and chunk.content:
                    response_text += chunk.content
        
        # Store conversation turn if we have a response and Supabase client
        if response_text and client_supabase:
            try:
                await _store_conversation_turn(
                    supabase_client=client_supabase,
                    user_id=user_id,
                    agent_id=agent.id,
                    conversation_id=conversation_id,
                    user_message=request.message,
                    agent_response=response_text,
                    session_id=request.session_id,
                    context_manager=context_manager  # Pass context manager for embeddings
                )
                logger.info(f"✅ Text conversation turn stored for conversation_id={conversation_id}")
            except Exception as e:
                logger.error(f"❌ Failed to store text conversation turn: {e}")
                # Continue - storage failure shouldn't break the response
                
    except Exception as e:
        logger.error(f"❌ Error in RAG text processing: {e}", exc_info=True)
        response_text = f"I apologize, but I encountered an error processing your message. Please try again."
    
    # Prepare response
    return {
        "mode": "text",
        "message_received": request.message,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "rag_enabled": bool(context_manager),
        "status": "text_message_processed",
        "response": response_text or f"I'm sorry, I couldn't process your message. Please ensure the {llm_provider} API key is configured.",
        "agent_response": response_text
    }


async def dispatch_agent_job(
    livekit_manager: LiveKitManager,
    room_name: str,
    agent,
    client,
    user_id: Optional[str] = None
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
        
        # Providers are validated in handle_voice_trigger; do not apply defaults here (no-fallback policy)
            
        # Debug logging to understand client structure
        logger.info(f"Client settings available: {bool(client.settings)}")
        if client.settings:
            logger.info(f"Client settings has supabase: {bool(client.settings.supabase)}")
            if client.settings.supabase:
                logger.info(f"Supabase URL: {client.settings.supabase.url[:50]}..." if client.settings.supabase.url else "No URL")
                logger.info(f"Supabase anon_key exists: {bool(client.settings.supabase.anon_key)}")
        
        job_metadata = {
            "client_id": client.id,
            "agent_slug": agent.slug,
            "agent_name": agent.name,
            "system_prompt": agent.system_prompt,
            "voice_settings": voice_settings,
            "webhooks": agent.webhooks.dict() if agent.webhooks else {},
            # Include client's Supabase credentials for context system
            "supabase_url": client.settings.supabase.url if client.settings and client.settings.supabase else None,
            "supabase_anon_key": client.settings.supabase.anon_key if client.settings and client.settings.supabase else None,
            # Include user_id if provided
            "user_id": user_id,
            # Include embedding configuration from client's additional_settings
            "embedding": client.additional_settings.get("embedding", {}) if client.additional_settings else {},
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
        # No fallback allowed: fail fast with clear error
        raise HTTPException(status_code=502, detail=f"Explicit dispatch failed: {type(e).__name__}: {e}")




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
            metadata=metadata_json,
            enable_agent_dispatch=False  # Disable automatic dispatch; rely on explicit API dispatch
        )
        create_duration = time.time() - create_start
        logger.info(f"⏱️ Room creation took {create_duration:.2f}s")
        
        logger.info(f"✅ Created room {room_name} successfully")
        
        # Quick wait to ensure room is fully created
        wait_start = time.time()
        await asyncio.sleep(0.2)  # Reduced from 1s to 0.2s
        wait_duration = time.time() - wait_start
        logger.info(f"⏱️ Room ready wait took {wait_duration:.2f}s")
        
        # No placeholder token required; room lifetime is managed by empty_timeout
        
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