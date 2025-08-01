"""
LiveKit Credential Management
Ensures we always use valid credentials, with fallback to client-specific ones
"""
import os
import logging
from typing import Optional, Dict, Tuple
from app.core.dependencies import get_client_service

logger = logging.getLogger(__name__)

class LiveKitCredentialManager:
    """Manages LiveKit credentials with proper fallbacks"""
    
    @staticmethod
    async def get_backend_credentials() -> Tuple[str, str, str]:
        """
        Get backend LiveKit credentials with validation
        Returns: (url, api_key, api_secret)
        """
        # First try environment variables
        url = os.getenv("LIVEKIT_URL")
        api_key = os.getenv("LIVEKIT_API_KEY")
        api_secret = os.getenv("LIVEKIT_API_SECRET")
        
        # Validate that we don't have the expired test credentials
        if api_key == "APIUtuiQ47BQBsk":
            logger.warning("Detected expired test LiveKit credentials, attempting to load from client config")
            url, api_key, api_secret = await LiveKitCredentialManager._load_from_autonomite_client()
        
        if not all([url, api_key, api_secret]):
            # Try to load from Autonomite client as fallback
            logger.warning("Missing LiveKit credentials in environment, loading from Autonomite client")
            url, api_key, api_secret = await LiveKitCredentialManager._load_from_autonomite_client()
        
        if not all([url, api_key, api_secret]):
            raise ValueError("No valid LiveKit credentials found in environment or database")
        
        logger.info(f"Using LiveKit URL: {url}")
        return url, api_key, api_secret
    
    @staticmethod
    async def _load_from_autonomite_client() -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Load LiveKit credentials from Autonomite client directly from Supabase"""
        try:
            # Import here to avoid circular dependency
            from app.integrations.supabase_client import SupabaseManager
            from app.config import settings
            
            # Initialize Supabase client
            supabase = SupabaseManager()
            
            # Get default client directly from database
            from app.utils.default_ids import get_default_client_id
            response = supabase.admin_client.table('clients').select('*').eq('id', get_default_client_id()).single().execute()
            
            if response.data:
                client_data = response.data
                settings_data = client_data.get('settings', {})
                
                # Handle both string and dict formats for settings
                if isinstance(settings_data, str):
                    import json
                    settings_data = json.loads(settings_data)
                
                livekit_settings = settings_data.get('livekit', {})
                
                url = livekit_settings.get('url')
                api_key = livekit_settings.get('api_key')
                api_secret = livekit_settings.get('api_secret')
                
                # Skip if these are the expired test credentials
                if api_key and api_key != "APIUtuiQ47BQBsk" and all([url, api_key, api_secret]):
                    logger.info("Successfully loaded LiveKit credentials from Autonomite client in Supabase")
                    # Also update environment variables for consistency
                    os.environ["LIVEKIT_URL"] = url
                    os.environ["LIVEKIT_API_KEY"] = api_key
                    os.environ["LIVEKIT_API_SECRET"] = api_secret
                    return url, api_key, api_secret
            
            logger.error("Failed to load valid LiveKit credentials from Autonomite client in Supabase")
            return None, None, None
            
        except Exception as e:
            logger.error(f"Error loading LiveKit credentials from Supabase: {e}")
            return None, None, None
    
    @staticmethod
    async def validate_credentials(url: str, api_key: str, api_secret: str) -> bool:
        """Validate LiveKit credentials by attempting to create a token"""
        try:
            from livekit import api
            token = api.AccessToken(api_key, api_secret)
            token.with_identity("validation-test")
            token.with_grants(api.VideoGrants(
                room_join=True,
                room="validation-test"
            ))
            jwt_token = token.to_jwt()
            return bool(jwt_token)
        except Exception as e:
            logger.error(f"LiveKit credential validation failed: {e}")
            return False