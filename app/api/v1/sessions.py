from fastapi import APIRouter, HTTPException, status, Depends
from datetime import datetime, timedelta
import json

from app.models.session import (
    SessionRequest, SessionResponse, RoomCreateRequest,
    RoomInfo, LiveKitToken
)
from app.models.common import APIResponse, SuccessResponse
from app.middleware.auth import get_current_auth, require_site_auth
from app.integrations.supabase_client import supabase_manager
from app.integrations.livekit_client import livekit_manager
from app.services.container_manager import container_manager
from app.utils.exceptions import NotFoundError, ServiceUnavailableError

router = APIRouter()

@router.post("/create-call", response_model=APIResponse[SessionResponse])
async def create_call_session(
    request: SessionRequest,
    auth=Depends(get_current_auth)
):
    """
    Create a new LiveKit call session with containerized agent
    """
    try:
        # Get agent configuration
        agent_config = await supabase_manager.get_agent_configuration(request.agent_slug)
        if not agent_config:
            raise NotFoundError(f"Agent '{request.agent_slug}' not found")
        
        # Determine site context
        site_id = None
        site_config = {}
        
        if auth.is_site_auth:
            # WordPress site making the request
            site_id = auth.site_id
            site_result = await supabase_manager.execute_query(
                supabase_manager.admin_client.table("wordpress_sites")
                .select("*")
                .eq("id", site_id)
                .single()
            )
            if site_result:
                site_config = site_result
        else:
            # User auth - need to determine which site/container to use
            # For now, use a default or user-specific container
            site_id = f"user_{auth.user_id}"
            site_config = {
                "domain": "user.autonomite.local",
                "owner_user_id": auth.user_id
            }
        
        # Ensure agent container is running
        container_info = await container_manager.deploy_agent_container(
            site_id=site_id,
            agent_slug=request.agent_slug,
            agent_config=agent_config,
            site_config=site_config
        )
        
        if not container_info or container_info["status"] != "running":
            raise ServiceUnavailableError("Failed to start agent container")
        
        # Create or get conversation
        conversation = await supabase_manager.get_conversation(request.conversation_id)
        if not conversation:
            # Create new conversation
            conversation_data = {
                "id": request.conversation_id,
                "user_id": request.user_id,
                "agent_slug": request.agent_slug,
                "session_id": request.session_id,
                "channel": "voice",
                "status": "active",
                "metadata": {
                    "site_id": site_id,
                    "container_name": container_info["name"]
                }
            }
            conversation = await supabase_manager.create_conversation(conversation_data)
        
        # Generate room name
        room_name = f"room_{request.session_id}"
        
        # Create LiveKit room
        room = await livekit_manager.create_room(
            name=room_name,
            empty_timeout=300,  # 5 minutes
            max_participants=2,
            metadata={
                "agent_slug": request.agent_slug,
                "conversation_id": request.conversation_id,
                "user_id": request.user_id,
                "site_id": site_id,
                "container_name": container_info["name"]
            }
        )
        
        # Create user token
        user_token = livekit_manager.create_token(
            identity=request.user_id,
            room_name=room_name,
            metadata={
                "user_id": request.user_id,
                "conversation_id": request.conversation_id
            },
            ttl=3600  # 1 hour
        )
        
        # The containerized agent will automatically join rooms based on metadata
        # No need to create agent token here - the container handles that
        
        # Log session creation
        session_log = {
            "session_id": request.session_id,
            "room_name": room_name,
            "agent_slug": request.agent_slug,
            "site_id": site_id,
            "container_name": container_info["name"],
            "created_at": datetime.utcnow().isoformat()
        }
        
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("agent_sessions").insert(session_log)
        )
        
        return APIResponse(
            success=True,
            data=SessionResponse(
                session_id=request.session_id,
                room_name=room_name,
                livekit_token=user_token,
                livekit_url=livekit_manager.url,
                expires_at=datetime.utcnow() + timedelta(hours=1)
            )
        )
        
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except ServiceUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/create-room", response_model=APIResponse[RoomInfo])
async def create_room(
    request: RoomCreateRequest,
    auth=Depends(get_current_auth)
):
    """
    Create a LiveKit room without starting a session
    """
    try:
        # Create LiveKit room
        room = await livekit_manager.create_room(
            name=request.room_name,
            empty_timeout=request.empty_timeout,
            max_participants=request.max_participants,
            metadata=request.metadata
        )
        
        return APIResponse(
            success=True,
            data=RoomInfo(**room)
        )
        
    except ServiceUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/token", response_model=APIResponse[LiveKitToken])
async def create_token(
    room_name: str,
    identity: str,
    metadata: dict = {},
    auth=Depends(get_current_auth)
):
    """
    Create a LiveKit access token for a room
    """
    try:
        token = livekit_manager.create_token(
            identity=identity,
            room_name=room_name,
            metadata=metadata,
            ttl=3600  # 1 hour
        )
        
        return APIResponse(
            success=True,
            data=LiveKitToken(
                token=token,
                room_name=room_name,
                identity=identity,
                expires_at=datetime.utcnow() + timedelta(hours=1),
                metadata=metadata
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/room/{room_name}", response_model=APIResponse[RoomInfo])
async def get_room_info(
    room_name: str,
    auth=Depends(get_current_auth)
):
    """
    Get information about a LiveKit room
    """
    try:
        room = await livekit_manager.get_room(room_name)
        
        if not room:
            raise NotFoundError(f"Room '{room_name}' not found")
        
        return APIResponse(
            success=True,
            data=RoomInfo(**room)
        )
        
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/room/{room_name}/participants", response_model=APIResponse)
async def list_room_participants(
    room_name: str,
    auth=Depends(get_current_auth)
):
    """
    List participants in a LiveKit room
    """
    try:
        participants = await livekit_manager.list_participants(room_name)
        
        return APIResponse(
            success=True,
            data={
                "room_name": room_name,
                "participants": participants,
                "count": len(participants)
            }
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.delete("/room/{room_name}", response_model=APIResponse[SuccessResponse])
async def delete_room(
    room_name: str,
    auth=Depends(get_current_auth)
):
    """
    Delete a LiveKit room
    """
    try:
        success = await livekit_manager.delete_room(room_name)
        
        if not success:
            raise ServiceUnavailableError("Failed to delete room")
        
        return APIResponse(
            success=True,
            data=SuccessResponse(
                message=f"Room '{room_name}' deleted successfully"
            )
        )
        
    except ServiceUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/room/{room_name}/remove-participant", response_model=APIResponse[SuccessResponse])
async def remove_participant(
    room_name: str,
    identity: str,
    auth=Depends(get_current_auth)
):
    """
    Remove a participant from a LiveKit room
    """
    try:
        success = await livekit_manager.remove_participant(room_name, identity)
        
        if not success:
            raise ServiceUnavailableError("Failed to remove participant")
        
        return APIResponse(
            success=True,
            data=SuccessResponse(
                message=f"Participant '{identity}' removed from room '{room_name}'"
            )
        )
        
    except ServiceUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )