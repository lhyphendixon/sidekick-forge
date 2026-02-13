#!/usr/bin/env python3
"""
API Key Loader for Agent Worker
Loads API keys from metadata, environment, or Supabase
Supports platform-managed keys for Adventurer tier clients
"""
import os
import logging
import json
from typing import Dict, Optional, Any, Tuple

logger = logging.getLogger(__name__)


class APIKeyLoader:
    """Handles loading API keys from various sources"""

    # Default provider configuration for Adventurer tier (platform-managed)
    PLATFORM_DEFAULT_CONFIG = {
        "llm": {
            "provider": "cerebras",
            "model": "zai-glm-4.7",  # Cerebras GLM 4.7 (reasoning toggle enabled)
        },
        "stt": {
            "provider": "cartesia",
        },
        "tts": {
            "provider": "cartesia",
        },
        "embedding": {
            "provider": "siliconflow",
            "model": "Qwen/Qwen3-Embedding-4B",
        },
        "rerank": {
            "enabled": True,
            "provider": "siliconflow",
            "model": "Qwen/Qwen3-Reranker-4B",
        }
    }

    @staticmethod
    def load_api_keys(metadata: Dict[str, Any]) -> Dict[str, str]:
        """
        Load API keys following the Dynamic API Key Loading Policy:
        1. Check if client uses platform keys (Adventurer tier)
        2. If platform keys: load from platform_api_keys table
        3. Otherwise: Start with metadata api_keys, merge with client's Supabase keys
        4. FAIL with clear error if required keys are missing

        NO FALLBACK to environment variables except for Supabase connection itself
        """
        # Start with metadata api_keys (includes livekit_url, livekit_api_key, livekit_api_secret)
        api_keys = dict(metadata.get('api_keys', {}))
        client_id = metadata.get('client_id')

        if client_id:
            # Check if client uses platform keys (Adventurer tier)
            uses_platform, client_tier = APIKeyLoader._check_uses_platform_keys(client_id)

            if uses_platform:
                logger.info(f"Client {client_id} uses platform keys (tier: {client_tier})")
                platform_keys = APIKeyLoader._load_platform_keys()
                if platform_keys:
                    api_keys.update(platform_keys)
                    logger.info(f"Loaded {len(platform_keys)} platform API keys for Adventurer tier")
                else:
                    logger.warning("Failed to load platform API keys - falling back to client keys")
                    # Fall back to client keys if platform keys not available
                    supabase_keys = APIKeyLoader._load_from_supabase(client_id)
                    if supabase_keys:
                        api_keys.update(supabase_keys)
            else:
                # Not using platform keys - load client's own keys
                logger.info(f"Loading API keys from Supabase for client {client_id} (tier: {client_tier})")
                supabase_keys = APIKeyLoader._load_from_supabase(client_id)
                if supabase_keys:
                    api_keys.update(supabase_keys)
                    logger.info(f"Merged {len(supabase_keys)} API keys from Supabase")
                else:
                    logger.warning(f"Failed to load API keys from Supabase for client {client_id}")

                # Supplement missing keys with platform defaults
                # This allows Champion clients to use platform-managed keys
                # (e.g., Polymarket) when they haven't set their own
                platform_keys = APIKeyLoader._load_platform_keys()
                if platform_keys:
                    supplemented = []
                    for key, value in platform_keys.items():
                        if key not in api_keys or not api_keys.get(key):
                            api_keys[key] = value
                            supplemented.append(key)
                    if supplemented:
                        logger.info(f"Supplemented {len(supplemented)} missing keys from platform defaults: {supplemented}")

        # Log if we only have metadata keys
        if not client_id and api_keys:
            logger.info("Using API keys from metadata only (no client_id)")
            
        # NO ENVIRONMENT VARIABLE FALLBACK - This violates the Dynamic API Key Loading Policy
        # Environment variables should only be used for initial bootstrap (Supabase connection)
        
        if not api_keys:
            logger.error(
                "❌ CRITICAL: No API keys found. The agent cannot function without API keys. "
                "This usually happens when:\n"
                "1. Supabase authentication failed (check SUPABASE_SERVICE_ROLE_KEY)\n"
                "2. The client has no API keys configured in their settings\n"
                "3. Platform API keys are not configured (for Adventurer tier)\n"
                "4. The job metadata doesn't include API keys\n"
                f"Client ID: {client_id}"
            )
            # Return empty dict - the agent will fail fast with ConfigurationError
                    
        # Log which keys are available (not the values)
        available_keys = []
        test_keys = []
        missing_keys = []

        for key, value in api_keys.items():
            # Skip internal metadata flags (e.g. _uses_platform_keys, _platform_inference_name)
            if key.startswith('_'):
                continue
            if not value:
                missing_keys.append(key)
            elif APIKeyLoader.validate_api_key(key, value):
                available_keys.append(key)
            else:
                test_keys.append(key)
        
        logger.info(f"API Key Summary - Available: {len(available_keys)}, Test/Invalid: {len(test_keys)}, Missing: {len(missing_keys)}")
        logger.info(f"Available API keys: {available_keys}")
        if test_keys:
            logger.warning(f"Test/Invalid API keys detected: {test_keys}")
        if missing_keys:
            logger.info(f"Missing API keys: {missing_keys}")
        
        return api_keys
    
    @staticmethod
    def _load_from_supabase(client_id: str) -> Dict[str, str]:
        """Load API keys from Supabase for a specific client"""
        try:
            # Import here to avoid circular dependencies
            from supabase import create_client, Client
            
            supabase_url = os.getenv('SUPABASE_URL')
            supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
            
            if not supabase_url or not supabase_key:
                logger.error("Supabase credentials not available - cannot load API keys")
                return {}
                
            # Create Supabase client
            supabase: Client = create_client(supabase_url, supabase_key)
            
            # Get client API keys from the 'clients' table
            # Platform database stores API keys as individual columns
            api_key_columns = [
                # LLM providers
                'openai_api_key',
                'groq_api_key',
                'cerebras_api_key',
                'deepinfra_api_key',
                'replicate_api_key',
                # Speech providers
                'deepgram_api_key',
                'elevenlabs_api_key',
                'cartesia_api_key',
                'speechify_api_key',
                # Embedding/rerank providers
                'novita_api_key',
                'cohere_api_key',
                'siliconflow_api_key',
                'jina_api_key',
                'perplexity_api_key',
                # Additional
                'anthropic_api_key',
                # Prediction markets
                'polymarket_api_key',
                'polymarket_api_secret',
                'polymarket_passphrase'
            ]
            
            # Also fetch additional_settings for keys stored there (like bithuman_api_secret)
            columns_str = ', '.join(api_key_columns) + ', additional_settings'
            result = supabase.table('clients').select(columns_str).eq('id', client_id).single().execute()

            if result.data:
                # Convert database columns to api_keys dict
                api_keys = {}
                for key in api_key_columns:
                    value = result.data.get(key)
                    if value and value != '<needs-actual-key>':
                        api_keys[key] = value

                # Also extract API keys from additional_settings.api_keys (e.g., bithuman_api_secret)
                additional_settings = result.data.get('additional_settings') or {}
                if isinstance(additional_settings, dict):
                    additional_api_keys = additional_settings.get('api_keys') or {}
                    if isinstance(additional_api_keys, dict):
                        for key, value in additional_api_keys.items():
                            if value and value != '<needs-actual-key>':
                                api_keys[key] = value
                                logger.info(f"Loaded {key} from additional_settings.api_keys")

                if api_keys:
                    logger.info(f"Successfully loaded {len(api_keys)} API keys from platform database for client {client_id}")
                    return api_keys
                else:
                    logger.warning(f"No API keys found for client {client_id} in platform database")
                    return {}
            else:
                logger.error(f"Client {client_id} not found in Supabase")
                return {}
                
        except Exception as e:
            error_str = str(e)
            if "Invalid API key" in error_str:
                logger.error(
                    f"❌ CRITICAL: Supabase authentication failed - service role key is invalid or expired. "
                    f"The worker cannot load API keys dynamically. Please update SUPABASE_SERVICE_ROLE_KEY "
                    f"in the environment configuration."
                )
                logger.error(f"Current SUPABASE_URL: {supabase_url}")
                logger.error(f"Error details: {e}")
            else:
                logger.error(f"Failed to load API keys from Supabase: {e}")
            return {}

    @staticmethod
    def _check_uses_platform_keys(client_id: str) -> Tuple[bool, str]:
        """
        Check if a client should use platform API keys.
        Returns (uses_platform_keys, tier)
        """
        try:
            from supabase import create_client, Client

            supabase_url = os.getenv('SUPABASE_URL')
            supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

            if not supabase_url or not supabase_key:
                return (False, 'unknown')

            supabase: Client = create_client(supabase_url, supabase_key)

            # Get client's tier and uses_platform_keys flag
            result = supabase.table('clients').select(
                'tier, uses_platform_keys'
            ).eq('id', client_id).single().execute()

            if result.data:
                tier = result.data.get('tier', 'champion')
                uses_platform = result.data.get('uses_platform_keys')

                # If explicitly set, use that value
                if uses_platform is not None:
                    return (uses_platform, tier)

                # Otherwise, default based on tier
                # Adventurer tier defaults to platform keys, others don't
                if tier == 'adventurer':
                    return (True, tier)
                else:
                    return (False, tier)

            return (False, 'unknown')

        except Exception as e:
            logger.warning(f"Failed to check uses_platform_keys for client {client_id}: {e}")
            return (False, 'unknown')

    @staticmethod
    def _load_platform_keys() -> Dict[str, str]:
        """
        Load platform API keys from the platform_api_keys table.
        These are shared keys managed by Sidekick Forge for Adventurer tier clients.
        """
        try:
            from supabase import create_client, Client

            # Use platform-specific Supabase credentials (fallback to regular if not set)
            supabase_url = os.getenv('PLATFORM_SUPABASE_URL') or os.getenv('SUPABASE_URL')
            supabase_key = os.getenv('PLATFORM_SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_SERVICE_ROLE_KEY')

            if not supabase_url or not supabase_key:
                logger.error("Platform Supabase credentials not available - cannot load platform keys")
                return {}

            supabase: Client = create_client(supabase_url, supabase_key)

            # Get all active platform API keys
            result = supabase.table('platform_api_keys').select(
                'key_name, key_value'
            ).eq('is_active', True).execute()

            if result.data:
                api_keys = {}
                for row in result.data:
                    key_name = row.get('key_name')
                    key_value = row.get('key_value')
                    if key_name and key_value:
                        api_keys[key_name] = key_value

                if api_keys:
                    logger.info(f"Successfully loaded {len(api_keys)} platform API keys")
                    return api_keys
                else:
                    logger.warning("No active platform API keys found")
                    return {}
            else:
                logger.warning("platform_api_keys table returned no data")
                return {}

        except Exception as e:
            logger.error(f"Failed to load platform API keys: {e}")
            return {}

    @staticmethod
    def get_platform_config() -> Dict[str, Any]:
        """
        Get the default provider configuration for Adventurer tier.
        This defines which providers and models to use with platform keys.
        """
        return APIKeyLoader.PLATFORM_DEFAULT_CONFIG.copy()

    @staticmethod
    def validate_api_key(key: str, value: Any) -> bool:
        """Validate that an API key looks real (not a test key)"""
        if not value or not isinstance(value, str):
            return False

        test_patterns = ['test', 'dummy', 'placeholder', 'example']
        value_lower = value.lower()
        
        for pattern in test_patterns:
            if pattern in value_lower:
                return False
                
        # Check for specific key patterns
        if key == 'openai_api_key' and not value.startswith('sk-'):
            return False
        elif key == 'groq_api_key' and not value.startswith('gsk_'):
            return False
            
        return True
