"""
Agent management service using Supabase only (no Redis)
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from supabase import Client as SupabaseClient
import logging
import json

from app.models.agent import Agent, AgentCreate, AgentUpdate, VoiceSettings, WebhookSettings
from app.services.client_service_supabase import ClientService

logger = logging.getLogger(__name__)


class AgentService:
    """Service for managing agents across multiple client Supabase instances"""
    
    def __init__(self, client_service: ClientService, redis_client=None):
        # Ignore redis_client for compatibility
        self.client_service = client_service
    
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
            client_id=client_id,
            agent_image=agent_data.get("agent_image"),
            system_prompt=agent_data.get("system_prompt", ""),
            voice_settings=voice_settings,
            webhooks=webhooks,
            enabled=agent_data.get("enabled", True),
            created_at=created_at,
            updated_at=updated_at,
            tools_config=tools_config
        )
    
    async def get_agent(self, client_id: str, agent_slug: str) -> Optional[Agent]:
        """Get a specific agent from a client's Supabase"""
        # Get client's Supabase instance
        client_supabase = await self.client_service.get_client_supabase_client(client_id)
        if not client_supabase:
            logger.warning(f"No Supabase client found for {client_id}")
            return None
        
        try:
            # Query the agents table
            result = client_supabase.table("agents").select("*").eq("slug", agent_slug).execute()
            
            if result.data and len(result.data) > 0:
                agent_data = result.data[0]
                return self._parse_agent_data(agent_data, client_id)
            
            return None
            
        except Exception as e:
            logger.error(f"Error fetching agent {agent_slug} for client {client_id}: {e}")
            return None
    
    async def get_client_agents(self, client_id: str) -> List[Agent]:
        """Get all agents for a specific client"""
        # Get client's Supabase instance
        client_supabase = await self.client_service.get_client_supabase_client(client_id)
        if not client_supabase:
            logger.warning(f"No Supabase client found for {client_id}")
            return []
        
        try:
            # Query the agents table
            result = client_supabase.table("agents").select("*").order("name").execute()
            
            agents = []
            if result.data:
                for agent_data in result.data:
                    try:
                        agent = self._parse_agent_data(agent_data, client_id)
                        agents.append(agent)
                    except Exception as e:
                        logger.error(f"Error parsing agent {agent_data.get('slug', 'unknown')}: {e}")
                        continue
            
            return agents
            
        except Exception as e:
            logger.error(f"Error fetching agents for client {client_id}: {e}")
            return []
    
    async def get_all_agents_with_clients(self) -> List[Dict[str, Any]]:
        """Get all agents from all clients with client information"""
        all_agents = []
        
        # Get all clients
        clients = await self.client_service.get_all_clients()
        
        for client in clients:
            try:
                # Skip clients without proper Supabase config
                if not client.settings or not client.settings.supabase or not client.settings.supabase.url:
                    continue
                
                # Skip placeholder URLs
                if "pending.supabase.co" in client.settings.supabase.url:
                    continue
                
                # Get agents for this client
                agents = await self.get_client_agents(client.id)
                
                # Add client information to each agent
                for agent in agents:
                    agent_dict = agent.dict()
                    agent_dict["client_name"] = client.name
                    agent_dict["client_domain"] = client.domain
                    all_agents.append(agent_dict)
                    
            except Exception as e:
                logger.error(f"Error fetching agents for client {client.id}: {e}")
                continue
        
        return all_agents
    
    async def create_agent(self, client_id: str, agent_data: AgentCreate) -> Optional[Agent]:
        """Create a new agent in a client's Supabase"""
        # Get client's Supabase instance
        client_supabase = await self.client_service.get_client_supabase_client(client_id)
        if not client_supabase:
            logger.error(f"No Supabase client found for {client_id}")
            return None
        
        try:
            # Create agent
            agent_dict = agent_data.dict()
            agent_dict["created_at"] = datetime.utcnow().isoformat()
            agent_dict["updated_at"] = datetime.utcnow().isoformat()
            
            result = client_supabase.table("agents").insert(agent_dict).execute()
            
            if result.data:
                agent_data = result.data[0]
                agent_data["client_id"] = client_id
                return Agent(**agent_data)
            
            return None
            
        except Exception as e:
            logger.error(f"Error creating agent for client {client_id}: {e}")
            return None
    
    async def update_agent(self, client_id: str, agent_slug: str, update_data: AgentUpdate) -> Optional[Agent]:
        """Update an agent in a client's Supabase"""
        # Get client's Supabase instance
        client_supabase = await self.client_service.get_client_supabase_client(client_id)
        if not client_supabase:
            logger.error(f"No Supabase client found for {client_id}")
            return None
        
        try:
            # Update agent
            update_dict = update_data.dict(exclude_unset=True)
            if update_dict:
                update_dict["updated_at"] = datetime.utcnow().isoformat()
                
                # Store the original voice_settings before converting to JSON
                voice_settings_dict = update_dict.get("voice_settings", {})
                
                # Remove fields that might not exist in the table
                update_dict.pop("tools_config", None)  # Remove if not in table schema
                
                # Convert voice_settings to JSON string if present
                if "voice_settings" in update_dict and update_dict["voice_settings"]:
                    update_dict["voice_settings"] = json.dumps(update_dict["voice_settings"])
                
                # Convert webhooks to individual fields
                if "webhooks" in update_dict:
                    webhooks = update_dict.pop("webhooks")
                    if webhooks:
                        if hasattr(webhooks, "voice_context_webhook_url"):
                            update_dict["n8n_text_webhook_url"] = webhooks.voice_context_webhook_url
                        if hasattr(webhooks, "text_context_webhook_url"):
                            update_dict["n8n_rag_webhook_url"] = webhooks.text_context_webhook_url
                
                result = client_supabase.table("agents").update(update_dict).eq("slug", agent_slug).execute()
                
                if result.data:
                    agent_data = result.data[0]
                    agent_data["client_id"] = client_id
                    
                    # Now update the agent_configurations table
                    await self._update_agent_configuration(client_supabase, agent_slug, update_data, voice_settings_dict)
                    
                    return Agent(**agent_data)
            
            return None
            
        except Exception as e:
            logger.error(f"Error updating agent {agent_slug} for client {client_id}: {e}")
            return None
    
    async def delete_agent(self, client_id: str, agent_slug: str) -> bool:
        """Delete an agent from a client's Supabase"""
        # Get client's Supabase instance
        client_supabase = await self.client_service.get_client_supabase_client(client_id)
        if not client_supabase:
            logger.error(f"No Supabase client found for {client_id}")
            return False
        
        try:
            result = client_supabase.table("agents").delete().eq("slug", agent_slug).execute()
            return len(result.data) > 0 if result.data else False
            
        except Exception as e:
            logger.error(f"Error deleting agent {agent_slug} for client {client_id}: {e}")
            return False
    
    async def get_agent_configuration(self, client_id: str, agent_slug: str) -> Optional[Dict[str, Any]]:
        """Get the latest agent configuration from a client's Supabase"""
        # Get client's Supabase instance
        client_supabase = await self.client_service.get_client_supabase_client(client_id)
        if not client_supabase:
            logger.warning(f"No Supabase client found for {client_id}")
            return None
        
        try:
            # Query the agent_configurations table
            result = client_supabase.table("agent_configurations").select("*").eq("agent_slug", agent_slug).order("last_updated", desc=True).limit(1).execute()
            
            if result.data and len(result.data) > 0:
                config_data = result.data[0]
                return config_data
            
            return None
            
        except Exception as e:
            logger.error(f"Error fetching agent configuration for {agent_slug} in client {client_id}: {e}")
            return None
    
    async def sync_agent_from_configuration(self, client_id: str, agent_slug: str) -> Optional[Agent]:
        """Sync agent data from the latest configuration"""
        config = await self.get_agent_configuration(client_id, agent_slug)
        if not config:
            return None
        
        # Get existing agent
        agent = await self.get_agent(client_id, agent_slug)
        
        # Update or create agent based on configuration
        agent_data = {
            "name": config.agent_name,
            "slug": agent_slug,
            "description": f"Synced from configuration at {config.last_updated}",
            "system_prompt": config.system_prompt,
            "voice_provider": config.tts_provider,
            "voice_settings": {
                "provider": config.tts_provider,
                "model": config.tts_model,
                "voice": config.tts_voice,
                "voice_id": config.cartesia_voice_id or config.elevenlabs_voice_id,
                "language": config.tts_language,
                "speed": 1.0
            },
            "webhooks": {
                "voice_context_webhook_url": config.voice_context_webhook_url,
                "text_context_webhook_url": config.text_context_webhook_url
            },
            "tools_config": config.tools_config or {},
            "enabled": True,
            "active": True
        }
        
        if agent:
            # Update existing agent
            update_data = AgentUpdate(**agent_data)
            return await self.update_agent(client_id, agent_slug, update_data)
        else:
            # Create new agent
            create_data = AgentCreate(**agent_data)
            return await self.create_agent(client_id, create_data)
    
    async def sync_agents_from_supabase(self, client_id: str) -> int:
        """Force sync agents from a client's Supabase and return count"""
        agents = await self.get_client_agents(client_id)
        return len(agents)
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics - returns empty for Supabase-only mode"""
        return {
            "cached_agents": 0,
            "cache_ttl_seconds": 0,
            "message": "No caching in Supabase-only mode"
        }
    
    async def _update_agent_configuration(self, client_supabase: SupabaseClient, agent_slug: str, update_data: AgentUpdate, voice_settings_dict: Dict[str, Any]) -> None:
        """Update the agent_configurations table with new settings"""
        try:
            # First, check if a configuration exists for this agent
            result = client_supabase.table("agent_configurations").select("*").eq("agent_slug", agent_slug).execute()
            
            if result.data and len(result.data) > 0:
                # Configuration exists, update it
                config_data = result.data[0]
                config_id = config_data.get("id")
                
                # Build the update payload for agent_configurations
                config_update = {
                    "last_updated": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                }
                
                # Update basic fields if they're provided
                if hasattr(update_data, "name") and update_data.name:
                    config_update["agent_name"] = update_data.name
                
                if hasattr(update_data, "system_prompt") and update_data.system_prompt is not None:
                    config_update["system_prompt"] = update_data.system_prompt
                
                if hasattr(update_data, "agent_image") and update_data.agent_image is not None:
                    config_update["agent_image"] = update_data.agent_image
                
                # Update voice settings
                if voice_settings_dict:
                    # Update the voice_settings JSON field
                    config_update["voice_settings"] = json.dumps(voice_settings_dict)
                    
                    # Extract specific voice settings
                    voice_id = voice_settings_dict.get("voice_id")
                    if voice_id:
                        config_update["voice_id"] = voice_id
                    
                    temperature = voice_settings_dict.get("temperature")
                    if temperature is not None:
                        config_update["temperature"] = float(temperature)
                    
                    # Build provider_config based on voice settings
                    provider_config = config_data.get("provider_config", {})
                    if isinstance(provider_config, str):
                        provider_config = json.loads(provider_config)
                    
                    # Update LLM settings
                    if voice_settings_dict.get("llm_provider"):
                        if "llm" not in provider_config:
                            provider_config["llm"] = {}
                        provider_config["llm"]["provider"] = voice_settings_dict["llm_provider"]
                        if voice_settings_dict.get("llm_model"):
                            provider_config["llm"]["model"] = voice_settings_dict["llm_model"]
                        if temperature is not None:
                            provider_config["llm"]["temperature"] = float(temperature)
                    
                    # Update STT settings
                    if voice_settings_dict.get("stt_provider"):
                        if "stt" not in provider_config:
                            provider_config["stt"] = {}
                        provider_config["stt"]["provider"] = voice_settings_dict["stt_provider"]
                        # Map provider to model if not specified
                        if voice_settings_dict["stt_provider"] == "deepgram":
                            provider_config["stt"]["model"] = "nova-2"
                        elif voice_settings_dict["stt_provider"] == "groq":
                            provider_config["stt"]["model"] = "whisper-large-v3"
                    
                    # Update TTS settings
                    tts_provider = voice_settings_dict.get("provider") or voice_settings_dict.get("tts_provider")
                    if tts_provider:
                        if "tts" not in provider_config:
                            provider_config["tts"] = {}
                        provider_config["tts"]["provider"] = tts_provider
                        
                        # Add provider-specific settings
                        if tts_provider == "openai":
                            if voice_id:
                                provider_config["tts"]["voice"] = voice_id
                            if voice_settings_dict.get("model"):
                                provider_config["tts"]["model"] = voice_settings_dict["model"]
                        elif tts_provider == "elevenlabs":
                            if voice_id:
                                provider_config["tts"]["voice_id"] = voice_id
                            if voice_settings_dict.get("model"):
                                provider_config["tts"]["model"] = voice_settings_dict["model"]
                            if voice_settings_dict.get("stability") is not None:
                                provider_config["tts"]["stability"] = float(voice_settings_dict["stability"])
                            if voice_settings_dict.get("similarity_boost") is not None:
                                provider_config["tts"]["similarity_boost"] = float(voice_settings_dict["similarity_boost"])
                        elif tts_provider == "cartesia":
                            if voice_id:
                                provider_config["tts"]["voice_id"] = voice_id
                            if voice_settings_dict.get("model"):
                                provider_config["tts"]["model"] = voice_settings_dict["model"]
                            if voice_settings_dict.get("output_format"):
                                provider_config["tts"]["output_format"] = voice_settings_dict["output_format"]
                        elif tts_provider == "speechify":
                            if voice_id:
                                provider_config["tts"]["speechify_voice_id"] = voice_id
                            if voice_settings_dict.get("model"):
                                provider_config["tts"]["speechify_model"] = voice_settings_dict["model"]
                            if voice_settings_dict.get("loudness_normalization") is not None:
                                provider_config["tts"]["speechify_loudness_normalization"] = voice_settings_dict["loudness_normalization"]
                            if voice_settings_dict.get("text_normalization") is not None:
                                provider_config["tts"]["speechify_text_normalization"] = voice_settings_dict["text_normalization"]
                    
                    # Update the provider_config field
                    config_update["provider_config"] = json.dumps(provider_config)
                
                # Update the configuration
                result = client_supabase.table("agent_configurations").update(config_update).eq("id", config_id).execute()
                
                if result.data:
                    logger.info(f"Successfully updated agent_configurations for {agent_slug}")
                else:
                    logger.warning(f"No data returned when updating agent_configurations for {agent_slug}")
            else:
                logger.warning(f"No agent_configurations entry found for {agent_slug} - skipping configuration update")
                
        except Exception as e:
            logger.error(f"Error updating agent_configurations for {agent_slug}: {e}")
            # Don't raise the exception - we don't want to fail the whole update if just the configuration update fails