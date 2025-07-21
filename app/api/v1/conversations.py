from fastapi import APIRouter, HTTPException, status, Depends, Query
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from app.models.conversation import (
    Conversation, ConversationMessage, ConversationCreateRequest,
    ConversationUpdateRequest, MessageCreateRequest, TranscriptStoreRequest,
    ConversationListResponse, MessageListResponse
)
from app.models.common import APIResponse, SuccessResponse, DeleteResponse
from app.middleware.auth import get_current_auth
from app.integrations.supabase_client import supabase_manager
from app.utils.exceptions import NotFoundError, ValidationError

router = APIRouter()

@router.get("/", response_model=APIResponse[ConversationListResponse])
async def list_conversations(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, pattern="^(active|archived|deleted)$"),
    agent_slug: Optional[str] = None,
    auth=Depends(get_current_auth)
):
    """
    List conversations for the authenticated user/site
    """
    try:
        # Build query
        query = supabase_manager.admin_client.table("conversations").select("*")
        
        # Filter by user if user auth
        if auth.is_user_auth:
            query = query.eq("user_id", auth.user_id)
        
        # Apply filters
        if status:
            query = query.eq("status", status)
        if agent_slug:
            query = query.eq("agent_slug", agent_slug)
        
        # Pagination
        offset = (page - 1) * per_page
        query = query.order("created_at", desc=True).limit(per_page).offset(offset)
        
        # Execute query
        result = await supabase_manager.execute_query(query)
        
        # Get total count (simplified - in production use a count query)
        total = len(result)
        
        return APIResponse(
            success=True,
            data=ConversationListResponse(
                conversations=result,
                total=total,
                page=page,
                per_page=per_page
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/", response_model=APIResponse[Conversation])
async def create_conversation(
    request: ConversationCreateRequest,
    auth=Depends(get_current_auth)
):
    """
    Create a new conversation
    """
    try:
        # Set user_id from auth if not provided
        if not request.user_id and auth.is_user_auth:
            request.user_id = auth.user_id
        
        conversation_data = request.dict(exclude_unset=True)
        conversation_data["created_at"] = datetime.utcnow().isoformat()
        
        # Create conversation
        result = await supabase_manager.create_conversation(conversation_data)
        
        # If initial message provided, add it
        if request.initial_message:
            message_data = {
                "conversation_id": result[0]["id"],
                "user_id": request.user_id,
                "content": request.initial_message,
                "role": "user",
                "sequence": 0,
                "channel": request.channel
            }
            await supabase_manager.add_conversation_message(message_data)
        
        return APIResponse(
            success=True,
            data=result[0]
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/{conversation_id}", response_model=APIResponse[Conversation])
async def get_conversation(
    conversation_id: UUID,
    auth=Depends(get_current_auth)
):
    """
    Get conversation details
    """
    try:
        conversation = await supabase_manager.get_conversation(str(conversation_id))
        
        if not conversation:
            raise NotFoundError("Conversation not found")
        
        # Check access permissions
        if auth.is_user_auth and conversation["user_id"] != str(auth.user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        return APIResponse(
            success=True,
            data=conversation
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.put("/{conversation_id}", response_model=APIResponse[Conversation])
async def update_conversation(
    conversation_id: UUID,
    request: ConversationUpdateRequest,
    auth=Depends(get_current_auth)
):
    """
    Update conversation details
    """
    try:
        # Check if conversation exists
        conversation = await supabase_manager.get_conversation(str(conversation_id))
        if not conversation:
            raise NotFoundError("Conversation not found")
        
        # Check access permissions
        if auth.is_user_auth and conversation["user_id"] != str(auth.user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Update conversation
        update_data = request.dict(exclude_unset=True)
        update_data["updated_at"] = datetime.utcnow().isoformat()
        
        result = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("conversations")
            .update(update_data)
            .eq("id", str(conversation_id))
        )
        
        return APIResponse(
            success=True,
            data=result[0]
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.delete("/{conversation_id}", response_model=APIResponse[DeleteResponse])
async def delete_conversation(
    conversation_id: UUID,
    auth=Depends(get_current_auth)
):
    """
    Delete a conversation (soft delete - sets status to 'deleted')
    """
    try:
        # Check if conversation exists
        conversation = await supabase_manager.get_conversation(str(conversation_id))
        if not conversation:
            raise NotFoundError("Conversation not found")
        
        # Check access permissions
        if auth.is_user_auth and conversation["user_id"] != str(auth.user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Soft delete
        update_data = {
            "status": "deleted",
            "updated_at": datetime.utcnow().isoformat()
        }
        
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("conversations")
            .update(update_data)
            .eq("id", str(conversation_id))
        )
        
        return APIResponse(
            success=True,
            data=DeleteResponse(
                deleted_id=str(conversation_id),
                deleted_at=datetime.utcnow()
            )
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/{conversation_id}/messages", response_model=APIResponse[MessageListResponse])
async def get_conversation_messages(
    conversation_id: UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    auth=Depends(get_current_auth)
):
    """
    Get messages for a conversation
    """
    try:
        # Check conversation exists and user has access
        conversation = await supabase_manager.get_conversation(str(conversation_id))
        if not conversation:
            raise NotFoundError("Conversation not found")
        
        if auth.is_user_auth and conversation["user_id"] != str(auth.user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Get messages
        offset = (page - 1) * per_page
        messages = await supabase_manager.get_conversation_messages(
            str(conversation_id),
            limit=per_page,
            offset=offset
        )
        
        return APIResponse(
            success=True,
            data=MessageListResponse(
                messages=messages,
                conversation=conversation,
                total=len(messages),
                page=page,
                per_page=per_page
            )
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/{conversation_id}/messages", response_model=APIResponse[ConversationMessage])
async def add_message(
    conversation_id: UUID,
    request: MessageCreateRequest,
    auth=Depends(get_current_auth)
):
    """
    Add a message to a conversation
    """
    try:
        # Check conversation exists and user has access
        conversation = await supabase_manager.get_conversation(str(conversation_id))
        if not conversation:
            raise NotFoundError("Conversation not found")
        
        if auth.is_user_auth and conversation["user_id"] != str(auth.user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Create message
        message_data = {
            "conversation_id": str(conversation_id),
            "user_id": conversation["user_id"],
            "content": request.content,
            "role": request.role,
            "session_id": request.session_id,
            "tool_calls": request.tool_calls,
            "tool_results": request.tool_results,
            "metadata": request.metadata,
            "created_at": datetime.utcnow().isoformat()
        }
        
        # Get sequence number
        existing_messages = await supabase_manager.get_conversation_messages(
            str(conversation_id), limit=1
        )
        message_data["sequence"] = len(existing_messages) + 1
        
        # Add message
        result = await supabase_manager.add_conversation_message(message_data)
        
        # Update conversation last interaction
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("conversations")
            .update({"last_interaction_at": datetime.utcnow().isoformat()})
            .eq("id", str(conversation_id))
        )
        
        return APIResponse(
            success=True,
            data=result[0]
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/store-transcript", response_model=APIResponse[SuccessResponse])
async def store_transcript(
    request: TranscriptStoreRequest,
    auth=Depends(get_current_auth)
):
    """
    Store conversation transcript (WordPress plugin compatibility)
    """
    try:
        # Process each message in the transcript
        for idx, message in enumerate(request.transcript):
            message_data = {
                "conversation_id": request.conversation_id,
                "user_id": request.user_id,
                "session_id": request.session_id,
                "content": message.get("content", ""),
                "message": message.get("content", ""),  # Compatibility field
                "role": message.get("role", "user"),
                "sequence": idx,
                "channel": "voice",
                "created_at": message.get("timestamp", datetime.utcnow().isoformat())
            }
            
            await supabase_manager.add_conversation_message(message_data)
        
        # Update conversation
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("conversations")
            .update({
                "last_interaction_at": datetime.utcnow().isoformat(),
                "session_id": request.session_id
            })
            .eq("id", request.conversation_id)
        )
        
        return APIResponse(
            success=True,
            data=SuccessResponse(
                message=f"Stored {len(request.transcript)} messages"
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )