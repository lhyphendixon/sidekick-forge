"""
Multi-tenant Agent management service for Sidekick Forge Platform

This service manages agents across multiple client databases,
using the ClientConnectionManager for proper tenant isolation.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID
import logging
import json

from app.models.agent import Agent, AgentCreate, AgentUpdate, VoiceSettings, WebhookSettings
from app.services.client_connection_manager import get_connection_manager, ClientConfigurationError

logger = logging.getLogger(__name__)


class AgentService:
    """Service for managing agents in a multi-tenant architecture"""
    
    def __init__(self):
        self.connection_manager = get_connection_manager()
    
    def _parse_agent_data(self, agent_data: Dict[str, Any], client_id: str) -> Agent:
        """Parse agent data from Supabase and handle JSON fields properly"""
        # Parse voice_settings if it's a string
        voice_settings_raw = agent_data.get("voice_settings", {})
        if isinstance(voice_settings_raw, str):
            try:
                voice_settings_dict = json.loads(voice_settings_raw)
            except (json.JSONDecodeError, TypeError):
                voice_settings_dict = {}
        elif isinstance(voice_settings_raw, dict):
            voice_settings_dict = voice_settings_raw
        else:
            voice_settings_dict = {}
        
        # Create VoiceSettings object with defaults
        try:
            voice_settings = VoiceSettings(**voice_settings_dict)
        except Exception:
            voice_settings = VoiceSettings()
        
        # Parse webhooks
        webhooks_raw = agent_data.get("webhooks", {})
        if isinstance(webhooks_raw, str):
            try:
                webhooks_dict = json.loads(webhooks_raw)
            except (json.JSONDecodeError, TypeError):
                webhooks_dict = {}
        elif isinstance(webhooks_raw, dict):
            webhooks_dict = webhooks_raw
        else:
            webhooks_dict = {}
        
        # Handle legacy webhook fields
        if not webhooks_dict.get("voice_context_webhook_url") and agent_data.get("n8n_text_webhook_url"):
            webhooks_dict["voice_context_webhook_url"] = agent_data.get("n8n_text_webhook_url")
        if not webhooks_dict.get("text_context_webhook_url") and agent_data.get("n8n_rag_webhook_url"):
            webhooks_dict["text_context_webhook_url"] = agent_data.get("n8n_rag_webhook_url")
        
        try:
            webhooks = WebhookSettings(**webhooks_dict)
        except Exception:
            webhooks = WebhookSettings()
        
        # Parse tools_config
        tools_config_raw = agent_data.get("tools_config", {})
        if isinstance(tools_config_raw, str):
            try:
                tools_config = json.loads(tools_config_raw)
            except (json.JSONDecodeError, TypeError):
                tools_config = {}
        else:
            tools_config = tools_config_raw or {}

        # Parse sound_settings
        sound_settings_raw = agent_data.get("sound_settings", {})
        if isinstance(sound_settings_raw, str):
            try:
                sound_settings_dict = json.loads(sound_settings_raw)
            except (json.JSONDecodeError, TypeError):
                sound_settings_dict = {}
        elif isinstance(sound_settings_raw, dict):
            sound_settings_dict = sound_settings_raw
        else:
            sound_settings_dict = {}

        # Create SoundSettings object with defaults
        try:
            from app.models.agent import SoundSettings
            sound_settings = SoundSettings(**sound_settings_dict)
        except Exception:
            from app.models.agent import SoundSettings
            sound_settings = SoundSettings()

        # Parse datetime fields
        created_at = agent_data.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            except ValueError:
                created_at = datetime.utcnow()
        elif not created_at:
            created_at = datetime.utcnow()
        
        updated_at = agent_data.get("updated_at")
        if isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
            except ValueError:
                updated_at = datetime.utcnow()
        elif not updated_at:
            updated_at = datetime.utcnow()
        
        # Create Agent object
        return Agent(
            id=agent_data.get("id"),
            slug=agent_data["slug"],
            name=agent_data["name"],
            description=agent_data.get("description", ""),
            agent_image=agent_data.get("agent_image", ""),
            system_prompt=agent_data.get("system_prompt", "You are a helpful assistant."),
            webhook_url=agent_data.get("webhook_url"),
            model=agent_data.get("model", "gpt-4"),
            enabled=agent_data.get("enabled", True),
            client_id=client_id,
            tools_config=tools_config,
            voice_settings=voice_settings,
            sound_settings=sound_settings,
            webhooks=webhooks,
            context_retention_minutes=agent_data.get("context_retention_minutes", 30),
            max_context_messages=agent_data.get("max_context_messages", 50),
            rag_results_limit=agent_data.get("rag_results_limit", 5),
            created_at=created_at,
            updated_at=updated_at
        )
    
    async def get_agents(self, client_id: UUID) -> List[Agent]:
        """Get all agents for a specific client"""
        try:
            # Get client-specific database connection
            client_db = self.connection_manager.get_client_db_client(client_id)
            
            # Fetch agents from client's database
            result = client_db.table("agents").select("*").execute()
            
            agents = []
            for agent_data in result.data:
                try:
                    agent = self._parse_agent_data(agent_data, str(client_id))
                    agents.append(agent)
                except Exception as e:
                    logger.error(f"Error parsing agent {agent_data.get('slug', 'unknown')}: {e}")
                    continue
            
            logger.info(f"Retrieved {len(agents)} agents for client {client_id}")
            return agents
            
        except ClientConfigurationError as e:
            logger.error(f"Client configuration error: {e}")
            raise
        except Exception as e:
            logger.error(f"Error fetching agents for client {client_id}: {e}")
            return []
    
    async def get_agent(self, client_id: UUID, agent_slug: str) -> Optional[Agent]:
        """Get a specific agent for a client"""
        try:
            # Get client-specific database connection
            client_db = self.connection_manager.get_client_db_client(client_id)
            
            # Fetch agent from client's database
            result = client_db.table("agents").select("*").eq("slug", agent_slug).single().execute()
            
            if result.data:
                return self._parse_agent_data(result.data, str(client_id))
            
            logger.warning(f"Agent {agent_slug} not found for client {client_id}")
            return None
            
        except ClientConfigurationError as e:
            logger.error(f"Client configuration error: {e}")
            raise
        except Exception as e:
            logger.error(f"Error fetching agent {agent_slug} for client {client_id}: {e}")
            return None
    
    async def create_agent(self, client_id: UUID, agent_data: AgentCreate) -> Optional[Agent]:
        """Create a new agent for a client"""
        try:
            # Get client-specific database connection with hosting info
            client_db, hosting_type, _ = self.connection_manager.get_client_db_client_with_info(client_id)

            # Prepare data for insertion
            data = {
                "slug": agent_data.slug,
                "name": agent_data.name,
                "description": agent_data.description,
                "system_prompt": agent_data.system_prompt,
                "enabled": agent_data.enabled,
                "voice_settings": agent_data.voice_settings.dict() if agent_data.voice_settings else {},
                "sound_settings": {"thinking_sound": "none", "thinking_volume": 0.3, "ambient_sound": "none", "ambient_volume": 0.15},
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            if agent_data.agent_image is not None:
                data["agent_image"] = agent_data.agent_image
            if agent_data.show_citations is not None:
                data["show_citations"] = agent_data.show_citations
            if agent_data.rag_results_limit is not None:
                data["rag_results_limit"] = agent_data.rag_results_limit

            # Shared pool requires client_id for tenant isolation
            if hosting_type == 'shared':
                data["client_id"] = str(client_id)

            # Insert into client's database
            result = client_db.table("agents").insert(data).execute()
            
            if result.data:
                logger.info(f"Created agent {agent_data.slug} for client {client_id}")
                return self._parse_agent_data(result.data[0], str(client_id))
            
            return None
            
        except ClientConfigurationError as e:
            logger.error(f"Client configuration error: {e}")
            raise
        except Exception as e:
            logger.error(f"Error creating agent for client {client_id}: {e}")
            return None
    
    async def update_agent(self, client_id: UUID, agent_slug: str, agent_update: AgentUpdate) -> Optional[Agent]:
        """Update an existing agent for a client"""
        try:
            # Get client-specific database connection
            client_db = self.connection_manager.get_client_db_client(client_id)
            
            # Prepare update data
            update_data = {}
            if agent_update.name is not None:
                update_data["name"] = agent_update.name
            if agent_update.description is not None:
                update_data["description"] = agent_update.description
            if agent_update.system_prompt is not None:
                update_data["system_prompt"] = agent_update.system_prompt
            if agent_update.agent_image is not None:
                update_data["agent_image"] = agent_update.agent_image
            if agent_update.enabled is not None:
                update_data["enabled"] = agent_update.enabled
            if agent_update.voice_settings is not None:
                update_data["voice_settings"] = agent_update.voice_settings.dict()
            if agent_update.sound_settings is not None:
                update_data["sound_settings"] = agent_update.sound_settings.dict()
                logger.info(f"[update_agent] Including sound_settings: {update_data['sound_settings']}")
            if agent_update.show_citations is not None:
                update_data["show_citations"] = agent_update.show_citations
            if agent_update.rag_results_limit is not None:
                update_data["rag_results_limit"] = agent_update.rag_results_limit

            update_data["updated_at"] = datetime.utcnow().isoformat()
            
            # Update in client's database
            result = client_db.table("agents").update(update_data).eq("slug", agent_slug).execute()
            
            if result.data:
                logger.info(f"Updated agent {agent_slug} for client {client_id}")
                return self._parse_agent_data(result.data[0], str(client_id))
            
            return None
            
        except ClientConfigurationError as e:
            logger.error(f"Client configuration error: {e}")
            raise
        except Exception as e:
            logger.error(f"Error updating agent {agent_slug} for client {client_id}: {e}")
            return None
    
    async def delete_agent(self, client_id: UUID, agent_slug: str) -> bool:
        """Delete an agent for a client"""
        try:
            # Get client-specific database connection
            client_db = self.connection_manager.get_client_db_client(client_id)

            # Verify agent exists and get its ID
            check = client_db.table("agents").select("id").eq("slug", agent_slug).execute()
            if not check.data:
                logger.warning(f"Agent {agent_slug} not found for client {client_id}")
                return False

            agent_id = check.data[0]["id"]

            # Delete related records that have foreign key references
            for table in [
                "conversation_transcripts",
                "conversations",
                "agent_tools",
                "agent_documents",
            ]:
                try:
                    client_db.table(table).delete().eq("agent_id", agent_id).execute()
                except Exception as e:
                    logger.debug(f"Cleanup {table} for agent {agent_id}: {e}")

            # Delete the agent
            client_db.table("agents").delete().eq("slug", agent_slug).execute()

            # Verify deletion succeeded
            verify = client_db.table("agents").select("id").eq("slug", agent_slug).execute()
            if verify.data:
                logger.error(f"Delete failed for agent {agent_slug} - still exists")
                return False

            logger.info(f"Deleted agent {agent_slug} for client {client_id}")
            return True

        except ClientConfigurationError as e:
            logger.error(f"Client configuration error: {e}")
            raise
        except Exception as e:
            logger.error(f"Error deleting agent {agent_slug} for client {client_id}: {e}")
            return False
    
    async def find_agent_client(self, agent_slug: str) -> Optional[UUID]:
        """
        Find which client owns a specific agent.
        
        This is used when we only have an agent_slug and need to find the client_id.
        """
        return await self.connection_manager.find_client_by_agent(agent_slug)
    
    async def get_client_info(self, client_id: UUID) -> Dict[str, Any]:
        """Get basic client information"""
        return self.connection_manager.get_client_info(client_id)
    
    async def get_client_api_keys(self, client_id: UUID) -> Dict[str, Optional[str]]:
        """Get API keys configured for a client"""
        return self.connection_manager.get_client_api_keys(client_id)
