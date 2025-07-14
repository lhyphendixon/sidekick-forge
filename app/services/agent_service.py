"""
Agent management service with client-specific Supabase connections
"""
import json
from typing import List, Optional, Dict, Any
from datetime import datetime
import redis
from fastapi import HTTPException
from supabase import create_client, Client as SupabaseClient

from app.models.agent import Agent, AgentCreate, AgentUpdate, AgentInDB, AgentWithClient
from app.models.client import ClientInDB
from app.services.client_service_hybrid import ClientService


class AgentService:
    """Service for managing agents with client-specific Supabase connections"""
    
    def __init__(self, client_service: ClientService, redis_client: redis.Redis):
        self.client_service = client_service
        self.redis = redis_client
        self.cache_ttl = 600  # 10 minutes cache TTL
        self.cache_prefix = "agent:"
        
    def _get_cache_key(self, client_id: str, agent_slug: str) -> str:
        """Get Redis cache key for an agent"""
        return f"{self.cache_prefix}{client_id}:{agent_slug}"
    
    def _get_client_agents_key(self, client_id: str) -> str:
        """Get Redis cache key for client's agent list"""
        return f"agents:client:{client_id}"
    
    def _parse_voice_settings(self, voice_settings_data: Any) -> Dict[str, Any]:
        """Parse voice_settings from Supabase (could be string or dict)"""
        if isinstance(voice_settings_data, str):
            try:
                return json.loads(voice_settings_data)
            except (json.JSONDecodeError, TypeError):
                return {}
        elif isinstance(voice_settings_data, dict):
            return voice_settings_data
        else:
            return {}
    
    def _build_agent_from_supabase_record(self, agent_record: Dict[str, Any], client_id: str) -> AgentInDB:
        """Build AgentInDB object from Supabase record"""
        voice_settings_raw = agent_record.get("voice_settings", {})
        voice_settings_parsed = self._parse_voice_settings(voice_settings_raw)
        
        return AgentInDB(
            id=agent_record.get("id"),
            client_id=client_id,
            slug=agent_record["slug"],
            name=agent_record["name"],
            description=agent_record.get("description"),
            agent_image=agent_record.get("agent_image"),
            system_prompt=agent_record["system_prompt"],
            voice_settings={
                "provider": agent_record.get("provider_type", "livekit"),
                "voice_id": voice_settings_parsed.get("voice_id", "alloy"),
                "temperature": voice_settings_parsed.get("temperature", 0.7),
                "provider_config": voice_settings_parsed.get("provider_config", {})
            },
            webhooks={
                "voice_context_webhook_url": agent_record.get("voice_context_webhook_url") or agent_record.get("n8n_text_webhook_url"),
                "text_context_webhook_url": agent_record.get("text_context_webhook_url") or agent_record.get("n8n_rag_webhook_url")
            },
            enabled=agent_record.get("enabled", True),
            tools_config=agent_record.get("tools_config"),
            created_at=agent_record.get("created_at"),
            updated_at=agent_record.get("updated_at")
        )
    
    async def _get_client_supabase(self, client_id: str) -> Optional[SupabaseClient]:
        """Get Supabase client for a specific client"""
        client = await self.client_service.get_client(client_id, auto_sync=False)
        if not client:
            raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
        
        try:
            return create_client(
                client.settings.supabase.url,
                client.settings.supabase.service_role_key
            )
        except Exception as e:
            print(f"Error creating Supabase client for {client_id}: {e}")
            return None
    
    async def create_agent(self, client_id: str, agent_data: AgentCreate) -> AgentInDB:
        """Create a new agent in client's Supabase"""
        # Check if agent already exists
        existing = await self.get_agent(client_id, agent_data.slug)
        if existing:
            raise HTTPException(status_code=400, detail=f"Agent with slug {agent_data.slug} already exists")
        
        # Get client's Supabase
        supabase = await self._get_client_supabase(client_id)
        if not supabase:
            raise HTTPException(status_code=500, detail="Could not connect to client's Supabase")
        
        # Prepare agent data
        now = datetime.utcnow()
        agent_dict = {
            "slug": agent_data.slug,
            "name": agent_data.name,
            "description": agent_data.description,
            "agent_image": agent_data.agent_image,
            "system_prompt": agent_data.system_prompt,
            "voice_settings": json.dumps(agent_data.voice_settings.dict()) if agent_data.voice_settings else json.dumps({"provider": "livekit", "voice_id": "alloy", "temperature": 0.7}),
            "n8n_text_webhook_url": agent_data.webhooks.voice_context_webhook_url if agent_data.webhooks else None,
            "n8n_rag_webhook_url": agent_data.webhooks.text_context_webhook_url if agent_data.webhooks else None,
            "enabled": agent_data.enabled,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat()
        }
        
        try:
            # Insert into Supabase
            result = supabase.table("agents").insert(agent_dict).execute()
            
            if result.data:
                # Create AgentInDB with client_id
                agent_record = result.data[0]
                agent = self._build_agent_from_supabase_record(agent_record, client_id)
                
                # Cache the agent
                self.redis.setex(
                    self._get_cache_key(client_id, agent.slug),
                    self.cache_ttl,
                    agent.json()
                )
                
                # Invalidate client's agent list cache
                self.redis.delete(self._get_client_agents_key(client_id))
                
                return agent
            else:
                raise HTTPException(status_code=500, detail="Failed to create agent")
                
        except Exception as e:
            print(f"Error creating agent: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create agent: {str(e)}")
    
    async def get_agent(self, client_id: str, agent_slug: str) -> Optional[AgentInDB]:
        """Get an agent by slug from client's Supabase"""
        # Check cache first
        cache_key = self._get_cache_key(client_id, agent_slug)
        cached_data = self.redis.get(cache_key)
        
        if cached_data:
            return AgentInDB.parse_raw(cached_data)
        
        # Get from client's Supabase
        supabase = await self._get_client_supabase(client_id)
        if not supabase:
            return None
        
        try:
            result = supabase.table("agents").select("*").eq("slug", agent_slug).execute()
            
            if result.data and len(result.data) > 0:
                agent_record = result.data[0]
                agent = self._build_agent_from_supabase_record(agent_record, client_id)
                
                # Cache for future requests
                self.redis.setex(cache_key, self.cache_ttl, agent.json())
                
                return agent
                
        except Exception as e:
            print(f"Error fetching agent {agent_slug} from client {client_id}: {e}")
            return None
        
        return None
    
    async def get_client_agents(self, client_id: str) -> List[AgentInDB]:
        """Get all agents for a specific client"""
        # Check cache first
        cache_key = self._get_client_agents_key(client_id)
        cached_list = self.redis.get(cache_key)
        
        if cached_list:
            agent_slugs = json.loads(cached_list)
            agents = []
            for slug in agent_slugs:
                agent = await self.get_agent(client_id, slug)
                if agent:
                    agents.append(agent)
            return agents
        
        # Get from client's Supabase
        supabase = await self._get_client_supabase(client_id)
        if not supabase:
            return []
        
        try:
            result = supabase.table("agents").select("*").order("name").execute()
            
            if result.data:
                agents = []
                agent_slugs = []
                
                for agent_record in result.data:
                    agent = self._build_agent_from_supabase_record(agent_record, client_id)
                    agents.append(agent)
                    agent_slugs.append(agent.slug)
                    
                    # Cache individual agent
                    self.redis.setex(
                        self._get_cache_key(client_id, agent.slug),
                        self.cache_ttl,
                        agent.json()
                    )
                
                # Cache the list of slugs
                self.redis.setex(cache_key, self.cache_ttl, json.dumps(agent_slugs))
                
                # Also fetch latest configurations from agent_configurations table
                await self._sync_agent_configurations(supabase, client_id, agents)
                
                return agents
                
        except Exception as e:
            print(f"Error fetching agents for client {client_id}: {e}")
            return []
        
        return []
    
    async def _sync_agent_configurations(self, supabase, client_id: str, agents: List[AgentInDB]):
        """Sync agent configurations from agent_configurations table"""
        try:
            # Fetch all agent configurations
            result = supabase.table("agent_configurations").select("*").execute()
            
            if result.data:
                # Create a mapping of agent_slug to configuration
                config_map = {}
                for config in result.data:
                    agent_slug = config.get("agent_slug")
                    if agent_slug:
                        config_map[agent_slug] = config
                
                # Store configurations in Redis
                for agent in agents:
                    if agent.slug in config_map:
                        config = config_map[agent.slug]
                        config_key = f"agent_config:{client_id}:{agent.slug}"
                        config_data = {
                            "slug": agent.slug,
                            "name": config.get("agent_name", agent.name),
                            "system_prompt": config.get("system_prompt", agent.system_prompt),
                            "voice_settings": config.get("voice_settings", agent.voice_settings),
                            "webhooks": {
                                "voice_context_webhook_url": config.get("voice_context_webhook_url"),
                                "text_context_webhook_url": config.get("text_context_webhook_url")
                            },
                            "tools_config": config.get("tools_config"),
                            "enabled": config.get("enabled", True),
                            "last_updated": config.get("last_updated", datetime.utcnow().isoformat())
                        }
                        self.redis.setex(config_key, self.cache_ttl, json.dumps(config_data))
                        print(f"Synced configuration for agent {agent.slug}")
                
        except Exception as e:
            print(f"Error syncing agent configurations: {e}")
    
    async def get_all_agents_with_clients(self) -> List[AgentWithClient]:
        """Get all agents across all clients with client info"""
        agents_with_clients = []
        
        # Get all clients
        clients = await self.client_service.get_all_clients()
        
        for client in clients:
            # Get agents for this client
            agents = await self.get_client_agents(client.id)
            
            for agent in agents:
                agent_with_client = AgentWithClient(
                    **agent.dict(),
                    client_name=client.name,
                    client_domain=client.domain
                )
                agents_with_clients.append(agent_with_client)
        
        return agents_with_clients
    
    async def update_agent(self, client_id: str, agent_slug: str, update_data: AgentUpdate) -> AgentInDB:
        """Update an agent in client's Supabase"""
        agent = await self.get_agent(client_id, agent_slug)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {agent_slug} not found")
        
        # First try to update in Supabase
        supabase = await self._get_client_supabase(client_id)
        if supabase:
            # Prepare update data
            update_dict = {}
            if update_data.name is not None:
                update_dict["name"] = update_data.name
            if update_data.description is not None:
                update_dict["description"] = update_data.description
            if update_data.agent_image is not None:
                update_dict["agent_image"] = update_data.agent_image
            if update_data.system_prompt is not None:
                update_dict["system_prompt"] = update_data.system_prompt
            if update_data.enabled is not None:
                update_dict["enabled"] = update_data.enabled
            if update_data.tools_config is not None:
                update_dict["tools_config"] = update_data.tools_config
            
            if update_data.voice_settings:
                # Update the voice_settings JSON field with the new settings
                update_dict["voice_settings"] = json.dumps(update_data.voice_settings.dict())
            
            if update_data.webhooks:
                update_dict["n8n_text_webhook_url"] = update_data.webhooks.voice_context_webhook_url
                update_dict["n8n_rag_webhook_url"] = update_data.webhooks.text_context_webhook_url
            
            if update_dict:
                update_dict["updated_at"] = datetime.utcnow().isoformat()
                
                try:
                    result = supabase.table("agents").update(update_dict).eq("slug", agent_slug).execute()
                    
                    if result.data:
                        # Invalidate cache
                        self.redis.delete(self._get_cache_key(client_id, agent_slug))
                        self.redis.delete(self._get_client_agents_key(client_id))
                        
                        # Return updated agent
                        return await self.get_agent(client_id, agent_slug)
                except Exception as e:
                    print(f"Error updating agent in Supabase: {e}")
                    # Continue to Redis fallback
        
        # Fallback to Redis-only update
        print(f"Updating agent {agent_slug} in Redis only (Supabase unavailable)")
        
        # Get current agent data from Redis
        cache_key = self._get_cache_key(client_id, agent_slug)
        agent_json = self.redis.get(cache_key)
        if agent_json:
            agent_dict = json.loads(agent_json)
        else:
            agent_dict = agent.dict()
        
        # Apply updates
        if update_data.name is not None:
            agent_dict["name"] = update_data.name
        if update_data.description is not None:
            agent_dict["description"] = update_data.description
        if update_data.agent_image is not None:
            agent_dict["agent_image"] = update_data.agent_image
        if update_data.system_prompt is not None:
            agent_dict["system_prompt"] = update_data.system_prompt
        if update_data.enabled is not None:
            agent_dict["enabled"] = update_data.enabled
        if update_data.tools_config is not None:
            agent_dict["tools_config"] = update_data.tools_config
            
        if update_data.voice_settings:
            agent_dict["voice_settings"] = update_data.voice_settings.dict()
            
        if update_data.webhooks:
            agent_dict["webhooks"] = update_data.webhooks.dict()
            
        agent_dict["updated_at"] = datetime.utcnow().isoformat()
        
        # Store updated agent in Redis
        self.redis.setex(cache_key, self.cache_ttl, json.dumps(agent_dict))
        
        # Also update the agent configuration
        config_key = f"agent_config:{client_id}:{agent_slug}"
        config_data = {
            "slug": agent_dict["slug"],
            "name": agent_dict["name"],
            "system_prompt": agent_dict["system_prompt"],
            "voice_settings": agent_dict["voice_settings"],
            "webhooks": agent_dict["webhooks"],
            "tools_config": agent_dict.get("tools_config"),
            "enabled": agent_dict["enabled"],
            "last_updated": datetime.utcnow().isoformat()
        }
        self.redis.setex(config_key, self.cache_ttl, json.dumps(config_data))
        
        # Return updated agent
        return AgentInDB(**agent_dict)
    
    async def delete_agent(self, client_id: str, agent_slug: str) -> bool:
        """Delete an agent from client's Supabase"""
        # Get client's Supabase
        supabase = await self._get_client_supabase(client_id)
        if not supabase:
            raise HTTPException(status_code=500, detail="Could not connect to client's Supabase")
        
        try:
            result = supabase.table("agents").delete().eq("slug", agent_slug).execute()
            
            # Clear cache
            self.redis.delete(self._get_cache_key(client_id, agent_slug))
            self.redis.delete(self._get_client_agents_key(client_id))
            
            return True
            
        except Exception as e:
            print(f"Error deleting agent: {e}")
            return False
    
    async def sync_agents_from_supabase(self, client_id: str) -> int:
        """Force sync all agents from a client's Supabase"""
        # Clear cache for this client
        self.redis.delete(self._get_client_agents_key(client_id))
        
        # Get fresh data
        agents = await self.get_client_agents(client_id)
        
        return len(agents)