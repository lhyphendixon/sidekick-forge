#!/usr/bin/env python3
"""
API Key Loader for Agent Worker
Loads API keys from metadata, environment, or Supabase
"""
import os
import logging
import json
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

class APIKeyLoader:
    """Handles loading API keys from various sources"""
    
    @staticmethod
    def load_api_keys(metadata: Dict[str, Any]) -> Dict[str, str]:
        """
        Load API keys following the Dynamic API Key Loading Policy:
        1. Check if client uses platform keys (Sidekick Forge Inference)
        2. If platform keys: load from platform_api_keys table
        3. If BYOK: load from client's columns in clients table
        4. Fall back to metadata if needed

        NO FALLBACK to environment variables except for Supabase connection itself
        """
        api_keys = {}
        client_id = metadata.get('client_id')

        # First, check if this client uses platform keys (Sidekick Forge Inference)
        uses_platform_keys = APIKeyLoader._check_uses_platform_keys(client_id)

        if uses_platform_keys:
            # Load from platform_api_keys table
            logger.info(f"Client {client_id} uses Sidekick Forge Inference - loading platform keys")
            api_keys = APIKeyLoader._load_platform_keys()
            if api_keys:
                logger.info(f"Successfully loaded {len(api_keys)} platform API keys")
            else:
                logger.warning("Failed to load platform keys, falling back to metadata")
        else:
            # Try to load from client's own columns in Supabase (BYOK mode)
            if client_id:
                logger.info(f"Loading API keys from Supabase for client {client_id} (BYOK mode)")
                supabase_keys = APIKeyLoader._load_from_supabase(client_id)
                if supabase_keys:
                    api_keys = supabase_keys
                    logger.info(f"Successfully loaded {len(api_keys)} API keys from Supabase")
                else:
                    logger.warning(f"Failed to load API keys from Supabase for client {client_id}")

        # If no keys yet, try metadata (secondary source)
        if not api_keys and metadata.get('api_keys'):
            logger.info("Loading API keys from metadata (secondary source)")
            api_keys = metadata['api_keys']
            
        # NO ENVIRONMENT VARIABLE FALLBACK - This violates the Dynamic API Key Loading Policy
        # Environment variables should only be used for initial bootstrap (Supabase connection)
        
        if not api_keys:
            logger.error(
                "❌ CRITICAL: No API keys found. The agent cannot function without API keys. "
                "This usually happens when:\n"
                "1. Supabase authentication failed (check SUPABASE_SERVICE_ROLE_KEY)\n"
                "2. The client has no API keys configured in their settings\n"
                "3. The job metadata doesn't include API keys\n"
                f"Client ID: {client_id}"
            )
            # Return empty dict - the agent will fail fast with ConfigurationError
                    
        # Log which keys are available (not the values)
        available_keys = []
        test_keys = []
        missing_keys = []
        
        for key, value in api_keys.items():
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
    def _check_uses_platform_keys(client_id: str) -> bool:
        """Check if a client uses platform keys (Sidekick Forge Inference)"""
        if not client_id:
            return True  # Default to platform keys

        try:
            from supabase import create_client, Client

            supabase_url = os.getenv('SUPABASE_URL')
            supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

            if not supabase_url or not supabase_key:
                return True  # Default to platform keys

            supabase: Client = create_client(supabase_url, supabase_key)
            result = supabase.table('clients').select('uses_platform_keys').eq('id', client_id).single().execute()

            if result.data:
                # Only use BYOK (return False) if explicitly set to False
                # None or True = use platform keys
                if result.data.get('uses_platform_keys') is False:
                    return False
            return True  # Default to platform keys
        except Exception as e:
            logger.warning(f"Failed to check uses_platform_keys: {e}")
            return True  # Default to platform keys

    @staticmethod
    def _load_platform_keys() -> Dict[str, str]:
        """Load API keys from the platform_api_keys table"""
        try:
            from supabase import create_client, Client

            supabase_url = os.getenv('SUPABASE_URL')
            supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

            if not supabase_url or not supabase_key:
                logger.error("Supabase credentials not available - cannot load platform keys")
                return {}

            supabase: Client = create_client(supabase_url, supabase_key)
            result = supabase.table('platform_api_keys').select('key_name, key_value').eq('is_active', True).execute()

            if result.data:
                api_keys = {}
                for row in result.data:
                    key_name = row.get('key_name')
                    key_value = row.get('key_value')
                    if key_name and key_value:
                        api_keys[key_name] = key_value
                logger.info(f"Loaded {len(api_keys)} keys from platform_api_keys table")
                return api_keys
            else:
                logger.warning("No platform API keys found in platform_api_keys table")
                return {}
        except Exception as e:
            logger.error(f"Failed to load platform keys: {e}")
            return {}

    @staticmethod
    def _load_from_supabase(client_id: str) -> Dict[str, str]:
        """Load API keys from Supabase for a specific client (BYOK mode)"""
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
                # Video/Media providers
                'descript_api_key',
                # SEO/Marketing providers
                'semrush_api_key',
                'ahrefs_api_key',
            ]
            
            # Use SELECT * so we don't fail when newer optional columns
            # (e.g. semrush_api_key, ahrefs_api_key) haven't been migrated to
            # this client's database yet. We then only read keys we recognise.
            try:
                result = supabase.table('clients').select('*').eq('id', client_id).single().execute()
            except Exception as select_err:
                # Fallback: try the explicit column list. If THAT also fails on
                # a missing column, retry with only the legacy/core key set.
                err_str = str(select_err)
                if 'does not exist' in err_str and '42703' in err_str:
                    legacy_columns = [c for c in api_key_columns if c not in ('semrush_api_key', 'ahrefs_api_key', 'descript_api_key', 'speechify_api_key')]
                    columns_str = ', '.join(legacy_columns)
                    result = supabase.table('clients').select(columns_str).eq('id', client_id).single().execute()
                else:
                    raise
            
            if result.data:
                # Convert database columns to api_keys dict
                api_keys = {}
                for key in api_key_columns:
                    value = result.data.get(key)
                    if value and value != '<needs-actual-key>':
                        api_keys[key] = value
                
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
    def validate_api_key(key: str, value: str) -> bool:
        """Validate that an API key looks real (not a test key)"""
        if not value:
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
