from fastapi import APIRouter, Request, HTTPException, status
import logging
from datetime import datetime
import hmac
import hashlib

from app.config import settings
from app.integrations.supabase_client import supabase_manager
from app.models.common import APIResponse, SuccessResponse
from app.middleware.logging import auth_logger

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/supabase/auth")
async def handle_supabase_auth_webhook(request: Request):
    """
    Handle Supabase Auth webhook events
    """
    try:
        # Verify webhook signature
        signature = request.headers.get("X-Supabase-Signature")
        body = await request.body()
        
        if not verify_supabase_webhook_signature(body, signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )
        
        # Parse event
        event_data = await request.json()
        event_type = event_data.get("type")
        
        logger.info(f"Supabase Auth webhook event: {event_type}", extra={"event_data": event_data})
        
        # Handle different event types
        if event_type == "user.created":
            await handle_user_created(event_data)
        elif event_type == "user.updated":
            await handle_user_updated(event_data)
        elif event_type == "user.deleted":
            await handle_user_deleted(event_data)
        elif event_type == "session.created":
            await handle_session_created(event_data)
        
        return APIResponse(
            success=True,
            data=SuccessResponse(message="Event processed")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Supabase webhook error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process webhook"
        )

def verify_supabase_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify Supabase webhook signature"""
    if not signature:
        return False
    
    # Supabase uses HMAC-SHA256 for webhook signatures
    expected_signature = hmac.new(
        settings.supabase_jwt_secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_signature, signature)

async def handle_user_created(event_data: dict):
    """Handle user created event"""
    user_data = event_data.get("record", {})
    
    logger.info(f"User created: {user_data.get('email')}")
    
    # Create user profile
    await supabase_manager.create_user_profile(
        user_id=user_data["id"],
        email=user_data["email"],
        metadata=user_data.get("user_metadata", {})
    )
    
    # Log auth event
    auth_logger.log_signup(
        email=user_data["email"],
        user_id=user_data["id"]
    )

async def handle_user_updated(event_data: dict):
    """Handle user updated event"""
    user_data = event_data.get("record", {})
    
    logger.info(f"User updated: {user_data.get('email')}")
    
    # Update user profile
    profile_update = {
        "email": user_data["email"],
        "updated_at": datetime.utcnow().isoformat()
    }
    
    # Extract metadata fields
    metadata = user_data.get("user_metadata", {})
    if "full_name" in metadata:
        profile_update["full_name"] = metadata["full_name"]
    if "company" in metadata:
        profile_update["company"] = metadata["company"]
    
    await supabase_manager.execute_query(
        supabase_manager.admin_client.table("profiles")
        .update(profile_update)
        .eq("id", user_data["id"])
    )

async def handle_user_deleted(event_data: dict):
    """Handle user deleted event"""
    user_data = event_data.get("old_record", {})
    
    logger.info(f"User deleted: {user_data.get('email')}")
    
    # Soft delete user data
    # Note: Actual deletion should be handled carefully to maintain data integrity
    
    # Log deletion event
    deletion_log = {
        "event_type": "user_deleted",
        "user_id": user_data["id"],
        "email": user_data["email"],
        "deleted_at": datetime.utcnow().isoformat()
    }
    
    await supabase_manager.execute_query(
        supabase_manager.admin_client.table("deletion_logs").insert(deletion_log)
    )

async def handle_session_created(event_data: dict):
    """Handle session created event"""
    session_data = event_data.get("record", {})
    
    logger.info(f"Session created for user: {session_data.get('user_id')}")
    
    # Log login event
    auth_logger.log_login(
        email=session_data.get("email", ""),
        user_id=session_data["user_id"],
        success=True
    )

@router.post("/supabase/database")
async def handle_supabase_database_webhook(request: Request):
    """
    Handle Supabase Database webhook events (Row Level Security events, etc.)
    """
    try:
        # Verify webhook signature
        signature = request.headers.get("X-Supabase-Signature")
        body = await request.body()
        
        if not verify_supabase_webhook_signature(body, signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )
        
        # Parse event
        event_data = await request.json()
        table = event_data.get("table")
        type = event_data.get("type")  # INSERT, UPDATE, DELETE
        
        logger.info(f"Supabase Database webhook: {table}.{type}", extra={"event_data": event_data})
        
        # Handle specific table events
        if table == "conversations" and type == "INSERT":
            await handle_conversation_created(event_data)
        elif table == "conversation_transcripts" and type == "INSERT":
            await handle_message_created(event_data)
        
        return APIResponse(
            success=True,
            data=SuccessResponse(message="Event processed")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Supabase database webhook error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process webhook"
        )

async def handle_conversation_created(event_data: dict):
    """Handle new conversation created"""
    record = event_data.get("record", {})
    
    logger.info(f"New conversation created: {record.get('id')}")
    
    # Could trigger additional processing here
    # For example, notify agents, update statistics, etc.

async def handle_message_created(event_data: dict):
    """Handle new message created"""
    record = event_data.get("record", {})
    
    logger.info(f"New message in conversation: {record.get('conversation_id')}")
    
    # Update conversation last_interaction_at
    await supabase_manager.execute_query(
        supabase_manager.admin_client.table("conversations")
        .update({"last_interaction_at": datetime.utcnow().isoformat()})
        .eq("id", record["conversation_id"])
    )