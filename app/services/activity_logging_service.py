"""
Activity Logging Service for Sidekick Forge Platform

This service handles logging of all system activities to the activity_log table
in the platform database for monitoring and audit purposes.
"""
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime
import logging

from app.integrations.supabase_client import supabase_manager

logger = logging.getLogger(__name__)


class ActivityLoggingService:
    """Service for logging activities to the platform activity_log table"""

    # Activity types
    SIDEKICK_CREATED = "sidekick_created"
    SIDEKICK_UPDATED = "sidekick_updated"
    SIDEKICK_DELETED = "sidekick_deleted"
    ABILITY_RUN = "ability_run"
    ABILITY_COMPLETED = "ability_completed"
    ABILITY_FAILED = "ability_failed"
    CONVERSATION_STARTED = "conversation_started"
    CONVERSATION_ENDED = "conversation_ended"
    MESSAGE_SENT = "message_sent"
    DOCUMENT_UPLOADED = "document_uploaded"
    DOCUMENT_PROCESSED = "document_processed"
    VOICE_CALL_STARTED = "voice_call_started"
    VOICE_CALL_ENDED = "voice_call_ended"
    CLIENT_CREATED = "client_created"
    CLIENT_UPDATED = "client_updated"

    # Actions
    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_RUN = "run"
    ACTION_START = "start"
    ACTION_END = "end"
    ACTION_SEND = "send"
    ACTION_UPLOAD = "upload"
    ACTION_PROCESS = "process"

    def __init__(self):
        self.admin_client = supabase_manager.admin_client

    async def log_activity(
        self,
        activity_type: str,
        action: str,
        client_id: Optional[UUID] = None,
        agent_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        resource_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        status: str = "success",
        error_message: Optional[str] = None
    ) -> Optional[str]:
        """
        Log an activity to the activity_log table.

        Args:
            activity_type: Type of activity (e.g., sidekick_created, ability_run)
            action: Action taken (e.g., create, update, delete, run)
            client_id: ID of the client this activity belongs to
            agent_id: ID of the agent involved (if applicable)
            user_id: ID of the user who triggered the action (if applicable)
            resource_type: Type of resource affected (e.g., sidekick, ability, document)
            resource_id: ID of the affected resource
            resource_name: Human-readable name of the resource
            details: Additional details as a JSON object
            status: Status of the activity (success, failed, pending)
            error_message: Error message if status is failed

        Returns:
            The ID of the created activity log entry, or None if failed
        """
        try:
            data = {
                "activity_type": activity_type,
                "action": action,
                "status": status,
                "details": details or {}
            }

            if client_id:
                data["client_id"] = str(client_id)
            if agent_id:
                data["agent_id"] = str(agent_id)
            if user_id:
                data["user_id"] = str(user_id)
            if resource_type:
                data["resource_type"] = resource_type
            if resource_id:
                data["resource_id"] = str(resource_id)
            if resource_name:
                data["resource_name"] = resource_name
            if error_message:
                data["error_message"] = error_message

            result = self.admin_client.table("activity_log").insert(data).execute()

            if result.data:
                logger.debug(f"Logged activity: {activity_type}/{action} for client {client_id}")
                return result.data[0].get("id")

            return None

        except Exception as e:
            # Log errors but don't fail the main operation
            logger.warning(f"Failed to log activity: {e}")
            return None

    # Convenience methods for common activities

    async def log_sidekick_created(
        self,
        client_id: UUID,
        agent_id: UUID,
        agent_name: str,
        agent_slug: str,
        user_id: Optional[UUID] = None
    ) -> Optional[str]:
        """Log sidekick creation"""
        return await self.log_activity(
            activity_type=self.SIDEKICK_CREATED,
            action=self.ACTION_CREATE,
            client_id=client_id,
            agent_id=agent_id,
            user_id=user_id,
            resource_type="sidekick",
            resource_id=str(agent_id),
            resource_name=agent_name,
            details={"slug": agent_slug}
        )

    async def log_sidekick_updated(
        self,
        client_id: UUID,
        agent_id: UUID,
        agent_name: str,
        changes: Optional[Dict[str, Any]] = None,
        user_id: Optional[UUID] = None
    ) -> Optional[str]:
        """Log sidekick update"""
        return await self.log_activity(
            activity_type=self.SIDEKICK_UPDATED,
            action=self.ACTION_UPDATE,
            client_id=client_id,
            agent_id=agent_id,
            user_id=user_id,
            resource_type="sidekick",
            resource_id=str(agent_id),
            resource_name=agent_name,
            details={"changes": changes} if changes else {}
        )

    async def log_sidekick_deleted(
        self,
        client_id: UUID,
        agent_id: UUID,
        agent_name: str,
        user_id: Optional[UUID] = None
    ) -> Optional[str]:
        """Log sidekick deletion"""
        return await self.log_activity(
            activity_type=self.SIDEKICK_DELETED,
            action=self.ACTION_DELETE,
            client_id=client_id,
            agent_id=agent_id,
            user_id=user_id,
            resource_type="sidekick",
            resource_id=str(agent_id),
            resource_name=agent_name
        )

    async def log_ability_run(
        self,
        client_id: UUID,
        agent_id: UUID,
        ability_name: str,
        ability_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Log ability execution start"""
        return await self.log_activity(
            activity_type=self.ABILITY_RUN,
            action=self.ACTION_RUN,
            client_id=client_id,
            agent_id=agent_id,
            resource_type="ability",
            resource_id=ability_id,
            resource_name=ability_name,
            details=details,
            status="pending"
        )

    async def log_ability_completed(
        self,
        client_id: UUID,
        agent_id: UUID,
        ability_name: str,
        ability_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
        result_summary: Optional[str] = None
    ) -> Optional[str]:
        """Log ability execution completion"""
        return await self.log_activity(
            activity_type=self.ABILITY_COMPLETED,
            action=self.ACTION_END,
            client_id=client_id,
            agent_id=agent_id,
            resource_type="ability",
            resource_id=ability_id,
            resource_name=ability_name,
            details={
                "duration_ms": duration_ms,
                "result_summary": result_summary
            }
        )

    async def log_ability_failed(
        self,
        client_id: UUID,
        agent_id: UUID,
        ability_name: str,
        error: str,
        ability_id: Optional[str] = None
    ) -> Optional[str]:
        """Log ability execution failure"""
        return await self.log_activity(
            activity_type=self.ABILITY_FAILED,
            action=self.ACTION_END,
            client_id=client_id,
            agent_id=agent_id,
            resource_type="ability",
            resource_id=ability_id,
            resource_name=ability_name,
            status="failed",
            error_message=error
        )

    async def log_conversation_started(
        self,
        client_id: UUID,
        agent_id: UUID,
        agent_name: str,
        conversation_id: Optional[str] = None,
        channel: str = "web"
    ) -> Optional[str]:
        """Log conversation start"""
        return await self.log_activity(
            activity_type=self.CONVERSATION_STARTED,
            action=self.ACTION_START,
            client_id=client_id,
            agent_id=agent_id,
            resource_type="conversation",
            resource_id=conversation_id,
            resource_name=f"Chat with {agent_name}",
            details={"channel": channel}
        )

    async def log_conversation_ended(
        self,
        client_id: UUID,
        agent_id: UUID,
        agent_name: str,
        conversation_id: Optional[str] = None,
        message_count: int = 0,
        duration_seconds: Optional[int] = None
    ) -> Optional[str]:
        """Log conversation end"""
        return await self.log_activity(
            activity_type=self.CONVERSATION_ENDED,
            action=self.ACTION_END,
            client_id=client_id,
            agent_id=agent_id,
            resource_type="conversation",
            resource_id=conversation_id,
            resource_name=f"Chat with {agent_name}",
            details={
                "message_count": message_count,
                "duration_seconds": duration_seconds
            }
        )

    async def log_document_uploaded(
        self,
        client_id: UUID,
        agent_id: UUID,
        document_name: str,
        document_id: Optional[str] = None,
        file_type: Optional[str] = None,
        file_size: Optional[int] = None
    ) -> Optional[str]:
        """Log document upload"""
        return await self.log_activity(
            activity_type=self.DOCUMENT_UPLOADED,
            action=self.ACTION_UPLOAD,
            client_id=client_id,
            agent_id=agent_id,
            resource_type="document",
            resource_id=document_id,
            resource_name=document_name,
            details={
                "file_type": file_type,
                "file_size": file_size
            }
        )

    async def log_document_processed(
        self,
        client_id: UUID,
        agent_id: UUID,
        document_name: str,
        document_id: Optional[str] = None,
        chunks_created: int = 0
    ) -> Optional[str]:
        """Log document processing completion"""
        return await self.log_activity(
            activity_type=self.DOCUMENT_PROCESSED,
            action=self.ACTION_PROCESS,
            client_id=client_id,
            agent_id=agent_id,
            resource_type="document",
            resource_id=document_id,
            resource_name=document_name,
            details={"chunks_created": chunks_created}
        )

    async def log_voice_call_started(
        self,
        client_id: UUID,
        agent_id: UUID,
        agent_name: str,
        room_name: Optional[str] = None
    ) -> Optional[str]:
        """Log voice call start"""
        return await self.log_activity(
            activity_type=self.VOICE_CALL_STARTED,
            action=self.ACTION_START,
            client_id=client_id,
            agent_id=agent_id,
            resource_type="voice_call",
            resource_id=room_name,
            resource_name=f"Voice call with {agent_name}",
            details={"room_name": room_name}
        )

    async def log_voice_call_ended(
        self,
        client_id: UUID,
        agent_id: UUID,
        agent_name: str,
        room_name: Optional[str] = None,
        duration_seconds: Optional[int] = None
    ) -> Optional[str]:
        """Log voice call end"""
        return await self.log_activity(
            activity_type=self.VOICE_CALL_ENDED,
            action=self.ACTION_END,
            client_id=client_id,
            agent_id=agent_id,
            resource_type="voice_call",
            resource_id=room_name,
            resource_name=f"Voice call with {agent_name}",
            details={
                "room_name": room_name,
                "duration_seconds": duration_seconds
            }
        )


# Global instance for easy import
activity_logger = ActivityLoggingService()
