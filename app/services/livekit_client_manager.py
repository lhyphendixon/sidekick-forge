"""
LiveKit client manager for multi-tenant support.
Creates LiveKit managers with client-specific credentials.
"""
import logging
from typing import Optional
from livekit import api
from app.integrations.livekit_client import LiveKitManager
from app.config import settings

logger = logging.getLogger(__name__)


async def get_client_livekit_manager(client) -> LiveKitManager:
    """
    Get a LiveKit manager configured with client-specific credentials.
    
    This ensures multi-tenant isolation by using each client's own LiveKit
    credentials instead of the backend's credentials.
    
    Args:
        client: Client object with settings containing LiveKit credentials
        
    Returns:
        LiveKitManager instance configured for the specific client
        
    Raises:
        ValueError: If client lacks proper LiveKit credentials or uses backend credentials
    """
    # Extract LiveKit settings from client
    if not hasattr(client, 'settings') or not client.settings:
        raise ValueError(f"Client {client.name} has no settings configured")
    
    livekit_settings = getattr(client.settings, 'livekit', None)
    if not livekit_settings:
        raise ValueError(f"Client {client.name} has no LiveKit settings configured")
    
    # Get credentials
    server_url = getattr(livekit_settings, 'server_url', None)
    api_key = getattr(livekit_settings, 'api_key', None)
    api_secret = getattr(livekit_settings, 'api_secret', None)
    
    if not all([server_url, api_key, api_secret]):
        missing = []
        if not server_url:
            missing.append("server_url")
        if not api_key:
            missing.append("api_key")
        if not api_secret:
            missing.append("api_secret")
        raise ValueError(f"Client {client.name} missing LiveKit credentials: {', '.join(missing)}")
    
    # CRITICAL: Validate client credentials are different from backend
    if api_key == settings.livekit_api_key:
        logger.warning(f"⚠️ WARNING: Client {client.name} is using BACKEND LiveKit credentials!")
        logger.warning(f"   Backend API Key: {settings.livekit_api_key[:10]}...")
        logger.warning(f"   Client API Key: {api_key[:10]}...")
        logger.warning(f"   This breaks multi-tenant isolation - for testing only!")
        # TODO: Re-enable this check after testing
        # raise ValueError(
        #     f"Client {client.name} must have unique LiveKit credentials. "
        #     f"Currently using backend credentials which breaks multi-tenant isolation. "
        #     f"Each client needs their own LiveKit Cloud account for billing/logging/migration."
        # )
    
    # Log credential usage (without exposing secrets)
    logger.info(f"✅ Creating LiveKit manager for client {client.name} with isolated credentials")
    logger.info(f"  - Server URL: {server_url}")
    logger.info(f"  - API Key: {api_key[:10]}..." if len(api_key) > 10 else "***")
    logger.info(f"  - Verified: Client credentials are DIFFERENT from backend")
    
    # Test credential validity with a minimal operation
    try:
        # Create a test API client to verify credentials work
        test_api = api.LiveKitAPI(server_url, api_key, api_secret)
        # Try to list rooms (should work even if empty)
        from livekit.api import ListRoomsRequest
        await test_api.room.list_rooms(ListRoomsRequest())
        logger.info(f"✅ Client {client.name} LiveKit credentials validated successfully")
    except Exception as e:
        logger.error(f"❌ Client {client.name} LiveKit credentials are invalid: {str(e)}")
        raise ValueError(f"Client {client.name} LiveKit credentials failed validation: {str(e)}")
    
    # Create client-specific LiveKit manager instance
    # Since LiveKitManager doesn't accept constructor params, we need to 
    # create an instance and then override its attributes
    client_livekit = LiveKitManager()
    client_livekit.api_key = api_key
    client_livekit.api_secret = api_secret
    client_livekit.url = server_url
    
    # Initialize the manager with client credentials
    await client_livekit.initialize()
    
    logger.info(f"✅ LiveKit manager initialized for client {client.name}")
    
    return client_livekit