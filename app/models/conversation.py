from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any
from datetime import datetime
from uuid import UUID

class ConversationBase(BaseModel):
    """Base conversation model"""
    conversation_title: Optional[str] = None
    status: str = Field(default="active", pattern="^(active|archived|deleted)$")
    channel: str = Field(default="voice", pattern="^(voice|text|hybrid)$")
    agent_id: Optional[UUID] = None
    agent_slug: Optional[str] = None
    session_id: Optional[str] = None
    summary: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class Conversation(ConversationBase):
    """Conversation model matching production conversations table"""
    id: Optional[UUID] = None
    user_id: UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_interaction_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class ConversationMessage(BaseModel):
    """Message model matching production conversation_transcripts table"""
    id: Optional[UUID] = None
    conversation_id: UUID
    user_id: UUID  # Supabase Auth ID
    session_id: Optional[UUID] = None
    content: str
    message: Optional[str] = None  # Compatibility field
    role: str = Field(..., pattern="^(user|assistant|system)$")
    sequence: Optional[int] = None
    channel: str = Field(default="voice", pattern="^(voice|text|hybrid)$")
    tool_calls: Optional[Dict[str, Any]] = None
    tool_results: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        from_attributes = True

class ConversationCreateRequest(ConversationBase):
    """Request model for creating a new conversation"""
    user_id: Optional[UUID] = None  # Can be set from auth context
    initial_message: Optional[str] = None

class ConversationUpdateRequest(BaseModel):
    """Request model for updating a conversation"""
    conversation_title: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(active|archived|deleted)$")
    summary: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class MessageCreateRequest(BaseModel):
    """Request model for adding a message to conversation"""
    content: str = Field(..., min_length=1)
    role: str = Field(..., pattern="^(user|assistant|system)$")
    session_id: Optional[str] = None
    tool_calls: Optional[Dict[str, Any]] = None
    tool_results: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class TranscriptStoreRequest(BaseModel):
    """Request model for storing conversation transcript (WordPress compatibility)"""
    conversation_id: str
    user_id: str
    session_id: str
    transcript: List[Dict[str, Any]]  # Array of message objects
    
    class Config:
        schema_extra = {
            "example": {
                "conversation_id": "conv_123",
                "user_id": "user_456",
                "session_id": "session_789",
                "transcript": [
                    {
                        "role": "user",
                        "content": "Hello, how are you?",
                        "timestamp": "2024-01-01T00:00:00Z"
                    },
                    {
                        "role": "assistant",
                        "content": "I'm doing well, thank you!",
                        "timestamp": "2024-01-01T00:00:01Z"
                    }
                ]
            }
        }

class ConversationListResponse(BaseModel):
    """Response model for conversation list"""
    conversations: List[Conversation]
    total: int
    page: int = 1
    per_page: int = 20

class MessageListResponse(BaseModel):
    """Response model for message list"""
    messages: List[ConversationMessage]
    conversation: Conversation
    total: int
    page: int = 1
    per_page: int = 50