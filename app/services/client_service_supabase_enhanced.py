"""
Enhanced Client management service using Supabase only (no Redis)
"""
import json
import logging
import os
from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import HTTPException
from supabase import create_client, Client as SupabaseClient
import httpx

logger = logging.getLogger(__name__)

from app.models.client import Client, ClientCreate, ClientUpdate, ClientInDB, APIKeys, ClientSettings


class ClientService:
    """Service for managing clients and their configurations in Supabase"""
    
    def __init__(self, supabase_url: str, supabase_key: str, redis_client=None):
        # Ignore redis_client parameter for compatibility
        self.supabase: SupabaseClient = create_client(supabase_url, supabase_key)
        self.table_name = "clients"
        
    async def ensure_table_exists(self):
        """Ensure the clients table exists in Supabase"""
        # This is handled by the SQL schema we created
        pass
        
    async def create_client(self, client_data: ClientCreate) -> ClientInDB:
        """Create a new client"""
        # Check if client already exists
        existing = await self.get_client(client_data.id)
        if existing:
            raise HTTPException(status_code=400, detail=f"Client with ID {client_data.id} already exists")
        
        # Create client object with all fields
        now = datetime.utcnow()
        
        # Extract settings into separate columns
        settings = client_data.settings or ClientSettings()
        
        client_dict = {
            "id": client_data.id,
            "name": client_data.name,
            "description": client_data.description,
            "domain": client_data.domain,
            "active": client_data.active,
            "supabase_url": settings.supabase.url if settings.supabase else "",
            "supabase_anon_key": settings.supabase.anon_key if settings.supabase else "",
            "supabase_service_role_key": settings.supabase.service_role_key if settings.supabase else "",
            "livekit_server_url": settings.livekit.server_url if settings.livekit else "",
            "livekit_api_key": settings.livekit.api_key if settings.livekit else "",
            "livekit_api_secret": settings.livekit.api_secret if settings.livekit else "",
            "settings": settings.dict() if settings else {},
            "created_at": now.isoformat(),
            "updated_at": now.isoformat()
        }
        
        # Store in Supabase
        result = self.supabase.table(self.table_name).insert(client_dict).execute()
        
        if result.data:
            return self._db_to_model(result.data[0])
        else:
            raise HTTPException(status_code=500, detail="Failed to create client")
    
    async def get_all_clients(self) -> List[ClientInDB]:
        """Get all clients"""
        result = self.supabase.table(self.table_name).select("*").order("name").execute()
        
        if result.data:
            return [self._db_to_model(client) for client in result.data]
        
        return []
    
    async def update_client(self, client_id: str, update_data: ClientUpdate) -> ClientInDB:
        """Update a client"""
        client = await self.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
        
        # Update fields
        update_dict = {}
        
        if update_data.name is not None:
            update_dict["name"] = update_data.name
        if update_data.description is not None:
            update_dict["description"] = update_data.description
        if update_data.domain is not None:
            update_dict["domain"] = update_data.domain
        if update_data.active is not None:
            update_dict["active"] = update_data.active
            
        # Handle settings update
        if update_data.settings:
            settings = update_data.settings
            if settings.supabase:
                update_dict["supabase_url"] = settings.supabase.url
                update_dict["supabase_anon_key"] = settings.supabase.anon_key
                update_dict["supabase_service_role_key"] = settings.supabase.service_role_key
            if settings.livekit:
                update_dict["livekit_server_url"] = settings.livekit.server_url
                update_dict["livekit_api_key"] = settings.livekit.api_key
                update_dict["livekit_api_secret"] = settings.livekit.api_secret
            
            # Merge with existing settings
            existing_settings = client.settings.dict() if client.settings else {}
            new_settings = update_data.settings.dict()
            merged_settings = {**existing_settings, **new_settings}
            
            # Serialize nested settings to JSON for Supabase JSONB column
            import json
            update_dict["settings"] = json.dumps(merged_settings)
        
        if update_dict:
            update_dict["updated_at"] = datetime.utcnow().isoformat()
            
            try:
                result = self.supabase.table(self.table_name).update(update_dict).eq("id", client_id).execute()
                
                if result.data and len(result.data) > 0:
                    logger.info(f"Successfully updated client {client_id} in Supabase")
                    return self._db_to_model(result.data[0])
                else:
                    logger.error(f"No data returned from Supabase update for client {client_id}")
                    raise HTTPException(status_code=500, detail="Failed to update client - no data returned")
            except Exception as e:
                logger.error(f"Supabase update failed for client {client_id}: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Database update failed: {str(e)}")
        
        return client
    
    async def delete_client(self, client_id: str) -> bool:
        """Delete a client"""
        result = self.supabase.table(self.table_name).delete().eq("id", client_id).execute()
        
        return len(result.data) > 0 if result.data else False
    
    async def get_active_clients(self) -> List[ClientInDB]:
        """Get all active clients"""
        result = self.supabase.table(self.table_name).select("*").eq("active", True).order("name").execute()
        
        if result.data:
            return [self._db_to_model(client) for client in result.data]
        
        return []
    
    async def get_client_by_domain(self, domain: str) -> Optional[ClientInDB]:
        """Get a client by domain"""
        result = self.supabase.table(self.table_name).select("*").eq("domain", domain).execute()
        
        if result.data and len(result.data) > 0:
            return self._db_to_model(result.data[0])
        
        return None
    
    async def validate_api_key(self, client_id: str, api_key: str) -> bool:
        """Validate an API key for a client"""
        client = await self.get_client(client_id)
        if not client:
            return False
            
        # Check if the provided API key matches the client's license key
        return client.settings.license_key == api_key if client.settings else False
    
    async def get_client_supabase_config(self, client_id: str) -> Optional[Dict[str, str]]:
        """Get Supabase configuration for a specific client"""
        client = await self.get_client(client_id)
        if not client or not client.settings or not client.settings.supabase:
            return None
            
        return {
            "url": str(client.settings.supabase.url),
            "anon_key": client.settings.supabase.anon_key,
            "service_role_key": client.settings.supabase.service_role_key
        }
    
    async def get_client_supabase_client(self, client_id: str) -> Optional[SupabaseClient]:
        """Get a Supabase client instance for a specific client"""
        config = await self.get_client_supabase_config(client_id)
        if not config or not config["url"] or not config["service_role_key"]:
            return None
            
        try:
            return create_client(config["url"], config["service_role_key"])
        except:
            return None
    
    async def initialize_default_clients(self):
        """Initialize default clients if they don't exist"""
        default_clients = [
            {
                "id": os.getenv("DEFAULT_CLIENT_ID", "11389177-e4d8-49a9-9a00-f77bb4de6592"),  # From environment
                "name": "Autonomite",
                "description": "Autonomite AI Platform",
                "domain": "autonomite.ai",
                "active": True,
                "settings": {
                    "supabase": {
                        "url": "https://yuowazxcxwhczywurmmw.supabase.co",
                        "anon_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzU3ODQ1NzMsImV4cCI6MjA1MTM2MDU3M30.SmqTIWrScKQWkJ2_PICWVJYpRSKfvqkRcjMMt0ApH1U",
                        "service_role_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"
                    },
                    "livekit": {
                        "server_url": "wss://litebridge-hw6srhvi.livekit.cloud",
                        "api_key": "APIUtuiQ47BQBsk",
                        "api_secret": "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
                    },
                    "api_keys": {},
                    "embedding": {
                        "provider": "novita",
                        "document_model": "Qwen/Qwen2.5-72B-Instruct",
                        "conversation_model": "Qwen/Qwen2.5-72B-Instruct"
                    },
                    "rerank": {
                        "enabled": False,
                        "provider": "siliconflow",
                        "model": "BAAI/bge-reranker-base",
                        "top_k": 3,
                        "candidates": 20
                    }
                }
            },
            {
                "id": "a5f3d2e1-7b4c-4a89-b5c9-3e8f9d2c1a7b",
                "name": "Live Free Academy",
                "description": "Educational platform powered by Autonomite",
                "domain": "livefreeacademy.com",
                "active": True,
                "settings": {
                    "supabase": {
                        "url": "https://pending.supabase.co",
                        "anon_key": "",
                        "service_role_key": ""
                    },
                    "livekit": {
                        "server_url": "wss://litebridge-hw6srhvi.livekit.cloud",
                        "api_key": "APIUtuiQ47BQBsk",
                        "api_secret": "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
                    },
                    "api_keys": {},
                    "embedding": {
                        "provider": "novita",
                        "document_model": "Qwen/Qwen2.5-72B-Instruct",
                        "conversation_model": "Qwen/Qwen2.5-72B-Instruct"
                    },
                    "rerank": {
                        "enabled": False,
                        "provider": "siliconflow",
                        "model": "BAAI/bge-reranker-base",
                        "top_k": 3,
                        "candidates": 20
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
    
    async def get_client(self, client_id: str, auto_sync: bool = True) -> Optional[ClientInDB]:
        """Get a client by ID with optional auto-sync from client's Supabase"""
        result = self.supabase.table(self.table_name).select("*").eq("id", client_id).execute()
        
        if result.data and len(result.data) > 0:
            client = self._db_to_model(result.data[0])
            
            # If auto_sync is enabled, update settings from client's Supabase
            if auto_sync and client.settings and client.settings.supabase and client.settings.supabase.url and client.settings.supabase.service_role_key:
                try:
                    # Fetch latest settings from client's Supabase
                    synced_settings = await self.fetch_settings_from_supabase(
                        client.settings.supabase.url,
                        client.settings.supabase.service_role_key
                    )
                    
                    # Update client settings with synced data
                    if synced_settings.get('api_keys'):
                        if not client.settings.api_keys:
                            client.settings.api_keys = APIKeys()
                        for key, value in synced_settings['api_keys'].items():
                            if value and hasattr(client.settings.api_keys, key):
                                setattr(client.settings.api_keys, key, value)
                    
                    # Update the client in database with synced settings
                    update_dict = {"settings": client.settings.dict(), "updated_at": datetime.utcnow().isoformat()}
                    self.supabase.table(self.table_name).update(update_dict).eq("id", client_id).execute()
                    
                except Exception as e:
                    # Log but don't fail if sync fails
                    print(f"Auto-sync failed for client {client_id}: {e}")
            
            return client
        
        return None
    
    async def fetch_settings_from_supabase(self, supabase_url: str, service_key: str) -> Dict[str, Any]:
        """
        Fetch settings from a Supabase instance's agent_configurations table.
        """
        # Clean up the URL
        supabase_url = supabase_url.rstrip('/')
        
        # Skip if placeholder URL
        if "pending.supabase.co" in supabase_url:
            return {"api_keys": {}, "message": "Placeholder URL - skipping sync"}
        
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
                    return {"api_keys": {}, "message": "No agent configurations found"}
                
                # Get the first (latest) configuration
                config = data[0]
                
                # Extract relevant settings from the agent configuration
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
                    "message": f"Successfully synced settings for agent: {config.get('agent_name', 'Unknown')}"
                }
                
                return settings
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise Exception("agent_configurations table not found")
                elif e.response.status_code == 401:
                    raise Exception("Invalid service role key")
                else:
                    raise Exception(f"HTTP error {e.response.status_code}")
            except httpx.ConnectError:
                raise Exception("Could not connect to Supabase")
            except Exception as e:
                raise Exception(f"Unexpected error: {str(e)}")
    
    def _db_to_model(self, db_row: Dict[str, Any]) -> ClientInDB:
        """Convert database row to ClientInDB model"""
        # Reconstruct settings from flat structure
        settings_raw = db_row.get("settings", {})
        
        # Deserialize JSON string if needed
        if isinstance(settings_raw, str):
            try:
                settings_dict = json.loads(settings_raw)
            except json.JSONDecodeError:
                logger.warning(f"Failed to decode settings JSON for client {db_row.get('id')}")
                settings_dict = {}
        else:
            settings_dict = settings_raw or {}
        
        # Override with individual columns if they exist
        if "supabase_url" in db_row:
            if "supabase" not in settings_dict:
                settings_dict["supabase"] = {}
            settings_dict["supabase"]["url"] = db_row.get("supabase_url", "")
            settings_dict["supabase"]["anon_key"] = db_row.get("supabase_anon_key", "")
            settings_dict["supabase"]["service_role_key"] = db_row.get("supabase_service_role_key", "")
        
        if "livekit_server_url" in db_row:
            if "livekit" not in settings_dict:
                settings_dict["livekit"] = {}
            settings_dict["livekit"]["server_url"] = db_row.get("livekit_server_url", "")
            settings_dict["livekit"]["api_key"] = db_row.get("livekit_api_key", "")
            settings_dict["livekit"]["api_secret"] = db_row.get("livekit_api_secret", "")
        
        # Create ClientInDB instance
        return ClientInDB(
            id=db_row["id"],
            name=db_row["name"],
            description=db_row.get("description"),
            domain=db_row.get("domain"),
            active=db_row.get("active", True),
            settings=ClientSettings(**settings_dict) if settings_dict else ClientSettings(),
            created_at=db_row.get("created_at"),
            updated_at=db_row.get("updated_at")
        )
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring - returns empty stats for Supabase-only"""
        return {
            "cached_clients": 0,
            "cache_ttl_seconds": 0,
            "client_list_cached": False,
            "active_list_cached": False,
            "message": "No caching in Supabase-only mode"
        }