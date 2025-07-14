"""Conversation management endpoints for WordPress integration"""
from typing import Dict, Any, Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, Field
import logging
import json
import uuid

from app.models.wordpress_site import WordPressSite
from app.api.v1.wordpress_sites import validate_wordpress_auth
from app.services.client_service_hybrid import ClientService
import redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations-proxy"])


class ConversationCreate(BaseModel):
    """Request to create a new conversation"""
    agent_slug: str
    user_id: str
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    initial_context: Optional[str] = None


class MessageAdd(BaseModel):
    """Request to add a message to conversation"""
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    timestamp: Optional[datetime] = None


class TranscriptStore(BaseModel):
    """Request to store conversation transcript"""
    messages: List[Dict[str, Any]]
    final_summary: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class ConversationResponse(BaseModel):
    """Conversation response"""
    conversation_id: str
    agent_slug: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any]
    message_count: int = 0


class MessageResponse(BaseModel):
    """Message response"""
    message_id: str
    conversation_id: str
    role: str
    content: str
    timestamp: datetime
    metadata: Dict[str, Any]


# Services will be injected from simple_main.py
redis_client: Optional[redis.Redis] = None
client_service: Optional[ClientService] = None


def get_redis_client() -> redis.Redis:
    """Get Redis client instance"""
    if redis_client is None:
        raise RuntimeError("Redis client not initialized")
    return redis_client


def get_client_service() -> ClientService:
    """Get client service instance"""
    if client_service is None:
        raise RuntimeError("Client service not initialized")
    return client_service


