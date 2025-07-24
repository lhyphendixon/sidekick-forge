"""
Room monitoring service to track LiveKit room status and health.
"""
import logging
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
from app.integrations.livekit_client import LiveKitManager

logger = logging.getLogger(__name__)


class RoomMonitor:
    """
    Monitor LiveKit rooms for health and status.
    Provides visibility into room lifecycle and participant activity.
    """
    
    def __init__(self):
        self._monitored_rooms: Dict[str, Dict] = {}  # room_name -> monitoring info
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False
        
    async def start(self, check_interval: int = 30):
        """
        Start the room monitoring service.
        
        Args:
            check_interval: How often to check room status in seconds (default 30s)
        """
        if self._running:
            logger.warning("Room monitor already running")
            return
            
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop(check_interval))
        logger.info(f"✅ Room monitor started with {check_interval}s check interval")
        
    async def stop(self):
        """Stop the room monitoring service"""
        self._running = False
        
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
                
        logger.info("🛑 Room monitor stopped")
        
    def add_room(self, room_name: str, livekit_manager: LiveKitManager, metadata: Optional[Dict] = None):
        """
        Add a room to monitor.
        
        Args:
            room_name: Name of the room
            livekit_manager: LiveKit manager for this room
            metadata: Optional metadata about the room
        """
        self._monitored_rooms[room_name] = {
            "livekit_manager": livekit_manager,
            "added_at": datetime.utcnow(),
            "metadata": metadata or {},
            "last_check": None,
            "last_status": None,
            "check_count": 0,
            "error_count": 0
        }
        logger.info(f"👁️ Added room {room_name} to monitoring")
        
    def remove_room(self, room_name: str):
        """Remove a room from monitoring"""
        if room_name in self._monitored_rooms:
            del self._monitored_rooms[room_name]
            logger.info(f"🔇 Removed room {room_name} from monitoring")
            
    async def check_room(self, room_name: str) -> Optional[Dict]:
        """
        Check the status of a specific room.
        
        Returns:
            Room status dict or None if room not found
        """
        if room_name not in self._monitored_rooms:
            return None
            
        room_info = self._monitored_rooms[room_name]
        livekit_manager = room_info["livekit_manager"]
        
        try:
            room = await livekit_manager.get_room(room_name)
            if room:
                status = {
                    "exists": True,
                    "participants": room.get("num_participants", 0),
                    "max_participants": room.get("max_participants"),
                    "creation_time": room.get("creation_time"),
                    "empty_timeout": room.get("empty_timeout"),
                    "metadata": room.get("metadata"),
                    "checked_at": datetime.utcnow().isoformat()
                }
                
                # Update monitoring info
                room_info["last_check"] = datetime.utcnow()
                room_info["last_status"] = status
                room_info["check_count"] += 1
                
                return status
            else:
                return {
                    "exists": False,
                    "checked_at": datetime.utcnow().isoformat()
                }
                
        except Exception as e:
            logger.error(f"Error checking room {room_name}: {e}")
            room_info["error_count"] += 1
            return {
                "exists": "unknown",
                "error": str(e),
                "checked_at": datetime.utcnow().isoformat()
            }
            
    async def get_all_statuses(self) -> Dict[str, Dict]:
        """Get status of all monitored rooms"""
        statuses = {}
        for room_name in list(self._monitored_rooms.keys()):
            status = await self.check_room(room_name)
            if status:
                statuses[room_name] = status
        return statuses
        
    async def _monitor_loop(self, check_interval: int):
        """Main monitoring loop"""
        try:
            while self._running:
                # Check all rooms
                for room_name in list(self._monitored_rooms.keys()):
                    if not self._running:
                        break
                        
                    status = await self.check_room(room_name)
                    if status:
                        # Log significant events
                        if not status.get("exists", True):
                            logger.warning(f"📭 Room {room_name} no longer exists")
                            self.remove_room(room_name)
                        elif status.get("participants", 0) == 0:
                            room_info = self._monitored_rooms[room_name]
                            age_minutes = (datetime.utcnow() - room_info["added_at"]).total_seconds() / 60
                            if age_minutes > 5:  # Only warn after 5 minutes
                                logger.info(f"🏚️ Room {room_name} is empty (age: {age_minutes:.0f}m)")
                                
                # Wait for next check
                await asyncio.sleep(check_interval)
                
        except asyncio.CancelledError:
            logger.info("Room monitor loop cancelled")
        except Exception as e:
            logger.error(f"Unexpected error in monitor loop: {e}")
            
    def get_room_info(self, room_name: str) -> Optional[Dict]:
        """Get monitoring info for a room"""
        return self._monitored_rooms.get(room_name)
        
    def list_rooms(self) -> List[str]:
        """List all monitored rooms"""
        return list(self._monitored_rooms.keys())


# Global instance
room_monitor = RoomMonitor()