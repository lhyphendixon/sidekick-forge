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
        Load API keys with fallback strategy:
        1. From metadata (if provided)
        2. From environment variables
        3. From Supabase (if client_id is available)
        """
        api_keys = {}
        
        # First try metadata
        if metadata.get('api_keys'):
            logger.info("Loading API keys from metadata")
            api_keys = metadata['api_keys']
            
        # Fill in missing keys from environment
        env_keys = {
            # LLM Providers
            'openai_api_key': os.getenv('OPENAI_API_KEY', ''),
            'groq_api_key': os.getenv('GROQ_API_KEY', ''),
            'deepinfra_api_key': os.getenv('DEEPINFRA_API_KEY', ''),
            'replicate_api_key': os.getenv('REPLICATE_API_KEY', ''),
            'anthropic_api_key': os.getenv('ANTHROPIC_API_KEY', ''),
            # Voice/Speech Providers
            'deepgram_api_key': os.getenv('DEEPGRAM_API_KEY', ''),
            'elevenlabs_api_key': os.getenv('ELEVENLABS_API_KEY', ''),
            'cartesia_api_key': os.getenv('CARTESIA_API_KEY', ''),
            'speechify_api_key': os.getenv('SPEECHIFY_API_KEY', ''),
            # Embedding/Reranking Providers
            'novita_api_key': os.getenv('NOVITA_API_KEY', ''),
            'cohere_api_key': os.getenv('COHERE_API_KEY', ''),
            'siliconflow_api_key': os.getenv('SILICONFLOW_API_KEY', ''),
            'jina_api_key': os.getenv('JINA_API_KEY', ''),
        }
        
        for key, value in env_keys.items():
            if not api_keys.get(key) and value:
                logger.info(f"Using {key} from environment")
                api_keys[key] = value
                
        # Try to load from Supabase if we have client_id
        client_id = metadata.get('client_id')
        if client_id and any(not v for v in api_keys.values()):
            logger.info(f"Attempting to load API keys from Supabase for client {client_id}")
            supabase_keys = APIKeyLoader._load_from_supabase(client_id)
            for key, value in supabase_keys.items():
                if not api_keys.get(key) and value:
                    logger.info(f"Using {key} from Supabase")
                    api_keys[key] = value
                    
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
    def _load_from_supabase(client_id: str) -> Dict[str, str]:
        """Load API keys from Supabase for a specific client"""
        try:
            # Import here to avoid circular dependencies
            from supabase import create_client, Client
            
            supabase_url = os.getenv('SUPABASE_URL')
            supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
            
            if not supabase_url or not supabase_key:
                logger.warning("Supabase credentials not available")
                return {}
                
            # Create Supabase client
            supabase: Client = create_client(supabase_url, supabase_key)
            
            # Try to get client settings
            result = supabase.table('client_settings').select('api_keys').eq('id', client_id).single().execute()
            
            if result.data and result.data.get('api_keys'):
                api_keys = result.data['api_keys']
                logger.info(f"Loaded API keys from Supabase for client {client_id}")
                return api_keys
            else:
                logger.warning(f"No API keys found in Supabase for client {client_id}")
                return {}
                
        except Exception as e:
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