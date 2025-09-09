"""Voice transcript streaming endpoint"""
from typing import Optional, AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
import logging
import json
import asyncio
import os
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
    last_timestamp: Optional[str] = None,
    include_citations: bool = False
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
        # Get the platform Supabase client to fetch client details
        platform_url = os.getenv('SUPABASE_URL')
        platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        
        if not platform_url or not platform_key:
            logger.error("Platform Supabase credentials not configured")
            yield f"data: {json.dumps({'error': 'Platform credentials not configured'})}\n\n"
            return
        
        platform_client = create_client(platform_url, platform_key)
        
        # Fetch the client's configuration from the platform DB
        try:
            result = platform_client.table("clients").select("*").eq("id", client_id).single().execute()
            client_data = result.data
            
            if not client_data:
                logger.error(f"Client not found: {client_id}")
                yield f"data: {json.dumps({'error': 'Client not found'})}\n\n"
                return
            
            # Get client's Supabase credentials directly from columns or additional_settings
            client_supabase_url = client_data.get('supabase_url')
            client_supabase_key = client_data.get('supabase_service_role_key')
            
            # Fallback to additional_settings if not in main columns
            if not client_supabase_url or not client_supabase_key:
                additional_settings = client_data.get('additional_settings', {})
                if isinstance(additional_settings, dict):
                    supabase_config = additional_settings.get('supabase', {})
                    if supabase_config:
                        client_supabase_url = client_supabase_url or supabase_config.get('url')
                        client_supabase_key = client_supabase_key or supabase_config.get('service_role_key')
            
            if not client_supabase_url or not client_supabase_key:
                logger.error(f"Client {client_id} has incomplete Supabase configuration")
                logger.debug(f"URL: {bool(client_supabase_url)}, Key: {bool(client_supabase_key)}")
                yield f"data: {json.dumps({'error': 'Incomplete Supabase configuration'})}\n\n"
                return
            
            # Extract project ID from URL for logging
            project_id = client_supabase_url.split('.')[0].split('/')[-1] if client_supabase_url else 'unknown'
            logger.info(f"📌 Connecting to client's Supabase project: {project_id}")
            
        except Exception as e:
            logger.error(f"Failed to fetch client configuration: {e}")
            yield f"data: {json.dumps({'error': f'Failed to fetch client: {str(e)}'})}\n\n"
            return
        
        # Create client-specific Supabase client
        client_supabase = create_client(client_supabase_url, client_supabase_key)
        
        # Keep track of last fetched timestamp
        # Start from epoch if no timestamp provided to capture all existing rows
        last_ts = last_timestamp or "1970-01-01T00:00:00Z"
        
        # Send initial connection success with debug info
        yield f"data: {json.dumps({'type': 'connection', 'status': 'connected', 'client_id': client_id, 'project': project_id})}\n\n"
        logger.info(f"SSE stream connected for conversation_id={conversation_id}, client_id={client_id}, project={project_id}")
        
        heartbeat_counter = 0
        error_count = 0
        max_errors = 5
        
        while True:
            try:
                # Fetch new transcripts since last timestamp from client's DB
                # Explicitly use public schema
                query = client_supabase.table("conversation_transcripts") \
                    .select("*") \
                    .eq("conversation_id", conversation_id) \
                    .gt("created_at", last_ts) \
                    .order("created_at", desc=False)
                
                result = query.execute()
                error_count = 0  # Reset error count on success
                
                if result.data:
                    logger.info(f"📝 Found {len(result.data)} new transcripts for conversation {conversation_id}")
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
                            "created_at": transcript.get("created_at"),
                            "source": transcript.get("source"),
                            "turn_id": transcript.get("turn_id"),
                        }
                        
                        # Include citations if requested and present
                        if include_citations and transcript.get("citations"):
                            event_data["citations"] = transcript.get("citations")
                        
                        # Send SSE event
                        yield f"data: {json.dumps(event_data)}\n\n"
                        
                        logger.debug(f"Streamed transcript: role={transcript.get('role')}, length={len(transcript.get('content', ''))}")
                
                # Send heartbeat every 10 iterations (5 seconds)
                heartbeat_counter += 1
                if heartbeat_counter >= 10:
                    yield f": ping\n\n"
                    heartbeat_counter = 0
                
                # Small delay to prevent overwhelming the database
                await asyncio.sleep(0.5)
                
            except Exception as e:
                error_count += 1
                logger.error(f"Error fetching transcripts (attempt {error_count}/{max_errors}): {e}")
                
                if error_count >= max_errors:
                    yield f"data: {json.dumps({'error': f'Too many errors: {str(e)}'})}\n\n"
                    break
                
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                await asyncio.sleep(2)  # Back off on error
                
    except Exception as e:
        logger.error(f"Error in transcript stream: {e}", exc_info=True)
        yield f"data: {json.dumps({'error': 'Stream error', 'details': str(e)})}\n\n"


