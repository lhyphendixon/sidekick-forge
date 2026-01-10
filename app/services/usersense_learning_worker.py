"""
UserSense Initial Learning Worker

Processes historical conversations to build initial user understanding
when UserSense is first enabled for a client.

Uses the Sidekick's configured LLM via the trigger API instead of hardcoded providers.
"""

import json
import logging
import asyncio
import uuid
import httpx
from typing import Dict, Any, List, Optional

from app.config import settings
from app.utils.supabase_credentials import SupabaseCredentialManager

logger = logging.getLogger(__name__)

# Batch size for processing conversations
CONVERSATION_BATCH_SIZE = 5
MAX_CONVERSATIONS_PER_USER = 50

# Learning prompt for initial understanding
INITIAL_LEARNING_PROMPT = """You are UserSense, an AI that builds understanding of users by analyzing their conversation history.

You are analyzing multiple conversations between a user and AI sidekicks to build an initial understanding profile.

## Conversations to Analyze

{conversations}

## Your Task

Based on these conversations, create a comprehensive user profile covering:

1. **identity** - Who is this user?
   - name (if mentioned)
   - profession/role
   - location (if mentioned)
   - background/expertise
   - communication_style (formal, casual, technical, etc.)

2. **goals** - What does this user want to achieve?
   - current (immediate objectives)
   - long_term (bigger aspirations)

3. **working_style** - How does this user prefer to work?
   - detail_preference (brief vs detailed)
   - interaction_pace (quick exchanges vs deep discussions)
   - decision_style (analytical vs intuitive)

4. **important_context** - Key facts to remember (as an array of strings)
   - Significant circumstances
   - Constraints or limitations
   - Past experiences mentioned

5. **relationship_history** - How has the relationship developed?
   - topics_discussed (array of main topics)
   - rapport_level (new, developing, established)
   - preferences (what they like/dislike)

## Response Format

Return a JSON object with the structure:
```json
{{
  "overview": {{
    "identity": {{}},
    "goals": {{}},
    "working_style": {{}},
    "important_context": [],
    "relationship_history": {{}}
  }},
  "summary": "Brief 1-2 sentence summary of who this user is"
}}
```

Be thorough but only include information that is clearly evidenced in the conversations.
Return ONLY the JSON object, no other text."""

# Sidekick-specific insights prompt
SIDEKICK_INSIGHTS_PROMPT = """You are analyzing conversations between a user and a specific AI sidekick named "{agent_name}".

Based on these conversations, identify insights specific to this sidekick's relationship with the user.

## Conversations with {agent_name}

{conversations}

## Current Shared Understanding
{shared_overview}

## Your Task

Identify insights specific to the {agent_name} <-> User relationship:

1. **relationship_context** - What role does this sidekick play for the user?
2. **unique_observations** - What does this sidekick know that others might not? (array)
3. **interaction_patterns** - How does the user interact specifically with this sidekick?
4. **topics_discussed** - Main topics covered with this specific sidekick (array)

## Response Format

```json
{{
  "relationship_context": "...",
  "unique_observations": ["...", "..."],
  "interaction_patterns": "...",
  "topics_discussed": ["...", "..."]
}}
```

Return ONLY the JSON object."""


