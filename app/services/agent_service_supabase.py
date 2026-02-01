"""
Agent management service using Supabase only (no Redis)
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from supabase import Client as SupabaseClient
import logging
import json
import re

from app.models.agent import Agent, AgentCreate, AgentUpdate, VoiceSettings, WebhookSettings, SoundSettings
from app.models.client import ChannelSettings, TelegramChannelSettings
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
            logger.info(f"[_parse_agent_data] Parsed voice_settings: avatar_image_url={voice_settings.avatar_image_url}, avatar_model_type={voice_settings.avatar_model_type}")
        except Exception as e:
            logger.error(f"[_parse_agent_data] Failed to parse voice_settings: {e}, raw={voice_settings_dict}")
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
            sound_settings = SoundSettings(**sound_settings_dict)
        except Exception as e:
            logger.warning(f"[_parse_agent_data] Failed to parse sound_settings: {e}, using defaults")
            sound_settings = SoundSettings()

        # Parse tools_config
        tools_config_raw = agent_data.get("tools_config", {})
        if isinstance(tools_config_raw, str):
            try:
                tools_config = json.loads(tools_config_raw)
            except (json.JSONDecodeError, TypeError):
                tools_config = {}
        else:
            tools_config = tools_config_raw or {}

        # Channel settings are stored inside tools_config to avoid schema drift
        channels_raw = {}
        if isinstance(tools_config, dict):
            channels_raw = tools_config.get("channels") or tools_config.get("_channels") or {}

        channels = None
        if channels_raw:
            try:
                channels = ChannelSettings(**channels_raw)
            except Exception:
                channels = None
        
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
            agent_image=agent_data.get("agent_image") or None,
            system_prompt=agent_data.get("system_prompt", ""),
            voice_settings=voice_settings,
            sound_settings=sound_settings,
            webhooks=webhooks,
            enabled=agent_data.get("enabled", True),
            show_citations=agent_data.get("show_citations", True),
            created_at=created_at,
            updated_at=updated_at,
            tools_config=tools_config,
            channels=channels,
            rag_results_limit=agent_data.get("rag_results_limit", 5),
            supertab_enabled=agent_data.get("supertab_enabled", False),
            supertab_experience_id=agent_data.get("supertab_experience_id"),
            voice_chat_enabled=agent_data.get("voice_chat_enabled", True),
            text_chat_enabled=agent_data.get("text_chat_enabled", True),
            video_chat_enabled=agent_data.get("video_chat_enabled", False),
        )
    
    async def get_agent(self, client_id: str, agent_slug: str) -> Optional[Agent]:
        """Get a specific agent from a client's Supabase"""
        import time
        method_start = time.time()
        
        # Check if this is the Autonomite client using the main Supabase instance
        from app.config import settings
        from supabase import create_client
        
        config_start = time.time()
        client_config = await self.client_service.get_client_supabase_config(client_id, auto_sync=False)
        logger.info(f"[TIMING] get_client_supabase_config took {time.time() - config_start:.2f}s")
        
        if client_config and client_config.get("url") == settings.supabase_url:
            # This client uses the main Supabase instance, so use admin client
            logger.info(f"Client {client_id} uses main Supabase instance, using admin client")
            # Create admin client directly - try service role key first, fall back to anon key
            client_create_start = time.time()
            try:
                client_supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
                logger.info(f"[TIMING] create_client took {time.time() - client_create_start:.2f}s")
                
                # Test the connection
                test_start = time.time()
                test_result = client_supabase.table("agents").select("id").limit(1).execute()
                logger.info(f"[TIMING] connection test took {time.time() - test_start:.2f}s")
            except Exception as e:
                logger.warning(f"Service role key failed, falling back to anon key: {e}")
                # Fall back to anon key from client config
                anon_key = client_config.get("anon_key", settings.supabase_anon_key)
                client_supabase = create_client(settings.supabase_url, anon_key)
        else:
            # Get client's separate Supabase instance
            client_fetch_start = time.time()
            client_supabase = await self.client_service.get_client_supabase_client(client_id, auto_sync=False)
            logger.info(f"[TIMING] get_client_supabase_client took {time.time() - client_fetch_start:.2f}s")
            if not client_supabase:
                logger.warning(f"No Supabase client found for {client_id}")
                return None
        
        try:
            # Query the agents table
            query_start = time.time()
            result = client_supabase.table("agents").select("*").eq("slug", agent_slug).execute()
            logger.info(f"[TIMING] agent query took {time.time() - query_start:.2f}s")
            
            if result.data and len(result.data) > 0:
                parse_start = time.time()
                agent_data = result.data[0]
                parsed_agent = self._parse_agent_data(agent_data, client_id)
                logger.info(f"[TIMING] parse_agent_data took {time.time() - parse_start:.2f}s")
                logger.info(f"[TIMING] TOTAL get_agent took {time.time() - method_start:.2f}s")
                return parsed_agent
            
            logger.info(f"[TIMING] TOTAL get_agent (no data) took {time.time() - method_start:.2f}s")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching agent {agent_slug} for client {client_id}: {e}")
            logger.info(f"[TIMING] TOTAL get_agent (error) took {time.time() - method_start:.2f}s")
            return None
    
    async def get_client_agents(self, client_id: str) -> List[Agent]:
        """Get all agents for a specific client from their Supabase instance"""
        from supabase import create_client
        
        # Get client's Supabase configuration
        client_config = await self.client_service.get_client_supabase_config(client_id)
        if not client_config:
            logger.warning(f"No Supabase config found for client {client_id}")
            return []
        
        # Skip placeholder URLs
        if "pending.supabase.co" in client_config.get("url", ""):
            logger.info(f"Skipping client {client_id} with placeholder URL")
            return []
        
        # Get client's Supabase instance - use service role key for admin access
        try:
            client_supabase = create_client(
                client_config["url"], 
                client_config["service_role_key"]
            )
            logger.info(f"Connected to client {client_id} Supabase at {client_config['url']}")
        except Exception as e:
            logger.error(f"Failed to create Supabase client for {client_id}: {e}")
            return []
        
        try:
            # Query the agents table in the client's database
            result = client_supabase.table("agents").select("*").order("name").execute()
            logger.info(f"Query returned {len(result.data) if result.data else 0} agents for client {client_id}")
            
            agents = []
            if result.data:
                for agent_data in result.data:
                    try:
                        agent = self._parse_agent_data(agent_data, client_id)
                        agents.append(agent)
                        logger.debug(f"Parsed agent: {agent.name} ({agent.slug})")
                    except Exception as e:
                        logger.error(f"Error parsing agent {agent_data.get('slug', 'unknown')}: {e}")
                        continue
            
            return agents
            
        except Exception as e:
            logger.error(f"Error querying agents table for client {client_id}: {e}", exc_info=True)
            # Check if it's a table not found error
            if "relation" in str(e) and "does not exist" in str(e):
                logger.error(f"Agents table does not exist in client {client_id} database")
            return []
    
    async def get_all_agents_with_clients(self) -> List[Dict[str, Any]]:
        """Get all agents from all clients with client information"""
        all_agents = []
        
        # Get all clients from platform database
        clients = await self.client_service.get_all_clients()
        logger.info(f"Found {len(clients)} clients in platform database")
        
        for client in clients:
            try:
                # Skip clients without proper Supabase config
                if not client.settings or not client.settings.supabase or not client.settings.supabase.url:
                    logger.warning(f"Client {client.name} missing Supabase configuration")
                    continue
                
                # Skip placeholder URLs
                if "pending.supabase.co" in client.settings.supabase.url:
                    logger.info(f"Skipping client {client.name} with placeholder URL")
                    continue
                
                logger.info(f"Fetching agents for client {client.name} from {client.settings.supabase.url}")
                
                # Get agents for this client from their database
                agents = await self.get_client_agents(client.id)
                logger.info(f"Found {len(agents)} agents for client {client.name}")
                
                # Add client information to each agent
                for agent in agents:
                    agent_dict = agent.dict()
                    agent_dict["client_name"] = client.name
                    agent_dict["client_domain"] = client.domain
                    all_agents.append(agent_dict)
                    
            except Exception as e:
                logger.error(f"Error fetching agents for client {client.name} ({client.id}): {e}", exc_info=True)
                continue
        
        logger.info(f"Total agents across all clients: {len(all_agents)}")
        return all_agents
    
    async def create_agent(self, client_id: str, agent_data: AgentCreate) -> Optional[Agent]:
        """Create a new agent in a client's Supabase"""
        # Check if this is the Autonomite client using the main Supabase instance
        from app.config import settings
        from supabase import create_client
        
        client_config = await self.client_service.get_client_supabase_config(client_id)
        if client_config and client_config.get("url") == settings.supabase_url:
            # This client uses the main Supabase instance, so use admin client
            logger.info(f"Client {client_id} uses main Supabase instance, using admin client")
            # Create admin client directly - try service role key first, fall back to anon key
            try:
                client_supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
                # Test the connection
                test_result = client_supabase.table("agents").select("id").limit(1).execute()
            except Exception as e:
                logger.warning(f"Service role key failed, falling back to anon key: {e}")
                # Fall back to anon key from client config
                anon_key = client_config.get("anon_key", settings.supabase_anon_key)
                client_supabase = create_client(settings.supabase_url, anon_key)
        else:
            # Get client's separate Supabase instance
            client_supabase = await self.client_service.get_client_supabase_client(client_id)
            if not client_supabase:
                logger.error(f"No Supabase client found for {client_id}")
                return None
        
        try:
            # Create agent
            agent_dict = agent_data.dict()
            agent_dict["created_at"] = datetime.utcnow().isoformat()
            agent_dict["updated_at"] = datetime.utcnow().isoformat()

            # Ensure show_citations always has a boolean (Supabase default won't fire if we send NULL)
            if agent_dict.get("show_citations") is None:
                agent_dict["show_citations"] = True

            # Convert nested objects to JSON strings for Supabase
            if "voice_settings" in agent_dict and agent_dict["voice_settings"]:
                agent_dict["voice_settings"] = json.dumps(agent_dict["voice_settings"])
            if "webhooks" in agent_dict and agent_dict["webhooks"]:
                agent_dict["webhooks"] = json.dumps(agent_dict["webhooks"])
            if "tools_config" in agent_dict and agent_dict["tools_config"]:
                agent_dict["tools_config"] = json.dumps(agent_dict["tools_config"])
            
            # Remove fields that are not in the database schema
            agent_dict.pop("client_id", None)
            agent_dict.pop("tools_config", None)  # Remove if not in table schema
            agent_dict.pop("webhooks", None)  # Remove if not in table schema
            
            logger.info(f"Creating agent with data: {agent_dict}")
            result = client_supabase.table("agents").insert(agent_dict).execute()
            
            if result.data and len(result.data) > 0:
                created_agent_data = result.data[0]
                # Ensure client_id is set for parsing
                created_agent_data["client_id"] = client_id

                # Auto-assign existing documents to the new agent
                agent_id = created_agent_data.get("id")
                if agent_id:
                    await self._auto_assign_existing_documents_to_agent(
                        agent_id=agent_id,
                        client_id=client_id,
                        client_supabase=client_supabase,
                    )

                return self._parse_agent_data(created_agent_data, client_id)

            logger.error(f"Agent creation returned no data for client {client_id}")
            return None
            
        except Exception as e:
            logger.error(f"Error creating agent for client {client_id}: {e}")
            logger.error(f"Agent data attempted: {agent_dict}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
    
    async def update_agent(self, client_id: str, agent_slug: str, update_data: AgentUpdate) -> Optional[Agent]:
        """Update an agent in a client's Supabase"""
        # Get client's Supabase instance (mirror get_agent special-casing for main instance)
        from app.config import settings
        from supabase import create_client
        client_supabase = None
        try:
            client_config = await self.client_service.get_client_supabase_config(client_id, auto_sync=False)
            if client_config and client_config.get("url") == settings.supabase_url:
                logger.info(f"Client {client_id} uses main Supabase instance for update, using admin client")
                try:
                    client_supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
                    # quick ping
                    client_supabase.table("agents").select("id").limit(1).execute()
                except Exception as e:
                    logger.error(f"Failed to create admin client for update: {e}")
            else:
                client_supabase = await self.client_service.get_client_supabase_client(client_id, auto_sync=False)
        except Exception as e:
            logger.error(f"Error getting Supabase client for update: {e}")

        if not client_supabase:
            logger.error(f"No Supabase client found for {client_id} (update)")
            return None
        
        try:
            # Update agent
            update_dict = update_data.dict(exclude_unset=True)
            if update_dict:
                update_dict["updated_at"] = datetime.utcnow().isoformat()
                
                # Remove fields that are not direct columns
                update_dict.pop("channels", None)      # Channels are stored inside tools_config, not as a column
                # Note: tools_config IS a valid column in the agents table, so we keep it
                
                # Ensure voice_settings is a plain dict with proper serialization (let Supabase handle JSONB)
                if "voice_settings" in update_dict and update_dict["voice_settings"]:
                    try:
                        if hasattr(update_dict["voice_settings"], "dict"):
                            # Use mode='json' to ensure enums are serialized to strings
                            if hasattr(update_dict["voice_settings"], "model_dump"):
                                # Pydantic v2
                                update_dict["voice_settings"] = update_dict["voice_settings"].model_dump(mode='json')
                            else:
                                # Pydantic v1 fallback - manually convert enums
                                vs_dict = update_dict["voice_settings"].dict()
                                # Convert any enum values to strings
                                for key, value in vs_dict.items():
                                    if hasattr(value, 'value'):
                                        vs_dict[key] = value.value
                                update_dict["voice_settings"] = vs_dict
                        elif isinstance(update_dict["voice_settings"], dict):
                            # Already a dict but might have enum objects
                            vs_dict = update_dict["voice_settings"]
                            for key, value in list(vs_dict.items()):
                                if hasattr(value, 'value'):
                                    vs_dict[key] = value.value
                        # Debug: log voice_settings content
                        logger.info(f"[agents.update] VOICE_SETTINGS DICT: {update_dict['voice_settings']}")
                        logger.info(f"[agents.update] avatar_image_url in dict: {update_dict['voice_settings'].get('avatar_image_url')}")
                    except Exception as e:
                        logger.error(f"[agents.update] voice_settings conversion error: {e}")
                
                # Handle sound_settings serialization (similar to voice_settings)
                if "sound_settings" in update_dict and update_dict["sound_settings"]:
                    try:
                        if hasattr(update_dict["sound_settings"], "model_dump"):
                            # Pydantic v2
                            update_dict["sound_settings"] = update_dict["sound_settings"].model_dump(mode='json')
                        elif hasattr(update_dict["sound_settings"], "dict"):
                            # Pydantic v1 fallback
                            update_dict["sound_settings"] = update_dict["sound_settings"].dict()
                        elif isinstance(update_dict["sound_settings"], dict):
                            # Already a dict - ensure it's a clean copy
                            update_dict["sound_settings"] = dict(update_dict["sound_settings"])
                        logger.info(f"[agents.update] SOUND_SETTINGS DICT: {update_dict['sound_settings']}")
                    except Exception as e:
                        logger.error(f"[agents.update] sound_settings conversion error: {e}")

                # Do not update legacy webhook columns here to avoid 400s on tenants
                # where these columns may not exist. Skip mapping entirely.
                if "webhooks" in update_dict:
                    update_dict.pop("webhooks", None)
                
                # Discover existing columns for this tenant's agents table and prune payload
                # Known core columns that exist on all agents tables (fallback if RPC fails)
                KNOWN_AGENT_COLUMNS = {
                    'id', 'slug', 'name', 'description', 'client_id', 'agent_image',
                    'system_prompt', 'voice_settings', 'sound_settings', 'enabled', 'show_citations',
                    'rag_results_limit', 'supertab_enabled', 'supertab_experience_id',
                    'voice_chat_enabled', 'text_chat_enabled', 'video_chat_enabled',
                    'tools_config', 'rag_config',
                    'created_at', 'updated_at'
                }
                cols = None
                try:
                    cols_info = client_supabase.rpc("pg_table_cols", {"table_name": "agents"}).execute()
                    # If RPC not available, fall back silently
                    if getattr(cols_info, 'data', None):
                        cols = {c.get('name') for c in cols_info.data if isinstance(c, dict)}
                        logger.info(f"[agents.update] discovered columns: {cols}")
                except Exception as col_err:
                    logger.warning(f"[agents.update] column discovery RPC failed: {col_err}")

                # Use discovered columns or fallback to known columns
                if not cols:
                    cols = KNOWN_AGENT_COLUMNS
                    logger.info(f"[agents.update] using fallback known columns: {cols}")

                if cols:
                    original_keys = set(update_dict.keys())
                    update_dict = {k: v for k, v in update_dict.items() if k in cols}
                    filtered_keys = original_keys - set(update_dict.keys())
                    if filtered_keys:
                        logger.warning(f"[agents.update] FILTERED OUT columns not in schema: {filtered_keys}")

                # Debug: log update payload
                try:
                    logger.info(f"[agents.update] client={client_id} slug={agent_slug} payload_keys={list(update_dict.keys())}")
                except Exception:
                    pass

                # Prefer updating by primary key id if available
                agent_id = None
                try:
                    cur = (
                        client_supabase
                        .table("agents")
                        .select("id")
                        .eq("slug", agent_slug)
                        .limit(1)
                        .execute()
                    )
                    if cur.data:
                        agent_id = cur.data[0].get("id")
                except Exception:
                    pass

                update_query = (
                    client_supabase
                    .table("agents")
                    .update(update_dict)
                )
                if agent_id:
                    update_query = update_query.eq("id", agent_id)
                else:
                    update_query = update_query.eq("slug", agent_slug)

                # Request the updated row to avoid silent empty responses
                try:
                    update_query = update_query.select("*")
                except Exception:
                    # Some client versions may not support select() chaining; fall back to plain update
                    pass

                # Execute the update with retry logic for missing columns
                logger.info(f"[agents.update] executing update for agent_slug={agent_slug}, agent_id={agent_id}")

                # Columns that may not exist on all tenant databases
                optional_columns = [
                    'voice_chat_enabled', 'text_chat_enabled', 'video_chat_enabled',
                    'show_citations', 'rag_results_limit', 'supertab_enabled',
                    'supertab_experience_id', 'tools_config', 'rag_config',
                    'sound_settings'
                ]

                result = None
                columns_to_remove = []
                max_retries = len(optional_columns)

                for attempt in range(max_retries + 1):
                    try:
                        # Remove any columns identified as missing in previous attempts
                        current_dict = {k: v for k, v in update_dict.items() if k not in columns_to_remove}

                        update_query = (
                            client_supabase
                            .table("agents")
                            .update(current_dict)
                        )
                        if agent_id:
                            update_query = update_query.eq("id", agent_id)
                        else:
                            update_query = update_query.eq("slug", agent_slug)

                        try:
                            update_query = update_query.select("*")
                        except Exception:
                            pass

                        result = update_query.execute()
                        logger.info(f"[agents.update] result.data={result.data}")
                        break  # Success, exit retry loop

                    except Exception as update_err:
                        error_str = str(update_err)
                        # Check for PostgREST column not found error (PGRST204)
                        if "PGRST204" in error_str or "Could not find the" in error_str:
                            # Extract the missing column name from error message
                            import re
                            match = re.search(r"Could not find the '(\w+)' column", error_str)
                            if match:
                                missing_col = match.group(1)
                                if missing_col not in columns_to_remove:
                                    columns_to_remove.append(missing_col)
                                    logger.warning(f"[agents.update] Column '{missing_col}' not found, retrying without it (attempt {attempt + 1})")
                                    continue
                        # If we can't handle the error or it's not a missing column error, re-raise
                        raise

                if result is None:
                    logger.error(f"[agents.update] All retry attempts failed")
                    return None

                # Debug: log what voice_settings came back from the database
                if result.data and len(result.data) > 0:
                    returned_vs = result.data[0].get('voice_settings', {})
                    logger.info(f"[agents.update] RETURNED voice_settings: {returned_vs}")
                    logger.info(f"[agents.update] RETURNED avatar_image_url: {returned_vs.get('avatar_image_url') if returned_vs else 'N/A'}")
                try:
                    # Some clients expose result.error; log if present
                    err = getattr(result, "error", None)
                    if err:
                        logger.error(f"[agents.update] supabase error: {err}")
                except Exception:
                    pass

                if result.data:
                    agent_data = result.data[0]
                    return self._parse_agent_data(agent_data, client_id)

                # Fallback: if update returned no rows (e.g., RLS prevents returning), try fetching the row
                try:
                    refetch_q = (
                        client_supabase
                        .table("agents")
                        .select("*")
                    )
                    if agent_id:
                        refetch_q = refetch_q.eq("id", agent_id)
                    else:
                        refetch_q = refetch_q.eq("slug", agent_slug)
                    refetch = refetch_q.execute()
                    if refetch.data:
                        agent_data = refetch.data[0]
                        return self._parse_agent_data(agent_data, client_id)
                except Exception as _:
                    pass
            
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

    async def _auto_assign_existing_documents_to_agent(
        self,
        agent_id: str,
        client_id: str,
        client_supabase=None,
    ):
        """
        Auto-assign all existing documents to a newly created agent.
        This ensures agents have access to all documents in the knowledge base.
        """
        try:
            if not client_supabase:
                client_supabase = await self.client_service.get_client_supabase_client(client_id)

            if not client_supabase:
                logger.error(f"No Supabase client found for client {client_id}")
                return

            # Get all documents with status 'ready' (processed and available)
            docs_result = client_supabase.table('documents').select('id').eq('status', 'ready').execute()

            if not docs_result.data:
                logger.info(f"No existing documents to assign for new agent {agent_id}")
                return

            documents = docs_result.data
            logger.info(f"Auto-assigning {len(documents)} existing documents to new agent {agent_id}")

            assigned_count = 0
            for doc in documents:
                doc_id = doc.get('id')
                if not doc_id:
                    continue

                # Check if assignment already exists
                existing = client_supabase.table('agent_documents') \
                    .select('id') \
                    .eq('agent_id', agent_id) \
                    .eq('document_id', doc_id) \
                    .limit(1) \
                    .execute()

                if existing.data:
                    continue

                # Create new assignment
                agent_doc_data = {
                    'agent_id': agent_id,
                    'document_id': doc_id,
                    'client_id': client_id,
                    'access_type': 'read',
                    'enabled': True
                }

                try:
                    client_supabase.table('agent_documents').insert(agent_doc_data).execute()
                    assigned_count += 1
                except Exception as insert_error:
                    logger.warning(f"Failed to assign document {doc_id} to agent {agent_id}: {insert_error}")

            logger.info(f"Auto-assigned {assigned_count} documents to new agent {agent_id}")

        except Exception as e:
            logger.error(f"Error auto-assigning existing documents to agent {agent_id}: {e}")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics - returns empty for Supabase-only mode"""
        return {
            "cached_agents": 0,
            "cache_ttl_seconds": 0,
            "message": "No caching in Supabase-only mode"
        }