@router.get("/stream")
async def stream_voice_transcripts(
    conversation_id: str = Query(..., description="Conversation ID"),
    client_id: str = Query(..., description="Client ID"),
    agent_id: str = Query(..., description="Agent ID"),
    last_timestamp: Optional[str] = Query(None, description="Fetch transcripts after this timestamp"),
    include_citations: bool = Query(True, description="Include citations in the response if available")
):
    """
    Stream voice transcripts via Server-Sent Events (SSE).
    
    This endpoint streams real-time voice transcripts for a given conversation.
    It polls the conversation_transcripts table and sends new entries as SSE events.
    
    The frontend can subscribe to this stream to display real-time transcripts
    alongside the voice conversation.
    """
    
    logger.info(f"Starting transcript stream for conversation: {conversation_id}, client: {client_id}")
    
    return StreamingResponse(
        stream_transcripts(conversation_id, client_id, agent_id, last_timestamp, include_citations),
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
    offset: int = Query(0, description="Offset for pagination"),
    include_citations: bool = Query(False, description="Include citations in the response if available")
):
    """
    Get historical transcripts for a conversation.
    
    Returns the most recent transcripts for a given conversation,
    useful for populating the initial view when a user joins.
    """
    try:
        # Get platform Supabase to fetch client details
        platform_url = os.getenv('SUPABASE_URL')
        platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        
        if not platform_url or not platform_key:
            raise HTTPException(status_code=500, detail="Platform credentials not configured")
        
        platform_client = create_client(platform_url, platform_key)
        
        # Fetch client configuration
        try:
            result = platform_client.table("clients").select("*").eq("id", client_id).single().execute()
            client_data = result.data
            
            if not client_data:
                raise HTTPException(status_code=404, detail="Client not found")
            
            # Get client's Supabase credentials
            client_supabase_url = client_data.get('supabase_url')
            client_supabase_key = client_data.get('supabase_service_role_key')
            
            # Fallback to additional_settings
            if not client_supabase_url or not client_supabase_key:
                additional_settings = client_data.get('additional_settings', {})
                if isinstance(additional_settings, dict):
                    supabase_config = additional_settings.get('supabase', {})
                    if supabase_config:
                        client_supabase_url = client_supabase_url or supabase_config.get('url')
                        client_supabase_key = client_supabase_key or supabase_config.get('service_role_key')
            
            if not client_supabase_url or not client_supabase_key:
                raise HTTPException(status_code=500, detail="Incomplete Supabase configuration")
            
        except Exception as e:
            logger.error(f"Failed to fetch client configuration: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch client: {str(e)}")
        
        # Create client-specific Supabase client
        client_supabase = create_client(client_supabase_url, client_supabase_key)
        
        # Fetch transcript history from client's DB
        result = client_supabase.table("conversation_transcripts") \
            .select("*") \
            .eq("conversation_id", conversation_id) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .offset(offset) \
            .execute()
        
        # Reverse to get chronological order
        transcripts = list(reversed(result.data)) if result.data else []
        
        # Filter out citations if not requested
        if not include_citations:
            for transcript in transcripts:
                transcript.pop('citations', None)
        
        project_id = client_supabase_url.split('.')[0].split('/')[-1] if client_supabase_url else 'unknown'
        
        return {
            "success": True,
            "data": transcripts,
            "count": len(transcripts),
            "client_id": client_id,
            "conversation_id": conversation_id,
            "project": project_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching transcript history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))