"""LiveKit proxy endpoints for WordPress integration"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
import logging
import os
import httpx
import jwt
import time

from app.models.wordpress_site import WordPressSite
from app.api.v1.wordpress_sites import validate_wordpress_auth
from app.services.client_service_supabase_enhanced import ClientService
from app.services.agent_service_supabase import AgentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/livekit", tags=["livekit-proxy"])


class RoomCreateRequest(BaseModel):
    """Request to create a LiveKit room"""
    room_name: str
    empty_timeout: Optional[int] = 300  # 5 minutes default
    max_participants: Optional[int] = 10
    metadata: Optional[Dict[str, Any]] = None


class RoomTokenRequest(BaseModel):
    """Request to generate a room token"""
    room_name: str
    participant_name: str
    participant_identity: str
    participant_metadata: Optional[Dict[str, Any]] = None
    can_publish: bool = True
    can_subscribe: bool = True
    can_publish_data: bool = True


class RoomResponse(BaseModel):
    """LiveKit room response"""
    room_name: str
    room_id: str
    token: str
    url: str
    metadata: Optional[Dict[str, Any]] = None


# These will be injected from simple_main.py
client_service: Optional[ClientService] = None
agent_service: Optional[AgentService] = None


def get_client_service() -> ClientService:
    """Get client service instance"""
    if client_service is None:
        raise RuntimeError("Client service not initialized")
    return client_service


def get_agent_service() -> AgentService:
    """Get agent service instance"""  
    if agent_service is None:
        raise RuntimeError("Agent service not initialized")
    return agent_service


@router.post("/rooms/create", response_model=RoomResponse)
async def create_room(
    request: RoomCreateRequest,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> RoomResponse:
    """Create a LiveKit room for WordPress site"""
    logger.info(f"Creating LiveKit room: {request.room_name}")
    try:
        # Validate WordPress auth
        from app.api.v1.wordpress_sites import wordpress_service as wp_service
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get client settings
        service = get_client_service()
        client = await service.get_client(site.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
            
        # Get LiveKit credentials from client
        livekit_url = client.settings.livekit.server_url
        api_key = client.settings.livekit.api_key
        api_secret = client.settings.livekit.api_secret
        
        if not all([livekit_url, api_key, api_secret]):
            raise HTTPException(status_code=500, detail="LiveKit not configured for this client")
            
        # Create room metadata
        room_metadata = {
            "wordpress_site_id": site.id,
            "wordpress_domain": site.domain,
            "client_id": site.client_id,
            **(request.metadata or {})
        }
        
        # Generate participant token
        token = generate_token(
            api_key=api_key,
            api_secret=api_secret,
            room_name=request.room_name,
            participant_identity=f"wp_site_{site.id}",
            participant_name=site.site_name,
            can_publish=True,
            can_subscribe=True,
            metadata={"site_id": site.id, "domain": site.domain}
        )
        
        response = RoomResponse(
            room_name=request.room_name,
            room_id=request.room_name,  # LiveKit uses room_name as ID
            token=token,
            url=livekit_url,
            metadata=room_metadata
        )
        
        logger.info(f"Successfully created room {request.room_name} for site {site.domain}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating room: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rooms/token", response_model=Dict[str, str])
async def generate_room_token(
    request: RoomTokenRequest,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> Dict[str, str]:
    """Generate a token for a participant to join a room"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get client settings
        service = get_client_service()
        client = await service.get_client(site.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
            
        # Get LiveKit credentials
        api_key = client.settings.livekit.api_key
        api_secret = client.settings.livekit.api_secret
        
        if not all([api_key, api_secret]):
            raise HTTPException(status_code=500, detail="LiveKit not configured")
            
        # Generate token
        token = generate_token(
            api_key=api_key,
            api_secret=api_secret,
            room_name=request.room_name,
            participant_identity=request.participant_identity,
            participant_name=request.participant_name,
            can_publish=request.can_publish,
            can_subscribe=request.can_subscribe,
            can_publish_data=request.can_publish_data,
            metadata=request.participant_metadata
        )
        
        return {
            "token": token,
            "url": client.settings.livekit.server_url
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating token: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rooms/{room_name}/trigger-agent")
async def trigger_agent_for_room(
    room_name: str,
    agent_slug: str,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """Trigger an agent to join a LiveKit room"""
    logger.info(f"Triggering agent {agent_slug} for room {room_name}")
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get agent
        agent_service = get_agent_service()
        agents = await agent_service.get_client_agents(site.client_id)
        agent = next((a for a in agents if a.slug == agent_slug), None)
        
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_slug}' not found")
            
        # Get client
        client_service = get_client_service()
        client = await client_service.get_client(site.client_id)
        
        # Trigger the agent via the trigger endpoint with retry
        # This avoids circular imports and uses the proper API flow
        from app.utils import RetryableHTTPClient, RetryConfig
        
        trigger_url = "http://localhost:8000/trigger-agent"
        retry_config = RetryConfig(max_attempts=3, initial_delay=0.5)
        
        async with RetryableHTTPClient(retry_config=retry_config) as client:
            response = await client.post(
                trigger_url,
                json={
                    "agent_slug": agent_slug,
                    "room_name": room_name,
                    "user_id": f"wp_site_{site.id}",
                    "client_id": site.client_id,
                    "mode": "voice",
                    "platform": "livekit",
                    "context": {
                        "wordpress_site_id": site.id,
                        "wordpress_domain": site.domain,
                        "triggered_via": "wordpress_proxy"
                    }
                }
            )
            
        result = response.json()
        logger.info(f"Successfully triggered agent {agent_slug} for room {room_name}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def generate_token(
    api_key: str,
    api_secret: str,
    room_name: str,
    participant_identity: str,
    participant_name: str,
    can_publish: bool = True,
    can_subscribe: bool = True,
    can_publish_data: bool = True,
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """Generate a LiveKit access token"""
    # Token claims
    claims = {
        "exp": int(time.time()) + 86400,  # 24 hour expiry
        "iss": api_key,
        "nbf": int(time.time()) - 60,  # Not before: 1 minute ago
        "sub": participant_identity,
        "name": participant_name,
        "video": {
            "room": room_name,
            "roomJoin": True,
            "canPublish": can_publish,
            "canSubscribe": can_subscribe,
            "canPublishData": can_publish_data
        }
    }
    
    if metadata:
        claims["metadata"] = str(metadata)
        
    # Generate JWT
    token = jwt.encode(claims, api_secret, algorithm="HS256")
    return token