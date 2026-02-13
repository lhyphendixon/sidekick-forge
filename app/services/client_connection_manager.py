"""
Client Connection Manager for Sidekick Forge Platform

This service manages connections to individual client databases,
implementing the multi-tenant architecture with tiered hosting:

- Adventurer (shared): Multiple clients share a pool database with client_id isolation
- Champion (dedicated): Each client has their own Supabase project
- Paragon (dedicated): Same as Champion with additional sovereign features
"""
import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from uuid import UUID
from supabase import create_client, Client
from functools import lru_cache
import asyncio

from app.services.tier_features import (
    ClientTier,
    HostingType,
    get_tier_features,
    is_shared_hosting,
    check_feature_access,
    check_limit,
)

logger = logging.getLogger(__name__)


class ClientConfigurationError(Exception):
    """Raised when client configuration is missing or invalid"""
    pass


class ClientConnectionManager:
    """
    Manages database connections for multi-tenant architecture.

    This is the core component that enables the platform to connect
    to different client databases based on client_id and hosting type.

    Supports tiered hosting:
    - Shared pool (Adventurer): Multiple clients in one database with client_id isolation
    - Dedicated (Champion/Paragon): Each client has their own Supabase project
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

        # Shared pool client (lazy loaded)
        self._shared_pool_client: Optional[Client] = None
        self._shared_pool_config: Optional[Dict[str, Any]] = None
    
    def _get_shared_pool_client(self) -> Client:
        """
        Get or create the shared pool Supabase client for Adventurer tier.

        Raises:
            ClientConfigurationError: If no active shared pool is configured
        """
        if self._shared_pool_client is None:
            # Load shared pool config from platform DB
            try:
                result = self.platform_client.table('shared_pool_config').select('*').eq(
                    'is_active', True
                ).eq('pool_name', 'adventurer_pool').single().execute()

                if not result.data:
                    raise ClientConfigurationError(
                        "No active shared pool configured. "
                        "Please configure shared_pool_config for Adventurer tier."
                    )

                self._shared_pool_config = result.data
                self._shared_pool_client = create_client(
                    result.data['supabase_url'],
                    result.data['supabase_service_role_key']
                )
                logger.info("Shared pool client initialized for Adventurer tier")

            except Exception as e:
                if "does not exist" in str(e):
                    raise ClientConfigurationError(
                        "Shared pool not yet configured. Adventurer tier unavailable."
                    )
                raise

        return self._shared_pool_client

    def get_client_db_client(self, client_id: UUID) -> Client:
        """
        Get a Supabase client configured for a specific tenant's database.

        This is the PRIMARY method that all services should use to get
        a database connection for client-specific operations.

        For shared hosting (Adventurer tier), returns the shared pool client.
        For dedicated hosting (Champion/Paragon), returns the client's own database.

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
        hosting_type = client_config.get('hosting_type', 'dedicated')

        status = (client_config.get('provisioning_status') or 'ready').lower()
        if status != 'ready':
            if not self._promote_legacy_client_if_ready(client_id_str, client_config):
                raise ClientConfigurationError(
                    f"Client {client_id} is not ready (provisioning_status={status})."
                )
            status = (client_config.get('provisioning_status') or 'ready').lower()

        # Route based on hosting type
        if hosting_type == 'shared':
            # Adventurer tier: use shared pool
            return self._get_shared_pool_client()
        else:
            # Champion/Paragon tier: use dedicated project
            if not client_config.get('supabase_url') or not client_config.get('supabase_service_role_key'):
                raise ClientConfigurationError(
                    f"Client {client_id} does not have database credentials configured. "
                    f"Please configure Supabase URL and service role key for this client."
                )

            return create_client(
                client_config['supabase_url'],
                client_config['supabase_service_role_key']
            )

    def get_client_db_client_with_info(self, client_id: UUID) -> Tuple[Client, str, str]:
        """
        Get a Supabase client along with hosting info.

        Returns:
            Tuple of (supabase_client, hosting_type, tier)
            - hosting_type: 'shared' or 'dedicated'
            - tier: 'adventurer', 'champion', or 'paragon'
        """
        client_id_str = str(client_id)

        if client_id_str not in self._client_cache:
            self._load_client_config(client_id)

        client_config = self._client_cache[client_id_str]
        hosting_type = client_config.get('hosting_type', 'dedicated')
        tier = client_config.get('tier', 'champion')

        db_client = self.get_client_db_client(client_id)
        return db_client, hosting_type, tier
    
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

    def get_client_api_keys(self, client_id: UUID, include_platform_keys: bool = False) -> Dict[str, Optional[str]]:
        """
        Get all API keys configured for a client.

        Returns a dictionary of all third-party API keys.
        If uses_platform_keys is True and include_platform_keys is False (default),
        returns an empty dict with a special '_uses_platform_keys' flag set to True.
        This prevents platform keys from being exposed to users.

        Set include_platform_keys=True only for internal agent runtime use.
        """
        client_id_str = str(client_id)

        # Ensure client config is loaded
        if client_id_str not in self._client_cache:
            self._load_client_config(client_id)

        client_config = self._client_cache[client_id_str]

        # Check if this client uses platform keys
        uses_platform_keys = client_config.get('uses_platform_keys', False)

        # If uses_platform_keys, either return platform keys (internal use) or empty dict with flag
        if uses_platform_keys:
            if include_platform_keys:
                # Internal agent runtime use - load actual platform keys
                platform_keys = self._load_platform_api_keys()
                if platform_keys:
                    logger.info(f"Client {client_id_str} uses platform keys: {list(platform_keys.keys())}")
                    # Also include LiveKit credentials from client config
                    platform_keys['livekit_url'] = client_config.get('livekit_url')
                    platform_keys['livekit_api_key'] = client_config.get('livekit_api_key')
                    platform_keys['livekit_api_secret'] = client_config.get('livekit_api_secret')
                    return platform_keys
            else:
                # User-facing - don't expose platform keys, just indicate they're using them
                logger.info(f"Client {client_id_str} uses Sidekick Forge Inference (platform keys)")
                # Still include client-specific keys (avatar providers, etc.) from additional_settings
                additional_api_keys = (client_config.get('additional_settings') or {}).get('api_keys', {}) or {}
                result = {
                    '_uses_platform_keys': True,
                    '_platform_inference_name': 'Sidekick Forge Inference',
                    # Still include LiveKit credentials as those are client-specific
                    'livekit_url': client_config.get('livekit_url'),
                    'livekit_api_key': client_config.get('livekit_api_key'),
                    'livekit_api_secret': client_config.get('livekit_api_secret'),
                }
                # Include client-specific non-platform keys (avatar providers, etc.)
                for key in ('bithuman_api_secret', 'bey_api_key', 'liveavatar_api_key'):
                    val = client_config.get(key) or additional_api_keys.get(key)
                    if val:
                        result[key] = val
                return result

        # Check additional_settings.api_keys for any stored keys
        additional_api_keys = (client_config.get('additional_settings') or {}).get('api_keys', {}) or {}

        # Extract all API keys (check top-level columns first, then additional_settings.api_keys)
        api_keys = {
            'openai_api_key': client_config.get('openai_api_key') or additional_api_keys.get('openai_api_key'),
            'groq_api_key': client_config.get('groq_api_key') or additional_api_keys.get('groq_api_key'),
            'deepgram_api_key': client_config.get('deepgram_api_key') or additional_api_keys.get('deepgram_api_key'),
            'elevenlabs_api_key': client_config.get('elevenlabs_api_key') or additional_api_keys.get('elevenlabs_api_key'),
            'cartesia_api_key': client_config.get('cartesia_api_key') or additional_api_keys.get('cartesia_api_key'),
            'speechify_api_key': client_config.get('speechify_api_key') or additional_api_keys.get('speechify_api_key'),
            'deepinfra_api_key': client_config.get('deepinfra_api_key') or additional_api_keys.get('deepinfra_api_key'),
            'replicate_api_key': client_config.get('replicate_api_key') or additional_api_keys.get('replicate_api_key'),
            'novita_api_key': client_config.get('novita_api_key') or additional_api_keys.get('novita_api_key'),
            'cohere_api_key': client_config.get('cohere_api_key') or additional_api_keys.get('cohere_api_key'),
            'siliconflow_api_key': client_config.get('siliconflow_api_key') or additional_api_keys.get('siliconflow_api_key'),
            'jina_api_key': client_config.get('jina_api_key') or additional_api_keys.get('jina_api_key'),
            'anthropic_api_key': client_config.get('anthropic_api_key') or additional_api_keys.get('anthropic_api_key'),
            'cerebras_api_key': client_config.get('cerebras_api_key') or additional_api_keys.get('cerebras_api_key'),
            'bithuman_api_secret': client_config.get('bithuman_api_secret') or additional_api_keys.get('bithuman_api_secret'),
            'bey_api_key': client_config.get('bey_api_key') or additional_api_keys.get('bey_api_key'),
        }

        # Also check for LiveKit credentials
        api_keys['livekit_url'] = client_config.get('livekit_url')
        api_keys['livekit_api_key'] = client_config.get('livekit_api_key')
        api_keys['livekit_api_secret'] = client_config.get('livekit_api_secret')

        return api_keys

    def _load_platform_api_keys(self) -> Dict[str, Optional[str]]:
        """Load API keys from the platform_api_keys table."""
        try:
            result = self._platform_supabase.table('platform_api_keys').select(
                'key_name, key_value'
            ).execute()

            if result.data:
                api_keys = {}
                for row in result.data:
                    key_name = row.get('key_name')
                    key_value = row.get('key_value')
                    if key_name and key_value:
                        api_keys[key_name] = key_value
                return api_keys
            return {}
        except Exception as e:
            logger.error(f"Failed to load platform API keys: {e}")
            return {}
    
    def get_client_info(self, client_id: UUID) -> Dict[str, Any]:
        """
        Get basic client information (non-sensitive) including tier info.
        """
        client_id_str = str(client_id)

        # Ensure client config is loaded
        if client_id_str not in self._client_cache:
            self._load_client_config(client_id)

        client_config = self._client_cache[client_id_str]
        tier = client_config.get('tier', 'champion')
        tier_features = get_tier_features(tier)

        return {
            'id': client_config.get('id'),
            'name': client_config.get('name'),
            'tier': tier,
            'tier_display': tier_features.get('display_name', 'Champion'),
            'tier_emoji': tier_features.get('display_emoji', '\U0001F535'),
            'hosting_type': client_config.get('hosting_type', 'dedicated'),
            'max_sidekicks': client_config.get('max_sidekicks'),
            'created_at': client_config.get('created_at'),
            'updated_at': client_config.get('updated_at'),
            'has_livekit': bool(client_config.get('livekit_url')),
            'has_supabase': bool(client_config.get('supabase_url')),
        }

    def get_client_tier(self, client_id: UUID) -> str:
        """Get the tier for a client."""
        client_id_str = str(client_id)
        if client_id_str not in self._client_cache:
            self._load_client_config(client_id)
        return self._client_cache[client_id_str].get('tier', 'champion')

    def check_client_feature(self, client_id: UUID, feature: str) -> bool:
        """Check if a client has access to a specific feature based on their tier."""
        tier = self.get_client_tier(client_id)
        return check_feature_access(tier, feature)

    def check_client_limit(self, client_id: UUID, limit_name: str, current_value: int) -> Tuple[bool, Optional[int]]:
        """
        Check if a client is within a specific limit.

        Returns:
            Tuple of (is_within_limit, max_allowed)
        """
        tier = self.get_client_tier(client_id)
        return check_limit(tier, limit_name, current_value)

    def is_shared_hosting_client(self, client_id: UUID) -> bool:
        """Check if a client uses shared hosting (Adventurer tier)."""
        client_id_str = str(client_id)
        if client_id_str not in self._client_cache:
            self._load_client_config(client_id)
        return self._client_cache[client_id_str].get('hosting_type') == 'shared'
    
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
