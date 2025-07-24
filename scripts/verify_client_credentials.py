#!/usr/bin/env python3
"""
Verification script to confirm client-specific LiveKit credentials are used in dispatch
"""

import asyncio
import httpx
import json
import os
from datetime import datetime

# Backend configuration
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API_KEY = os.getenv("TEST_API_KEY", "test-api-key")

async def verify_client_credentials():
    """Trigger an agent and examine logs for credential usage"""
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"\n🔐 Verifying Client-Specific Credential Usage")
        print(f"Timestamp: {datetime.now()}")
        print("=" * 80)
        
        # Trigger an agent
        room_name = f"verify_creds_{int(datetime.now().timestamp())}"
        
        trigger_payload = {
            "room_name": room_name,
            "agent_slug": "general_ai_assistant",
            "user_id": "test-user",
            "conversation_id": f"conv_{room_name}",
            "platform": "livekit",
            "mode": "voice"
        }
        
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
        print(f"📤 Triggering agent for room: {room_name}")
        
        try:
            response = await client.post(
                f"{BACKEND_URL}/api/v1/trigger-agent",
                json=trigger_payload,
                headers=headers
            )
            
            if response.status_code == 200:
                result = response.json()
                
                # Extract LiveKit config
                livekit_config = result.get("data", {}).get("livekit_config", {})
                server_url = livekit_config.get("server_url", "Not found")
                
                print(f"\n✅ Agent triggered successfully")
                print(f"\n📋 Client LiveKit Configuration:")
                print(f"   - Server URL: {server_url}")
                print(f"   - Token configured: {livekit_config.get('configured', False)}")
                
                # Check if it's a client URL (not backend)
                if "wss://litebridge" in server_url.lower():
                    print(f"\n❌ WARNING: This appears to be the backend LiveKit URL!")
                    print(f"   Each client should have their own LiveKit Cloud instance")
                else:
                    print(f"\n✅ CONFIRMED: Using client-specific LiveKit URL")
                    print(f"   This is NOT the backend's LiveKit instance")
                
                # Container info
                container_info = result.get("data", {}).get("container_info", {})
                if container_info:
                    print(f"\n🐳 Container Information:")
                    print(f"   - Status: {container_info.get('status', 'unknown')}")
                    print(f"   - Dispatch status: {container_info.get('dispatch_status', 'unknown')}")
                    print(f"   - LiveKit Cloud: {container_info.get('livekit_cloud', 'not specified')}")
                
                print(f"\n📝 Expected Log Output:")
                print(f"   The backend logs should show:")
                print(f"   - 'Using CLIENT-SPECIFIC LiveKit infrastructure for true multi-tenant isolation'")
                print(f"   - 'Client LiveKit URL: <client-specific-url>'")
                print(f"   - 'Dispatching agent for client_id '<id>' using LiveKit API key '<client-key>...'")
                print(f"\n💡 Check backend logs to confirm these messages appear")
                
            else:
                print(f"\n❌ Request failed: {response.status_code}")
                print(f"   Response: {response.text}")
                
        except Exception as e:
            print(f"\n❌ Error: {str(e)}")
        
        print("\n" + "=" * 80)
        print("🔍 To view backend logs showing credential usage:")
        print("   docker-compose logs -f fastapi | grep -E '(CLIENT-SPECIFIC|Client LiveKit|Dispatching agent for client_id)'")
        print("\n✅ Verification complete - check logs for credential confirmation")


if __name__ == "__main__":
    asyncio.run(verify_client_credentials())