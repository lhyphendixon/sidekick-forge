"""Text chat endpoints for WordPress integration"""
from typing import Dict, Any, Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
import logging
import json
import uuid
import asyncio

from app.models.wordpress_site import WordPressSite
from app.api.v1.wordpress_sites import validate_wordpress_auth
from app.services.client_service_hybrid import ClientService
from app.services.agent_service import AgentService
import redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/text-chat", tags=["text-chat-proxy"])


class TextChatRequest(BaseModel):
    """Text chat request"""
    message: str
    conversation_id: Optional[str] = None
    agent_slug: str
    user_id: str
    user_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)


class TextChatResponse(BaseModel):
    """Text chat response"""
    response: str
    conversation_id: str
    message_id: str
    agent_response_id: str
    metadata: Dict[str, Any]


class StreamingTextChatRequest(BaseModel):
    """Streaming text chat request"""
    message: str
    conversation_id: str
    agent_slug: str
    stream: bool = True


# Services will be injected from simple_main.py
redis_client: Optional[redis.Redis] = None
client_service: Optional[ClientService] = None
agent_service: Optional[AgentService] = None


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


def get_agent_service() -> AgentService:
    """Get agent service instance"""  
    if agent_service is None:
        raise RuntimeError("Agent service not initialized")
    return agent_service


