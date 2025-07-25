from fastapi import APIRouter, Request, HTTPException, status
import logging
from datetime import datetime

from app.integrations.livekit_client import livekit_manager
from app.integrations.supabase_client import supabase_manager
from app.models.common import APIResponse, SuccessResponse

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/livekit/events")
async def handle_livekit_webhook(request: Request):
    """
    Handle LiveKit webhook events
    """
    try:
        # Get webhook signature
        auth_header = request.headers.get("Authorization", "")
        
        # Get body
        body = await request.body()
        
        # Verify webhook signature
        if not livekit_manager.verify_webhook(auth_header, body):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )
        
        # Parse event
        event_data = await request.json()
        event_type = event_data.get("event")
        
        logger.info(f"LiveKit webhook event: {event_type}", extra={"event_data": event_data})
        
        # Handle different event types
        if event_type == "room_started":
            await handle_room_started(event_data)
        elif event_type == "room_finished":
            await handle_room_finished(event_data)
        elif event_type == "participant_joined":
            await handle_participant_joined(event_data)
        elif event_type == "participant_left":
            await handle_participant_left(event_data)
        elif event_type == "track_published":
            await handle_track_published(event_data)
        
        return APIResponse(
            success=True,
            data=SuccessResponse(message="Event processed")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"LiveKit webhook error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process webhook"
        )

async def handle_room_started(event_data: dict):
    """Handle room started event"""
    room = event_data.get("room", {})
    room_name = room.get("name")
    
    logger.info(f"Room started: {room_name}")
    
    # Extract metadata
    metadata = room.get("metadata", {})
    if isinstance(metadata, str):
        import json
        try:
            metadata = json.loads(metadata)
        except:
            metadata = {}
    
    # Log room event
    event_log = {
        "event_type": "room_started",
        "room_name": room_name,
        "room_sid": room.get("sid"),
        "metadata": metadata,
        "created_at": datetime.utcnow().isoformat()
    }
    
    await supabase_manager.execute_query(
        supabase_manager.admin_client.table("livekit_events").insert(event_log)
    )

async def handle_room_finished(event_data: dict):
    """Handle room finished event"""
    room = event_data.get("room", {})
    room_name = room.get("name")
    
    logger.info(f"Room finished: {room_name}")
    
    # Update conversation status if linked
    metadata = room.get("metadata", {})
    if isinstance(metadata, str):
        import json
        try:
            metadata = json.loads(metadata)
        except:
            metadata = {}
    
    conversation_id = metadata.get("conversation_id")
    if conversation_id:
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("conversations")
            .update({
                "status": "completed",
                "updated_at": datetime.utcnow().isoformat()
            })
            .eq("id", conversation_id)
        )
    
    # Log room event
    event_log = {
        "event_type": "room_finished",
        "room_name": room_name,
        "room_sid": room.get("sid"),
        "duration": room.get("duration"),
        "metadata": metadata,
        "created_at": datetime.utcnow().isoformat()
    }
    
    await supabase_manager.execute_query(
        supabase_manager.admin_client.table("livekit_events").insert(event_log)
    )

async def handle_participant_joined(event_data: dict):
    """Handle participant joined event"""
    participant = event_data.get("participant", {})
    room = event_data.get("room", {})
    
    logger.info(f"Participant joined: {participant.get('identity')} in room {room.get('name')}")
    
    # Log participant event
    event_log = {
        "event_type": "participant_joined",
        "room_name": room.get("name"),
        "room_sid": room.get("sid"),
        "participant_sid": participant.get("sid"),
        "participant_identity": participant.get("identity"),
        "metadata": participant.get("metadata", {}),
        "created_at": datetime.utcnow().isoformat()
    }
    
    await supabase_manager.execute_query(
        supabase_manager.admin_client.table("livekit_events").insert(event_log)
    )

async def handle_participant_left(event_data: dict):
    """Handle participant left event"""
    participant = event_data.get("participant", {})
    room = event_data.get("room", {})
    
    logger.info(f"Participant left: {participant.get('identity')} from room {room.get('name')}")
    
    # Log participant event
    event_log = {
        "event_type": "participant_left",
        "room_name": room.get("name"),
        "room_sid": room.get("sid"),
        "participant_sid": participant.get("sid"),
        "participant_identity": participant.get("identity"),
        "duration": participant.get("duration"),
        "created_at": datetime.utcnow().isoformat()
    }
    
    await supabase_manager.execute_query(
        supabase_manager.admin_client.table("livekit_events").insert(event_log)
    )

async def handle_track_published(event_data: dict):
    """Handle track published event"""
    track = event_data.get("track", {})
    participant = event_data.get("participant", {})
    room = event_data.get("room", {})
    
    logger.info(f"Track published: {track.get('type')} by {participant.get('identity')}")
    
    # Log track event
    event_log = {
        "event_type": "track_published",
        "room_name": room.get("name"),
        "participant_identity": participant.get("identity"),
        "track_type": track.get("type"),
        "track_source": track.get("source"),
        "created_at": datetime.utcnow().isoformat()
    }
    
    await supabase_manager.execute_query(
        supabase_manager.admin_client.table("livekit_events").insert(event_log)
    )