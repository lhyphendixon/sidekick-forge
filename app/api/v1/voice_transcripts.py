"""Voice transcript streaming endpoint"""
from typing import Optional, AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
import logging
import json
import asyncio
from datetime import datetime, timedelta
from supabase import Client, create_client
from app.services.client_service_supabase_enhanced import ClientService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-transcripts", tags=["voice-transcripts"])

# Service will be injected from main.py
client_service: Optional[ClientService] = None

def get_client_service() -> ClientService:
    """Get client service instance"""
    if client_service is None:
        raise RuntimeError("Client service not initialized")
    return client_service


async def stream_transcripts(
    conversation_id: str,
    client_id: str,
    agent_id: str,
    last_timestamp: Optional[str] = None
) -> AsyncGenerator[str, None]:
    """
    Stream voice transcripts from the conversation_transcripts table.
    
    Args:
        conversation_id: The conversation ID to stream transcripts for
        client_id: The client ID
        agent_id: The agent ID
        last_timestamp: Optional timestamp to fetch transcripts after
    
    Yields:
        SSE formatted transcript events
    """
    try:
        # Get client service and fetch Supabase credentials
        service = get_client_service()
        client = await service.get_client(client_id)
        
        if not client:
            logger.error(f"Client not found: {client_id}")
            yield f"data: {json.dumps({'error': 'Client not found'})}\n\n"
            return
        
        # Create client-specific Supabase client
        client_supabase = create_client(
            client.settings["supabase"]["url"],
            client.settings["supabase"]["service_role_key"]
        )
        
        # Keep track of last fetched timestamp
        # Start from epoch if no timestamp provided to capture all existing rows
        last_ts = last_timestamp or "1970-01-01T00:00:00Z"
        
        while True:
            try:
                # Fetch new transcripts since last timestamp
                query = client_supabase.table("conversation_transcripts") \
                    .select("*") \
                    .eq("conversation_id", conversation_id) \
                    .eq("source", "voice") \
                    .gt("created_at", last_ts) \
                    .order("created_at", desc=False)
                
                result = query.execute()
                
                if result.data:
                    for transcript in result.data:
                        # Update last timestamp
                        last_ts = transcript.get("created_at", last_ts)
                        
                        # Format transcript event
                        event_data = {
                            "id": transcript.get("id"),
                            "conversation_id": transcript.get("conversation_id"),
                            "role": transcript.get("role"),
                            "content": transcript.get("content"),
                            "transcript": transcript.get("transcript"),
                            "citations": transcript.get("citations"),
                            "created_at": transcript.get("created_at"),
                            "source": transcript.get("source"),
                        }
                        
                        # Send SSE event
                        yield f"data: {json.dumps(event_data)}\n\n"
                        
                        logger.debug(f"Streamed transcript: role={transcript.get('role')}, length={len(transcript.get('content', ''))}")
                
                # Keep connection alive with ping
                yield f": ping\n\n"
                
                # Small delay to prevent overwhelming the database
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error fetching transcripts: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                await asyncio.sleep(2)  # Back off on error
                
    except Exception as e:
        logger.error(f"Error in transcript stream: {e}")
        yield f"data: {json.dumps({'error': 'Stream error'})}\n\n"


@router.get("/stream")
async def stream_voice_transcripts(
    conversation_id: str = Query(..., description="Conversation ID"),
    client_id: str = Query(..., description="Client ID"),
    agent_id: str = Query(..., description="Agent ID"),
    last_timestamp: Optional[str] = Query(None, description="Fetch transcripts after this timestamp")
):
    """
    Stream voice transcripts via Server-Sent Events (SSE).
    
    This endpoint streams real-time voice transcripts for a given conversation.
    It polls the conversation_transcripts table and sends new entries as SSE events.
    
    The frontend can subscribe to this stream to display real-time transcripts
    alongside the voice conversation.
    """
    
    logger.info(f"Starting transcript stream for conversation: {conversation_id}")
    
    return StreamingResponse(
        stream_transcripts(conversation_id, client_id, agent_id, last_timestamp),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        }
    )


@router.get("/history")
async def get_transcript_history(
    conversation_id: str = Query(..., description="Conversation ID"),
    client_id: str = Query(..., description="Client ID"),
    limit: int = Query(50, description="Number of transcripts to fetch"),
    offset: int = Query(0, description="Offset for pagination")
):
    """
    Get historical transcripts for a conversation.
    
    Returns the most recent transcripts for a given conversation,
    useful for populating the initial view when a user joins.
    """
    try:
        # Get client service to fetch Supabase credentials
        service = get_client_service()
        client = await service.get_client(client_id)
        
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        
        # Create client-specific Supabase client
        client_supabase = create_client(
            client.settings["supabase"]["url"],
            client.settings["supabase"]["service_role_key"]
        )
        
        # Fetch transcript history
        query = client_supabase.table("conversation_transcripts") \
            .select("*") \
            .eq("conversation_id", conversation_id) \
            .eq("source", "voice") \
            .order("created_at", desc=True) \
            .limit(limit) \
            .offset(offset)
        
        result = query.execute()
        
        # Reverse to get chronological order
        transcripts = list(reversed(result.data)) if result.data else []
        
        return {
            "conversation_id": conversation_id,
            "transcripts": transcripts,
            "count": len(transcripts),
            "offset": offset,
            "limit": limit
        }
        
    except Exception as e:
        logger.error(f"Error fetching transcript history: {e}")
        raise HTTPException(status_code=500, detail=str(e))