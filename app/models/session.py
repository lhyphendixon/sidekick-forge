from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID

class SessionRequest(BaseModel):
    """Request model for creating a LiveKit session"""
    agent_slug: str = Field(..., min_length=1, max_length=100)
    user_id: str  # Can be Supabase Auth ID or custom ID
    conversation_id: str
    session_id: str
    agent_name: Optional[str] = Field(default="LITEBRIDGE")
    
    # Optional metadata
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    
    class Config:
        schema_extra = {
            "example": {
                "agent_slug": "support-agent",
                "user_id": "user_123",
                "conversation_id": "conv_456",
                "session_id": "session_789",
                "agent_name": "Support Agent"
            }
        }

class SessionResponse(BaseModel):
    """Response model for session creation"""
    session_id: str
    room_name: str
    livekit_token: str
    livekit_url: str
    expires_at: datetime
    
    class Config:
        schema_extra = {
            "example": {
                "session_id": "session_789",
                "room_name": "room_abc123",
                "livekit_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
                "livekit_url": "wss://livekit.example.com",
                "expires_at": "2024-01-01T01:00:00Z"
            }
        }

class LiveKitToken(BaseModel):
    """LiveKit token details"""
    token: str
    room_name: str
    identity: str
    expires_at: datetime
    metadata: Optional[Dict[str, Any]] = None

class RoomCreateRequest(BaseModel):
    """Request model for creating a LiveKit room"""
    room_name: Optional[str] = None  # Auto-generated if not provided
    agent_slug: str
    max_participants: int = Field(default=2, ge=1, le=100)
    empty_timeout: int = Field(default=300)  # 5 minutes
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class RoomInfo(BaseModel):
    """LiveKit room information"""
    room_name: str
    sid: Optional[str] = None
    created_at: datetime
    num_participants: int = 0
    max_participants: int = 2
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ParticipantInfo(BaseModel):
    """LiveKit participant information"""
    sid: str
    identity: str
    name: Optional[str] = None
    state: str
    joined_at: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)

class SessionStatus(BaseModel):
    """Session status information"""
    session_id: str
    room_name: str
    status: str = Field(..., pattern="^(active|completed|failed)$")
    participants: int
    duration_seconds: Optional[float] = None
    created_at: datetime
    ended_at: Optional[datetime] = None

class WebhookEvent(BaseModel):
    """LiveKit webhook event"""
    event: str
    room: Optional[Dict[str, Any]] = None
    participant: Optional[Dict[str, Any]] = None
    track: Optional[Dict[str, Any]] = None
    timestamp: int
    
    class Config:
        schema_extra = {
            "example": {
                "event": "participant_joined",
                "room": {
                    "name": "room_abc123",
                    "sid": "RM_xyz789"
                },
                "participant": {
                    "sid": "PA_123456",
                    "identity": "user_123"
                },
                "timestamp": 1704067200
            }
        }