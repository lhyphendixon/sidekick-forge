"""
Wizard Completion Service

Handles the creation of sidekicks from wizard session data.
Converts wizard inputs into agent configuration and creates the agent
in the tenant's database.
"""

import logging
from typing import Dict, Any, Optional, List
from uuid import UUID
from datetime import datetime

from app.services.wizard_session_service import wizard_session_service
from app.services.agent_service_multitenant import AgentService
from app.services.client_connection_manager import get_connection_manager
from app.models.agent import AgentCreate, VoiceSettings
from app.integrations.supabase_client import supabase_manager

logger = logging.getLogger(__name__)


class WizardCompletionService:
    """Service for completing wizard sessions and creating sidekicks."""

    # Default configuration values
    DEFAULT_STT_PROVIDER = "cartesia"
    DEFAULT_TTS_PROVIDER = "cartesia"
    DEFAULT_TTS_MODEL = "sonic-3"  # Required for Cartesia TTS
    DEFAULT_LLM_PROVIDER = "cerebras"
    DEFAULT_LLM_MODEL = "zai-glm-4.7"
    DEFAULT_EMBEDDING_PROVIDER = "siliconflow"

    def __init__(self):
        self.agent_service = AgentService()
        self.connection_manager = get_connection_manager()

    def _generate_system_prompt(self, step_data: Dict[str, Any]) -> str:
        """
        Generate a system prompt based on wizard personality inputs.

        Uses the personality description and Big Five traits to create
        a comprehensive system prompt for the agent.

        IMPORTANT: The user's personality description takes absolute precedence.
        We do NOT add generic "be helpful and friendly" guidelines as they can
        directly contradict specific user instructions.
        """
        name = step_data.get("name", "Assistant")
        description = step_data.get("personality_description", "")
        traits = step_data.get("personality_traits", {})

        # Start with base prompt
        prompt_parts = [
            f"You are {name}, an AI assistant.",
        ]

        # Add personality description if provided - this is the PRIMARY directive
        if description:
            prompt_parts.append("")
            prompt_parts.append(f"PERSONALITY AND BEHAVIOR (follow these instructions precisely):")
            prompt_parts.append(description)

        # Add trait-based guidance if traits are provided (secondary to description)
        if traits:
            trait_guidance = self._traits_to_guidance(traits)
            if trait_guidance:
                prompt_parts.append("")
                prompt_parts.append(trait_guidance)

        # Only add minimal operational guidelines that don't conflict with personality
        prompt_parts.extend([
            "",
            "Operational notes:",
            "- If you don't know something, say so honestly",
            "- Respect user privacy and maintain appropriate boundaries",
        ])

        return "\n".join(prompt_parts)

    def _traits_to_guidance(self, traits: Dict[str, int]) -> str:
        """
        Convert Big Five personality traits to behavioral guidance.

        Traits are expected as percentages (0-100).
        """
        guidance_parts = []

        # Openness (high = creative, curious; low = practical, conventional)
        openness = traits.get("openness", 50)
        if openness >= 70:
            guidance_parts.append("Be creative and explore ideas freely")
        elif openness <= 30:
            guidance_parts.append("Focus on practical, straightforward solutions")

        # Conscientiousness (high = organized, thorough; low = flexible, spontaneous)
        conscientiousness = traits.get("conscientiousness", 50)
        if conscientiousness >= 70:
            guidance_parts.append("Be thorough and detail-oriented in responses")
        elif conscientiousness <= 30:
            guidance_parts.append("Keep things flexible and adaptable")

        # Extraversion (high = energetic, talkative; low = reserved, thoughtful)
        extraversion = traits.get("extraversion", 50)
        if extraversion >= 70:
            guidance_parts.append("Be enthusiastic and engaging in conversation")
        elif extraversion <= 30:
            guidance_parts.append("Be calm and reflective in your responses")

        # Agreeableness (high = cooperative, warm; low = direct, analytical)
        agreeableness = traits.get("agreeableness", 50)
        if agreeableness >= 70:
            guidance_parts.append("Prioritize warmth and supportiveness")
        elif agreeableness <= 30:
            guidance_parts.append("Be direct and objective, even if challenging")

        # Neuroticism (high = sensitive, emotional; low = stable, calm)
        neuroticism = traits.get("neuroticism", 50)
        if neuroticism >= 70:
            guidance_parts.append("Show empathy and emotional understanding")
        elif neuroticism <= 30:
            guidance_parts.append("Maintain a steady, composed demeanor")

        if guidance_parts:
            return "Communication style: " + "; ".join(guidance_parts)
        return ""

    def _build_voice_settings(self, step_data: Dict[str, Any]) -> VoiceSettings:
        """
        Build VoiceSettings from wizard configuration.

        Handles both default and advanced configuration modes.
        """
        config_mode = step_data.get("config_mode", "default")
        advanced_config = step_data.get("advanced_config", {})

        # Determine providers based on config mode
        if config_mode == "default":
            stt_provider = self.DEFAULT_STT_PROVIDER
            tts_provider = self.DEFAULT_TTS_PROVIDER
            tts_model = self.DEFAULT_TTS_MODEL
            llm_provider = self.DEFAULT_LLM_PROVIDER
            llm_model = self.DEFAULT_LLM_MODEL
        else:
            stt_provider = advanced_config.get("stt_provider", self.DEFAULT_STT_PROVIDER)
            tts_provider = advanced_config.get("tts_provider", self.DEFAULT_TTS_PROVIDER)
            tts_model = advanced_config.get("tts_model", self.DEFAULT_TTS_MODEL)
            llm_provider = advanced_config.get("llm_provider", self.DEFAULT_LLM_PROVIDER)

            # Map LLM provider to model
            llm_model = self._get_llm_model(llm_provider)

        # Get voice ID from step data
        voice_id = step_data.get("voice_id")
        voice_provider = step_data.get("voice_provider", tts_provider)

        return VoiceSettings(
            voice_id=voice_id or "alloy",
            stt_provider=stt_provider,
            tts_provider=tts_provider or voice_provider,
            model=tts_model,  # Required for Cartesia TTS
            llm_provider=llm_provider,
            llm_model=llm_model,
            temperature=0.7,
        )

    def _get_llm_model(self, provider: str) -> str:
        """Get the default model for an LLM provider."""
        models = {
            "cerebras": "zai-glm-4.7",
            "openai": "gpt-4o",
            "anthropic": "claude-sonnet-4-20250514",
            "groq": "llama-3.3-70b-versatile",
        }
        return models.get(provider, "gpt-4o")

    async def _store_api_keys(
        self,
        client_id: str,
        api_keys: Dict[str, str]
    ) -> bool:
        """
        Store API keys in the client's settings.

        API keys are stored encrypted in the platform database.
        """
        if not api_keys:
            return True

        try:
            if not supabase_manager._initialized:
                await supabase_manager.initialize()

            # Filter out empty keys
            valid_keys = {k: v for k, v in api_keys.items() if v}

            if not valid_keys:
                return True

            # Get existing client settings
            result = supabase_manager.admin_client.table("clients").select(
                "settings"
            ).eq("id", client_id).single().execute()

            existing_settings = result.data.get("settings", {}) if result.data else {}
            existing_api_keys = existing_settings.get("api_keys", {})

            # Merge new keys with existing
            merged_keys = {**existing_api_keys, **valid_keys}
            existing_settings["api_keys"] = merged_keys

            # Update client settings
            supabase_manager.admin_client.table("clients").update({
                "settings": existing_settings
            }).eq("id", client_id).execute()

            logger.info(f"Stored API keys for client {client_id}: {list(valid_keys.keys())}")
            return True

        except Exception as e:
            logger.error(f"Error storing API keys for client {client_id}: {e}")
            return False

    async def _check_uses_platform_keys(self, client_id: str) -> bool:
        """
        Check if a client should use platform API keys.

        Returns True if client uses platform keys (BYOK disabled).
        Returns False if client brings their own keys (BYOK enabled).
        """
        try:
            if not supabase_manager._initialized:
                await supabase_manager.initialize()

            result = supabase_manager.admin_client.table("clients").select(
                "tier, uses_platform_keys"
            ).eq("id", client_id).single().execute()

            if not result.data:
                # Default to BYOK enabled (not using platform keys) if client not found
                return False

            tier = result.data.get("tier", "champion")
            uses_platform = result.data.get("uses_platform_keys")

            # If explicitly set, use that value
            if uses_platform is not None:
                return uses_platform

            # Default based on tier: Adventurer uses platform keys, others don't
            return tier == "adventurer"

        except Exception as e:
            logger.error(f"Error checking uses_platform_keys for client {client_id}: {e}")
            # Default to BYOK enabled (not using platform keys) on error
            return False

    async def _assign_documents_to_agent(
        self,
        session_id: str,
        agent_id: str,
        client_id: str
    ) -> int:
        """
        Assign all ready documents from the wizard session to the new agent.

        Returns the number of documents assigned.
        """
        try:
            if not supabase_manager._initialized:
                await supabase_manager.initialize()

            # Get pending documents that are ready
            result = supabase_manager.admin_client.table(
                "wizard_pending_documents"
            ).select("*").eq(
                "session_id", session_id
            ).eq("status", "ready").execute()

            if not result.data:
                logger.info(f"No ready documents to assign for session {session_id}")
                return 0

            # Get client database connection with hosting info
            client_db, hosting_type, _ = self.connection_manager.get_client_db_client_with_info(UUID(client_id))
            is_shared = hosting_type == 'shared'

            assigned_count = 0
            for pending_doc in result.data:
                document_id = pending_doc.get("document_id")
                if not document_id:
                    continue

                try:
                    # Check if assignment already exists
                    existing = client_db.table("agent_documents").select(
                        "id"
                    ).eq("agent_id", agent_id).eq(
                        "document_id", document_id
                    ).limit(1).execute()

                    if existing.data:
                        continue

                    # Create assignment
                    agent_doc_data = {
                        "agent_id": agent_id,
                        "document_id": document_id,
                    }
                    # Shared pool requires client_id for tenant isolation
                    if is_shared and client_id:
                        agent_doc_data["client_id"] = client_id
                    client_db.table("agent_documents").insert(agent_doc_data).execute()

                    assigned_count += 1

                except Exception as e:
                    logger.warning(f"Failed to assign document {document_id} to agent: {e}")

            logger.info(f"Assigned {assigned_count} documents to agent {agent_id}")
            return assigned_count

        except Exception as e:
            logger.error(f"Error assigning documents to agent: {e}")
            return 0

    async def _assign_abilities_to_agent(
        self,
        agent_id: str,
        client_id: str,
        abilities: Dict[str, bool]
    ) -> int:
        """
        Assign enabled abilities (tools) to the new agent.

        Maps wizard ability slugs to platform tool slugs and creates
        agent_tools records for enabled abilities.

        Returns the number of abilities assigned.
        """
        # Map wizard ability slugs to platform tool slugs
        ability_to_tool_slug = {
            "web_search": "perplexity_ask",
            "usersense": "usersense",
            "documentsense": "documentsense",
            "content_catalyst": "content_catalyst",
        }

        # Filter to only enabled abilities
        enabled_abilities = [slug for slug, enabled in abilities.items() if enabled]
        logger.info(f"[wizard] enabled_abilities: {enabled_abilities} (from {abilities})")

        if not enabled_abilities:
            logger.info(f"No abilities enabled for agent {agent_id}")
            return 0

        try:
            if not supabase_manager._initialized:
                await supabase_manager.initialize()

            # Get tool IDs from platform database
            tool_slugs = [ability_to_tool_slug.get(a, a) for a in enabled_abilities]
            logger.info(f"[wizard] Looking up tool slugs: {tool_slugs}")
            result = supabase_manager.admin_client.table("tools").select(
                "id, slug, name"
            ).in_("slug", tool_slugs).execute()
            logger.info(f"[wizard] Tools found: {result.data}")

            if not result.data:
                logger.warning(f"No tools found for slugs: {tool_slugs}")
                return 0

            # Use platform database for agent_tools (same as tools_service_supabase.py)
            platform_db = supabase_manager.admin_client

            assigned_count = 0
            for tool in result.data:
                tool_id = tool["id"]
                tool_slug = tool["slug"]

                try:
                    # Check if assignment already exists
                    existing = platform_db.table("agent_tools").select(
                        "tool_id"
                    ).eq("agent_id", agent_id).eq(
                        "tool_id", tool_id
                    ).limit(1).execute()

                    if existing.data:
                        continue

                    # Create assignment
                    platform_db.table("agent_tools").insert({
                        "agent_id": agent_id,
                        "tool_id": tool_id,
                    }).execute()

                    assigned_count += 1
                    logger.info(f"Assigned tool {tool_slug} ({tool_id}) to agent {agent_id}")

                except Exception as e:
                    logger.warning(f"Failed to assign tool {tool_slug} to agent: {e}")

            logger.info(f"Assigned {assigned_count} abilities/tools to agent {agent_id}")
            return assigned_count

        except Exception as e:
            logger.error(f"Error assigning abilities to agent: {e}")
            return 0

    async def complete_wizard(
        self,
        session_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """
        Complete the wizard and create the sidekick.

        This is the main entry point for wizard completion. It:
        1. Validates the session and ownership
        2. Creates the agent in the tenant database
        3. Configures voice/LLM settings
        4. Sets up the system prompt from personality
        5. Assigns documents to the agent
        6. Stores API keys if provided
        7. Marks the session as completed

        Returns:
            Dict with success status, agent_id, agent_slug, and name
        """
        # Get session
        session = await wizard_session_service.get_session(session_id)
        if not session:
            return {"success": False, "error": "Session not found"}

        if session["user_id"] != user_id:
            return {"success": False, "error": "Access denied"}

        if session["status"] == "completed":
            return {
                "success": True,
                "already_completed": True,
                "agent_id": session.get("agent_id"),
                "message": "Wizard already completed"
            }

        step_data = session.get("step_data", {})
        client_id = session["client_id"]

        # Validate required fields
        name = step_data.get("name")
        if not name:
            return {"success": False, "error": "Sidekick name is required"}

        slug = step_data.get("slug") or self._generate_slug(name)

        try:
            # Generate system prompt from personality
            system_prompt = self._generate_system_prompt(step_data)

            # Build voice settings
            voice_settings = self._build_voice_settings(step_data)

            # Create the agent
            agent_create = AgentCreate(
                slug=slug,
                name=name,
                description=step_data.get("personality_description", ""),
                system_prompt=system_prompt,
                agent_image=step_data.get("avatar_url"),
                voice_settings=voice_settings,
                enabled=True,
                show_citations=True,
                rag_results_limit=5,
            )

            agent = await self.agent_service.create_agent(
                client_id=UUID(client_id),
                agent_data=agent_create
            )

            if not agent:
                return {"success": False, "error": "Failed to create agent"}

            agent_id = agent.id
            logger.info(f"Created agent {agent_id} ({slug}) for client {client_id}")

            # Store the raw wizard input on the agent for reference
            try:
                client_db = self.connection_manager.get_client_db_client(UUID(client_id))
                # Store the full step_data so we can see exactly what the user requested
                client_db.table("agents").update({
                    "wizard_input": step_data
                }).eq("id", str(agent_id)).execute()
                logger.info(f"Stored wizard input for agent {agent_id}")
            except Exception as exc:
                logger.warning(f"Failed to store wizard input for agent {agent_id}: {exc}")

            # Persist personality traits to agent_personality table
            personality_traits = step_data.get("personality_traits", {})
            if personality_traits:
                try:
                    payload = {
                        "agent_id": str(agent_id),
                        "openness": max(0, min(100, int(personality_traits.get("openness", 50)))),
                        "conscientiousness": max(0, min(100, int(personality_traits.get("conscientiousness", 50)))),
                        "extraversion": max(0, min(100, int(personality_traits.get("extraversion", 50)))),
                        "agreeableness": max(0, min(100, int(personality_traits.get("agreeableness", 50)))),
                        "neuroticism": max(0, min(100, int(personality_traits.get("neuroticism", 50)))),
                    }
                    client_db.table("agent_personality").upsert(payload, on_conflict="agent_id").execute()
                    logger.info(f"Saved personality traits for agent {agent_id}: {payload}")
                except Exception as exc:
                    logger.warning(f"Failed to save personality traits for agent {agent_id}: {exc}")

            # Check if client uses platform keys (BYOK disabled)
            uses_platform_keys = await self._check_uses_platform_keys(client_id)

            if not uses_platform_keys:
                # User provides their own keys - store them
                api_keys = step_data.get("api_keys", {})
                if api_keys:
                    await self._store_api_keys(client_id, api_keys)
                    logger.info(f"Stored API keys for BYOK client {client_id}")
            else:
                # Using platform keys - no need to store API keys
                logger.info(f"Client {client_id} uses platform keys - skipping API key storage")

            # Assign documents to the agent
            doc_count = await self._assign_documents_to_agent(
                session_id=session_id,
                agent_id=agent_id,
                client_id=client_id
            )

            # Assign abilities (tools) to the agent
            abilities = step_data.get("abilities", {})
            logger.info(f"[wizard] abilities from step_data: {abilities}")
            ability_count = await self._assign_abilities_to_agent(
                agent_id=agent_id,
                client_id=client_id,
                abilities=abilities
            )

            # Mark session as completed
            await wizard_session_service.complete_session(session_id, agent_id)

            return {
                "success": True,
                "agent_id": agent_id,
                "agent_slug": slug,
                "client_id": client_id,
                "name": name,
                "documents_assigned": doc_count,
                "abilities_assigned": ability_count,
                "message": "Sidekick created successfully!"
            }

        except Exception as e:
            logger.error(f"Error completing wizard for session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    def _generate_slug(self, name: str) -> str:
        """Generate a URL-safe slug from a name."""
        import re
        slug = name.lower()
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        slug = re.sub(r'[\s_]+', '-', slug)
        slug = re.sub(r'-+', '-', slug)
        slug = slug.strip('-')
        return slug or "sidekick"


# Singleton instance
wizard_completion_service = WizardCompletionService()
