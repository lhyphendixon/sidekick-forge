"""
Platform Credential Sync Service
Ensures platform has valid LiveKit credentials for platform operations.
Client-specific LiveKit credentials are loaded dynamically per request.
"""
import os
import logging
import asyncio
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

class PlatformCredentialSync:
    """Manages platform LiveKit credentials (not client credentials)"""
    
    # Platform credentials are stored in .env, not loaded from clients
    ENV_FILE_PATH = "/root/sidekick-forge/.env"
    
    @classmethod
    async def verify_platform_credentials(cls) -> bool:
        """Verify platform has valid LiveKit credentials for platform operations"""
        try:
            # Get platform LiveKit credentials from environment
            url = os.getenv('LIVEKIT_URL')
            api_key = os.getenv('LIVEKIT_API_KEY')
            api_secret = os.getenv('LIVEKIT_API_SECRET')
            
            if not all([url, api_key, api_secret]):
                logger.error("Platform LiveKit credentials not configured in environment")
                return False
            
            # Check if credentials are the known invalid test credentials
            if api_key == "APIUtuiQ47BQBsk":
                logger.warning(f"Platform has expired LiveKit credentials: {api_key}")
                logger.warning("Please update LIVEKIT_API_KEY in .env with valid platform credentials")
                return False
            
            # Verify credentials are valid by testing connection
            from livekit import api
            try:
                livekit_api = api.LiveKitAPI(url, api_key, api_secret)
                # Try to list rooms as a test
                await livekit_api.room.list_rooms(api.ListRoomsRequest())
                logger.info(f"✅ Platform LiveKit credentials are valid")
                logger.info(f"   URL: {url}")
                logger.info(f"   API Key: {api_key[:8]}...{api_key[-4:]}")
                return True
            except Exception as e:
                logger.error(f"Platform LiveKit credentials are invalid: {e}")
                return False
                
            # Note: Platform credentials should be updated manually in .env
            # We don't auto-update them from any client
            
        except Exception as e:
            logger.error(f"Failed to sync LiveKit credentials: {e}")
            return False
    
    
    
    @classmethod
    async def get_client_livekit_credentials(cls, client_id: str) -> Optional[dict]:
        """Get LiveKit credentials for a specific client from platform database"""
        try:
            from app.core.dependencies import get_client_service
            client_service = get_client_service()
            
            # Get client from platform database
            client = await client_service.get_client(client_id)
            if not client:
                logger.error(f"Client {client_id} not found in platform database")
                return None
            
            # Extract LiveKit credentials if available
            if hasattr(client, 'livekit_url') and client.livekit_url:
                return {
                    "url": client.livekit_url,
                    "api_key": client.livekit_api_key,
                    "api_secret": client.livekit_api_secret
                }
            elif client.settings and hasattr(client.settings, 'livekit'):
                livekit_config = client.settings.livekit
                return {
                    "url": getattr(livekit_config, 'server_url', None),
                    "api_key": getattr(livekit_config, 'api_key', None),
                    "api_secret": getattr(livekit_config, 'api_secret', None)
                }
            else:
                logger.info(f"Client {client_id} has no LiveKit configuration, will use platform credentials")
                return None
                
        except Exception as e:
            logger.error(f"Failed to get client LiveKit credentials: {e}")
            return None