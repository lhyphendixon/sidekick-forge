"""
Enhanced Client management service using Supabase only (no Redis)
"""
import json
import os
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from fastapi import HTTPException
from supabase import create_client, Client as SupabaseClient
import httpx

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
        import uuid
        
        # Generate UUID if not provided
        client_id = client_data.id if client_data.id else str(uuid.uuid4())
        
        # Check if client already exists
        existing = await self.get_client(client_id)
        if existing:
            raise HTTPException(status_code=400, detail=f"Client with ID {client_id} already exists")
        
        # Create client object with all fields
        now = datetime.now(timezone.utc)
        
        # Extract settings into separate columns
        settings = client_data.settings or ClientSettings()
        
        client_dict = {
            "id": client_id,
            "slug": client_data.slug,
            "name": client_data.name,
            # store top-level fields used by queries
            "domain": client_data.domain or "",
            "active": True,
            "additional_settings": {
                "description": client_data.description,
                "domain": client_data.domain,
                "supabase_anon_key": settings.supabase.anon_key if settings.supabase else "",
                "embedding": settings.embedding.dict() if settings.embedding else {},
                "rerank": settings.rerank.dict() if settings.rerank else {}
            },
            "supabase_url": settings.supabase.url if settings.supabase else "",
            "supabase_service_role_key": settings.supabase.service_role_key if settings.supabase else "",
            "livekit_url": settings.livekit.server_url if settings.livekit else "",
            "livekit_api_key": settings.livekit.api_key if settings.livekit else "",
            "livekit_api_secret": settings.livekit.api_secret if settings.livekit else "",
            "settings": settings.dict() if settings else {},
            "created_at": now.isoformat(),
            "updated_at": now.isoformat()
        }
        
        # Add API keys as direct columns if present
        if settings.api_keys:
            api_keys = settings.api_keys
            if api_keys.openai_api_key:
                client_dict["openai_api_key"] = api_keys.openai_api_key
            if api_keys.groq_api_key:
                client_dict["groq_api_key"] = api_keys.groq_api_key
            if api_keys.deepinfra_api_key:
                client_dict["deepinfra_api_key"] = api_keys.deepinfra_api_key
            if api_keys.replicate_api_key:
                client_dict["replicate_api_key"] = api_keys.replicate_api_key
            if api_keys.deepgram_api_key:
                client_dict["deepgram_api_key"] = api_keys.deepgram_api_key
            if api_keys.elevenlabs_api_key:
                client_dict["elevenlabs_api_key"] = api_keys.elevenlabs_api_key
            if api_keys.cartesia_api_key:
                client_dict["cartesia_api_key"] = api_keys.cartesia_api_key
            if api_keys.speechify_api_key:
                client_dict["speechify_api_key"] = api_keys.speechify_api_key
            if api_keys.novita_api_key:
                client_dict["novita_api_key"] = api_keys.novita_api_key
            if api_keys.cohere_api_key:
                client_dict["cohere_api_key"] = api_keys.cohere_api_key
            if api_keys.siliconflow_api_key:
                client_dict["siliconflow_api_key"] = api_keys.siliconflow_api_key
            if api_keys.jina_api_key:
                client_dict["jina_api_key"] = api_keys.jina_api_key
            if api_keys.cerebras_api_key:
                client_dict["cerebras_api_key"] = api_keys.cerebras_api_key
            # Note: anthropic_api_key removed - not in APIKeys model
        
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
        
        # Direct column updates
        if update_data.name is not None:
            update_dict["name"] = update_data.name
        if update_data.slug is not None:
            update_dict["slug"] = update_data.slug
            
        # Fields that go in additional_settings JSONB
        additional_settings = {}
        if update_data.description is not None:
            additional_settings["description"] = update_data.description
        if update_data.domain is not None:
            additional_settings["domain"] = update_data.domain
        if update_data.active is not None:
            additional_settings["active"] = update_data.active
            
        # Add embedding and rerank settings to additional_settings
        if update_data.settings:
            if update_data.settings.embedding:
                additional_settings["embedding"] = update_data.settings.embedding.dict()
            if update_data.settings.rerank:
                additional_settings["rerank"] = update_data.settings.rerank.dict()
            
        # If we have additional settings to update, add them to update_dict
        if additional_settings:
            # Get existing additional_settings
            result = self.supabase.table(self.table_name).select("additional_settings").eq("id", client_id).execute()
            existing_additional = result.data[0].get("additional_settings", {}) if result.data else {}
            
            # Merge with new settings
            merged_additional = {**existing_additional, **additional_settings}
            update_dict["additional_settings"] = merged_additional
            
        # Handle settings update
        if update_data.settings:
            settings = update_data.settings
            if settings.supabase:
                update_dict["supabase_url"] = settings.supabase.url
                update_dict["supabase_service_role_key"] = settings.supabase.service_role_key
                # Store anon_key in additional_settings since column doesn't exist
                if settings.supabase.anon_key:
                    additional_settings["supabase_anon_key"] = settings.supabase.anon_key
            if settings.livekit:
                update_dict["livekit_url"] = settings.livekit.server_url
                update_dict["livekit_api_key"] = settings.livekit.api_key
                update_dict["livekit_api_secret"] = settings.livekit.api_secret
            
            # Also update API key columns directly
            if settings.api_keys:
                api_keys = settings.api_keys
                if api_keys.openai_api_key is not None:
                    update_dict["openai_api_key"] = api_keys.openai_api_key
                if api_keys.groq_api_key is not None:
                    update_dict["groq_api_key"] = api_keys.groq_api_key
                if api_keys.deepinfra_api_key is not None:
                    update_dict["deepinfra_api_key"] = api_keys.deepinfra_api_key
                if api_keys.replicate_api_key is not None:
                    update_dict["replicate_api_key"] = api_keys.replicate_api_key
                if api_keys.deepgram_api_key is not None:
                    update_dict["deepgram_api_key"] = api_keys.deepgram_api_key
                if api_keys.elevenlabs_api_key is not None:
                    update_dict["elevenlabs_api_key"] = api_keys.elevenlabs_api_key
                if api_keys.cartesia_api_key is not None:
                    update_dict["cartesia_api_key"] = api_keys.cartesia_api_key
                if api_keys.speechify_api_key is not None:
                    update_dict["speechify_api_key"] = api_keys.speechify_api_key
                if api_keys.novita_api_key is not None:
                    update_dict["novita_api_key"] = api_keys.novita_api_key
                if api_keys.cohere_api_key is not None:
                    update_dict["cohere_api_key"] = api_keys.cohere_api_key
                if api_keys.siliconflow_api_key is not None:
                    update_dict["siliconflow_api_key"] = api_keys.siliconflow_api_key
                if api_keys.jina_api_key is not None:
                    update_dict["jina_api_key"] = api_keys.jina_api_key
                if api_keys.cerebras_api_key is not None:
                    update_dict["cerebras_api_key"] = api_keys.cerebras_api_key
                # Note: anthropic_api_key is not in the APIKeys model
            
            # Note: We don't have a settings column in the platform database
            # All settings are stored in individual columns
        
        if update_dict:
            update_dict["updated_at"] = datetime.now(timezone.utc).isoformat()
            
            result = self.supabase.table(self.table_name).update(update_dict).eq("id", client_id).execute()
            
            if result.data:
                return self._db_to_model(result.data[0])
            else:
                raise HTTPException(status_code=500, detail="Failed to update client")
        
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
    
    async def get_client_supabase_config(self, client_id: str, auto_sync: bool = False) -> Optional[Dict[str, str]]:
        """Get Supabase configuration for a specific client"""
        client = await self.get_client(client_id, auto_sync=auto_sync)
        if not client or not client.settings or not client.settings.supabase:
            return None
            
        return {
            "url": str(client.settings.supabase.url),
            "anon_key": client.settings.supabase.anon_key,
            "service_role_key": client.settings.supabase.service_role_key
        }
    
    async def get_client_supabase_client(self, client_id: str, auto_sync: bool = False) -> Optional[SupabaseClient]:
        """Get a Supabase client instance for a specific client"""
        config = await self.get_client_supabase_config(client_id, auto_sync=auto_sync)
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
        import time
        import logging
        import uuid
        logger = logging.getLogger(__name__)
        
        # Validate UUID format
        try:
            # Try to parse as UUID to validate format
            uuid.UUID(client_id)
        except (ValueError, AttributeError):
            # Invalid UUID format, return None
            logger.warning(f"Invalid UUID format for client_id: {client_id}")
            return None
        
        method_start = time.time()
        query_start = time.time()
        result = self.supabase.table(self.table_name).select("*").eq("id", client_id).execute()
        logger.info(f"[TIMING] client query took {time.time() - query_start:.2f}s")
        
        if result.data and len(result.data) > 0:
            parse_start = time.time()
            client = self._db_to_model(result.data[0])
            logger.info(f"[TIMING] _db_to_model took {time.time() - parse_start:.2f}s")
            
            # If auto_sync is enabled, update settings from client's Supabase
            if auto_sync and client.settings and client.settings.supabase and client.settings.supabase.url and client.settings.supabase.service_role_key:
                try:
                    sync_start = time.time()
                    logger.info(f"[TIMING] Starting auto-sync for client {client_id}")
                    
                    # Fetch latest settings from client's Supabase
                    fetch_start = time.time()
                    synced_settings = await self.fetch_settings_from_supabase(
                        client.settings.supabase.url,
                        client.settings.supabase.service_role_key
                    )
                    logger.info(f"[TIMING] fetch_settings_from_supabase took {time.time() - fetch_start:.2f}s")
                    
                    # Update client settings with synced data
                    if synced_settings.get('api_keys'):
                        if not client.settings.api_keys:
                            client.settings.api_keys = APIKeys()
                        for key, value in synced_settings['api_keys'].items():
                            if value and hasattr(client.settings.api_keys, key):
                                setattr(client.settings.api_keys, key, value)
                    
                    # Update the client in database with synced API keys
                    update_start = time.time()
                    update_dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
                    
                    # Update individual API key columns ONLY if they're missing in platform DB
                    if synced_settings.get('api_keys'):
                        # First get current platform values
                        platform_result = self.supabase.table(self.table_name).select(
                            'openai_api_key, groq_api_key, deepgram_api_key, elevenlabs_api_key, '
                            'cartesia_api_key, speechify_api_key, deepinfra_api_key, replicate_api_key, '
                            'novita_api_key, cohere_api_key, siliconflow_api_key, jina_api_key'
                        ).eq('id', client_id).execute()
                        
                        platform_keys = platform_result.data[0] if platform_result.data else {}
                        
                        for key, value in synced_settings['api_keys'].items():
                            # Only update if platform doesn't have this key or it's a placeholder
                            platform_value = platform_keys.get(key)
                            if value and key in ['openai_api_key', 'groq_api_key', 'deepgram_api_key', 
                                               'elevenlabs_api_key', 'cartesia_api_key', 'speechify_api_key',
                                               'deepinfra_api_key', 'replicate_api_key', 'novita_api_key',
                                               'cohere_api_key', 'siliconflow_api_key', 'jina_api_key']:
                                if not platform_value or platform_value == '<needs-actual-key>':
                                    update_dict[key] = value
                                    logger.info(f"Auto-sync: Adding missing {key} from client database")
                                else:
                                    logger.debug(f"Auto-sync: Keeping platform value for {key}")
                    
                    if len(update_dict) > 1:  # More than just updated_at
                        self.supabase.table(self.table_name).update(update_dict).eq("id", client_id).execute()
                    logger.info(f"[TIMING] client update took {time.time() - update_start:.2f}s")
                    logger.info(f"[TIMING] Total auto-sync took {time.time() - sync_start:.2f}s")
                    
                except Exception as e:
                    # Log but don't fail if sync fails
                    logger.warning(f"Auto-sync failed for client {client_id}: {e}")
            
            logger.info(f"[TIMING] TOTAL get_client took {time.time() - method_start:.2f}s")
            return client
        
        logger.info(f"[TIMING] TOTAL get_client (not found) took {time.time() - method_start:.2f}s")
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
                    }
                }
                
                # Also fetch from global_settings table
                global_settings_url = f"{supabase_url}/rest/v1/global_settings?select=*"
                
                try:
                    global_response = await client.get(global_settings_url, headers=headers, timeout=10.0)
                    global_response.raise_for_status()
                    
                    global_data = global_response.json()
                    
                    # Build a dict of global settings
                    global_settings_dict = {}
                    for setting in global_data:
                        setting_key = setting.get('setting_key', '')
                        setting_value = setting.get('setting_value', '')
                        if setting_key:
                            global_settings_dict[setting_key] = setting_value
                    
                    # Merge API keys from global_settings
                    # Only update if the key exists in global_settings and has a non-empty value
                    api_key_mappings = [
                        'openai_api_key', 'groq_api_key', 'deepinfra_api_key', 'replicate_api_key',
                        'deepgram_api_key', 'elevenlabs_api_key', 'cartesia_api_key', 'speechify_api_key',
                        'novita_api_key', 'cohere_api_key', 'siliconflow_api_key', 'jina_api_key'
                    ]
                    
                    for key in api_key_mappings:
                        if key in global_settings_dict and global_settings_dict[key]:
                            settings["api_keys"][key] = global_settings_dict[key]
                    
                    # Extract embedding settings
                    settings["embedding"] = {
                        "provider": global_settings_dict.get('embedding_provider', 'openai'),
                        "document_model": global_settings_dict.get('embedding_model_documents', 'text-embedding-3-small'),
                        "conversation_model": global_settings_dict.get('embedding_model_conversations', 'text-embedding-3-small')
                    }
                    
                    # Extract rerank settings
                    settings["rerank"] = {
                        "enabled": global_settings_dict.get('rerank_enabled', 'false').lower() == 'true',
                        "provider": global_settings_dict.get('rerank_provider', 'siliconflow'),
                        "model": global_settings_dict.get('rerank_model', 'BAAI/bge-reranker-base'),
                        "top_k": int(global_settings_dict.get('rerank_top_k', '3')),
                        "candidates": int(global_settings_dict.get('rerank_candidates', '20'))
                    }
                    
                except Exception as e:
                    # Log but don't fail if global_settings fetch fails
                    print(f"Failed to fetch global_settings: {e}")
                    # Use defaults
                    settings["embedding"] = {
                        "provider": "openai",
                        "document_model": "text-embedding-3-small",
                        "conversation_model": "text-embedding-3-small"
                    }
                    settings["rerank"] = {
                        "enabled": False,
                        "provider": "siliconflow",
                        "model": "BAAI/bge-reranker-base",
                        "top_k": 3,
                        "candidates": 20
                    }
                
                settings["message"] = f"Successfully synced settings for agent: {config.get('agent_name', 'Unknown')}"
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
        settings_dict = db_row.get("settings", {})
        
        # Override with individual columns if they exist
        # Supabase config (required)
        if "supabase" not in settings_dict:
            settings_dict["supabase"] = {}
        settings_dict["supabase"]["url"] = db_row.get("supabase_url", "")
        # Get anon_key from additional_settings since column doesn't exist
        additional = db_row.get("additional_settings", {})
        settings_dict["supabase"]["anon_key"] = additional.get("supabase_anon_key", "")
        settings_dict["supabase"]["service_role_key"] = db_row.get("supabase_service_role_key", "")
        
        # LiveKit config (required by model but may be empty)
        if "livekit" not in settings_dict:
            settings_dict["livekit"] = {}
        settings_dict["livekit"]["server_url"] = db_row.get("livekit_url", db_row.get("livekit_server_url", ""))
        settings_dict["livekit"]["api_key"] = db_row.get("livekit_api_key", "")
        settings_dict["livekit"]["api_secret"] = db_row.get("livekit_api_secret", "")
        
        # API keys from individual columns
        if "api_keys" not in settings_dict:
            settings_dict["api_keys"] = {}
        api_key_fields = [
            "openai_api_key", "groq_api_key", "deepgram_api_key", "elevenlabs_api_key",
            "cartesia_api_key", "replicate_api_key", "deepinfra_api_key", "cerebras_api_key",
            "novita_api_key", "cohere_api_key", "siliconflow_api_key", "jina_api_key", "speechify_api_key"
        ]
        for key_field in api_key_fields:
            if key_field in db_row and db_row[key_field]:
                settings_dict["api_keys"][key_field] = db_row[key_field]
        
        # Extract additional fields from additional_settings JSONB
        additional = db_row.get("additional_settings", {})
        
        # Get embedding settings from additional_settings
        if "embedding" in additional and additional["embedding"]:
            settings_dict["embedding"] = additional["embedding"]
        
        # Get rerank settings from additional_settings
        if "rerank" in additional and additional["rerank"]:
            settings_dict["rerank"] = additional["rerank"]
        
        # Create ClientInDB instance
        return ClientInDB(
            id=db_row["id"],
            slug=db_row.get("slug", ""),
            name=db_row["name"],
            description=additional.get("description", db_row.get("description")),
            domain=additional.get("domain", db_row.get("domain")),
            active=additional.get("active", db_row.get("active", True)),
            settings=ClientSettings(**settings_dict) if settings_dict else ClientSettings(),
            created_at=db_row.get("created_at"),
            updated_at=db_row.get("updated_at"),
            additional_settings=additional  # Include the full additional_settings
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