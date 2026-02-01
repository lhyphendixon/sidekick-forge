import asyncio
import os
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Union

from livekit import api, rtc

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
        self.livekit_api = None
        self._worker_pool_enabled = os.getenv("LIVEKIT_WORKER_POOL", "false").lower() == "true"
        self._worker_pool_size = int(os.getenv("LIVEKIT_WORKER_POOL_SIZE", "3"))
        self._warm_workers: asyncio.Queue = asyncio.Queue(maxsize=self._worker_pool_size) if self._worker_pool_enabled else None
    
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

            self.livekit_api = api.LiveKitAPI(self.url, self.api_key, self.api_secret)
            
            # Test connection by creating a test token
            self.create_token("test", "test-room")
            self._initialized = True
            logger.info(f"LiveKit manager initialized successfully with URL: {self.url}")
        except Exception as e:
            logger.error(f"Failed to initialize LiveKit: {e}")
            self.livekit_api = None
            raise ServiceUnavailableError(f"Failed to connect to LiveKit: {str(e)}")
    
    async def close(self):
        """Close LiveKit connections"""
        if self.livekit_api:
            # LiveKitAPI uses aclose() for async close
            await self.livekit_api.aclose()
        self._initialized = False
        self.livekit_api = None

    def _get_api_client(self) -> api.LiveKitAPI:
        if not self.livekit_api or not self._initialized:
            raise ServiceUnavailableError("LiveKitManager is not initialized. Call initialize() first.")
        return self.livekit_api

    async def get_warm_worker(self) -> Optional[str]:
        """Return a warmed worker id if pooling is enabled."""
        if not self._worker_pool_enabled or not self._warm_workers:
            return None
        try:
            worker_id = self._warm_workers.get_nowait()
            logger.info(f"♻️ Reusing warm worker {worker_id}")
            return worker_id
        except asyncio.QueueEmpty:
            return None

    async def return_worker_to_pool(self, worker_id: Optional[str]) -> None:
        """Return a worker id to the pool for future reuse."""
        if not worker_id or not self._worker_pool_enabled or not self._warm_workers:
            return
        try:
            self._warm_workers.put_nowait(worker_id)
            logger.info(f"✅ Worker {worker_id} returned to warm pool")
        except asyncio.QueueFull:
            logger.debug("Worker pool full; discarding worker id")

    async def health_check(self) -> bool:
        """Check LiveKit service health"""
        try:
            self._get_api_client()
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
        ttl: int = 3600,  # 1 hour default
        dispatch_agent_name: Optional[str] = None,
        dispatch_metadata: Optional[Dict[str, Any]] = None,
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
            token = token.with_metadata(json.dumps(metadata))
        
        token = token.with_ttl(timedelta(seconds=ttl))

        # Optionally embed RoomConfiguration with RoomAgentDispatch so the agent is dispatched
        # automatically when this participant connects (per LiveKit docs: Dispatch on participant connection)
        try:
            if dispatch_agent_name:
                room_cfg = api.RoomConfiguration(
                    agents=[
                        api.RoomAgentDispatch(
                            agent_name=dispatch_agent_name,
                            metadata=json.dumps(dispatch_metadata) if dispatch_metadata else "",
                        )
                    ]
                )
                token = token.with_room_config(room_cfg)
                logger.info(f"🔧 Embedded RoomAgentDispatch for agent '{dispatch_agent_name}' into token")
        except Exception as e:
            logger.warning(f"Failed to embed RoomAgentDispatch into token: {type(e).__name__}: {e}")

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
        enable_agent_dispatch: bool = False,
        agent_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a LiveKit room"""
        logger.info(f"Creating room: name={name}, empty_timeout={empty_timeout}, max_participants={max_participants}")
        logger.debug(f"Metadata type: {type(metadata)}, length: {len(str(metadata)) if metadata else 0}")
        
        try:
            livekit_api = self._get_api_client()
            
            # Generate room name if not provided
            if not name:
                name = f"room_{int(time.time() * 1000)}"
            
            # Handle metadata - it might already be a JSON string
            room_metadata = None
            if metadata:
                if isinstance(metadata, str):
                    room_metadata = metadata
                else:
                    room_metadata = json.dumps(metadata)
            
            logger.info(f"Sending room creation request to LiveKit")
            
            agents_list = []
            if enable_agent_dispatch:
                if agent_name:
                    agent_dispatch_options = api.RoomAgentDispatch(
                        agent_name=agent_name,
                        metadata=room_metadata if room_metadata else ""
                    )
                    agents_list.append(agent_dispatch_options)
                    logger.info(f"🤖 Creating room with explicit agent dispatch: agent_name={agent_name}")
                else:
                    agent_dispatch_options = api.RoomAgentDispatch(
                        metadata=room_metadata if room_metadata else ""
                    )
                    agents_list.append(agent_dispatch_options)
                    logger.info(f"🤖 Creating room with general agent dispatch (any worker)")
            else:
                logger.info(f"📹 Creating standard room without agent dispatch")
            
            room_request = api.CreateRoomRequest(
                name=name,
                empty_timeout=empty_timeout,
                max_participants=max_participants,
                metadata=room_metadata,
                agents=agents_list
            )
            
            room = await livekit_api.room.create_room(room_request)
            
            log_msg = f"✅ Room '{room.name}' (sid: {room.sid}) created"
            if enable_agent_dispatch:
                log_msg += f" with agent dispatch enabled."
            else:
                log_msg += f" without agent dispatch."
            logger.info(log_msg)

            return {
                "name": room.name,
                "sid": room.sid,
                "created_at": datetime.utcnow(),
                "max_participants": room.max_participants,
                "metadata": metadata
            }
            
        except Exception as e:
            logger.error(f"Failed to create room: {e}")
            raise ServiceUnavailableError(f"Failed to create LiveKit room: {str(e)}")
    
    async def get_room(self, room_name: str) -> Optional[Dict[str, Any]]:
        """Get room information"""
        try:
            livekit_api = self._get_api_client()
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
            livekit_api = self._get_api_client()
            
            participants = await livekit_api.room.list_participants(
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
            livekit_api = self._get_api_client()
            await livekit_api.room.remove_participant(
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
            livekit_api = self._get_api_client()
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
            livekit_api = self._get_api_client()
            await livekit_api.room.delete_room(
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
            livekit_api = self._get_api_client()
            await livekit_api.room.send_data(
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
        """Verify LiveKit webhook signature using WebhookReceiver"""
        try:
            # Create TokenVerifier and WebhookReceiver
            token_verifier = api.TokenVerifier(
                self.api_key,
                self.api_secret
            )
            webhook_receiver = api.WebhookReceiver(token_verifier)

            # Extract token from Authorization header (format: "Bearer <token>" or just "<token>")
            auth_token = auth_header
            if auth_header.lower().startswith("bearer "):
                auth_token = auth_header[7:]  # Remove "Bearer " prefix

            # Convert body to string if needed
            body_str = body.decode("utf-8") if isinstance(body, bytes) else body

            # Verify and parse the webhook event
            # This will raise an exception if verification fails
            webhook_receiver.receive(body_str, auth_token)
            return True

        except Exception as e:
            logger.error(f"Webhook verification failed: {e}")
            return False

# Create singleton instance
livekit_manager = LiveKitManager()
