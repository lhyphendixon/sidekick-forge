#!/usr/bin/env python3
"""
Test LiveKit credentials
"""
import os
import sys
from livekit import api
import asyncio

async def test_credentials(url, api_key, api_secret):
    """Test LiveKit credentials by creating a token and checking API access"""
    print(f"\nTesting LiveKit credentials...")
    print(f"URL: {url}")
    print(f"API Key: {api_key[:20]}..." if len(api_key) > 20 else f"API Key: {api_key}")
    print("-" * 60)
    
    try:
        # Test 1: Create a token
        print("Test 1: Creating access token...")
        token = api.AccessToken(api_key, api_secret)
        token.with_identity("test-user")
        token.with_grants(api.VideoGrants(
            room_join=True,
            room="test-room"
        ))
        jwt_token = token.to_jwt()
        print("✅ Token created successfully")
        
        # Test 2: Try to use the API
        print("\nTest 2: Testing API access...")
        livekit_api = api.LiveKitAPI(url, api_key, api_secret)
        
        # List rooms (should work even if empty)
        try:
            rooms = await livekit_api.room.list_rooms(api.ListRoomsRequest())
            print(f"✅ API access successful - Found {len(rooms.rooms)} rooms")
        except Exception as e:
            if "401" in str(e) or "unauthorized" in str(e).lower():
                print("❌ API access FAILED - Invalid credentials (401 Unauthorized)")
                return False
            else:
                # Other errors might be OK (e.g., network issues)
                print(f"⚠️  API test inconclusive: {e}")
        
        # Test 3: Check if these are the known expired credentials
        if api_key == "APIUtuiQ47BQBsk":
            print("\n⚠️  WARNING: These are the expired test credentials!")
            print("   Even if they appear to work locally, they will fail in production.")
            
        return True
        
    except Exception as e:
        print(f"\n❌ Credential test FAILED: {e}")
        return False

async def main():
    # Get credentials from environment
    url = os.getenv("LIVEKIT_URL", "wss://litebridge-hw6srhvi.livekit.cloud")
    api_key = os.getenv("LIVEKIT_API_KEY", "")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "")
    
    if not all([url, api_key, api_secret]):
        print("❌ LiveKit credentials not found in environment")
        return
    
    result = await test_credentials(url, api_key, api_secret)
    
    if result:
        print("\n✅ LiveKit credentials appear to be valid")
    else:
        print("\n❌ LiveKit credentials are INVALID")

if __name__ == "__main__":
    asyncio.run(main())