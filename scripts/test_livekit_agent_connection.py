#!/usr/bin/env python3
"""
Test LiveKit agent connection specifically
"""
import os
import asyncio
import aiohttp
from livekit import api
import base64
import json

async def test_agent_connection(url, api_key, api_secret):
    """Test agent WebSocket connection"""
    print(f"\nTesting LiveKit Agent Connection")
    print("=" * 60)
    print(f"URL: {url}")
    print(f"API Key: {api_key}")
    print(f"API Secret: {'*' * 20}")
    print()
    
    # Create agent token
    token = api.AccessToken(api_key, api_secret)
    token.with_identity("test-agent-worker")
    token.with_grants(api.VideoGrants(
        room_join=True,
        room="*",
        can_publish=True,
        can_subscribe=True,
        room_admin=True
    ))
    
    jwt_token = token.to_jwt()
    
    # Parse token to see claims
    print("Token Claims:")
    try:
        # JWT tokens have 3 parts separated by dots
        parts = jwt_token.split('.')
        if len(parts) >= 2:
            # Decode the payload (second part)
            payload = parts[1]
            # Add padding if needed
            payload += '=' * (4 - len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload)
            claims = json.loads(decoded)
            print(json.dumps(claims, indent=2))
    except Exception as e:
        print(f"Could not decode token: {e}")
    
    print("\nTesting WebSocket connections:")
    
    # Test 1: Regular participant WebSocket
    print("\n1. Testing participant WebSocket connection...")
    participant_url = f"{url}/rtc?access_token={jwt_token}"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.ws_connect(participant_url) as ws:
                print("✅ Participant WebSocket connected")
                await ws.close()
        except Exception as e:
            print(f"❌ Participant WebSocket failed: {e}")
    
    # Test 2: Agent WebSocket (this is what's failing)
    print("\n2. Testing agent WebSocket connection...")
    agent_url = f"{url}/agent"
    headers = {
        "Authorization": f"Bearer {api_key}:{api_secret}"
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.ws_connect(agent_url, headers=headers) as ws:
                print("✅ Agent WebSocket connected!")
                await ws.close()
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                print("❌ Agent WebSocket failed: 401 Unauthorized")
                print("   This means the credentials are not authorized for agent connections")
                print("   Possible reasons:")
                print("   - These are participant-only credentials")
                print("   - Agent feature not enabled for this API key")
                print("   - Wrong credential format for agent auth")
            else:
                print(f"❌ Agent WebSocket failed: {e}")
        except Exception as e:
            print(f"❌ Agent WebSocket failed: {e}")
    
    # Test 3: Try basic auth format
    print("\n3. Testing agent WebSocket with basic auth...")
    auth = aiohttp.BasicAuth(api_key, api_secret)
    
    async with aiohttp.ClientSession(auth=auth) as session:
        try:
            async with session.ws_connect(agent_url) as ws:
                print("✅ Agent WebSocket connected with basic auth!")
                await ws.close()
        except Exception as e:
            print(f"❌ Agent WebSocket with basic auth failed: {e}")

async def main():
    url = os.getenv("LIVEKIT_URL", "wss://litebridge-hw6srhvi.livekit.cloud")
    api_key = os.getenv("LIVEKIT_API_KEY", "APIUtuiQ47BQBsk")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM")
    
    await test_agent_connection(url, api_key, api_secret)
    
    print("\n" + "=" * 60)
    print("CONCLUSION:")
    if api_key == "APIUtuiQ47BQBsk":
        print("❌ You are still using the expired test credentials")
        print("   These credentials cannot be used for agent connections")
        print("   Please provide the new LiveKit credentials")
    else:
        print("✅ Using custom credentials")

if __name__ == "__main__":
    asyncio.run(main())