#!/usr/bin/env python3
"""
Test client configuration update
"""
import asyncio
import sys
sys.path.append('/root/sidekick-forge')

from app.core.dependencies import get_client_service
from app.models.client import ClientUpdate, ClientSettings, LiveKitConfig, APIKeys

async def test_update_client(client_id: str, new_api_key: str, new_api_secret: str):
    """Test updating client configuration"""
    print(f"\nTesting client configuration update")
    print("=" * 60)
    
    # Get client service
    client_service = get_client_service()
    
    # Get current client
    client = await client_service.get_client(client_id)
    if not client:
        print(f"❌ Client {client_id} not found!")
        return
    
    print(f"✅ Found client: {client.name}")
    
    # Show current LiveKit config
    if client.settings and hasattr(client.settings, 'livekit'):
        current_lk = client.settings.livekit
        print(f"\nCurrent LiveKit config:")
        print(f"  URL: {current_lk.server_url if hasattr(current_lk, 'server_url') else 'Not set'}")
        print(f"  API Key: {current_lk.api_key if hasattr(current_lk, 'api_key') else 'Not set'}")
    
    # Create update
    print(f"\nUpdating LiveKit credentials...")
    update_data = ClientUpdate(
        settings=ClientSettings(
            livekit=LiveKitConfig(
                server_url="wss://litebridge-hw6srhvi.livekit.cloud",
                api_key=new_api_key,
                api_secret=new_api_secret
            )
        )
    )
    
    try:
        # Update client
        updated_client = await client_service.update_client(client_id, update_data)
        
        if updated_client:
            print(f"✅ Client updated successfully!")
            
            # Verify the update
            verify_client = await client_service.get_client(client_id)
            if verify_client and verify_client.settings and hasattr(verify_client.settings, 'livekit'):
                new_lk = verify_client.settings.livekit
                print(f"\nNew LiveKit config:")
                print(f"  URL: {new_lk.server_url if hasattr(new_lk, 'server_url') else 'Not set'}")
                print(f"  API Key: {new_lk.api_key if hasattr(new_lk, 'api_key') else 'Not set'}")
                
                if new_lk.api_key == new_api_key:
                    print(f"\n✅ Configuration saved correctly!")
                else:
                    print(f"\n❌ Configuration not saved properly!")
                    print(f"   Expected: {new_api_key}")
                    print(f"   Got: {new_lk.api_key}")
        else:
            print(f"❌ Update returned None")
            
    except Exception as e:
        print(f"❌ Error updating client: {e}")
        import traceback
        traceback.print_exc()

async def main():
    client_id = "df91fd06-816f-4273-a903-5a4861277040"
    
    # Test with new credentials
    new_api_key = "TEST_NEW_KEY_123"
    new_api_secret = "TEST_NEW_SECRET_456"
    
    await test_update_client(client_id, new_api_key, new_api_secret)

if __name__ == "__main__":
    asyncio.run(main())