class UserSenseLearningWorker:
    """Processes initial learning jobs for UserSense using the Sidekick's configured LLM."""

    def __init__(self):
        self._running = False
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for internal API calls."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=120.0)
        return self._http_client

    async def start(self):
        """Start the learning worker loop."""
        if self._running:
            logger.warning("Learning worker already running")
            return

        self._running = True
        logger.info("UserSense Learning Worker started")

        while self._running:
            try:
                # Check for pending jobs
                job = await self._claim_next_job()
                if job:
                    await self._process_job(job)
                else:
                    # No jobs, wait before checking again
                    await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"Learning worker error: {e}", exc_info=True)
                await asyncio.sleep(10)

    async def stop(self):
        """Stop the learning worker."""
        self._running = False
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
        logger.info("UserSense Learning Worker stopped")

    async def _claim_next_job(self) -> Optional[Dict[str, Any]]:
        """Claim the next pending learning job from the platform database."""
        try:
            from supabase import create_client
            platform_sb = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key
            )

            result = platform_sb.rpc('claim_next_learning_job').execute()
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None

        except Exception as e:
            logger.error(f"Failed to claim job: {e}")
            return None

    async def _get_usersense_agent(self, client_id: str, client_sb) -> Optional[Dict[str, Any]]:
        """Find the agent that has UserSense ability assigned."""
        try:
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
                    logger.info(f"Using fallback agent for learning: {agent['slug']}")
                    return agent

            logger.warning(f"No suitable agent found for UserSense learning in client {client_id}")
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
                "user_id": f"usersense-learning-{uuid.uuid4().hex[:8]}",
                "session_id": f"learning-{uuid.uuid4().hex[:8]}",
                "conversation_id": str(uuid.uuid4()),
                "context": {
                    "internal_learning": True,
                    "skip_transcript_storage": True
                }
            }

            # Call the internal API
            # Use localhost since we're in the same container
            api_url = f"http://localhost:8000/api/v1/trigger-agent"

            logger.info(f"Calling Sidekick API for learning: {agent_slug}")

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

    async def _process_job(self, job: Dict[str, Any]):
        """Process a single learning job."""
        job_id = job['id']
        client_id = job['client_id']
        user_id = job.get('user_id')

        logger.info(f"Processing learning job {job_id} for client {client_id}")

        try:
            # Get client credentials
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            # Find the agent to use for learning
            agent = await self._get_usersense_agent(client_id, client_sb)
            if not agent:
                await self._complete_job(job_id, success=False, error="No suitable agent found for UserSense learning")
                return

            # If user_id is placeholder, get all users needing learning
            if str(user_id) == '00000000-0000-0000-0000-000000000000':
                await self._process_client_initial_learning(job_id, client_id, client_sb, agent)
            else:
                await self._process_user_learning(job_id, client_id, user_id, client_sb, agent)

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}", exc_info=True)
            await self._complete_job(job_id, success=False, error=str(e))

    async def _process_client_initial_learning(
        self,
        job_id: str,
        client_id: str,
        client_sb,
        agent: Dict[str, Any]
    ):
        """Process initial learning for all users of a client."""
        try:
            # Get users needing learning
            result = client_sb.rpc(
                'get_users_needing_learning',
                {'p_client_id': client_id, 'p_limit': 100}
            ).execute()

            users = result.data or []
            total_users = len(users)

            if total_users == 0:
                await self._complete_job(job_id, success=True, summary="No users with conversations found")
                return

            logger.info(f"Found {total_users} users needing learning for client {client_id}")

            # Process each user
            for i, user_row in enumerate(users):
                user_id = user_row['user_id']
                conv_count = user_row['conversation_count']

                progress = int((i / total_users) * 100)
                await self._update_job_progress(
                    job_id,
                    progress,
                    f"Learning about user {i+1}/{total_users}...",
                    i
                )

                try:
                    await self._learn_user(client_id, user_id, client_sb, conv_count, agent)
                except Exception as e:
                    logger.error(f"Failed to learn user {user_id}: {e}")
                    # Continue with other users

            await self._complete_job(
                job_id,
                success=True,
                summary=f"Learned about {total_users} users"
            )

        except Exception as e:
            logger.error(f"Client initial learning failed: {e}", exc_info=True)
            await self._complete_job(job_id, success=False, error=str(e))

    async def _process_user_learning(
        self,
        job_id: str,
        client_id: str,
        user_id: str,
        client_sb,
        agent: Dict[str, Any]
    ):
        """Process learning for a single user."""
        try:
            # Get conversation count
            result = client_sb.rpc(
                'get_conversations_for_learning',
                {'p_user_id': user_id, 'p_agent_ids': None, 'p_limit': MAX_CONVERSATIONS_PER_USER}
            ).execute()

            conversations = result.data or []
            if not conversations:
                await self._complete_job(job_id, success=True, summary="No conversations found")
                return

            await self._update_job_progress(job_id, 10, "Gathering conversations...", 0)
            await self._learn_user(client_id, user_id, client_sb, len(conversations), agent)
            await self._complete_job(job_id, success=True, summary="Learning complete")

        except Exception as e:
            logger.error(f"User learning failed: {e}", exc_info=True)
            await self._complete_job(job_id, success=False, error=str(e))

    async def _learn_user(
        self,
        client_id: str,
        user_id: str,
        client_sb,
        expected_conversations: int,
        agent: Dict[str, Any]
    ):
        """Learn about a single user from their conversation history."""
        logger.info(f"Learning user {str(user_id)[:8]}... ({expected_conversations} conversations) using agent {agent['slug']}")

        # Update learning status
        client_sb.rpc('update_learning_status', {
            'p_user_id': user_id,
            'p_client_id': client_id,
            'p_status': 'in_progress',
            'p_progress': 0
        }).execute()

        try:
            # Get conversations grouped by agent
            result = client_sb.rpc(
                'get_conversations_for_learning',
                {'p_user_id': user_id, 'p_agent_ids': None, 'p_limit': MAX_CONVERSATIONS_PER_USER}
            ).execute()

            conversation_info = result.data or []

            # Fetch transcripts
            all_conversations = []
            conversations_by_agent = {}

            for conv in conversation_info:
                conv_id = conv['conversation_id']
                agent_id = conv['agent_id']

                # Fetch transcript
                transcript_result = client_sb.table('conversation_transcripts').select(
                    'role', 'content', 'created_at'
                ).eq('conversation_id', conv_id).order('created_at').limit(100).execute()

                if transcript_result.data:
                    formatted = self._format_transcript(transcript_result.data)
                    all_conversations.append({
                        'agent_id': agent_id,
                        'transcript': formatted
                    })

                    if agent_id not in conversations_by_agent:
                        conversations_by_agent[agent_id] = []
                    conversations_by_agent[agent_id].append(formatted)

            if not all_conversations:
                client_sb.rpc('update_learning_status', {
                    'p_user_id': user_id,
                    'p_client_id': client_id,
                    'p_status': 'completed',
                    'p_progress': 100,
                    'p_conversations_analyzed': 0
                }).execute()
                return

            # Build initial shared understanding using the Sidekick API
            overview = await self._build_shared_understanding(all_conversations, agent['slug'], client_id)

            # Save shared understanding
            for section, data in overview.get('overview', {}).items():
                if data:  # Only update non-empty sections
                    if section == 'important_context' and isinstance(data, list):
                        for item in data:
                            client_sb.rpc('update_user_overview', {
                                'p_user_id': user_id,
                                'p_client_id': client_id,
                                'p_section': section,
                                'p_action': 'append',
                                'p_key': 'items',
                                'p_value': item,
                                'p_agent_id': None,
                                'p_reason': 'Initial UserSense learning'
                            }).execute()
                    elif isinstance(data, dict):
                        for key, value in data.items():
                            client_sb.rpc('update_user_overview', {
                                'p_user_id': user_id,
                                'p_client_id': client_id,
                                'p_section': section,
                                'p_action': 'set',
                                'p_key': key,
                                'p_value': json.dumps(value) if not isinstance(value, str) else value,
                                'p_agent_id': None,
                                'p_reason': 'Initial UserSense learning'
                            }).execute()

            # Build sidekick-specific insights
            for conv_agent_id, agent_conversations in conversations_by_agent.items():
                if len(agent_conversations) >= 1:
                    # Get agent name
                    agent_result = client_sb.table('agents').select('name, slug').eq('id', conv_agent_id).limit(1).execute()
                    if agent_result.data:
                        conv_agent_name = agent_result.data[0]['name']
                        conv_agent_slug = agent_result.data[0]['slug']

                        insights = await self._build_sidekick_insights(
                            conv_agent_name,
                            agent_conversations,
                            overview.get('overview', {}),
                            agent['slug'],  # Use the UserSense agent for the API call
                            client_id
                        )

                        if insights:
                            client_sb.rpc('update_sidekick_insights', {
                                'p_user_id': user_id,
                                'p_client_id': client_id,
                                'p_agent_id': conv_agent_id,
                                'p_agent_name': conv_agent_name,
                                'p_insights': json.dumps(insights),
                                'p_reason': 'Initial UserSense learning'
                            }).execute()

            # Mark learning complete
            client_sb.rpc('update_learning_status', {
                'p_user_id': user_id,
                'p_client_id': client_id,
                'p_status': 'completed',
                'p_progress': 100,
                'p_conversations_analyzed': len(all_conversations)
            }).execute()

            logger.info(f"Completed learning for user {str(user_id)[:8]}... - {len(all_conversations)} conversations")

        except Exception as e:
            logger.error(f"Failed to learn user {user_id}: {e}", exc_info=True)
            client_sb.rpc('update_learning_status', {
                'p_user_id': user_id,
                'p_client_id': client_id,
                'p_status': 'failed',
                'p_progress': 0
            }).execute()
            raise

    async def _build_shared_understanding(
        self,
        conversations: List[Dict[str, Any]],
        agent_slug: str,
        client_id: str
    ) -> Dict[str, Any]:
        """Build shared understanding from all conversations using the Sidekick API."""
        # Combine conversations into batches
        all_transcripts = []
        for conv in conversations[:MAX_CONVERSATIONS_PER_USER]:
            all_transcripts.append(f"--- Conversation ---\n{conv['transcript']}")

        combined = "\n\n".join(all_transcripts)

        # Truncate if too long (roughly 100k tokens max)
        if len(combined) > 300000:
            combined = combined[:300000] + "\n\n[Truncated due to length]"

        prompt = INITIAL_LEARNING_PROMPT.format(conversations=combined)

        try:
            response_text = await self._call_sidekick_api(agent_slug, prompt, client_id)

            if response_text:
                return self._parse_json_response(response_text)
            else:
                logger.warning("No response from Sidekick API for shared understanding")
                return {"overview": {}}

        except Exception as e:
            logger.error(f"Failed to build shared understanding: {e}")
            return {"overview": {}}

    async def _build_sidekick_insights(
        self,
        agent_name: str,
        conversations: List[str],
        shared_overview: Dict[str, Any],
        api_agent_slug: str,
        client_id: str
    ) -> Dict[str, Any]:
        """Build sidekick-specific insights using the Sidekick API."""
        combined = "\n\n--- Conversation ---\n".join(conversations[:20])

        if len(combined) > 100000:
            combined = combined[:100000] + "\n\n[Truncated]"

        prompt = SIDEKICK_INSIGHTS_PROMPT.format(
            agent_name=agent_name,
            conversations=combined,
            shared_overview=json.dumps(shared_overview, indent=2)
        )

        try:
            response_text = await self._call_sidekick_api(api_agent_slug, prompt, client_id)

            if response_text:
                return self._parse_json_response(response_text)
            else:
                logger.warning(f"No response from Sidekick API for {agent_name} insights")
                return {}

        except Exception as e:
            logger.error(f"Failed to build sidekick insights for {agent_name}: {e}")
            return {}

    def _format_transcript(self, messages: List[Dict[str, Any]]) -> str:
        """Format transcript messages into readable text."""
        lines = []
        for msg in messages:
            role = msg.get('role', 'unknown').upper()
            content = msg.get('content', '')
            lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    def _parse_json_response(self, response_text: str) -> Dict[str, Any]:
        """Parse JSON from LLM response."""
        try:
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                return json.loads(response_text[json_start:json_end])
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON response: {e}")

        return {}

    async def _update_job_progress(
        self,
        job_id: str,
        progress: int,
        message: str,
        conversations_processed: int = None
    ):
        """Update job progress in platform database."""
        try:
            from supabase import create_client
            platform_sb = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key
            )

            platform_sb.rpc('update_learning_job_progress', {
                'p_job_id': job_id,
                'p_progress_percent': progress,
                'p_progress_message': message,
                'p_conversations_processed': conversations_processed
            }).execute()

        except Exception as e:
            logger.warning(f"Failed to update job progress: {e}")

    async def _complete_job(
        self,
        job_id: str,
        success: bool,
        summary: str = None,
        error: str = None
    ):
        """Mark a job as completed or failed."""
        try:
            from supabase import create_client
            platform_sb = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key
            )

            platform_sb.rpc('complete_learning_job', {
                'p_job_id': job_id,
                'p_success': success,
                'p_result_summary': summary,
                'p_error_message': error
            }).execute()

            logger.info(f"Job {job_id} completed: success={success}")

        except Exception as e:
            logger.error(f"Failed to complete job: {e}")


# Singleton instance
usersense_learning_worker = UserSenseLearningWorker()
