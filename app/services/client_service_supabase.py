"""
Enhanced Client management service using Supabase only (no Redis)
"""
import json
import os
import socket
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import HTTPException
from supabase import create_client, Client as SupabaseClient
import httpx

from app.models.client import Client, ClientCreate, ClientUpdate, ClientInDB, APIKeys, ClientSettings
from app.config import settings


class ClientService:
    """Service for managing clients and their configurations in Supabase"""

    def __init__(self, supabase_url: str, supabase_key: str, redis_client=None):
        # Ignore redis_client parameter for compatibility
        self.supabase: SupabaseClient = create_client(supabase_url, supabase_key)
        self.table_name = "clients"
        self.logger = logging.getLogger(__name__)
        self._platform_supabase_config = {
            "url": settings.supabase_url,
            "service_role_key": settings.supabase_service_role_key,
            "anon_key": settings.supabase_anon_key,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_hostname(url: str) -> Optional[str]:
        if not url:
            return None
        try:
            parsed = urlparse(url)
            return parsed.hostname
        except Exception:
            return None

    def _hostname_resolves(self, url: str) -> bool:
        hostname = self._extract_hostname(url)
        if not hostname:
            return False
        try:
            socket.getaddrinfo(hostname, None)
            return True
        except socket.gaierror:
            return False

    def _platform_fallback_config(self, original_config: Dict[str, str]) -> Dict[str, str]:
        fallback = dict(self._platform_supabase_config)
        # Preserve anon key preference if caller supplied one (useful for client UI flows)
        if original_config.get("anon_key"):
            fallback["anon_key"] = original_config["anon_key"]
        fallback["_fallback"] = True
        return fallback

    def _normalize_supabase_config(
        self,
        client_id: str,
        config: Optional[Dict[str, str]]
    ) -> Optional[Dict[str, str]]:
        """Validate and, if necessary, fall back to the platform Supabase configuration."""
        if not config:
            return None

        url = config.get("url")
        service_key = config.get("service_role_key")

        if not url or not service_key:
            return None

        # Already the platform project – nothing to normalize
        if url == self._platform_supabase_config["url"]:
            return config

        if not self._hostname_resolves(url):
            self.logger.warning(
                "Supabase host %s for client %s is not resolvable – falling back to platform project",
                url,
                client_id,
            )
            return self._platform_fallback_config(config)

        return config
        
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
        
        # Persist slug inside additional_settings for backward compatibility
        client_dict = {
            "id": client_id,
            "name": client_data.name,
            # store minimal top-level fields only (avoid non-existent columns)
            "additional_settings": {
                "description": client_data.description,
                "domain": client_data.domain,
                "slug": client_data.slug,
                "supabase_anon_key": settings.supabase.anon_key if settings.supabase else "",
                "embedding": settings.embedding.dict() if settings.embedding else {},
                "rerank": settings.rerank.dict() if settings.rerank else {}
            },
            "supabase_url": settings.supabase.url if settings.supabase else "",
            "supabase_service_role_key": settings.supabase.service_role_key if settings.supabase else "",
            "livekit_url": settings.livekit.server_url if settings.livekit else "",
            "livekit_api_key": settings.livekit.api_key if settings.livekit else "",
            "livekit_api_secret": settings.livekit.api_secret if settings.livekit else "",
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
            if api_keys.perplexity_api_key:
                client_dict["perplexity_api_key"] = api_keys.perplexity_api_key
            # Note: anthropic_api_key removed - not in APIKeys model

        if client_data.perplexity_api_key and "perplexity_api_key" not in client_dict:
            client_dict["perplexity_api_key"] = client_data.perplexity_api_key
        
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
            # Merge slug into additional_settings JSONB
            result = self.supabase.table(self.table_name).select("additional_settings").eq("id", client_id).execute()
            existing_additional = result.data[0].get("additional_settings", {}) if result.data else {}
            merged_additional = {**existing_additional, **{"slug": update_data.slug}}
            update_dict["additional_settings"] = merged_additional
            
        if update_data.perplexity_api_key is not None:
            update_dict["perplexity_api_key"] = update_data.perplexity_api_key

        # UserSense enabled flag (direct column)
        if update_data.usersense_enabled is not None:
            update_dict["usersense_enabled"] = update_data.usersense_enabled

        # Supertab client ID (direct column)
        if update_data.supertab_client_id is not None:
            update_dict["supertab_client_id"] = update_data.supertab_client_id

        # Firecrawl API key (direct column)
        if update_data.firecrawl_api_key is not None:
            update_dict["firecrawl_api_key"] = update_data.firecrawl_api_key

        # Fields that go in additional_settings JSONB (we merge once at the end to avoid losing changes)
        additional_settings = {}
        if update_data.description is not None:
            additional_settings["description"] = update_data.description
        if update_data.domain is not None:
            additional_settings["domain"] = update_data.domain
        if update_data.active is not None:
            additional_settings["active"] = update_data.active

        # Add embedding, rerank, and channel settings to additional_settings
        if update_data.settings:
            if update_data.settings.embedding:
                additional_settings["embedding"] = update_data.settings.embedding.dict()
            if update_data.settings.rerank:
                additional_settings["rerank"] = update_data.settings.rerank.dict()
            if getattr(update_data.settings, "channels", None):
                try:
                    additional_settings["channels"] = update_data.settings.channels.dict()
                except Exception:
                    pass

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
                if api_keys.perplexity_api_key is not None:
                    update_dict["perplexity_api_key"] = api_keys.perplexity_api_key
                # Note: anthropic_api_key is not in the APIKeys model

                # bithuman_api_secret doesn't have a dedicated column, store in additional_settings.api_keys
                if api_keys.bithuman_api_secret is not None:
                    if "api_keys" not in additional_settings:
                        additional_settings["api_keys"] = {}
                    additional_settings["api_keys"]["bithuman_api_secret"] = api_keys.bithuman_api_secret
                    self.logger.info(f"BITHUMAN SERVICE DEBUG - adding to additional_settings: {api_keys.bithuman_api_secret[:20] if api_keys.bithuman_api_secret else 'None'}...")

                # bey_api_key (Beyond Presence) - store in additional_settings.api_keys
                if api_keys.bey_api_key is not None:
                    if "api_keys" not in additional_settings:
                        additional_settings["api_keys"] = {}
                    additional_settings["api_keys"]["bey_api_key"] = api_keys.bey_api_key
                    self.logger.info(f"BEY SERVICE DEBUG - adding to additional_settings: {api_keys.bey_api_key[:20] if api_keys.bey_api_key else 'None'}...")

                # liveavatar_api_key (HeyGen LiveAvatar) - store in additional_settings.api_keys
                if api_keys.liveavatar_api_key is not None:
                    if "api_keys" not in additional_settings:
                        additional_settings["api_keys"] = {}
                    additional_settings["api_keys"]["liveavatar_api_key"] = api_keys.liveavatar_api_key
                    self.logger.info(f"LIVEAVATAR SERVICE DEBUG - adding to additional_settings: {api_keys.liveavatar_api_key[:20] if api_keys.liveavatar_api_key else 'None'}...")

            # Note: We don't have a settings column in the platform database
            # All settings are stored in individual columns
        
        # Merge and persist additional_settings once, after all settings have been gathered
        if additional_settings:
            result = self.supabase.table(self.table_name).select("additional_settings").eq("id", client_id).execute()
            existing_additional = result.data[0].get("additional_settings", {}) if result.data else {}
            # Normalize nulls to dict to avoid TypeError on merge
            if not isinstance(existing_additional, dict):
                existing_additional = {}

            # Deep merge for nested objects like api_keys
            merged_additional = {**existing_additional}
            for key, value in additional_settings.items():
                if key == "api_keys" and isinstance(value, dict):
                    # Deep merge api_keys
                    existing_api_keys = merged_additional.get("api_keys", {}) or {}
                    merged_additional["api_keys"] = {**existing_api_keys, **value}
                else:
                    merged_additional[key] = value

            update_dict["additional_settings"] = merged_additional
            self.logger.info(f"BITHUMAN SERVICE DEBUG - merged additional_settings api_keys: {merged_additional.get('api_keys', {}).keys()}")

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

        config = {
            "url": str(client.settings.supabase.url),
            "anon_key": client.settings.supabase.anon_key,
            "service_role_key": client.settings.supabase.service_role_key,
        }

        return self._normalize_supabase_config(client_id, config)

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
                        "url": os.getenv("CLIENT_SUPABASE_URL", settings.supabase_url),
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
                        "url": settings.supabase_url,
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
                    
                    # NEVER update API keys from client database - they come from platform only
                    # Skip any api_keys in synced_settings to preserve platform keys
                    # if synced_settings.get('api_keys'):
                    #     # We intentionally ignore API keys from client databases
                    
                    # Update only the timestamp, NEVER update API keys from client database
                    update_start = time.time()
                    update_dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
                    
                    # IMPORTANT: API keys are NEVER synced from client databases
                    # They must be managed in the platform database only for security
                    # Skip any API key updates from synced_settings
                    
                    # Only update timestamp
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

        if not self._hostname_resolves(supabase_url):
            self.logger.warning(
                "Skipping settings sync for Supabase host %s – hostname is not resolvable",
                supabase_url,
            )
            return {"api_keys": {}, "message": "Supabase host unreachable - skipped sync"}

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
                # IMPORTANT: We do NOT fetch API keys from client databases
                # API keys should only come from the platform database for security
                # The client database may have api_keys for their own use, but we ignore them
                
                settings = {
                    "api_keys": {},  # Always empty - API keys come from platform DB only
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
        if "supabase" not in settings_dict:
            settings_dict["supabase"] = {}

        additional = db_row.get("additional_settings", {}) or {}

        # Prefer legacy supabase_url, fall back to the newer supabase_project_url set by the provisioning worker
        supabase_url = db_row.get("supabase_url") or db_row.get("supabase_project_url") or ""
        settings_dict["supabase"]["url"] = supabase_url or ""

        # Pull anon key from dedicated column first, then from legacy additional settings
        supabase_anon = db_row.get("supabase_anon_key") or additional.get("supabase_anon_key", "")
        settings_dict["supabase"]["anon_key"] = supabase_anon

        # Service role key remains stored on the main row
        settings_dict["supabase"]["service_role_key"] = db_row.get("supabase_service_role_key") or ""
        
        # LiveKit config (required by model but may be empty)
        if "livekit" not in settings_dict:
            settings_dict["livekit"] = {}
        settings_dict["livekit"]["server_url"] = (db_row.get("livekit_url") or db_row.get("livekit_server_url") or settings.livekit_url or "https://example.com")
        settings_dict["livekit"]["api_key"] = db_row.get("livekit_api_key") or settings.livekit_api_key or "placeholder"
        settings_dict["livekit"]["api_secret"] = db_row.get("livekit_api_secret") or settings.livekit_api_secret or "placeholder"
        
        # API keys from individual columns
        if "api_keys" not in settings_dict:
            settings_dict["api_keys"] = {}
        api_key_fields = [
            "openai_api_key", "groq_api_key", "deepgram_api_key", "elevenlabs_api_key",
            "cartesia_api_key", "replicate_api_key", "deepinfra_api_key", "cerebras_api_key",
            "novita_api_key", "cohere_api_key", "siliconflow_api_key", "jina_api_key", "speechify_api_key",
            "perplexity_api_key", "anthropic_api_key"
        ]
        for key_field in api_key_fields:
            if key_field in db_row and db_row[key_field]:
                settings_dict["api_keys"][key_field] = db_row[key_field]
        
        # Extract additional fields from additional_settings JSONB
        additional = db_row.get("additional_settings", {}) or {}

        # Extract API keys stored in additional_settings.api_keys (like bithuman_api_secret, bey_api_key, liveavatar_api_key)
        additional_api_keys = additional.get("api_keys", {}) or {}
        if additional_api_keys.get("bithuman_api_secret"):
            settings_dict["api_keys"]["bithuman_api_secret"] = additional_api_keys["bithuman_api_secret"]
        if additional_api_keys.get("bey_api_key"):
            settings_dict["api_keys"]["bey_api_key"] = additional_api_keys["bey_api_key"]
        if additional_api_keys.get("liveavatar_api_key"):
            settings_dict["api_keys"]["liveavatar_api_key"] = additional_api_keys["liveavatar_api_key"]

        if db_row.get("supabase_project_ref") and "supabase_project_ref" not in additional:
            additional["supabase_project_ref"] = db_row.get("supabase_project_ref")
        if db_row.get("provisioning_status") and "provisioning_status" not in additional:
            additional["provisioning_status"] = db_row.get("provisioning_status")
        if db_row.get("provisioning_error") and "provisioning_error" not in additional:
            additional["provisioning_error"] = db_row.get("provisioning_error")
        if db_row.get("schema_version") and "schema_version" not in additional:
            additional["schema_version"] = db_row.get("schema_version")

        # Get embedding settings from additional_settings
        if "embedding" in additional and additional["embedding"]:
            settings_dict["embedding"] = additional["embedding"]
        
        # Get rerank settings from additional_settings
        if "rerank" in additional and additional["rerank"]:
            settings_dict["rerank"] = additional["rerank"]

        # Channel settings (Telegram etc.) from additional_settings
        if "channels" in additional and additional["channels"]:
            settings_dict["channels"] = additional["channels"]
        
        # Compute slug from column, additional_settings, or name fallback
        raw_slug = db_row.get("slug") or additional.get("slug")
        if not raw_slug:
            # simple slugify from name or domain
            base = (db_row.get("name") or db_row.get("domain") or "client").lower()
            import re
            raw_slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "client"

        # Create ClientInDB instance
        return ClientInDB(
            id=db_row["id"],
            slug=raw_slug,
            name=db_row["name"],
            description=additional.get("description", db_row.get("description")),
            domain=additional.get("domain", db_row.get("domain")),
            active=additional.get("active", db_row.get("active", True)),
            settings=ClientSettings(**settings_dict) if settings_dict else ClientSettings(),
            created_at=db_row.get("created_at"),
            updated_at=db_row.get("updated_at"),
            additional_settings=additional,  # Include the full additional_settings
            perplexity_api_key=db_row.get("perplexity_api_key"),
            supertab_client_id=db_row.get("supertab_client_id"),
            firecrawl_api_key=db_row.get("firecrawl_api_key")
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
