"""
Hybrid Client management service using both Redis (caching) and Supabase (persistent storage)
"""
import json
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import redis
from fastapi import HTTPException
from supabase import create_client, Client as SupabaseClient
import httpx

from app.models.client import Client, ClientCreate, ClientUpdate, ClientInDB, APIKeys, ClientSettings, SupabaseConfig, LiveKitConfig


class ClientService:
    """Hybrid service for managing clients with Redis caching and Supabase persistence"""
    
    def __init__(self, supabase_url: str, supabase_key: str, redis_client: redis.Redis):
        self.supabase: SupabaseClient = create_client(supabase_url, supabase_key)
        self.redis = redis_client
        self.table_name = "clients"
        self.cache_ttl = 600  # 10 minutes cache TTL
        self.cache_prefix = "client:"
        self.client_list_key = "clients:all"
        self.active_clients_key = "clients:active"
        
    def _get_cache_key(self, client_id: str) -> str:
        """Get Redis cache key for a client"""
        return f"{self.cache_prefix}{client_id}"
    
    def _get_domain_cache_key(self, domain: str) -> str:
        """Get Redis cache key for domain lookup"""
        return f"client:domain:{domain.lower()}"
    
    def _invalidate_client_cache(self, client_id: str, domain: Optional[str] = None):
        """Invalidate all cache entries for a client"""
        # Delete client data
        self.redis.delete(self._get_cache_key(client_id))
        
        # Delete client lists
        self.redis.delete(self.client_list_key)
        self.redis.delete(self.active_clients_key)
        
        # Delete domain mapping if provided
        if domain:
            self.redis.delete(self._get_domain_cache_key(domain))
    
    async def ensure_table_exists(self):
        """Ensure the clients table exists in Supabase"""
        # This would typically be done via migrations
        # Schema documented in client_service_supabase.py
        pass
        
    async def create_client(self, client_data: ClientCreate) -> ClientInDB:
        """Create a new client in Supabase"""
        # Check if client already exists
        existing = await self.get_client(client_data.id)
        if existing:
            raise HTTPException(status_code=400, detail=f"Client with ID {client_data.id} already exists")
        
        # Create client object
        now = datetime.utcnow()
        client_dict = {
            **client_data.dict(),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat()
        }
        
        # For demo/development: If Supabase is not properly configured, just store in Redis
        try:
            # Try to store in Supabase
            result = self.supabase.table(self.table_name).insert(client_dict).execute()
            
            if result.data:
                client = ClientInDB(**result.data[0])
            else:
                raise HTTPException(status_code=500, detail="Failed to create client in Supabase")
        except Exception as e:
            print(f"Warning: Could not store in Supabase ({e}), storing in Redis only")
            # Create the client object for Redis storage
            client = ClientInDB(**client_dict)
        
        # Always cache in Redis (this serves as primary storage when Supabase is unavailable)
        self.redis.setex(
            self._get_cache_key(client.id),
            self.cache_ttl * 100,  # Longer TTL when using Redis as primary storage
            client.json()
        )
        
        # Also store in the client list
        client_ids = []
        cached_list = self.redis.get(self.client_list_key)
        if cached_list:
            client_ids = json.loads(cached_list)
        if client.id not in client_ids:
            client_ids.append(client.id)
        self.redis.setex(self.client_list_key, self.cache_ttl * 100, json.dumps(client_ids))
        
        # Invalidate active clients cache
        self.redis.delete(self.active_clients_key)
        
        return client
    
    async def get_client(self, client_id: str, auto_sync: bool = True) -> Optional[ClientInDB]:
        """Get a client by ID with Redis caching and optional auto-sync from client's Supabase"""
        # Check Redis cache first
        cache_key = self._get_cache_key(client_id)
        cached_data = self.redis.get(cache_key)
        
        if cached_data:
            client = ClientInDB.parse_raw(cached_data)
            
            # If auto_sync is enabled, update settings from client's Supabase
            if auto_sync and client.settings.supabase.url and client.settings.supabase.service_role_key:
                try:
                    # Fetch latest settings from client's Supabase
                    synced_settings = await self.fetch_settings_from_supabase(
                        client.settings.supabase.url,
                        client.settings.supabase.service_role_key
                    )
                    
                    # Update client settings with synced data
                    if synced_settings.get('api_keys'):
                        client.settings.api_keys = client.settings.api_keys or APIKeys()
                        for key, value in synced_settings['api_keys'].items():
                            if value and hasattr(client.settings.api_keys, key):
                                setattr(client.settings.api_keys, key, value)
                    
                    if synced_settings.get('livekit'):
                        for key, value in synced_settings['livekit'].items():
                            if value and hasattr(client.settings.livekit, key):
                                setattr(client.settings.livekit, key, value)
                    
                    # Update cache with synced data
                    self.redis.setex(cache_key, self.cache_ttl, client.json())
                    
                except Exception as e:
                    # Log but don't fail if sync fails
                    print(f"Auto-sync failed for client {client_id}: {e}")
            
            return client
        
        # Not in cache, fetch from Supabase
        try:
            result = self.supabase.table(self.table_name).select("*").eq("id", client_id).execute()
            
            if result.data and len(result.data) > 0:
                client = ClientInDB(**result.data[0])
                
                # Auto-sync if enabled
                if auto_sync and client.settings.supabase.url and client.settings.supabase.service_role_key:
                    try:
                        synced_settings = await self.fetch_settings_from_supabase(
                            client.settings.supabase.url,
                            client.settings.supabase.service_role_key
                        )
                        
                        if synced_settings.get('api_keys'):
                            client.settings.api_keys = client.settings.api_keys or APIKeys()
                            for key, value in synced_settings['api_keys'].items():
                                if value and hasattr(client.settings.api_keys, key):
                                    setattr(client.settings.api_keys, key, value)
                        
                        if synced_settings.get('livekit'):
                            for key, value in synced_settings['livekit'].items():
                                if value and hasattr(client.settings.livekit, key):
                                    setattr(client.settings.livekit, key, value)
                    except Exception as e:
                        print(f"Auto-sync failed for client {client_id}: {e}")
                
                # Cache for future requests
                self.redis.setex(cache_key, self.cache_ttl, client.json())
                
                # Also cache domain mapping if domain exists
                if client.domain:
                    self.redis.setex(
                        self._get_domain_cache_key(client.domain),
                        self.cache_ttl,
                        client_id
                    )
                
                return client
        except Exception as e:
            print(f"Error fetching client {client_id} from Supabase: {e}")
            # Don't return None immediately - check if we have it in any other cache first
            
            # Check if we have the client ID in our client list cache
            cached_list = self.redis.get(self.client_list_key)
            if cached_list:
                client_ids = json.loads(cached_list)
                if client_id in client_ids:
                    # The client exists in our list but we couldn't fetch it from Supabase
                    # This might be a temporary issue, so we should not return None
                    print(f"Client {client_id} exists in cache list but couldn't fetch from Supabase")
                    # Try to reconstruct from any cached data we might have
                    pass
            
            return None
        
        return None
    
    async def get_all_clients(self) -> List[ClientInDB]:
        """Get all clients with Redis caching"""
        # Check if we have a cached list
        cached_list = self.redis.get(self.client_list_key)
        
        if cached_list:
            client_ids = json.loads(cached_list)
            clients = []
            
            # Get each client (which may be individually cached)
            for client_id in client_ids:
                client = await self.get_client(client_id)
                if client:
                    clients.append(client)
            
            return clients
        
        # Not in cache, fetch from Supabase
        try:
            result = self.supabase.table(self.table_name).select("*").order("name").execute()
            
            if result.data:
                clients = [ClientInDB(**client) for client in result.data]
                
                # Cache the list of IDs
                client_ids = [client.id for client in clients]
                self.redis.setex(self.client_list_key, self.cache_ttl, json.dumps(client_ids))
                
                # Cache each client individually
                for client in clients:
                    self.redis.setex(
                        self._get_cache_key(client.id),
                        self.cache_ttl,
                        client.json()
                    )
                    
                    # Cache domain mapping
                    if client.domain:
                        self.redis.setex(
                            self._get_domain_cache_key(client.domain),
                            self.cache_ttl,
                            client.id
                        )
                
                return clients
        except Exception as e:
            # If Supabase is not configured or table doesn't exist, return empty list
            print(f"Error fetching clients from Supabase: {e}")
            return []
        
        return []
    
    async def update_client(self, client_id: str, update_data: ClientUpdate) -> Optional[ClientInDB]:
        """Update a client in Supabase and invalidate cache"""
        client = await self.get_client(client_id, auto_sync=False)  # Don't auto-sync during update
        
        # If get_client returned None, it might be because Supabase is unavailable
        # Let's check if we have any record of this client in our caches
        if not client:
            # Check if the client exists in our client list
            cached_list = self.redis.get(self.client_list_key)
            if cached_list:
                client_ids = json.loads(cached_list)
                if client_id in client_ids:
                    # The client exists in our list, so let's try to recreate a minimal client object
                    # This allows updates to work even when Supabase is down
                    print(f"Client {client_id} not found via get_client but exists in client list - creating minimal client for update")
                    
                    # Create a minimal client object with default values
                    # The update will apply new values on top of this
                    from datetime import datetime
                    minimal_client_dict = {
                        "id": client_id,
                        "name": client_id,  # Use ID as name if we don't have it
                        "description": "",
                        "domain": "",
                        "active": True,
                        "settings": {
                            "supabase": {
                                "url": "",
                                "anon_key": "",
                                "service_role_key": ""
                            },
                            "livekit": {
                                "server_url": "",
                                "api_key": "",
                                "api_secret": ""
                            },
                            "api_keys": {},
                            "license_key": ""
                        },
                        "created_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat()
                    }
                    client = ClientInDB(**minimal_client_dict)
                else:
                    raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
            else:
                raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
        
        # Store old domain for cache invalidation
        old_domain = client.domain
        
        # Update fields
        update_dict = update_data.dict(exclude_unset=True)
        if update_dict:
            update_dict["updated_at"] = datetime.utcnow().isoformat()
            
            # Create a new client object with the updates
            client_dict = client.dict()
            
            # Apply updates to the dictionary
            for key, value in update_dict.items():
                if key == "settings" and isinstance(value, dict):
                    # Merge settings
                    if "settings" not in client_dict:
                        client_dict["settings"] = {}
                    for settings_key, settings_value in value.items():
                        client_dict["settings"][settings_key] = settings_value
                else:
                    client_dict[key] = value
            
            # Create a new ClientInDB instance with the updated data
            updated_client = ClientInDB(**client_dict)
            
            try:
                # Try to update in Supabase
                result = self.supabase.table(self.table_name).update(update_dict).eq("id", client_id).execute()
                
                if result.data:
                    updated_client = ClientInDB(**result.data[0])
                # else: already have updated_client from above
            except Exception as e:
                print(f"Warning: Could not update in Supabase ({e}), updating in Redis only")
                # Already have updated_client from above
            
            # Don't use _invalidate_client_cache as it deletes the client
            # Instead, just update the cache with new data
            
            # Delete old domain mapping if domain changed
            if old_domain and update_data.domain and update_data.domain != old_domain:
                self.redis.delete(self._get_domain_cache_key(old_domain))
            
            # Cache the updated client (this serves as primary storage when Supabase is unavailable)
            self.redis.setex(
                self._get_cache_key(updated_client.id),
                self.cache_ttl * 100,  # Longer TTL when using Redis as primary storage
                updated_client.json()
            )
            
            # Update domain mapping if exists
            if updated_client.domain:
                self.redis.setex(
                    self._get_domain_cache_key(updated_client.domain),
                    self.cache_ttl * 100,
                    updated_client.id
                )
            
            # Don't delete the client list cache - it will be refreshed on next access
            
            # If Supabase credentials were updated, trigger agent sync
            if update_data.settings and (
                (update_data.settings.supabase and (
                    update_data.settings.supabase.url != client.settings.supabase.url or
                    update_data.settings.supabase.service_role_key != client.settings.supabase.service_role_key
                ))
            ):
                print(f"Supabase credentials changed for client {client_id}, triggering agent sync...")
                # Clear agent caches to force re-sync from new Supabase
                for key in self.redis.scan_iter(f"agent:{client_id}:*"):
                    self.redis.delete(key)
                self.redis.delete(f"agents:client:{client_id}")
            
            return updated_client
        
        return client
    
    async def delete_client(self, client_id: str) -> bool:
        """Delete a client from Supabase and cache"""
        # Get client to find domain for cache invalidation
        client = await self.get_client(client_id)
        if not client:
            return False
        
        deleted = False
        
        # Try to delete from Supabase
        try:
            result = self.supabase.table(self.table_name).delete().eq("id", client_id).execute()
            if result.data:
                deleted = True
        except Exception as e:
            print(f"Warning: Could not delete from Supabase ({e})")
        
        # Always remove from Redis
        self._invalidate_client_cache(client_id, client.domain)
        
        # Remove from client list in Redis
        cached_list = self.redis.get(self.client_list_key)
        if cached_list:
            client_ids = json.loads(cached_list)
            if client_id in client_ids:
                client_ids.remove(client_id)
                self.redis.setex(self.client_list_key, self.cache_ttl * 100, json.dumps(client_ids))
                deleted = True
        
        return deleted
    
    async def get_active_clients(self) -> List[ClientInDB]:
        """Get all active clients with caching"""
        # Check if we have a cached active list
        cached_list = self.redis.get(self.active_clients_key)
        
        if cached_list:
            client_ids = json.loads(cached_list)
            clients = []
            
            for client_id in client_ids:
                client = await self.get_client(client_id)
                if client and client.active:
                    clients.append(client)
            
            return clients
        
        # Not in cache, fetch from Supabase
        result = self.supabase.table(self.table_name).select("*").eq("active", True).order("name").execute()
        
        if result.data:
            clients = [ClientInDB(**client) for client in result.data]
            
            # Cache the list of active IDs
            client_ids = [client.id for client in clients]
            self.redis.setex(self.active_clients_key, self.cache_ttl, json.dumps(client_ids))
            
            return clients
        
        return []
    
    async def get_client_by_domain(self, domain: str) -> Optional[ClientInDB]:
        """Get a client by domain with caching"""
        # Check domain cache first
        domain_key = self._get_domain_cache_key(domain)
        cached_client_id = self.redis.get(domain_key)
        
        if cached_client_id:
            return await self.get_client(cached_client_id)
        
        # Not in cache, fetch from Supabase
        result = self.supabase.table(self.table_name).select("*").eq("domain", domain).execute()
        
        if result.data and len(result.data) > 0:
            client = ClientInDB(**result.data[0])
            
            # Cache the domain mapping
            self.redis.setex(domain_key, self.cache_ttl, client.id)
            
            # Cache the client data
            self.redis.setex(
                self._get_cache_key(client.id),
                self.cache_ttl,
                client.json()
            )
            
            return client
        
        return None
    
    async def validate_api_key(self, client_id: str, api_key: str) -> bool:
        """Validate an API key for a client"""
        client = await self.get_client(client_id)
        if not client:
            return False
            
        # Check if the provided API key matches the client's license key
        # In production, you'd want a more sophisticated API key system
        return client.settings.license_key == api_key
    
    async def get_client_supabase_config(self, client_id: str) -> Optional[Dict[str, str]]:
        """Get Supabase configuration for a specific client"""
        client = await self.get_client(client_id)
        if not client:
            return None
            
        return {
            "url": str(client.settings.supabase.url),
            "anon_key": client.settings.supabase.anon_key,
            "service_role_key": client.settings.supabase.service_role_key
        }
    
    def get_client_supabase_client(self, client_id: str) -> Optional[SupabaseClient]:
        """Get a Supabase client instance for a specific client"""
        # Note: This is synchronous since it's often needed in sync contexts
        # Consider caching these client instances too if frequently used
        cache_key = self._get_cache_key(client_id)
        cached_data = self.redis.get(cache_key)
        
        if cached_data:
            client = ClientInDB.parse_raw(cached_data)
            return create_client(
                str(client.settings.supabase.url),
                client.settings.supabase.service_role_key
            )
        
        # If not cached, we'd need to make it async or handle differently
        return None
    
    async def initialize_default_clients(self):
        """Initialize default clients if they don't exist"""
        # Check if table exists first
        await self.ensure_table_exists()
        
        default_clients = [
            {
                "id": "sidekick-agent",
                "name": "Autonomite Agent",
                "description": "First-party agents by Autonomite",
                "domain": "autonomite.net",
                "settings": {
                    "supabase": {
                        "url": "https://yuowazxcxwhczywurmmw.supabase.co",
                        "anon_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzU3ODQ1NzMsImV4cCI6MjA1MTM2MDU3M30.SmqTIWrScKQWkJ2_PICWVJYpRSKfvqkRcjMMt0ApH1U",
                        "service_role_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"
                    },
                    "livekit": {
                        "server_url": "https://YOUR_LIVEKIT_SERVER",
                        "api_key": "YOUR_LIVEKIT_API_KEY",
                        "api_secret": "YOUR_LIVEKIT_API_SECRET"
                    }
                }
            },
            {
                "id": "live-free-academy",
                "name": "Live Free Academy",
                "description": "Live Free Academy client",
                "domain": "livefreeacademy.com",
                "settings": {
                    "supabase": {
                        "url": "https://yuowazxcxwhczywurmmw.supabase.co",
                        "anon_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzU3ODQ1NzMsImV4cCI6MjA1MTM2MDU3M30.SmqTIWrScKQWkJ2_PICWVJYpRSKfvqkRcjMMt0ApH1U", 
                        "service_role_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"
                    },
                    "livekit": {
                        "server_url": "https://YOUR_LIVEKIT_SERVER",
                        "api_key": "YOUR_LFA_LIVEKIT_API_KEY",
                        "api_secret": "YOUR_LFA_LIVEKIT_API_SECRET"
                    }
                }
            }
        ]
        
        for client_data in default_clients:
            try:
                existing = await self.get_client(client_data["id"])
                if not existing:
                    await self.create_client(ClientCreate(**client_data))
                    print(f"Created default client: {client_data['name']}")
            except Exception as e:
                print(f"Error creating default client {client_data['id']}: {e}")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring"""
        pattern = f"{self.cache_prefix}*"
        cached_clients = len(list(self.redis.scan_iter(match=pattern)))
        
        return {
            "cached_clients": cached_clients,
            "cache_ttl_seconds": self.cache_ttl,
            "client_list_cached": bool(self.redis.exists(self.client_list_key)),
            "active_list_cached": bool(self.redis.exists(self.active_clients_key))
        }
    
    async def fetch_settings_from_supabase(self, supabase_url: str, service_key: str) -> Dict[str, Any]:
        """
        Fetch settings from a Supabase instance's agent_configurations table.
        Supabase is the source of truth for all settings.
        Both WordPress and the SaaS backend sync their settings from Supabase.
        """
        # Clean up the URL
        supabase_url = supabase_url.rstrip('/')
        
        # Build the API URL to get the latest agent configuration
        api_url = f"{supabase_url}/rest/v1/agent_configurations?select=*&order=last_updated.desc&limit=1"
        
        # Set up headers
        headers = {
            'apikey': service_key,
            'Authorization': f'Bearer {service_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(api_url, headers=headers, timeout=10.0)
                response.raise_for_status()
                
                data = response.json()
                if not data or len(data) == 0:
                    # No configurations found, return empty settings
                    return {
                        "api_keys": {},
                        "embedding": {},
                        "rerank": {},
                        "message": "No agent configurations found in Supabase"
                    }
                
                # Get the first (latest) configuration
                config = data[0]
                
                # Extract relevant settings from the agent configuration
                # Based on WordPress plugin's autonomite_agent_sync_settings_to_supabase function
                settings = {
                    "api_keys": {
                        # LLM Providers
                        "openai_api_key": config.get('openai_api_key', ''),
                        "groq_api_key": config.get('groq_api_key', ''),
                        "deepinfra_api_key": config.get('deepinfra_api_key', ''),
                        "replicate_api_key": config.get('replicate_api_key', ''),
                        # Voice/Speech Providers
                        "deepgram_api_key": config.get('deepgram_api_key', ''),
                        "elevenlabs_api_key": config.get('elevenlabs_api_key', ''),
                        "cartesia_api_key": config.get('cartesia_api_key', ''),
                        "speechify_api_key": config.get('speechify_api_key', ''),
                        # Embedding/Reranking Providers
                        "novita_api_key": config.get('novita_api_key', ''),
                        "cohere_api_key": config.get('cohere_api_key', ''),
                        "siliconflow_api_key": config.get('siliconflow_api_key', ''),
                        "jina_api_key": config.get('jina_api_key', ''),
                    },
                    "livekit": {
                        "server_url": config.get('livekit_url', ''),
                        "api_key": config.get('livekit_api_key', ''),
                        "api_secret": config.get('livekit_api_secret', '')
                    },
                    "embedding": {
                        "provider": "novita",  # Default from WordPress
                        "document_model": "Qwen/Qwen2.5-72B-Instruct",
                        "conversation_model": "Qwen/Qwen2.5-72B-Instruct"
                    },
                    "rerank": {
                        "enabled": False,
                        "provider": "siliconflow",
                        "model": "BAAI/bge-reranker-base",
                        "top_k": 3,
                        "candidates": 20
                    },
                    "agent_info": {
                        "agent_id": config.get('agent_id', ''),
                        "agent_slug": config.get('agent_slug', ''),
                        "agent_name": config.get('agent_name', ''),
                        "last_updated": config.get('last_updated', '')
                    },
                    "message": f"Successfully retrieved settings from Supabase for agent: {config.get('agent_name', 'Unknown')}"
                }
                
                return settings
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise Exception("agent_configurations table not found in Supabase.")
                elif e.response.status_code == 401:
                    raise Exception("Invalid service role key")
                else:
                    raise Exception(f"HTTP error {e.response.status_code}: {e.response.text}")
            except httpx.ConnectError:
                raise Exception("Could not connect to Supabase. Check the URL is correct.")
            except Exception as e:
                raise Exception(f"Unexpected error: {str(e)}")