@router.post("/create", response_model=ConversationResponse)
async def create_conversation(
    request: ConversationCreate,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> ConversationResponse:
    """Create a new conversation for WordPress site"""
    logger.info(f"Creating conversation for agent {request.agent_slug}, user {request.user_id}")
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Generate conversation ID
        conversation_id = str(uuid.uuid4())
        
        # Create conversation metadata
        conversation_data = {
            "conversation_id": conversation_id,
            "wordpress_site_id": site.id,
            "wordpress_domain": site.domain,
            "client_id": site.client_id,
            "agent_slug": request.agent_slug,
            "user_id": request.user_id,
            "user_email": request.user_email,
            "user_name": request.user_name,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "metadata": {
                **request.metadata,
                "wordpress_site": site.domain,
                "initial_context": request.initial_context
            },
            "message_count": 0,
            "messages": []
        }
        
        # Store in Redis with TTL of 7 days
        redis = get_redis_client()
        try:
            key = f"conversation:{site.id}:{conversation_id}"
            redis.setex(key, 7 * 24 * 3600, json.dumps(conversation_data))
            
            # Also store in a list for the site
            site_conversations_key = f"site_conversations:{site.id}"
            redis.lpush(site_conversations_key, conversation_id)
            redis.expire(site_conversations_key, 30 * 24 * 3600)  # 30 days
        except RedisError as e:
            logger.error(f"Redis error storing conversation: {e}")
            raise HTTPException(status_code=503, detail="Storage service temporarily unavailable")
        
        response = ConversationResponse(
            conversation_id=conversation_id,
            agent_slug=request.agent_slug,
            user_id=request.user_id,
            created_at=datetime.fromisoformat(conversation_data["created_at"]),
            updated_at=datetime.fromisoformat(conversation_data["updated_at"]),
            metadata=conversation_data["metadata"],
            message_count=0
        )
        
        logger.info(f"Created conversation {conversation_id} for site {site.domain}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{conversation_id}/messages", response_model=MessageResponse)
async def add_message(
    conversation_id: str,
    request: MessageAdd,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> MessageResponse:
    """Add a message to an existing conversation"""
    logger.info(f"Adding {request.role} message to conversation {conversation_id}")
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get conversation from Redis
        redis = get_redis_client()
        key = f"conversation:{site.id}:{conversation_id}"
        conversation_data = redis.get(key)
        
        if not conversation_data:
            raise HTTPException(status_code=404, detail="Conversation not found")
            
        conversation = json.loads(conversation_data)
        
        # Verify the conversation belongs to this site
        if conversation.get("wordpress_site_id") != site.id:
            raise HTTPException(status_code=403, detail="Access denied")
            
        # Create message
        message_id = str(uuid.uuid4())
        message = {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "role": request.role,
            "content": request.content,
            "timestamp": (request.timestamp or datetime.utcnow()).isoformat(),
            "metadata": request.metadata
        }
        
        # Add message to conversation
        conversation["messages"].append(message)
        conversation["message_count"] += 1
        conversation["updated_at"] = datetime.utcnow().isoformat()
        
        # Update in Redis
        redis.setex(key, 7 * 24 * 3600, json.dumps(conversation))
        
        # Store individual message for quick access
        message_key = f"message:{site.id}:{conversation_id}:{message_id}"
        redis.setex(message_key, 7 * 24 * 3600, json.dumps(message))
        
        response = MessageResponse(
            message_id=message_id,
            conversation_id=conversation_id,
            role=request.role,
            content=request.content,
            timestamp=datetime.fromisoformat(message["timestamp"]),
            metadata=message["metadata"]
        )
        
        logger.debug(f"Added message {message_id} to conversation {conversation_id}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{conversation_id}", response_model=Dict[str, Any])
async def get_conversation(
    conversation_id: str,
    include_messages: bool = Query(default=True),
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """Get a conversation by ID"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get conversation from Redis
        redis = get_redis_client()
        key = f"conversation:{site.id}:{conversation_id}"
        conversation_data = redis.get(key)
        
        if not conversation_data:
            raise HTTPException(status_code=404, detail="Conversation not found")
            
        conversation = json.loads(conversation_data)
        
        # Verify the conversation belongs to this site
        if conversation.get("wordpress_site_id") != site.id:
            raise HTTPException(status_code=403, detail="Access denied")
            
        # Optionally exclude messages
        if not include_messages:
            conversation.pop("messages", None)
            
        return conversation
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/", response_model=List[ConversationResponse])
async def list_conversations(
    user_id: Optional[str] = Query(None),
    agent_slug: Optional[str] = Query(None),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> List[ConversationResponse]:
    """List conversations for a WordPress site"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get conversation IDs from Redis
        redis = get_redis_client()
        site_conversations_key = f"site_conversations:{site.id}"
        conversation_ids = redis.lrange(site_conversations_key, offset, offset + limit - 1)
        
        conversations = []
        for conv_id in conversation_ids:
            key = f"conversation:{site.id}:{conv_id}"
            conv_data = redis.get(key)
            if conv_data:
                conv = json.loads(conv_data)
                
                # Apply filters
                if user_id and conv.get("user_id") != user_id:
                    continue
                if agent_slug and conv.get("agent_slug") != agent_slug:
                    continue
                    
                conversations.append(ConversationResponse(
                    conversation_id=conv["conversation_id"],
                    agent_slug=conv["agent_slug"],
                    user_id=conv["user_id"],
                    created_at=datetime.fromisoformat(conv["created_at"]),
                    updated_at=datetime.fromisoformat(conv["updated_at"]),
                    metadata=conv["metadata"],
                    message_count=conv["message_count"]
                ))
                
        return conversations
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing conversations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{conversation_id}/complete", response_model=Dict[str, str])
async def complete_conversation(
    conversation_id: str,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> Dict[str, str]:
    """Mark a conversation as completed"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get conversation from Redis
        redis = get_redis_client()
        key = f"conversation:{site.id}:{conversation_id}"
        conversation_data = redis.get(key)
        
        if not conversation_data:
            raise HTTPException(status_code=404, detail="Conversation not found")
            
        conversation = json.loads(conversation_data)
        
        # Verify the conversation belongs to this site
        if conversation.get("wordpress_site_id") != site.id:
            raise HTTPException(status_code=403, detail="Access denied")
            
        # Update conversation
        conversation["completed_at"] = datetime.utcnow().isoformat()
        conversation["updated_at"] = datetime.utcnow().isoformat()
        conversation["metadata"]["status"] = "completed"
        
        # Update in Redis with shorter TTL (1 day for completed conversations)
        redis.setex(key, 24 * 3600, json.dumps(conversation))
        
        return {
            "status": "completed",
            "conversation_id": conversation_id,
            "completed_at": conversation["completed_at"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{conversation_id}/transcript", response_model=Dict[str, str])
async def store_transcript(
    conversation_id: str,
    request: TranscriptStore,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> Dict[str, str]:
    """Store or update the full transcript of a conversation"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get conversation from Redis
        redis = get_redis_client()
        key = f"conversation:{site.id}:{conversation_id}"
        conversation_data = redis.get(key)
        
        if not conversation_data:
            raise HTTPException(status_code=404, detail="Conversation not found")
            
        conversation = json.loads(conversation_data)
        
        # Verify the conversation belongs to this site
        if conversation.get("wordpress_site_id") != site.id:
            raise HTTPException(status_code=403, detail="Access denied")
            
        # Update conversation with transcript
        conversation["messages"] = request.messages
        conversation["message_count"] = len(request.messages)
        conversation["final_summary"] = request.final_summary
        conversation["updated_at"] = datetime.utcnow().isoformat()
        conversation["metadata"].update(request.metadata)
        
        # Update in Redis
        redis.setex(key, 7 * 24 * 3600, json.dumps(conversation))
        
        # Store transcript separately for quick access
        transcript_key = f"transcript:{site.id}:{conversation_id}"
        transcript_data = {
            "conversation_id": conversation_id,
            "messages": request.messages,
            "final_summary": request.final_summary,
            "stored_at": datetime.utcnow().isoformat()
        }
        redis.setex(transcript_key, 7 * 24 * 3600, json.dumps(transcript_data))
        
        return {
            "status": "stored",
            "conversation_id": conversation_id,
            "message_count": len(request.messages),
            "stored_at": transcript_data["stored_at"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error storing transcript: {e}")
        raise HTTPException(status_code=500, detail=str(e))