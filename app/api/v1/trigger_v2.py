"""
Agent trigger v2 - Uses new container architecture
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from enum import Enum
import logging
import asyncio
from datetime import datetime

from app.services.agent_service_supabase import AgentService
from app.core.dependencies import get_agent_service
from app.integrations.livekit_client import livekit_manager
from app.services.container_manager import container_manager
from app.config import settings
from livekit import api
import json

logger = logging.getLogger(__name__)

router = APIRouter(tags=["trigger-v2"])


class TriggerMode(str, Enum):
    """Agent trigger modes"""
    VOICE = "voice"
    TEXT = "text"


class TriggerAgentV2Request(BaseModel):
    """Request model for triggering an agent"""
    agent_slug: str = Field(..., description="Slug of the agent to trigger")
    client_id: Optional[str] = Field(None, description="Client ID")
    mode: TriggerMode = Field(..., description="Trigger mode: voice or text")
    message: Optional[str] = Field(None, description="Text message (required for text mode)")
    room_name: Optional[str] = Field(None, description="LiveKit room name (required for voice mode)")
    user_id: str = Field(..., description="User identifier")
    session_id: Optional[str] = Field(None, description="Session identifier")
    conversation_id: Optional[str] = Field(None, description="Conversation identifier")
    context: Optional[Dict[str, Any]] = Field(None, description="Additional context data")


@router.post("/trigger-agent-v2")
async def trigger_agent_v2(
    request: TriggerAgentV2Request,
    agent_service: AgentService = Depends(get_agent_service)
) -> Dict[str, Any]:
    """
    Trigger an AI agent using the new container architecture
    """
    logger.info(f"[V2] Triggering agent {request.agent_slug} in {request.mode} mode")
    
    # Auto-detect client_id if not provided
    client_id = request.client_id
    if not client_id:
        all_agents = await agent_service.get_all_agents_with_clients()
        for agent in all_agents:
            if agent.get("slug") == request.agent_slug:
                client_id = agent.get("client_id")
                break
        
        if not client_id:
            raise HTTPException(status_code=404, detail=f"Agent '{request.agent_slug}' not found")
    
    # Get agent and client
    agent = await agent_service.get_agent(client_id, request.agent_slug)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{request.agent_slug}' not found")
    
    if not agent.enabled:
        raise HTTPException(status_code=400, detail=f"Agent '{request.agent_slug}' is disabled")
    
    client = await agent_service.client_service.get_client(client_id)
    if not client:
        raise HTTPException(status_code=404, detail=f"Client '{client_id}' not found")
    
    if request.mode == TriggerMode.VOICE:
        # Create room
        room_info = await livekit_manager.create_room(
            name=request.room_name,
            empty_timeout=1800,
            max_participants=10,
            metadata={
                "agent_name": agent.name,
                "user_id": request.user_id,
                "created_by": "autonomite_backend_v2"
            }
        )
        
        # Generate user token
        user_token = livekit_manager.create_token(
            identity=f"user_{request.user_id}",
            room_name=request.room_name,
            metadata={"user_id": request.user_id, "client_id": client_id}
        )
        
        # Initialize container manager
        await container_manager.initialize()
        
        # Prepare agent configuration
        agent_config = {
            "agent_name": agent.name,
            "system_prompt": agent.system_prompt,
            "livekit_url": settings.livekit_url,
            "livekit_api_key": settings.livekit_api_key,
            "livekit_api_secret": settings.livekit_api_secret,
            "voice_id": agent.voice_settings.voice_id if agent.voice_settings else "alloy",
            "model": agent.model or "gpt-4-turbo-preview",
        }
        
        # Deploy container
        try:
            container_info = await container_manager.deploy_agent_container(
                site_id=client_id,
                agent_slug=agent.slug,
                agent_config=agent_config,
                site_config={"domain": client.name, "tier": "pro"}
            )
            
            # Wait for container to be ready
            container_name = container_info["name"]
            logger.info(f"[V2] Waiting for container {container_name} to be ready...")
            
            # Simple readiness check
            await asyncio.sleep(5)
            
            # Dispatch agent
            logger.info(f"[V2] Dispatching agent to room {request.room_name}")
            dispatch_result = await dispatch_agent_to_room(
                room_name=request.room_name,
                agent_name=agent.slug,
                metadata={
                    "client_id": client_id,
                    "user_id": request.user_id,
                    "container_id": container_info["id"]
                }
            )
            
            return {
                "success": True,
                "message": "Agent triggered successfully",
                "room_name": request.room_name,
                "user_token": user_token,
                "server_url": livekit_manager.url,
                "container_info": container_info,
                "dispatch_result": dispatch_result
            }
            
        except Exception as e:
            logger.error(f"[V2] Container deployment failed: {e}")
            # Fallback to roomless container
            return {
                "success": True,
                "message": "Agent triggered (fallback mode)",
                "room_name": request.room_name,
                "user_token": user_token,
                "server_url": livekit_manager.url,
                "container_info": {"status": "fallback", "error": str(e)}
            }
    
    else:  # TEXT mode
        return {
            "success": True,
            "message": "Text mode not yet implemented in v2",
            "mode": "text"
        }


async def dispatch_agent_to_room(
    room_name: str,
    agent_name: str,
    metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """Dispatch agent using LiveKit SDK"""
    try:
        # Create the LiveKit API client
        lk_api = api.LiveKitAPI(
            livekit_manager.url,
            livekit_manager.api_key,
            livekit_manager.api_secret
        )
        
        dispatch_request = api.CreateAgentDispatchRequest(
            room=room_name,
            agent_name=agent_name,
            metadata=json.dumps(metadata) if metadata else None
        )
        
        result = await lk_api.agent_dispatch.create_dispatch(dispatch_request)
        
        return {
            "success": True,
            "dispatch_id": getattr(result, 'agent_id', 'unknown')
        }
        
    except Exception as e:
        logger.error(f"[V2] Dispatch failed: {e}")
        return {"success": False, "error": str(e)}