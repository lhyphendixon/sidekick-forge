"""
Ambient Ability Service - manages background abilities that run after sessions or on schedule.
"""

import logging
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime

from app.config import settings
from app.models.ambient import (
    AmbientAbilityRun,
    AmbientAbilityRunCreate,
    AmbientNotification,
    AmbientRunStatus,
    AmbientTriggerType,
)

logger = logging.getLogger(__name__)


class AmbientAbilityService:
    """Service for managing ambient ability runs and notifications."""

    def __init__(self):
        self._platform_sb = None

    @property
    def platform_sb(self):
        """Lazy-load platform Supabase client."""
        if self._platform_sb is None:
            from supabase import create_client
            self._platform_sb = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key
            )
        return self._platform_sb

    async def get_usersense_ability_id(self) -> Optional[str]:
        """Get the UserSense ability ID."""
        try:
            result = self.platform_sb.table("tools").select("id").eq(
                "slug", "usersense"
            ).eq("execution_phase", "ambient").maybe_single().execute()

            if result.data:
                return result.data.get("id")
            return None
        except Exception as e:
            logger.error(f"Failed to get UserSense ability ID: {e}")
            return None

    async def is_usersense_enabled(self, client_id: str) -> bool:
        """Check if UserSense is enabled for a client."""
        try:
            result = self.platform_sb.table("clients").select(
                "usersense_enabled"
            ).eq("id", client_id).maybe_single().execute()

            if result.data:
                return result.data.get("usersense_enabled", False)
            return False
        except Exception as e:
            logger.error(f"Failed to check UserSense status for client {client_id}: {e}")
            return False

    async def get_ambient_abilities_for_trigger(
        self,
        client_id: str,
        trigger_type: AmbientTriggerType,
        agent_slug: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all ambient abilities that should run for a given trigger."""
        try:
            # Get global ambient abilities
            query = self.platform_sb.table("tools").select("*").eq(
                "execution_phase", "ambient"
            ).eq("enabled", True)

            result = query.execute()
            abilities = result.data or []

            # Filter by trigger type and agent
            filtered = []
            for ability in abilities:
                trigger_config = ability.get("trigger_config") or {}

                # Check trigger type matches
                if trigger_config.get("trigger") != trigger_type:
                    continue

                # Check agent filter
                allowed_agents = trigger_config.get("agents")
                if allowed_agents is not None and agent_slug not in allowed_agents:
                    continue

                # For UserSense, check if enabled for this client
                if ability.get("slug") == "usersense":
                    if not await self.is_usersense_enabled(client_id):
                        continue

                filtered.append(ability)

            return filtered

        except Exception as e:
            logger.error(f"Failed to get ambient abilities: {e}")
            return []

    async def queue_post_session_abilities(
        self,
        client_id: str,
        user_id: str,
        conversation_id: str,
        session_id: Optional[str] = None,
        message_count: int = 0,
        agent_slug: Optional[str] = None,
        transcript: Optional[List[Dict[str, Any]]] = None,
        user_overview: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """Queue all applicable post-session ambient abilities."""
        queued_run_ids = []

        try:
            # Get applicable abilities
            abilities = await self.get_ambient_abilities_for_trigger(
                client_id=client_id,
                trigger_type=AmbientTriggerType.POST_SESSION,
                agent_slug=agent_slug
            )

            for ability in abilities:
                trigger_config = ability.get("trigger_config") or {}
                min_messages = trigger_config.get("min_messages", 3)

                # Check minimum message count
                if message_count < min_messages:
                    logger.debug(
                        f"Skipping {ability['slug']} - message_count {message_count} < min {min_messages}"
                    )
                    continue

                # Determine notification message
                notification_message = None
                if ability.get("slug") == "usersense":
                    notification_message = "User Understanding Expanded"

                # Build input context
                input_context = {
                    "message_count": message_count,
                    "agent_slug": agent_slug,
                }
                if transcript:
                    input_context["transcript"] = transcript
                if user_overview:
                    input_context["user_overview"] = user_overview

                # Look up agent details by slug to get agent_id and agent_name
                if agent_slug:
                    try:
                        from app.utils.supabase_credentials import SupabaseCredentialManager
                        client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
                        from supabase import create_client
                        client_sb = create_client(client_url, client_key)
                        agent_result = client_sb.table("agents").select("id, name").eq("slug", agent_slug).limit(1).execute()
                        if agent_result.data:
                            input_context["agent_id"] = agent_result.data[0]["id"]
                            input_context["agent_name"] = agent_result.data[0]["name"]
                    except Exception as agent_err:
                        logger.warning(f"Could not fetch agent details for {agent_slug}: {agent_err}")

                # Queue the run
                run_id = await self.queue_run(
                    ability_id=ability["id"],
                    client_id=client_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    session_id=session_id,
                    trigger_type=AmbientTriggerType.POST_SESSION,
                    input_context=input_context,
                    notification_message=notification_message
                )

                if run_id:
                    queued_run_ids.append(run_id)
                    logger.info(
                        f"Queued ambient ability {ability['slug']} for user {user_id[:8]}... "
                        f"(run_id: {run_id[:8]}...)"
                    )

        except Exception as e:
            logger.error(f"Failed to queue post-session abilities: {e}")

        return queued_run_ids

    async def queue_run(
        self,
        ability_id: str,
        client_id: str,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        trigger_type: AmbientTriggerType = AmbientTriggerType.POST_SESSION,
        input_context: Optional[Dict[str, Any]] = None,
        notification_message: Optional[str] = None
    ) -> Optional[str]:
        """Queue a single ambient ability run."""
        try:
            result = self.platform_sb.rpc(
                "queue_ambient_ability_run",
                {
                    "p_ability_id": ability_id,
                    "p_client_id": client_id,
                    "p_user_id": user_id,
                    "p_conversation_id": conversation_id,
                    "p_session_id": session_id,
                    "p_trigger_type": trigger_type,
                    "p_input_context": input_context,
                    "p_notification_message": notification_message
                }
            ).execute()

            return result.data
        except Exception as e:
            logger.error(f"Failed to queue ambient run: {e}")
            return None

    async def get_pending_runs(self, limit: int = 10) -> List[AmbientAbilityRun]:
        """Get pending runs that are ready for execution."""
        try:
            result = self.platform_sb.rpc(
                "get_pending_ambient_runs",
                {"p_limit": limit}
            ).execute()

            runs = []
            for row in result.data or []:
                runs.append(AmbientAbilityRun(
                    id=row["id"],
                    ability_id=row["ability_id"],
                    ability_slug=row.get("ability_slug"),
                    ability_type=row.get("ability_type"),
                    ability_config=row.get("ability_config"),
                    trigger_config=row.get("trigger_config"),
                    client_id=row["client_id"],
                    user_id=row.get("user_id"),
                    conversation_id=row.get("conversation_id"),
                    session_id=row.get("session_id"),
                    trigger_type=row["trigger_type"],
                    input_context=row.get("input_context"),
                    notification_message=row.get("notification_message"),
                    created_at=row["created_at"],
                    status=AmbientRunStatus.PENDING
                ))

            return runs

        except Exception as e:
            logger.error(f"Failed to get pending runs: {e}")
            return []

    async def update_run_status(
        self,
        run_id: str,
        status: AmbientRunStatus,
        output_result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ) -> bool:
        """Update the status of an ambient ability run."""
        try:
            result = self.platform_sb.rpc(
                "update_ambient_run_status",
                {
                    "p_run_id": run_id,
                    "p_status": status,
                    "p_output_result": output_result,
                    "p_error": error
                }
            ).execute()

            return result.data is True
        except Exception as e:
            logger.error(f"Failed to update run status: {e}")
            return False

    async def get_user_notifications(
        self,
        user_id: str,
        client_id: str
    ) -> List[AmbientNotification]:
        """Get pending notifications for a user."""
        try:
            result = self.platform_sb.rpc(
                "get_user_ambient_notifications",
                {
                    "p_user_id": user_id,
                    "p_client_id": client_id
                }
            ).execute()

            notifications = []
            for row in result.data or []:
                notifications.append(AmbientNotification(
                    id=row["id"],
                    ability_slug=row["ability_slug"],
                    notification_message=row["notification_message"],
                    output_result=row.get("output_result"),
                    completed_at=row["completed_at"]
                ))

            return notifications

        except Exception as e:
            logger.error(f"Failed to get user notifications: {e}")
            return []

    async def mark_notification_shown(self, run_id: str) -> bool:
        """Mark a notification as shown to the user."""
        try:
            result = self.platform_sb.rpc(
                "mark_ambient_notification_shown",
                {"p_run_id": run_id}
            ).execute()

            return result.data is True
        except Exception as e:
            logger.error(f"Failed to mark notification shown: {e}")
            return False


# Singleton instance
ambient_ability_service = AmbientAbilityService()
