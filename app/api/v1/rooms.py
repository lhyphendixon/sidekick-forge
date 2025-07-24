"""
Room management endpoints for LiveKit rooms.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, List
import logging

from app.services.room_monitor import room_monitor
# from app.middleware.auth import verify_token  # TODO: Fix auth import

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rooms", tags=["rooms"])


@router.get("/status/{room_name}")
async def get_room_status(room_name: str) -> Dict:  # TODO: Add auth with user=Depends(verify_token)
    """
    Get the status of a specific room.
    
    Returns room existence, participant count, and other metadata.
    """
    status = await room_monitor.check_room(room_name)
    if status:
        return {
            "room_name": room_name,
            "status": status
        }
    else:
        # Room not being monitored, return basic info
        return {
            "room_name": room_name,
            "status": {
                "monitored": False,
                "message": "Room is not being monitored"
            }
        }


@router.get("/monitored")
async def list_monitored_rooms() -> List[Dict]:  # TODO: Add auth with user=Depends(verify_token)
    """
    List all rooms currently being monitored.
    """
    rooms = room_monitor.list_rooms()
    room_list = []
    
    for room_name in rooms:
        info = room_monitor.get_room_info(room_name)
        if info:
            room_list.append({
                "room_name": room_name,
                "added_at": info["added_at"].isoformat(),
                "metadata": info.get("metadata", {}),
                "last_check": info["last_check"].isoformat() if info["last_check"] else None,
                "check_count": info["check_count"],
                "error_count": info["error_count"]
            })
            
    return room_list


@router.get("/all-statuses")
async def get_all_room_statuses() -> Dict:  # TODO: Add auth with user=Depends(verify_token)
    """
    Get the current status of all monitored rooms.
    """
    statuses = await room_monitor.get_all_statuses()
    return {
        "count": len(statuses),
        "rooms": statuses
    }