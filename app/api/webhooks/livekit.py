from fastapi import APIRouter, Request, HTTPException, status
import logging
import json
from datetime import datetime

from app.integrations.livekit_client import livekit_manager
from app.integrations.supabase_client import supabase_manager
from app.models.common import APIResponse, SuccessResponse
from app.services.usage_tracking import usage_tracking_service

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
    room_name = room.get("name", "")

    # Update conversation status if linked
    metadata = room.get("metadata", {})
    if isinstance(metadata, str):
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

    # Track voice usage for quota metering (per-agent)
    # Duration is in seconds from LiveKit
    raw_duration = room.get("duration")
    client_id = metadata.get("client_id")
    agent_id = metadata.get("agent_id")
    is_text_room = room_name.startswith("text-") if room_name else False

    # Log room finished event with diagnostic info including all room fields
    room_fields = {k: v for k, v in room.items() if k not in ("metadata",)}  # Exclude large metadata
    logger.info(
        "Room finished: room=%s, duration=%s (type=%s), client_id=%s, agent_id=%s, is_text=%s, room_fields=%s",
        room_name, raw_duration, type(raw_duration).__name__, client_id, agent_id, is_text_room, room_fields
    )

    # Convert duration to int, handling None/null cases
    try:
        duration_seconds = int(raw_duration) if raw_duration is not None else 0
    except (TypeError, ValueError):
        logger.warning(
            "Invalid duration value in room_finished event: %s (type=%s)",
            raw_duration, type(raw_duration).__name__
        )
        duration_seconds = 0

    # Only track voice usage for non-text rooms
    if not is_text_room:
        if duration_seconds > 0 and client_id and agent_id:
            try:
                await usage_tracking_service.initialize()
                is_within_quota, quota_status = await usage_tracking_service.increment_agent_voice_usage(
                    client_id=str(client_id),
                    agent_id=str(agent_id),
                    seconds=duration_seconds,
                )
                logger.info(
                    "Tracked voice usage: agent=%s, client=%s, duration=%ds, total=%d/%d seconds (%.1f%%)",
                    agent_id, client_id, duration_seconds,
                    quota_status.used, quota_status.limit, quota_status.percent_used
                )
                if not is_within_quota:
                    logger.warning(
                        "Voice quota exceeded for agent %s (client %s): %d/%d seconds",
                        agent_id, client_id, quota_status.used, quota_status.limit
                    )
            except Exception as usage_err:
                logger.error("Failed to track voice usage: %s", usage_err, exc_info=True)
        elif duration_seconds > 0:
            # Duration exists but missing client_id or agent_id
            logger.warning(
                "Room finished with duration %ds but missing metadata for usage tracking: client_id=%s, agent_id=%s, room=%s",
                duration_seconds, client_id, agent_id, room_name
            )
        elif duration_seconds == 0:
            # Duration is 0 - this could indicate an issue with LiveKit or very short call
            logger.warning(
                "Room finished with zero duration - voice usage NOT tracked: room=%s, client_id=%s, agent_id=%s, raw_duration=%s",
                room_name, client_id, agent_id, raw_duration
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