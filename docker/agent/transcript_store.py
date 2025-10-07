"""
Transcript Store Module for Agent Container

Handles storage of conversation transcripts with citations support.
Provides a unified interface for storing voice conversation turns.
"""
import logging
import uuid
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime
from supabase import Client

logger = logging.getLogger(__name__)


async def store_turn(
    turn_data: Dict[str, Any],
    supabase_client: Client
) -> Dict[str, Any]:
    """
    Store a complete conversation turn (user + assistant messages) with citations.
    
    Args:
        turn_data: Dictionary containing:
            - conversation_id: str
            - session_id: str (optional, defaults to conversation_id)
            - agent_id: str
            - user_id: str
            - user_text: str
            - assistant_text: str
            - citations: List[Dict] (optional, for assistant message)
            - metadata: Dict (optional, additional metadata)
        supabase_client: Supabase client for database operations
    
    Returns:
        Dictionary with:
            - turn_id: The UUID linking the two messages
            - user_row_id: ID of inserted user message
            - assistant_row_id: ID of inserted assistant message
            - success: Boolean indicating success
            - error: Error message if failed
    """
    logger.info("🔄 store_turn called!")
    logger.info(f"   User text: {turn_data.get('user_text', '')[:100]}...")
    logger.info(f"   Assistant text: {turn_data.get('assistant_text', '')[:100]}...")
    logger.info(f"   Has supabase_client: {supabase_client is not None}")
    
    start_time = datetime.now()
    
    # Generate a single turn_id for both messages
    turn_id = str(uuid.uuid4())
    
    # Extract required fields
    conversation_id = turn_data.get('conversation_id')
    session_id = turn_data.get('session_id') or conversation_id
    agent_id = turn_data.get('agent_id')
    user_id = turn_data.get('user_id')
    user_text = turn_data.get('user_text', '')
    assistant_text = turn_data.get('assistant_text', '')
    citations = turn_data.get('citations', [])
    metadata = turn_data.get('metadata', {})
    if not isinstance(metadata, dict):
        metadata = {}
    else:
        metadata = dict(metadata)  # avoid mutating upstream state

    # Normalize user identifiers to UUIDs (Supabase schema requires uuid)
    original_user_id = user_id
    normalization_details = None
    try:
        if user_id:
            user_id = str(uuid.UUID(str(user_id)))
        else:
            raise ValueError("empty user_id")
    except Exception as normalize_exc:
        user_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(original_user_id))) if original_user_id else str(uuid.uuid4())
        normalization_details = {
            "original": original_user_id,
            "normalized": user_id,
            "strategy": "uuid5" if original_user_id else "generated_uuid4",
            "error": str(normalize_exc)
        }
        logger.warning(
            "Normalizing non-UUID user_id for transcript storage (agent container)",
            extra={"original_user_id": original_user_id, "normalized_user_id": user_id}
        )

    if normalization_details:
        metadata.setdefault("normalization", {})["user_id"] = normalization_details
    
    # Validate required fields
    if not conversation_id:
        error_msg = "Missing required field: conversation_id"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}
    
    result = {
        "turn_id": turn_id,
        "success": False
    }
    
    try:
        timestamp = datetime.utcnow().isoformat()
        
        # Ensure a conversation record exists locally (tenant DB enforces FK)
        if conversation_id:
            try:
                existing = await asyncio.to_thread(
                    lambda: supabase_client.table("conversations").select("id").eq("id", conversation_id).limit(1).execute()
                )
                if not existing.data:
                    await asyncio.to_thread(
                        lambda: supabase_client.table("conversations").insert(
                            {
                                "id": conversation_id,
                                "agent_id": agent_id,
                                "user_id": user_id,
                                "channel": metadata.get("channel", "voice"),
                                "created_at": timestamp,
                                "updated_at": timestamp,
                            }
                        ).execute()
                    )
            except Exception as conversation_exc:
                logger.warning(f"Failed to ensure conversation {conversation_id} exists: {conversation_exc}")

        # Prepare user message row
        user_row = {
            "conversation_id": conversation_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "role": "user",
            "content": user_text,
            "transcript": user_text,
            "turn_id": turn_id,
            "created_at": timestamp,
            "metadata": metadata
        }
        
        # Add source field conditionally (in case column doesn't exist yet)
        # This makes the code backward compatible
        user_row["source"] = "voice"
        
        # Prepare assistant message row
        assistant_row = {
            "conversation_id": conversation_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "role": "assistant",
            "content": assistant_text,
            "transcript": assistant_text,
            "turn_id": turn_id,
            "created_at": timestamp,
            "metadata": metadata
        }
        
        # Add source field conditionally
        assistant_row["source"] = "voice"
        
        # Add citations to assistant row if available
        if citations:
            assistant_row["citations"] = citations
            logger.info(f"📚 Including {len(citations)} citations in assistant transcript")
        
        # Store both rows using asyncio.to_thread for sync Supabase client
        logger.info(f"📤 Attempting to insert user row for turn_id={turn_id}")
        user_result = await asyncio.to_thread(
            lambda: supabase_client.table("conversation_transcripts").insert(user_row).execute()
        )
        logger.info(f"✅ User row inserted successfully")
        
        logger.info(f"📤 Attempting to insert assistant row for turn_id={turn_id}")
        assistant_result = await asyncio.to_thread(
            lambda: supabase_client.table("conversation_transcripts").insert(assistant_row).execute()
        )
        logger.info(f"✅ Assistant row inserted successfully")
        
        # Extract row IDs if available
        if user_result.data and len(user_result.data) > 0:
            result["user_row_id"] = user_result.data[0].get("id")
        
        if assistant_result.data and len(assistant_result.data) > 0:
            result["assistant_row_id"] = assistant_result.data[0].get("id")
        
        # Calculate processing time
        processing_time_ms = (datetime.now() - start_time).total_seconds() * 1000
        
        # Log success with observability details
        logger.info(
            f"✅ Stored conversation turn | "
            f"turn_id={turn_id} | "
            f"conversation_id={conversation_id} | "
            f"user_length={len(user_text)} | "
            f"assistant_length={len(assistant_text)} | "
            f"citations_count={len(citations)} | "
            f"processing_time_ms={processing_time_ms:.2f}"
        )
        
        result["success"] = True
        result["processing_time_ms"] = processing_time_ms
        
        # Best-effort embedding generation (if embedder is provided)
        if 'embedder' in turn_data and turn_data['embedder']:
            await generate_embeddings_best_effort(
                turn_data['embedder'],
                supabase_client,
                user_text,
                assistant_text,
                result.get("user_row_id"),
                result.get("assistant_row_id")
            )
        
        return result
        
    except Exception as e:
        error_msg = f"Failed to store conversation turn: {e}"
        logger.error(error_msg)
        result["error"] = str(e)
        return result


