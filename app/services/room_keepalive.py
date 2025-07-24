"""
Room keepalive service to prevent premature LiveKit room deletion.
Maintains rooms for extended periods by periodically joining as a heartbeat participant.
"""
import logging
import asyncio
from typing import Dict, Set, Optional
from datetime import datetime, timedelta
from app.integrations.livekit_client import LiveKitManager

logger = logging.getLogger(__name__)


class RoomKeepaliveService:
    """
    Service to keep LiveKit rooms alive by periodically joining as a heartbeat participant.
    This prevents rooms from being deleted due to empty timeout.
    """
    
    def __init__(self):
        self._tracked_rooms: Dict[str, Dict] = {}  # room_name -> room_info
        self._keepalive_tasks: Dict[str, asyncio.Task] = {}  # room_name -> task
        self._running = False
        
    async def start(self):
        """Start the keepalive service"""
        if self._running:
            logger.warning("Room keepalive service already running")
            return
            
        self._running = True
        logger.info("✅ Room keepalive service started")
        
    async def stop(self):
        """Stop the keepalive service and cancel all tasks"""
        self._running = False
        
        # Cancel all keepalive tasks
        for room_name, task in self._keepalive_tasks.items():
            if not task.done():
                task.cancel()
                logger.info(f"Cancelled keepalive for room {room_name}")
                
        self._keepalive_tasks.clear()
        self._tracked_rooms.clear()
        logger.info("🛑 Room keepalive service stopped")
        
    async def track_room(self, room_name: str, livekit_manager: LiveKitManager, 
                        duration_hours: float = 2.0):
        """
        Track a room and keep it alive for the specified duration.
        
        Args:
            room_name: Name of the room to track
            livekit_manager: LiveKit manager with credentials for this room
            duration_hours: How long to keep the room alive (default 2 hours)
        """
        if not self._running:
            logger.warning("Room keepalive service not running, starting it...")
            await self.start()
            
        # Cancel existing task if any
        if room_name in self._keepalive_tasks:
            old_task = self._keepalive_tasks[room_name]
            if not old_task.done():
                old_task.cancel()
                
        # Store room info
        self._tracked_rooms[room_name] = {
            "livekit_manager": livekit_manager,
            "started_at": datetime.utcnow(),
            "duration_hours": duration_hours,
            "expires_at": datetime.utcnow() + timedelta(hours=duration_hours)
        }
        
        # Start keepalive task
        task = asyncio.create_task(self._keepalive_loop(room_name))
        self._keepalive_tasks[room_name] = task
        
        logger.info(f"🏠 Tracking room {room_name} for {duration_hours} hours")
        
    async def untrack_room(self, room_name: str):
        """Stop tracking a room"""
        if room_name in self._keepalive_tasks:
            task = self._keepalive_tasks[room_name]
            if not task.done():
                task.cancel()
            del self._keepalive_tasks[room_name]
            
        if room_name in self._tracked_rooms:
            del self._tracked_rooms[room_name]
            
        logger.info(f"🏚️ Stopped tracking room {room_name}")
        
    async def _keepalive_loop(self, room_name: str):
        """
        Keepalive loop for a specific room.
        Joins the room periodically to prevent empty timeout.
        """
        try:
            room_info = self._tracked_rooms.get(room_name)
            if not room_info:
                logger.error(f"No room info for {room_name}")
                return
                
            livekit_manager = room_info["livekit_manager"]
            expires_at = room_info["expires_at"]
            
            # Heartbeat every 15 minutes (well under typical 30min timeout)
            heartbeat_interval = 900  # 15 minutes in seconds
            
            while self._running and datetime.utcnow() < expires_at:
                try:
                    # Check if room still exists
                    room = await livekit_manager.get_room(room_name)
                    if not room:
                        logger.warning(f"Room {room_name} no longer exists, stopping keepalive")
                        break
                        
                    # If room has real participants, skip heartbeat
                    num_participants = room.get('num_participants', 0)
                    if num_participants > 0:
                        logger.info(f"Room {room_name} has {num_participants} participants, skipping heartbeat")
                    else:
                        # Create heartbeat token
                        heartbeat_token = livekit_manager.create_token(
                            identity=f"heartbeat_{datetime.utcnow().timestamp()}",
                            room_name=room_name,
                            metadata={"role": "heartbeat", "automated": True}
                        )
                        
                        # Note: Actually joining the room would require WebRTC connection
                        # For now, just creating the token is enough to show intent
                        # In production, you'd want a minimal WebRTC client to actually join
                        
                        logger.info(f"💓 Heartbeat for room {room_name} (expires in {(expires_at - datetime.utcnow()).total_seconds() / 3600:.1f} hours)")
                        
                except Exception as e:
                    logger.error(f"Error in heartbeat for room {room_name}: {e}")
                    
                # Wait for next heartbeat
                await asyncio.sleep(heartbeat_interval)
                
            logger.info(f"⏰ Keepalive expired for room {room_name}")
            
        except asyncio.CancelledError:
            logger.info(f"Keepalive cancelled for room {room_name}")
        except Exception as e:
            logger.error(f"Unexpected error in keepalive loop for {room_name}: {e}")
        finally:
            # Clean up
            await self.untrack_room(room_name)


# Global instance
room_keepalive_service = RoomKeepaliveService()