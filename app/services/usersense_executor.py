"""
UserSense Executor - reflects on conversations to update user overviews.
Uses the Sidekick's configured LLM via the trigger API.
"""

import json
import logging
import uuid
from typing import Dict, Any, List, Optional

import httpx

from app.config import settings
from app.models.ambient import AmbientAbilityRun, UserSenseResult, UserOverviewUpdate
from app.utils.supabase_credentials import SupabaseCredentialManager

logger = logging.getLogger(__name__)

# UserSense reflection prompt
USERSENSE_REFLECTION_PROMPT = """You are UserSense, an AI that reflects on conversations to build understanding of users.

Your task is to analyze a completed conversation and identify any meaningful updates to the user's overview profile. The user overview helps AI assistants provide more personalized and contextual interactions.

This conversation was with a sidekick named "{agent_name}". You should identify both:
1. **Shared updates** - Information that applies across ALL sidekicks (identity, general goals, working style)
2. **Sidekick-specific insights** - Information specific to this user's relationship with {agent_name}

## Current User Overview (Shared)
{user_overview}

## Current {agent_name} Insights
{sidekick_insights}

## Conversation Transcript (with {agent_name})
{transcript}

## Instructions

Analyze the conversation and identify:

### A. SHARED UPDATES (apply to all sidekicks)
Updates for these sections:
1. **identity** - Who the user is (name, profession, location, preferences)
2. **biography** - User's life story, background, and personal history (where they're from, where they've lived, cultural background, life experiences, education, family details)
3. **goals** - What the user is trying to achieve (current goals, long-term aspirations)
4. **working_style** - How the user prefers to interact (communication style, detail level, pace)
5. **important_context** - Key facts to remember (circumstances, constraints, past experiences)
6. **relationship_history** - How the relationship has developed (topics discussed, rapport level, interests)

### B. SIDEKICK-SPECIFIC INSIGHTS (for {agent_name} only)
1. **relationship_context** - What role does {agent_name} play for this user?
2. **unique_observations** - What does {agent_name} know that other sidekicks might not? (array)
3. **interaction_patterns** - How does this user interact specifically with {agent_name}?
4. **topics_discussed** - Main topics covered with {agent_name} (array)

## Rules

- Only include GENUINELY NEW information not already in the overview/insights
- Be conservative - only add high-confidence observations
- Prefer specific facts over vague generalizations
- For arrays/lists, use "append" action; for single values, use "set" action
- Keep values concise but informative
- If nothing meaningful to update, return empty arrays

## Response Format

Return a JSON object:
```json
{{
  "updates": [
    {{"section": "identity", "action": "set", "key": "profession", "value": "software engineer"}},
    {{"section": "important_context", "action": "append", "key": "items", "value": "Has a busy schedule on Tuesdays"}}
  ],
  "sidekick_insights": {{
    "relationship_context": "User relies on {agent_name} for technical guidance",
    "unique_observations": ["Prefers code examples over explanations"],
    "interaction_patterns": "Usually asks follow-up questions for clarification",
    "topics_discussed": ["Python debugging", "API design"]
  }},
  "summary": "Learned user is a software engineer who prefers code examples. They use {agent_name} for technical guidance.",
  "confidence": 0.85
}}
```

Only return the JSON object, no other text."""


