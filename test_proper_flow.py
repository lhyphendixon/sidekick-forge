#!/usr/bin/env python3
"""
Test script to demonstrate proper agent trigger flow through API endpoints
"""

import asyncio
import httpx
import json
from datetime import datetime

async def test_trigger_agent():
    """Test the trigger-agent endpoint"""
    
    # API endpoint
    base_url = "http://localhost:8000"
    
    # Test agent configuration
    test_request = {
        "agent_slug": "test-agent",
        "mode": "voice",
        "room_name": f"api-test-room-{int(datetime.now().timestamp())}",
        "user_id": "test-user-123",
        "client_id": "df91fd06-816f-4273-a903-5a4861277040"  # Example client ID
    }
    
    print("Testing agent trigger through proper API endpoint...")
    print(f"Request: {json.dumps(test_request, indent=2)}")
    
    async with httpx.AsyncClient() as client:
        try:
            # Call trigger-agent endpoint
            response = await client.post(
                f"{base_url}/api/v1/trigger-agent",
                json=test_request,
                headers={"Content-Type": "application/json"}
            )
            
            print(f"\nResponse status: {response.status_code}")
            print(f"Response body: {json.dumps(response.json(), indent=2)}")
            
            if response.status_code == 200:
                data = response.json()
                print("\n✅ Success! Room created with metadata")
                print(f"Room name: {data.get('data', {}).get('room_name')}")
                print(f"User token: {data.get('data', {}).get('livekit_config', {}).get('user_token', '')[:50]}...")
            else:
                print("\n❌ Failed to trigger agent")
                
        except Exception as e:
            print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    print("="*60)
    print("PROPER FLOW: Frontend -> API Endpoint -> Room with Metadata")
    print("="*60)
    asyncio.run(test_trigger_agent())