@router.post("/send", response_model=TextChatResponse)
async def send_text_message(
    request: TextChatRequest,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> TextChatResponse:
    """Send a text message and get AI response"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get or create conversation
        redis = get_redis_client()
        conversation_id = request.conversation_id
        
        if not conversation_id:
            # Create new conversation
            conversation_id = str(uuid.uuid4())
            conversation_data = {
                "conversation_id": conversation_id,
                "wordpress_site_id": site.id,
                "wordpress_domain": site.domain,
                "client_id": site.client_id,
                "agent_slug": request.agent_slug,
                "user_id": request.user_id,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
                "metadata": {
                    **request.user_metadata,
                    "mode": "text",
                    "wordpress_site": site.domain
                },
                "messages": []
            }
            
            # Store in Redis with error handling
            conv_key = f"conversation:{site.id}:{conversation_id}"
            try:
                redis.setex(conv_key, 7 * 24 * 3600, json.dumps(conversation_data))
                
                # Add to site conversations
                site_conversations_key = f"site_conversations:{site.id}"
                redis.lpush(site_conversations_key, conversation_id)
            except RedisError as e:
                logger.error(f"Redis error creating conversation: {e}")
                raise HTTPException(status_code=503, detail="Storage service temporarily unavailable")
        else:
            # Get existing conversation with error handling
            conv_key = f"conversation:{site.id}:{conversation_id}"
            try:
                conv_data = redis.get(conv_key)
            except RedisError as e:
                logger.error(f"Redis error fetching conversation: {e}")
                raise HTTPException(status_code=503, detail="Storage service temporarily unavailable")
                
            if not conv_data:
                raise HTTPException(status_code=404, detail="Conversation not found")
            conversation_data = json.loads(conv_data)
            
            # Verify conversation belongs to this site
            if conversation_data.get("wordpress_site_id") != site.id:
                raise HTTPException(status_code=403, detail="Access denied")
        
        # Add user message
        user_message_id = str(uuid.uuid4())
        user_message = {
            "message_id": user_message_id,
            "role": "user",
            "content": request.message,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": request.user_metadata
        }
        conversation_data["messages"].append(user_message)
        
        # Get agent and client
        agent_svc = get_agent_service()
        agents = await agent_svc.get_client_agents(site.client_id)
        agent = next((a for a in agents if a.slug == request.agent_slug), None)
        
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent '{request.agent_slug}' not found")
            
        # Get client for API keys
        client_svc = get_client_service()
        client = await client_svc.get_client(site.client_id)
        
        # Prepare context from conversation history
        messages_context = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in conversation_data["messages"][-10:]  # Last 10 messages
        ]
        
        # Add system prompt
        if agent.system_prompt:
            messages_context.insert(0, {"role": "system", "content": agent.system_prompt})
        
        # TODO: In production, this would:
        # 1. Call the actual LLM API based on agent configuration
        # 2. Handle streaming responses
        # 3. Apply RAG context if enabled
        # 4. Execute tools if configured
        
        # For now, generate a mock response
        ai_response = f"I understand you said: '{request.message}'. As {agent.name}, I'm here to help you. This is a demo response from the thin-client backend."
        
        # Add AI response to conversation
        ai_message_id = str(uuid.uuid4())
        ai_message = {
            "message_id": ai_message_id,
            "role": "assistant",
            "content": ai_response,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": {
                "agent_slug": agent.slug,
                "model": agent.model_provider or "mock"
            }
        }
        conversation_data["messages"].append(ai_message)
        conversation_data["updated_at"] = datetime.utcnow().isoformat()
        
        # Update conversation in Redis with error handling
        try:
            redis.setex(conv_key, 7 * 24 * 3600, json.dumps(conversation_data))
        except RedisError as e:
            logger.error(f"Redis error updating conversation: {e}")
            # Don't fail the response, just log the error
        
        return TextChatResponse(
            response=ai_response,
            conversation_id=conversation_id,
            message_id=user_message_id,
            agent_response_id=ai_message_id,
            metadata={
                "agent_name": agent.name,
                "model": agent.model_provider or "mock",
                "conversation_length": len(conversation_data["messages"])
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in text chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream")
async def stream_text_message(
    request: StreamingTextChatRequest,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
):
    """Stream text chat responses (SSE endpoint)"""
    from fastapi.responses import StreamingResponse
    
    async def generate():
        try:
            # Validate WordPress auth
            site = await validate_wordpress_auth(authorization, x_api_key)
            
            # Get conversation with error handling
            redis = get_redis_client()
            conv_key = f"conversation:{site.id}:{request.conversation_id}"
            
            try:
                conv_data = redis.get(conv_key)
            except RedisError as e:
                logger.error(f"Redis error in streaming: {e}")
                yield f"data: {json.dumps({'error': 'Storage service temporarily unavailable'})}\n\n"
                return
            
            if not conv_data:
                yield f"data: {json.dumps({'error': 'Conversation not found'})}\n\n"
                return
                
            conversation = json.loads(conv_data)
            
            # Verify access
            if conversation.get("wordpress_site_id") != site.id:
                yield f"data: {json.dumps({'error': 'Access denied'})}\n\n"
                return
            
            # Mock streaming response
            response_chunks = [
                "I understand",
                " you're asking about",
                f" '{request.message}'.",
                " Let me help you",
                " with that.",
                " This is a streaming",
                " response from the",
                " thin-client backend."
            ]
            
            # Stream chunks
            for i, chunk in enumerate(response_chunks):
                await asyncio.sleep(0.1)  # Simulate LLM delay
                yield f"data: {json.dumps({'chunk': chunk, 'index': i})}\n\n"
                
            # Send completion
            yield f"data: {json.dumps({'done': True, 'message': ' '.join(response_chunks)})}\n\n"
            
        except Exception as e:
            logger.error(f"Error in streaming: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )


@router.post("/context/webhook")
async def context_webhook(
    conversation_id: str,
    query: str,
    agent_slug: str,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """Webhook endpoint for agents to fetch context during conversations"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get conversation to verify access with error handling
        redis = get_redis_client()
        conv_key = f"conversation:{site.id}:{conversation_id}"
        
        try:
            conv_data = redis.get(conv_key)
        except RedisError as e:
            logger.error(f"Redis error in webhook: {e}")
            raise HTTPException(status_code=503, detail="Storage service temporarily unavailable")
        
        if not conv_data:
            raise HTTPException(status_code=404, detail="Conversation not found")
            
        conversation = json.loads(conv_data)
        
        # Verify access
        if conversation.get("wordpress_site_id") != site.id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # In production, this would:
        # 1. Search RAG documents for the agent
        # 2. Query any external APIs configured
        # 3. Fetch user-specific context
        # 4. Apply any custom business logic
        
        # Mock context response
        context = {
            "conversation_id": conversation_id,
            "query": query,
            "agent_slug": agent_slug,
            "context": {
                "user_info": {
                    "user_id": conversation.get("user_id"),
                    "site": site.domain,
                    "previous_interactions": len(conversation.get("messages", []))
                },
                "relevant_info": [
                    "This is contextual information from the RAG system",
                    "User is on WordPress site: " + site.domain,
                    "Agent " + agent_slug + " is configured for this context"
                ],
                "suggested_responses": [
                    "I can help you with that.",
                    "Let me find more information.",
                    "Could you provide more details?"
                ]
            },
            "metadata": {
                "source": "wordpress_thin_client",
                "timestamp": datetime.utcnow().isoformat()
            }
        }
        
        return context
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in context webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))