#!/usr/bin/env python3
"""Test LiveKit credential sync from Supabase"""
import asyncio
import sys
import os

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

async def test_sync():
    """Test LiveKit credential sync"""
    
    # Initialize app dependencies
    from app.integrations.supabase_client import supabase_manager
    await supabase_manager.initialize()
    
    # Get client service using dependency
    from app.core.dependencies import get_client_service
    from app.core.service_factory import ServiceFactory
    
    # Initialize service factory
    await ServiceFactory.initialize()
    client_service = ServiceFactory.get_client_service()
    
    # Get Autonomite client
    client_id = "df91fd06-816f-4273-a903-5a4861277040"
    client = await client_service.get_client(client_id)
    
    if not client:
        print(f"❌ Client {client_id} not found")
        return
    
    print(f"✅ Found client: {client.name}")
    
    # Check LiveKit settings
    if not client.settings or not hasattr(client.settings, 'livekit'):
        print("❌ No LiveKit settings found")
        return
    
    livekit = client.settings.livekit
    print(f"\n📡 LiveKit Configuration in Supabase:")
    print(f"   URL: {livekit.server_url}")
    print(f"   API Key: {livekit.api_key}")
    print(f"   API Secret: {livekit.api_secret[:10]}...{livekit.api_secret[-10:]}")
    
    # Check current env
    print(f"\n🔧 Current Environment:")
    print(f"   URL: {os.getenv('LIVEKIT_URL', 'Not set')}")
    print(f"   API Key: {os.getenv('LIVEKIT_API_KEY', 'Not set')}")
    print(f"   API Secret: {os.getenv('LIVEKIT_API_SECRET', 'Not set')[:10]}...{os.getenv('LIVEKIT_API_SECRET', 'Not set')[-10:]}")
    
    # Run sync
    from app.services.backend_livekit_sync import BackendLiveKitSync
    print("\n🔄 Running sync...")
    result = await BackendLiveKitSync.sync_credentials()
    
    if result:
        print("✅ Sync successful!")
        print(f"\n📡 Updated Environment:")
        print(f"   URL: {os.getenv('LIVEKIT_URL')}")
        print(f"   API Key: {os.getenv('LIVEKIT_API_KEY')}")
        print(f"   API Secret: {os.getenv('LIVEKIT_API_SECRET')[:10]}...{os.getenv('LIVEKIT_API_SECRET')[-10:]}")
    else:
        print("❌ Sync failed!")

if __name__ == "__main__":
    asyncio.run(test_sync())