async def generate_embeddings_best_effort(
    embedder,
    supabase_client: Client,
    user_text: str,
    assistant_text: str,
    user_row_id: Optional[str],
    assistant_row_id: Optional[str]
) -> None:
    """
    Generate and store embeddings for transcripts (best-effort, non-blocking).
    
    Failures in embedding generation do not affect transcript storage.
    """
    # List of trivial messages to skip
    trivial_messages = {
        "ok", "okay", "yes", "no", "thanks", "thank you", 
        "hello", "hi", "bye", "goodbye", "sure", "alright"
    }
    
    try:
        # Generate embedding for user message if non-trivial
        if user_row_id and len(user_text) >= 8 and user_text.lower() not in trivial_messages:
            try:
                user_embedding = await embedder.create_embedding(user_text)
                await asyncio.to_thread(
                    lambda: supabase_client.table("conversation_transcripts")
                    .update({"embeddings": user_embedding})
                    .eq("id", user_row_id)
                    .execute()
                )
                logger.debug(f"Generated embedding for user message (id={user_row_id})")
            except Exception as e:
                logger.warning(f"Failed to generate user embedding: {e}")
        
        # Generate embedding for assistant message if non-trivial
        if assistant_row_id and len(assistant_text) >= 8 and assistant_text.lower() not in trivial_messages:
            try:
                assistant_embedding = await embedder.create_embedding(assistant_text)
                await asyncio.to_thread(
                    lambda: supabase_client.table("conversation_transcripts")
                    .update({"embeddings": assistant_embedding})
                    .eq("id", assistant_row_id)
                    .execute()
                )
                logger.debug(f"Generated embedding for assistant message (id={assistant_row_id})")
            except Exception as e:
                logger.warning(f"Failed to generate assistant embedding: {e}")
                
    except Exception as e:
        logger.warning(f"Failed during embedding generation process: {e}")
