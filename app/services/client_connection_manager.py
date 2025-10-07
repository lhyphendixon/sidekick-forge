"""
Client Connection Manager for Sidekick Forge Platform

This service manages connections to individual client databases,
implementing the multi-tenant architecture where each client has
their own Supabase project.
"""
import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID
from supabase import create_client, Client
from functools import lru_cache
import asyncio

logger = logging.getLogger(__name__)


class ClientConfigurationError(Exception):
    """Raised when client configuration is missing or invalid"""
    pass


class ClientConnectionManager:
    """
    Manages database connections for multi-tenant architecture.
    
    This is the core component that enables the platform to connect
    to different client databases based on client_id.
    """
    
    def __init__(self):
        # Platform database credentials (Sidekick Forge)
        self.platform_url = os.getenv("SUPABASE_URL")
        self.platform_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not self.platform_url or not self.platform_key:
            raise ClientConfigurationError(
                "Platform database credentials not configured. "
                "Please set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )
        
        # Initialize platform database client
        self.platform_client = create_client(self.platform_url, self.platform_key)
        logger.info("ClientConnectionManager initialized with platform database")
        
        # Cache for client configurations to reduce database queries
        self._client_cache: Dict[str, Dict[str, Any]] = {}
    
    def get_client_db_client(self, client_id: UUID) -> Client:
        """
        Get a Supabase client configured for a specific tenant's database.
        
        This is the PRIMARY method that all services should use to get
        a database connection for client-specific operations.
        
        Args:
            client_id: The UUID of the client
            
        Returns:
            A configured Supabase client for the tenant's database
            
        Raises:
            ClientConfigurationError: If client not found or credentials missing
        """
        client_id_str = str(client_id)
        
        # Check cache first
        if client_id_str not in self._client_cache:
            self._load_client_config(client_id)
        
        client_config = self._client_cache[client_id_str]

        status = (client_config.get('provisioning_status') or 'ready').lower()
        if status != 'ready':
            if not self._promote_legacy_client_if_ready(client_id_str, client_config):
                raise ClientConfigurationError(
                    f"Client {client_id} is not ready (provisioning_status={status})."
                )
            status = (client_config.get('provisioning_status') or 'ready').lower()

        # Validate required credentials
        if not client_config.get('supabase_url') or not client_config.get('supabase_service_role_key'):
            raise ClientConfigurationError(
                f"Client {client_id} does not have database credentials configured. "
                f"Please configure Supabase URL and service role key for this client."
            )
        
        # Create and return client connection
        return create_client(
            client_config['supabase_url'],
            client_config['supabase_service_role_key']
        )
    
    def _load_client_config(self, client_id: UUID) -> None:
        """
        Load client configuration from platform database.
        
        NO FALLBACKS - if client doesn't exist, we fail with clear error.
        """
        try:
            result = self.platform_client.table('clients').select('*').eq('id', str(client_id)).single().execute()
            
            if not result.data:
                raise ClientConfigurationError(
                    f"Client {client_id} not found in platform database. "
                    f"Please ensure the client is properly registered."
                )
            
            self._client_cache[str(client_id)] = result.data
            logger.info(f"Loaded configuration for client {client_id}: {result.data.get('name', 'Unknown')}")
            
        except Exception as e:
            if "relation" in str(e) and "does not exist" in str(e):
                raise ClientConfigurationError(
                    "Platform database not properly initialized. "
                    "Please run the database setup scripts first."
                )
            raise ClientConfigurationError(f"Failed to load client configuration: {str(e)}")

    def _promote_legacy_client_if_ready(self, client_id: str, client_config: Dict[str, Any]) -> bool:
        """Promote pre-provisioning clients with manual credentials to ready status."""
        try:
            auto_provision = bool(client_config.get('auto_provision'))
            if auto_provision:
                return False

            has_credentials = bool(client_config.get('supabase_url') and client_config.get('supabase_service_role_key'))
            if not has_credentials:
                return False

            update_payload = {
                'provisioning_status': 'ready',
                'provisioning_error': None,
            }

            now_iso = datetime.utcnow().isoformat()
            if not client_config.get('provisioning_started_at'):
                update_payload['provisioning_started_at'] = now_iso
            if not client_config.get('provisioning_completed_at'):
                update_payload['provisioning_completed_at'] = now_iso

            self.platform_client.table('clients').update(update_payload).eq('id', client_id).execute()
            client_config.update(update_payload)
            logger.info("Promoted legacy client %s to ready status", client_id)
            return True
        except Exception as exc:
            logger.warning(
                "Failed to auto-promote client %s provisioning status: %s",
                client_id,
                exc,
            )
            return False

    def get_client_api_keys(self, client_id: UUID) -> Dict[str, Optional[str]]:
        """
        Get all API keys configured for a client.
        
        Returns a dictionary of all third-party API keys.
        """
        client_id_str = str(client_id)
        
        # Ensure client config is loaded
        if client_id_str not in self._client_cache:
            self._load_client_config(client_id)
        
        client_config = self._client_cache[client_id_str]
        
        # Extract all API keys
        api_keys = {
            'openai_api_key': client_config.get('openai_api_key'),
            'groq_api_key': client_config.get('groq_api_key'),
            'deepgram_api_key': client_config.get('deepgram_api_key'),
            'elevenlabs_api_key': client_config.get('elevenlabs_api_key'),
            'cartesia_api_key': client_config.get('cartesia_api_key'),
            'speechify_api_key': client_config.get('speechify_api_key'),
            'deepinfra_api_key': client_config.get('deepinfra_api_key'),
            'replicate_api_key': client_config.get('replicate_api_key'),
            'novita_api_key': client_config.get('novita_api_key'),
            'cohere_api_key': client_config.get('cohere_api_key'),
            'siliconflow_api_key': client_config.get('siliconflow_api_key'),
            'jina_api_key': client_config.get('jina_api_key'),
            'anthropic_api_key': client_config.get('anthropic_api_key'),
        }
        
        # Also check for LiveKit credentials
        api_keys['livekit_url'] = client_config.get('livekit_url')
        api_keys['livekit_api_key'] = client_config.get('livekit_api_key')
        api_keys['livekit_api_secret'] = client_config.get('livekit_api_secret')
        
        return api_keys
    
    def get_client_info(self, client_id: UUID) -> Dict[str, Any]:
        """
        Get basic client information (non-sensitive).
        """
        client_id_str = str(client_id)
        
        # Ensure client config is loaded
        if client_id_str not in self._client_cache:
            self._load_client_config(client_id)
        
        client_config = self._client_cache[client_id_str]
        
        return {
            'id': client_config.get('id'),
            'name': client_config.get('name'),
            'created_at': client_config.get('created_at'),
            'updated_at': client_config.get('updated_at'),
            'has_livekit': bool(client_config.get('livekit_url')),
            'has_supabase': bool(client_config.get('supabase_url')),
        }
    
    async def find_client_by_agent(self, agent_slug: str) -> Optional[UUID]:
        """
        Find which client owns a specific agent by searching all client databases.
        
        This is used when we receive an agent_slug without knowing the client_id.
        """
        try:
            # Get all clients from platform database
            result = self.platform_client.table('clients').select('id, name').execute()
            
            if not result.data:
                logger.warning("No clients found in platform database")
                return None
            
            # Search each client's database for the agent
            for client in result.data:
                client_id = UUID(client['id'])
                try:
                    client_db = self.get_client_db_client(client_id)
                    
                    # Check if agent exists in this client's database
                    agent_result = client_db.table('agents').select('slug').eq('slug', agent_slug).single().execute()
                    
                    if agent_result.data:
                        logger.info(f"Found agent '{agent_slug}' in client '{client['name']}' (ID: {client_id})")
                        return client_id
                        
                except Exception as e:
                    logger.debug(f"Error checking client {client_id} for agent: {e}")
                    continue
            
            logger.warning(f"Agent '{agent_slug}' not found in any client database")
            return None
            
        except Exception as e:
            logger.error(f"Error searching for agent across clients: {e}")
            return None
    
    def clear_cache(self, client_id: Optional[UUID] = None) -> None:
        """
        Clear cached client configurations.
        
        Args:
            client_id: If provided, only clear cache for this client.
                      If None, clear entire cache.
        """
        if client_id:
            self._client_cache.pop(str(client_id), None)
            logger.info(f"Cleared cache for client {client_id}")
        else:
            self._client_cache.clear()
            logger.info("Cleared entire client cache")


# Global instance for easy access
_connection_manager: Optional[ClientConnectionManager] = None


def get_connection_manager() -> ClientConnectionManager:
    """
    Get the global ClientConnectionManager instance.
    
    This ensures we have a single instance managing all connections.
    """
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ClientConnectionManager()
    return _connection_manager
