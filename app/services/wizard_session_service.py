"""
Wizard Session Service - Manages onboarding wizard sessions and related data.

Provides CRUD operations for wizard sessions, generated avatars, and pending documents.
"""
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import uuid

from app.integrations.supabase_client import supabase_manager
from app.utils.exceptions import DatabaseError

logger = logging.getLogger(__name__)


class WizardSessionService:
    """Service for managing sidekick onboarding wizard sessions."""

    # Wizard steps configuration
    TOTAL_STEPS = 9
    STEP_NAMES = {
        1: "name",
        2: "personality",
        3: "voice",
        4: "avatar",
        5: "abilities",      # NEW - Enable built-in tools
        6: "knowledge",      # was 5
        7: "config",         # was 6
        8: "api_keys",       # was 7
        9: "launch"          # was 8
    }

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    async def _get_admin_client(self):
        """Get the admin Supabase client, initializing if needed."""
        if not supabase_manager._initialized:
            await supabase_manager.initialize()
        return supabase_manager.admin_client

    # ============================================================
    # Session Management
    # ============================================================

    async def create_session(
        self,
        user_id: str,
        client_id: str
    ) -> Dict[str, Any]:
        """Create a new wizard session for a user."""
        client = await self._get_admin_client()

        session_data = {
            "user_id": user_id,
            "client_id": client_id,
            "current_step": 1,
            "completed_steps": [],
            "status": "in_progress",
            "step_data": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        try:
            result = client.table("sidekick_wizard_sessions").insert(session_data).execute()
            if result.data:
                self.logger.info(f"Created wizard session {result.data[0]['id']} for user {user_id}")
                return result.data[0]
            raise DatabaseError("Failed to create wizard session")
        except Exception as e:
            self.logger.error(f"Error creating wizard session: {e}")
            raise DatabaseError(f"Failed to create wizard session: {str(e)}")

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get a wizard session by ID."""
        client = await self._get_admin_client()

        try:
            result = client.table("sidekick_wizard_sessions").select("*").eq("id", session_id).single().execute()
            return result.data
        except Exception as e:
            self.logger.error(f"Error getting wizard session {session_id}: {e}")
            return None

    async def get_active_session(
        self,
        user_id: str,
        client_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get the most recent in-progress session for a user."""
        client = await self._get_admin_client()

        try:
            result = client.table("sidekick_wizard_sessions").select("*").eq(
                "user_id", user_id
            ).eq(
                "client_id", client_id
            ).eq(
                "status", "in_progress"
            ).order(
                "updated_at", desc=True
            ).limit(1).execute()

            return result.data[0] if result.data else None
        except Exception as e:
            self.logger.error(f"Error getting active session for user {user_id}: {e}")
            return None

    async def get_or_create_session(
        self,
        user_id: str,
        client_id: str
    ) -> Dict[str, Any]:
        """Get existing active session or create a new one."""
        existing = await self.get_active_session(user_id, client_id)
        if existing:
            return existing
        return await self.create_session(user_id, client_id)

    async def update_session(
        self,
        session_id: str,
        updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a wizard session."""
        client = await self._get_admin_client()

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            result = client.table("sidekick_wizard_sessions").update(updates).eq("id", session_id).execute()
            if result.data:
                return result.data[0]
            return None
        except Exception as e:
            self.logger.error(f"Error updating wizard session {session_id}: {e}")
            raise DatabaseError(f"Failed to update wizard session: {str(e)}")

    async def update_step(
        self,
        session_id: str,
        step_number: int,
        step_data: Dict[str, Any],
        advance: bool = False
    ) -> Dict[str, Any]:
        """Update step data and optionally advance to next step."""
        session = await self.get_session(session_id)
        if not session:
            raise DatabaseError("Session not found")

        # Merge new step data with existing
        current_data = session.get("step_data", {})
        current_data.update(step_data)

        # Prepare updates
        updates = {
            "step_data": current_data,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        # Handle step advancement
        if advance:
            completed_steps = session.get("completed_steps", [])
            if step_number not in completed_steps:
                completed_steps.append(step_number)
            updates["completed_steps"] = completed_steps
            updates["current_step"] = min(step_number + 1, self.TOTAL_STEPS)

        result = await self.update_session(session_id, updates)
        return {
            "success": True,
            "current_step": result.get("current_step"),
            "step_data": result.get("step_data"),
            "completed_steps": result.get("completed_steps")
        }

    async def complete_session(
        self,
        session_id: str,
        agent_id: str
    ) -> Dict[str, Any]:
        """Mark a wizard session as completed."""
        updates = {
            "status": "completed",
            "agent_id": agent_id,
            "completed_at": datetime.now(timezone.utc).isoformat()
        }

        result = await self.update_session(session_id, updates)
        self.logger.info(f"Wizard session {session_id} completed with agent {agent_id}")
        return result

    async def abandon_session(self, session_id: str) -> bool:
        """Mark a wizard session as abandoned."""
        try:
            await self.update_session(session_id, {"status": "abandoned"})
            self.logger.info(f"Wizard session {session_id} abandoned")
            return True
        except Exception as e:
            self.logger.error(f"Error abandoning session {session_id}: {e}")
            return False

    async def delete_session(self, session_id: str) -> bool:
        """Delete a wizard session (cascades to avatars and pending docs)."""
        client = await self._get_admin_client()

        try:
            client.table("sidekick_wizard_sessions").delete().eq("id", session_id).execute()
            self.logger.info(f"Wizard session {session_id} deleted")
            return True
        except Exception as e:
            self.logger.error(f"Error deleting wizard session {session_id}: {e}")
            return False

    # ============================================================
    # Avatar Management
    # ============================================================

    async def create_avatar(
        self,
        session_id: str,
        prompt: str,
        image_url: str,
        provider: str,
        model: str = None,
        params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Record a generated avatar for a session."""
        client = await self._get_admin_client()

        avatar_data = {
            "session_id": session_id,
            "prompt": prompt,
            "image_url": image_url,
            "generation_provider": provider,
            "generation_model": model,
            "generation_params": params or {},
            "selected": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        try:
            result = client.table("wizard_generated_avatars").insert(avatar_data).execute()
            if result.data:
                return result.data[0]
            raise DatabaseError("Failed to create avatar record")
        except Exception as e:
            self.logger.error(f"Error creating avatar for session {session_id}: {e}")
            raise DatabaseError(f"Failed to create avatar: {str(e)}")

    async def get_avatars(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all generated avatars for a session."""
        client = await self._get_admin_client()

        try:
            result = client.table("wizard_generated_avatars").select("*").eq(
                "session_id", session_id
            ).order("created_at", desc=True).execute()
            return result.data or []
        except Exception as e:
            self.logger.error(f"Error getting avatars for session {session_id}: {e}")
            return []

    async def select_avatar(
        self,
        session_id: str,
        avatar_id: str
    ) -> bool:
        """Select an avatar as the final choice."""
        client = await self._get_admin_client()

        try:
            # Deselect all avatars for this session
            client.table("wizard_generated_avatars").update(
                {"selected": False}
            ).eq("session_id", session_id).execute()

            # Select the specified avatar
            client.table("wizard_generated_avatars").update(
                {"selected": True}
            ).eq("id", avatar_id).execute()

            # Get the avatar URL and update session step_data
            avatar_result = client.table("wizard_generated_avatars").select(
                "image_url"
            ).eq("id", avatar_id).single().execute()

            if avatar_result.data:
                session = await self.get_session(session_id)
                step_data = session.get("step_data", {})
                step_data["avatar_url"] = avatar_result.data["image_url"]
                await self.update_session(session_id, {"step_data": step_data})

            return True
        except Exception as e:
            self.logger.error(f"Error selecting avatar {avatar_id}: {e}")
            return False

    # ============================================================
    # Pending Documents Management
    # ============================================================

    async def create_pending_document(
        self,
        session_id: str,
        source_type: str,
        source_name: str,
        file_size: int = None,
        file_type: str = None,
        staged_path: str = None
    ) -> Dict[str, Any]:
        """Create a pending document record for tracking."""
        client = await self._get_admin_client()

        doc_data = {
            "session_id": session_id,
            "source_type": source_type,  # 'file' or 'website'
            "source_name": source_name,
            "status": "pending",
            "file_size": file_size,
            "file_type": file_type,
            "staged_path": staged_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        try:
            result = client.table("wizard_pending_documents").insert(doc_data).execute()
            if result.data:
                return result.data[0]
            raise DatabaseError("Failed to create pending document record")
        except Exception as e:
            self.logger.error(f"Error creating pending document for session {session_id}: {e}")
            raise DatabaseError(f"Failed to create pending document: {str(e)}")

    async def update_pending_document(
        self,
        pending_doc_id: str,
        updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a pending document record."""
        client = await self._get_admin_client()

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            result = client.table("wizard_pending_documents").update(updates).eq("id", pending_doc_id).execute()
            if result.data:
                return result.data[0]
            return None
        except Exception as e:
            self.logger.error(f"Error updating pending document {pending_doc_id}: {e}")
            raise DatabaseError(f"Failed to update pending document: {str(e)}")

    async def get_pending_documents(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all pending documents for a session."""
        client = await self._get_admin_client()

        try:
            result = client.table("wizard_pending_documents").select("*").eq(
                "session_id", session_id
            ).order("created_at", desc=False).execute()
            return result.data or []
        except Exception as e:
            self.logger.error(f"Error getting pending documents for session {session_id}: {e}")
            return []

    async def get_knowledge_status(self, session_id: str) -> Dict[str, Any]:
        """Get aggregated knowledge processing status for a session."""
        docs = await self.get_pending_documents(session_id)

        status_counts = {
            "pending": 0,
            "processing": 0,
            "ready": 0,
            "error": 0
        }

        for doc in docs:
            status = doc.get("status", "pending")
            if status in status_counts:
                status_counts[status] += 1

        return {
            "items": docs,
            "pending_count": status_counts["pending"],
            "processing_count": status_counts["processing"],
            "ready_count": status_counts["ready"],
            "error_count": status_counts["error"],
            "total_count": len(docs),
            "all_complete": status_counts["pending"] == 0 and status_counts["processing"] == 0
        }

    async def delete_pending_document(
        self,
        pending_doc_id: str
    ) -> bool:
        """Delete a pending document."""
        client = await self._get_admin_client()

        try:
            client.table("wizard_pending_documents").delete().eq("id", pending_doc_id).execute()
            return True
        except Exception as e:
            self.logger.error(f"Error deleting pending document {pending_doc_id}: {e}")
            return False

    async def get_ready_document_ids(self, session_id: str) -> List[str]:
        """Get document IDs for all ready documents in a session."""
        docs = await self.get_pending_documents(session_id)
        return [
            doc["document_id"]
            for doc in docs
            if doc.get("status") == "ready" and doc.get("document_id")
        ]


# Create singleton instance
wizard_session_service = WizardSessionService()