class UserSenseExecutor:
    """Executes UserSense reflection on conversations using the Sidekick API."""

    def __init__(self):
        self._http_client = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for API calls."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=120.0)
        return self._http_client

    async def _get_usersense_agent(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Find the agent that has UserSense ability or an agent with LLM config."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            # Get all agents for this client
            agents_result = client_sb.table('agents').select('id, slug, name, voice_settings').execute()

            if not agents_result.data:
                logger.warning(f"No agents found for client {client_id}")
                return None

            # First, get all tools to find the UserSense tool ID
            tools_result = client_sb.table('tools').select('id, slug').eq('slug', 'usersense').execute()
            usersense_tool_id = None
            if tools_result.data:
                usersense_tool_id = tools_result.data[0]['id']

            # Get assigned tools to find which agent has UserSense
            if usersense_tool_id:
                for agent in agents_result.data:
                    # Check agent_tools junction table for this specific tool
                    agent_tools_result = client_sb.table('agent_tools').select(
                        'tool_id'
                    ).eq('agent_id', agent['id']).eq('tool_id', usersense_tool_id).execute()

                    if agent_tools_result.data:
                        logger.info(f"Found UserSense agent: {agent['slug']} ({agent['name']})")
                        return agent

            # If no agent has UserSense assigned, use the first agent with voice_settings
            for agent in agents_result.data:
                if agent.get('voice_settings') and agent['voice_settings'].get('llm_provider'):
                    logger.info(f"Using fallback agent for UserSense: {agent['slug']}")
                    return agent

            logger.warning(f"No suitable agent found for UserSense in client {client_id}")
            return None

        except Exception as e:
            logger.error(f"Failed to get UserSense agent: {e}")
            return None

    async def _call_sidekick_api(
        self,
        agent_slug: str,
        message: str,
        client_id: str
    ) -> Optional[str]:
        """Call the Sidekick trigger API to get an LLM response."""
        try:
            client = await self._get_http_client()

            # Build the request payload
            payload = {
                "agent_slug": agent_slug,
                "message": message,
                "mode": "text",
                "user_id": f"usersense-reflection-{uuid.uuid4().hex[:8]}",
                "session_id": f"reflection-{uuid.uuid4().hex[:8]}",
                "conversation_id": str(uuid.uuid4()),
                "context": {
                    "internal_reflection": True,
                    "skip_transcript_storage": True
                }
            }

            # Call the internal API
            api_url = "http://localhost:8000/api/v1/trigger-agent"

            logger.info(f"Calling Sidekick API for UserSense reflection: {agent_slug}")

            response = await client.post(
                api_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Client-ID": client_id,
                    "X-Internal-Request": "true"
                }
            )

            if response.status_code == 200:
                result = response.json()
                if result.get('success') and result.get('data', {}).get('response'):
                    return result['data']['response']
                else:
                    logger.warning(f"API call succeeded but no response text: {result}")
                    return None
            else:
                logger.error(f"Sidekick API call failed: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Failed to call Sidekick API: {e}", exc_info=True)
            return None

    async def execute(self, run: AmbientAbilityRun) -> UserSenseResult:
        """
        Execute UserSense reflection on a conversation.

        Args:
            run: The ambient ability run containing context

        Returns:
            UserSenseResult with updates to apply
        """
        try:
            # Extract context
            input_context = run.input_context or {}
            transcript = input_context.get("transcript", [])
            user_overview = input_context.get("user_overview", {})
            agent_name = input_context.get("agent_name", "Assistant")
            agent_id = input_context.get("agent_id")  # The actual agent the user talked to
            agent_slug = input_context.get("agent_slug")

            # If transcript not in context, fetch it
            if not transcript and run.conversation_id:
                transcript = await self._fetch_transcript(
                    str(run.client_id),
                    str(run.conversation_id)
                )

            # If user_overview not in context, fetch it
            if not user_overview and run.user_id:
                user_overview = await self._fetch_user_overview(
                    str(run.client_id),
                    str(run.user_id)
                )

            # If agent_id/agent_name not in context, try to fetch from agent_slug or conversation
            if not agent_id and agent_slug:
                agent_id, fetched_name = await self._fetch_agent_by_slug(str(run.client_id), agent_slug)
                if fetched_name and agent_name == "Assistant":
                    agent_name = fetched_name
            elif not agent_id and run.conversation_id:
                # Try to get agent from conversation
                agent_id, fetched_name = await self._fetch_agent_from_conversation(
                    str(run.client_id), str(run.conversation_id)
                )
                if fetched_name and agent_name == "Assistant":
                    agent_name = fetched_name
            elif agent_name == "Assistant" and agent_id:
                fetched_name = await self._fetch_agent_name(str(run.client_id), agent_id)
                if fetched_name:
                    agent_name = fetched_name

            # Fetch sidekick-specific insights if available
            sidekick_insights = {}
            if run.user_id and agent_id:
                sidekick_insights = await self._fetch_sidekick_insights(
                    str(run.client_id),
                    str(run.user_id),
                    agent_id
                )

            if not transcript:
                logger.warning(f"No transcript found for run {run.id}")
                return UserSenseResult(summary="No transcript available for reflection")

            # Format transcript for prompt
            formatted_transcript = self._format_transcript(transcript)
            formatted_overview = json.dumps(user_overview, indent=2) if user_overview else "{}"
            formatted_insights = json.dumps(sidekick_insights, indent=2) if sidekick_insights else "{}"

            # Build prompt
            prompt = USERSENSE_REFLECTION_PROMPT.format(
                agent_name=agent_name,
                user_overview=formatted_overview,
                sidekick_insights=formatted_insights,
                transcript=formatted_transcript
            )

            # Get the agent to use for LLM call
            usersense_agent = await self._get_usersense_agent(str(run.client_id))
            if not usersense_agent:
                logger.error(f"No suitable agent found for UserSense reflection in client {run.client_id}")
                return UserSenseResult(
                    summary="No agent available for reflection",
                    confidence=0
                )

            # Call LLM via Sidekick API
            logger.info(f"UserSense reflecting on conversation {run.conversation_id} with {agent_name} using agent {usersense_agent['slug']}")

            response_text = await self._call_sidekick_api(
                usersense_agent['slug'],
                prompt,
                str(run.client_id)
            )

            if not response_text:
                logger.warning(f"No response from Sidekick API for UserSense reflection")
                return UserSenseResult(
                    summary="Failed to get response from agent",
                    confidence=0
                )

            # Extract JSON from response
            result, new_sidekick_insights = self._parse_response_with_insights(response_text)

            # Apply shared updates to user overview
            if result.updates and run.user_id:
                await self._apply_updates(
                    str(run.client_id),
                    str(run.user_id),
                    result.updates,
                    agent_id,
                    result.summary
                )
                result.sections_updated = list(set(u.section for u in result.updates))

            # Apply sidekick-specific insights
            if new_sidekick_insights and run.user_id and agent_id:
                await self._apply_sidekick_insights(
                    str(run.client_id),
                    str(run.user_id),
                    agent_id,
                    agent_name,
                    new_sidekick_insights,
                    result.summary
                )

            logger.info(
                f"UserSense completed for user {str(run.user_id)[:8]}... with {agent_name} - "
                f"{len(result.updates)} shared updates, sidekick insights: {bool(new_sidekick_insights)}"
            )

            return result

        except Exception as e:
            logger.error(f"UserSense execution failed: {e}", exc_info=True)
            return UserSenseResult(
                summary=f"Reflection failed: {str(e)}",
                confidence=0
            )

    async def _fetch_transcript(
        self,
        client_id: str,
        conversation_id: str
    ) -> List[Dict[str, Any]]:
        """Fetch conversation transcript from client's database."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            result = client_sb.table("conversation_transcripts").select(
                "role", "content", "created_at"
            ).eq("conversation_id", conversation_id).order(
                "created_at", desc=False
            ).execute()

            return result.data or []

        except Exception as e:
            logger.error(f"Failed to fetch transcript: {e}")
            return []

    async def _fetch_user_overview(
        self,
        client_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """Fetch user overview from client's database."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            result = client_sb.rpc(
                "get_user_overview",
                {"p_user_id": user_id, "p_client_id": client_id}
            ).execute()

            if result.data:
                return result.data.get("overview", {})
            return {}

        except Exception as e:
            logger.error(f"Failed to fetch user overview: {e}")
            return {}

    def _format_transcript(self, transcript: List[Dict[str, Any]]) -> str:
        """Format transcript for the prompt."""
        lines = []
        for msg in transcript:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            lines.append(f"[{role}]: {content}")
        return "\n\n".join(lines)

    async def _fetch_agent_name(self, client_id: str, agent_id: str) -> Optional[str]:
        """Fetch agent name from client's database."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            result = client_sb.table("agents").select("name").eq("id", agent_id).limit(1).execute()

            if result.data and len(result.data) > 0:
                return result.data[0].get("name")
            return None

        except Exception as e:
            logger.error(f"Failed to fetch agent name: {e}")
            return None

    async def _fetch_agent_by_slug(self, client_id: str, agent_slug: str) -> tuple:
        """Fetch agent ID and name by slug from client's database."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            result = client_sb.table("agents").select("id, name").eq("slug", agent_slug).limit(1).execute()

            if result.data and len(result.data) > 0:
                return result.data[0].get("id"), result.data[0].get("name")
            return None, None

        except Exception as e:
            logger.error(f"Failed to fetch agent by slug: {e}")
            return None, None

    async def _fetch_agent_from_conversation(self, client_id: str, conversation_id: str) -> tuple:
        """Fetch agent ID and name from a conversation."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            # Get conversation to find agent_id
            conv_result = client_sb.table("conversations").select("agent_id").eq("id", conversation_id).limit(1).execute()

            if conv_result.data and conv_result.data[0].get("agent_id"):
                agent_id = conv_result.data[0]["agent_id"]
                # Now get agent name
                agent_result = client_sb.table("agents").select("name").eq("id", agent_id).limit(1).execute()
                if agent_result.data:
                    return agent_id, agent_result.data[0].get("name")
                return agent_id, None

            return None, None

        except Exception as e:
            logger.error(f"Failed to fetch agent from conversation: {e}")
            return None, None

    async def _fetch_sidekick_insights(
        self,
        client_id: str,
        user_id: str,
        agent_id: str
    ) -> Dict[str, Any]:
        """Fetch sidekick-specific insights for a user and agent."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            # Get insights from user_overviews table
            result = client_sb.table("user_overviews").select(
                "sidekick_insights"
            ).eq("user_id", user_id).eq("client_id", client_id).limit(1).execute()

            if result.data and len(result.data) > 0:
                sidekick_insights = result.data[0].get("sidekick_insights", {})
                if sidekick_insights and agent_id in sidekick_insights:
                    return sidekick_insights[agent_id].get("insights", {})
            return {}

        except Exception as e:
            logger.error(f"Failed to fetch sidekick insights: {e}")
            return {}

    def _parse_response(self, response_text: str) -> UserSenseResult:
        """Parse LLM response into UserSenseResult (legacy, for backwards compatibility)."""
        result, _ = self._parse_response_with_insights(response_text)
        return result

    def _parse_response_with_insights(self, response_text: str) -> tuple:
        """Parse LLM response into UserSenseResult and sidekick insights."""
        try:
            # Try to extract JSON from response
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                data = json.loads(json_str)

                updates = []
                for u in data.get("updates", []):
                    updates.append(UserOverviewUpdate(
                        section=u["section"],
                        action=u["action"],
                        key=u["key"],
                        value=u["value"]
                    ))

                result = UserSenseResult(
                    updates=updates,
                    summary=data.get("summary", ""),
                    confidence=data.get("confidence")
                )

                # Extract sidekick insights
                sidekick_insights = data.get("sidekick_insights", {})

                return result, sidekick_insights

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse UserSense response as JSON: {e}")

        return UserSenseResult(summary="Failed to parse reflection response"), {}

    async def _apply_updates(
        self,
        client_id: str,
        user_id: str,
        updates: List[UserOverviewUpdate],
        agent_id: Optional[str] = None,
        reason: Optional[str] = None
    ) -> bool:
        """Apply updates to user overview in client's database."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            for update in updates:
                try:
                    result = client_sb.rpc(
                        "update_user_overview",
                        {
                            "p_user_id": user_id,
                            "p_client_id": client_id,
                            "p_section": update.section,
                            "p_action": update.action,
                            "p_key": update.key,
                            "p_value": json.dumps(update.value) if not isinstance(update.value, str) else update.value,
                            "p_agent_id": agent_id,
                            "p_reason": reason or "UserSense automatic reflection",
                            "p_expected_version": None  # Don't enforce version for automatic updates
                        }
                    ).execute()

                    if result.data and result.data.get("success"):
                        logger.debug(f"Applied update: {update.section}.{update.key}")
                    else:
                        logger.warning(f"Update may have failed: {update.section}.{update.key}")

                except Exception as e:
                    logger.error(f"Failed to apply update {update.section}.{update.key}: {e}")

            return True

        except Exception as e:
            logger.error(f"Failed to apply user overview updates: {e}")
            return False

    async def _apply_sidekick_insights(
        self,
        client_id: str,
        user_id: str,
        agent_id: str,
        agent_name: str,
        insights: Dict[str, Any],
        reason: Optional[str] = None
    ) -> bool:
        """Apply sidekick-specific insights to user overview."""
        try:
            if not insights:
                return True

            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            # Call the update_sidekick_insights RPC function
            result = client_sb.rpc(
                "update_sidekick_insights",
                {
                    "p_user_id": user_id,
                    "p_client_id": client_id,
                    "p_agent_id": agent_id,
                    "p_agent_name": agent_name,
                    "p_insights": json.dumps(insights),
                    "p_reason": reason or "UserSense automatic reflection"
                }
            ).execute()

            if result.data and result.data.get("success"):
                logger.debug(f"Applied sidekick insights for {agent_name}")
                return True
            else:
                logger.warning(f"Sidekick insights update may have failed for {agent_name}")
                return False

        except Exception as e:
            logger.error(f"Failed to apply sidekick insights for {agent_name}: {e}")
            return False


# Singleton instance
usersense_executor = UserSenseExecutor()
