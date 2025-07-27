from livekit import api, rtc
from typing import Optional, Dict, Any, List, Union
import logging
import time
import jwt
import json
from datetime import datetime, timedelta

from app.config import settings
from app.utils.exceptions import ServiceUnavailableError
from app.utils.livekit_credentials import LiveKitCredentialManager

logger = logging.getLogger(__name__)

class LiveKitManager:
    """Manages LiveKit connections and operations"""
    
    def __init__(self):
        # Don't load credentials in __init__ - wait for async initialize
        self.api_key = None
        self.api_secret = None
        self.url = None
        self._initialized = False
    
    async def initialize(self):
        """Initialize LiveKit connection"""
        if self._initialized:
            return
        
        try:
            # Load credentials with proper fallbacks
            self.url, self.api_key, self.api_secret = await LiveKitCredentialManager.get_backend_credentials()
            
            # Validate credentials
            if not await LiveKitCredentialManager.validate_credentials(self.url, self.api_key, self.api_secret):
                raise ValueError("LiveKit credentials validation failed")
            
            # Test connection by creating a test token
            self.create_token("test", "test-room")
            self._initialized = True
            logger.info(f"LiveKit manager initialized successfully with URL: {self.url}")
        except Exception as e:
            logger.error(f"Failed to initialize LiveKit: {e}")
            raise ServiceUnavailableError(f"Failed to connect to LiveKit: {str(e)}")
    
    async def close(self):
        """Close LiveKit connections"""
        self._initialized = False
    
    async def health_check(self) -> bool:
        """Check LiveKit service health"""
        try:
            # Try to create a token as health check
            token = self.create_token("health-check", "health-check-room")
            return bool(token)
        except Exception as e:
            logger.error(f"LiveKit health check failed: {e}")
            return False
    
    def create_token(
        self,
        identity: str,
        room_name: str,
        metadata: Optional[Dict[str, Any]] = None,
        permissions: Optional[api.VideoGrants] = None,
        ttl: int = 3600  # 1 hour default
    ) -> str:
        """Create a LiveKit access token"""
        if not permissions:
            # Default permissions for participants
            permissions = api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True
            )
        
        # Create access token using proper LiveKit SDK methods
        token = api.AccessToken(self.api_key, self.api_secret)
        token = token.with_identity(identity)
        token = token.with_grants(permissions)
        
        if metadata:
            token = token.with_metadata(str(metadata))
        
        token = token.with_ttl(timedelta(seconds=ttl))
        
        return token.to_jwt()
    
    def create_agent_token(
        self,
        agent_name: str,
        room_name: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create a token specifically for AI agents"""
        # Agent permissions - can publish but limited subscribe
        permissions = api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            hidden=False  # Agents are visible
        )
        
        agent_metadata = {
            "agent": True,
            "agent_name": agent_name,
            **(metadata or {})
        }
        
        return self.create_token(
            identity=f"agent_{agent_name}",
            room_name=room_name,
            metadata=agent_metadata,
            permissions=permissions,
            ttl=7200  # 2 hours for agents
        )
    
    async def create_room(
        self,
        name: Optional[str] = None,
        empty_timeout: int = 300,
        max_participants: int = 2,
        metadata: Optional[Union[Dict[str, Any], str]] = None,
        enable_agent_dispatch: bool = True,
        agent_name: str = "sidekick-agent"
    ) -> Dict[str, Any]:
        """Create a LiveKit room
        
        Args:
            name: Room name
            empty_timeout: Seconds before empty room is deleted
            max_participants: Maximum participants allowed
            metadata: Room metadata (can be dict or JSON string)
        """
        logger.info(f"Creating room: name={name}, empty_timeout={empty_timeout}, max_participants={max_participants}")
        logger.debug(f"Metadata type: {type(metadata)}, length: {len(str(metadata)) if metadata else 0}")
        
        try:
            # Use LiveKitAPI instead of RoomServiceClient
            livekit_api = api.LiveKitAPI(
                url=self.url,
                api_key=self.api_key,
                api_secret=self.api_secret
            )
            
            # Generate room name if not provided
            if not name:
                name = f"room_{int(time.time() * 1000)}"
            
            # Handle metadata - it might already be a JSON string
            room_metadata = None
            if metadata:
                if isinstance(metadata, str):
                    # Already JSON encoded
                    room_metadata = metadata
                else:
                    # Need to encode to JSON
                    room_metadata = json.dumps(metadata)
            
            logger.info(f"Sending room creation request to LiveKit")
            
            # Create room request
            room_request = api.CreateRoomRequest(
                name=name,
                empty_timeout=empty_timeout,
                max_participants=max_participants,
                metadata=room_metadata
            )
            
            # Configure agent dispatch if enabled
            if enable_agent_dispatch:
                # CRITICAL: Configure agent dispatch AT ROOM CREATION TIME
                # This is the correct pattern for automatic agent dispatch
                agent_dispatch_options = api.RoomAgentDispatch(
                    agent_name=agent_name,  # Must match the worker's agent name
                    metadata=room_metadata if room_metadata else ""
                )
                # Use list assignment instead of append to ensure proper initialization
                room_request.agents = [agent_dispatch_options]
                logger.info(f"🤖 Creating room with agent dispatch enabled: agent_name={agent_name}")
            else:
                logger.info(f"📹 Creating standard room without agent dispatch")
            
            room = await livekit_api.room.create_room(room_request)
            
            logger.info(f"✅ Room created successfully with agent dispatch: name={room.name}, sid={room.sid}")
            
            return {
                "name": room.name,
                "sid": room.sid,
                "created_at": datetime.utcnow(),
                "max_participants": room.max_participants,
                "metadata": metadata  # Return original metadata (not the JSON string)
            }
            
        except Exception as e:
            logger.error(f"Failed to create room: {e}")
            raise ServiceUnavailableError(f"Failed to create LiveKit room: {str(e)}")
    
    async def get_room(self, room_name: str) -> Optional[Dict[str, Any]]:
        """Get room information"""
        try:
            livekit_api = api.LiveKitAPI(
                url=self.url,
                api_key=self.api_key,
                api_secret=self.api_secret
            )
            
            rooms = await livekit_api.room.list_rooms(
                api.ListRoomsRequest(names=[room_name])
            )
            
            if rooms.rooms:
                room = rooms.rooms[0]
                return {
                    "name": room.name,
                    "sid": room.sid,
                    "num_participants": room.num_participants,
                    "max_participants": room.max_participants,
                    "creation_time": room.creation_time,
                    "metadata": room.metadata
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get room: {e}")
            return None
    
    async def list_participants(self, room_name: str) -> List[Dict[str, Any]]:
        """List participants in a room"""
        try:
            room_service = api.RoomServiceClient(
                self.url,
                self.api_key,
                self.api_secret
            )
            
            participants = await room_service.list_participants(
                api.ListParticipantsRequest(room=room_name)
            )
            
            return [
                {
                    "sid": p.sid,
                    "identity": p.identity,
                    "name": p.name,
                    "state": p.state,
                    "joined_at": p.joined_at,
                    "metadata": p.metadata,
                    "is_publisher": p.permission and p.permission.can_publish
                }
                for p in participants.participants
            ]
            
        except Exception as e:
            logger.error(f"Failed to list participants: {e}")
            return []
    
    async def remove_participant(self, room_name: str, identity: str) -> bool:
        """Remove a participant from a room"""
        try:
            room_service = api.RoomServiceClient(
                self.url,
                self.api_key,
                self.api_secret
            )
            
            await room_service.remove_participant(
                api.RoomParticipantIdentity(
                    room=room_name,
                    identity=identity
                )
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to remove participant: {e}")
            return False
    
    async def update_room_metadata(self, room_name: str, metadata: Dict[str, Any]) -> bool:
        """Update room metadata"""
        try:
            livekit_api = api.LiveKitAPI(
                url=self.url,
                api_key=self.api_key,
                api_secret=self.api_secret
            )
            
            # Update room with new metadata
            await livekit_api.room.update_room(
                api.UpdateRoomRequest(
                    room=room_name,
                    metadata=json.dumps(metadata) if metadata else None
                )
            )
            
            logger.info(f"Updated room {room_name} metadata")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update room metadata: {e}")
            return False
    
    async def delete_room(self, room_name: str) -> bool:
        """Delete a LiveKit room"""
        try:
            room_service = api.RoomServiceClient(
                self.url,
                self.api_key,
                self.api_secret
            )
            
            await room_service.delete_room(
                api.DeleteRoomRequest(room=room_name)
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete room: {e}")
            return False
    
    async def send_data(
        self,
        room_name: str,
        data: bytes,
        destination_identities: Optional[List[str]] = None,
        topic: Optional[str] = None
    ) -> bool:
        """Send data message to room participants"""
        try:
            room_service = api.RoomServiceClient(
                self.url,
                self.api_key,
                self.api_secret
            )
            
            await room_service.send_data(
                api.SendDataRequest(
                    room=room_name,
                    data=data,
                    kind=api.DataPacket.Kind.RELIABLE,
                    destination_identities=destination_identities,
                    topic=topic
                )
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send data: {e}")
            return False
    
    def verify_webhook(self, auth_header: str, body: bytes) -> bool:
        """Verify LiveKit webhook signature"""
        try:
            token_verifier = api.TokenVerifier(
                self.api_key,
                self.api_secret
            )
            
            # LiveKit uses SHA256 HMAC for webhook verification
            token_verifier.verify(auth_header, body)
            return True
            
        except Exception as e:
            logger.error(f"Webhook verification failed: {e}")
            return False

# Create singleton instance
livekit_manager = LiveKitManager()