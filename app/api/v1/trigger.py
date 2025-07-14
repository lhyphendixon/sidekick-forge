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

from app.services.agent_service import AgentService
from app.services.client_service_hybrid import ClientService
from app.core.dependencies import get_redis_client
import redis
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


def get_client_service(redis_client: redis.Redis = Depends(get_redis_client)) -> ClientService:
    """Get client service instance"""
    import os
    master_supabase_url = os.getenv("MASTER_SUPABASE_URL", "https://xyzxyzxyzxyzxyzxyz.supabase.co")
    master_supabase_key = os.getenv("MASTER_SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inh5enh5enh5enh5enh5enh5eiIsInJvbGUiOiJzZXJ2aWNlX3JvbGUiLCJpYXQiOjE2NDYyMzkwMjIsImV4cCI6MTk2MTgxNTAyMn0.dummy-key-for-testing")
    return ClientService(master_supabase_url, master_supabase_key, redis_client)


def get_agent_service(
    redis_client: redis.Redis = Depends(get_redis_client),
    client_service: ClientService = Depends(get_client_service)
) -> AgentService:
    """Get agent service instance"""
    return AgentService(client_service, redis_client)


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
                if agent.slug == request.agent_slug:
                    client_id = agent.client_id
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


async def handle_voice_trigger(
    request: TriggerAgentRequest, 
    agent, 
    client
) -> Dict[str, Any]:
    """
    Handle voice mode agent triggering
    
    This should trigger a Python LiveKit agent to join the specified room
    """
    logger.info(f"Handling voice trigger for agent {agent.slug} in room {request.room_name}")
    
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
    
    # LiveKit configuration from client
    livekit_config = {
        "server_url": client.settings.livekit.server_url if client.settings and client.settings.livekit else None,
        "api_key": client.settings.livekit.api_key if client.settings and client.settings.livekit else None,
        "api_secret": client.settings.livekit.api_secret if client.settings and client.settings.livekit else None
    }
    
    # Trigger actual LiveKit agent container
    container_result = await spawn_agent_container(
        agent=agent,
        client=client,
        room_name=request.room_name,
        user_id=request.user_id,
        session_id=request.session_id,
        conversation_id=request.conversation_id,
        context=request.context
    )
    
    return {
        "mode": "voice",
        "room_name": request.room_name,
        "platform": request.platform,
        "agent_context": agent_context,
        "livekit_config": {
            "server_url": livekit_config["server_url"],
            "configured": bool(livekit_config["server_url"] and livekit_config["api_key"])
        },
        "container_info": container_result,
        "status": "voice_agent_triggered",
        "message": f"LiveKit agent {agent.slug} has been triggered to join room {request.room_name}"
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
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Spawn a LiveKit agent container to join the specified room
    
    This function creates the necessary environment and starts the agent process
    that will join the LiveKit room and handle voice/text interactions.
    """
    logger.info(f"Spawning agent container for {agent.slug} in room {room_name}")
    
    try:
        # Generate unique container/process ID
        container_id = f"agent-{agent.slug}-{uuid.uuid4().hex[:8]}"
        
        # Prepare environment variables for the agent process
        agent_env = {
            # Base environment
            **os.environ,
            
            # LiveKit configuration
            "LIVEKIT_URL": client.settings.livekit.server_url,
            "LIVEKIT_API_KEY": client.settings.livekit.api_key,
            "LIVEKIT_API_SECRET": client.settings.livekit.api_secret,
            
            # Supabase configuration
            "SUPABASE_URL": client.settings.supabase.url,
            "SUPABASE_ANON_KEY": client.settings.supabase.anon_key,
            "SUPABASE_SERVICE_ROLE_KEY": client.settings.supabase.service_role_key,
            
            # Agent configuration
            "AUTONOMITE_AGENT_LABEL": agent.slug,
            
            # API Keys from client settings
            "GROQ_API_KEY": getattr(client.settings.api_keys, 'groq_api_key', None) or "",
            "OPENAI_API_KEY": getattr(client.settings.api_keys, 'openai_api_key', None) or "",
            "DEEPGRAM_API_KEY": getattr(client.settings.api_keys, 'deepgram_api_key', None) or "",
            "ELEVENLABS_API_KEY": getattr(client.settings.api_keys, 'elevenlabs_api_key', None) or "",
            "CARTESIA_API_KEY": getattr(client.settings.api_keys, 'cartesia_api_key', None) or "",
            "SPEECHIFY_API_KEY": getattr(client.settings.api_keys, 'speechify_api_key', None) or "",
            "DEEPINFRA_API_KEY": getattr(client.settings.api_keys, 'deepinfra_api_key', None) or "",
            "REPLICATE_API_KEY": getattr(client.settings.api_keys, 'replicate_api_key', None) or "",
            "COHERE_API_KEY": getattr(client.settings.api_keys, 'cohere_api_key', None) or "",
            "NOVITA_API_KEY": getattr(client.settings.api_keys, 'novita_api_key', None) or "",
            "SILICONFLOW_API_KEY": getattr(client.settings.api_keys, 'siliconflow_api_key', None) or "",
            "JINA_API_KEY": getattr(client.settings.api_keys, 'jina_api_key', None) or "",
        }
        
        # Prepare job metadata for the agent
        job_metadata = {
            "user_id": user_id,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "agent_slug": agent.slug,
            "client_id": client.id,
            "context": context or {}
        }
        
        # Path to the agent script and virtual environment
        agent_dir = "/root/wordpress-plugin/autonomite-agent/livekit-agents"
        python_path = f"{agent_dir}/agent_env/bin/python"
        agent_script_path = f"{agent_dir}/autonomite_agent_v1_1_19_text_support.py"
        
        # Create metadata file for the job
        metadata_file = f"/tmp/job_metadata_{container_id}.json"
        with open(metadata_file, 'w') as f:
            json.dump(job_metadata, f)
        
        # Prepare the command to run the agent
        # LiveKit agents CLI expects specific format
        cmd = [
            python_path,
            agent_script_path,
            "dev"  # Run in dev mode which accepts room connections
        ]
        
        logger.info(f"Starting agent process: {' '.join(cmd[:3])} [with room and metadata]")
        
        # Start the agent process asynchronously
        process = await asyncio.create_subprocess_exec(
            *cmd,
            env=agent_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=agent_dir
        )
        
        # Wait a short time to see if the process starts successfully
        try:
            await asyncio.wait_for(asyncio.sleep(2), timeout=3)
            
            # Check if process is still running
            if process.returncode is None:
                logger.info(f"✅ Agent process started successfully with PID: {process.pid}")
                return {
                    "container_id": container_id,
                    "process_id": process.pid,
                    "status": "started",
                    "room_name": room_name,
                    "agent_slug": agent.slug,
                    "message": f"Agent container {container_id} started successfully"
                }
            else:
                # Process exited early - get error info
                stdout, stderr = await process.communicate()
                logger.error(f"❌ Agent process exited early with code {process.returncode}")
                logger.error(f"STDOUT: {stdout.decode()}")
                logger.error(f"STDERR: {stderr.decode()}")
                
                return {
                    "container_id": container_id,
                    "status": "failed",
                    "error": f"Process exited with code {process.returncode}",
                    "stderr": stderr.decode() if stderr else None,
                    "message": f"Agent container {container_id} failed to start"
                }
                
        except asyncio.TimeoutError:
            # Process is likely still starting up
            logger.info(f"⏱️ Agent process starting (PID: {process.pid})")
            return {
                "container_id": container_id,
                "process_id": process.pid,
                "status": "starting",
                "room_name": room_name,
                "agent_slug": agent.slug,
                "message": f"Agent container {container_id} is starting up"
            }
            
    except Exception as e:
        logger.error(f"❌ Failed to spawn agent container: {str(e)}")
        return {
            "container_id": None,
            "status": "error",
            "error": str(e),
            "message": f"Failed to spawn agent container: {str(e)}"
        }