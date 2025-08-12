#!/usr/bin/env python3
"""
Test voice chat functionality after fixing relevance field mapping
"""

import asyncio
import httpx
import json
from datetime import datetime, UTC

API_BASE_URL = "http://localhost:8000"

async def test_voice_fix():
    """Test voice chat after fixing the relevance field mapping"""
    
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    print(f"🎤 VOICE CHAT FIX VERIFICATION TEST")
    print(f"=" * 60)
    print(f"Timestamp: {timestamp}")
    print(f"=" * 60)
    
    # Test client and agent configuration
    client_id = "11389177-e4d8-49a9-9a00-f77bb4de6592"  # Coherence client
    agent_slug = "clarence-coherence"
    
    async with httpx.AsyncClient() as client:
        # First, test text chat to verify the fix works there too
        print("\n📝 Testing text chat first...")
        text_payload = {
            "message": "Hello! Can you tell me about machine learning?",
            "agent_slug": agent_slug,
            "session_id": f"fix_test_{timestamp}",
            "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
            "mode": "text",
            "client_id": client_id
        }
        
        try:
            response = await client.post(
                f"{API_BASE_URL}/api/v1/trigger-agent",
                json=text_payload,
                timeout=30.0
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("data", {}).get("response"):
                    print("✅ Text chat works - RAG context building successful")
                    print(f"   Response preview: {result['data']['response'][:100]}...")
                else:
                    print("❌ Text chat failed - check logs for errors")
            else:
                print(f"❌ Text chat request failed: {response.status_code}")
        except Exception as e:
            print(f"❌ Text chat error: {e}")
        
        # Now test voice chat setup
        print("\n🎤 Testing voice chat setup...")
        voice_payload = {
            "mode": "voice",
            "agent_slug": agent_slug,
            "room_name": f"test_voice_fix_{int(datetime.now().timestamp())}",
            "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
            "client_id": client_id
        }
        
        try:
            response = await client.post(
                f"{API_BASE_URL}/api/v1/trigger-agent",
                json=voice_payload,
                timeout=30.0
            )
            
            if response.status_code == 200:
                result = response.json()
                data = result.get("data", {})
                if data.get("room_name") and data.get("dispatch_info", {}).get("status") == "dispatched":
                    print("✅ Voice chat room created successfully")
                    print(f"   Room: {data['room_name']}")
                    print(f"   Dispatch: {data['dispatch_info']['dispatch_id']}")
                    print(f"   Token: {'Provided' if data.get('livekit_config', {}).get('user_token') else 'Missing'}")
                    print(f"   Agent should now be able to process speech with RAG context")
                    print("\n📊 Next Steps:")
                    print("   1. Connect to the voice room")
                    print("   2. Speak a message that requires RAG context")
                    print("   3. Agent should respond without KeyError")
                else:
                    print("❌ Voice chat setup incomplete")
                    print(f"   Response: {json.dumps(result, indent=2)}")
            else:
                print(f"❌ Voice chat request failed: {response.status_code}")
                print(f"   Error: {response.text}")
        except Exception as e:
            print(f"❌ Voice chat error: {e}")
    
    print(f"\n📊 VERIFICATION SUMMARY:")
    print(f"=" * 60)
    print(f"1. Check agent worker logs for KeyError - should NOT appear")
    print(f"2. Check for successful RAG context building logs")
    print(f"3. Agent should respond to speech that triggers RAG queries")
    print(f"=" * 60)

if __name__ == "__main__":
    asyncio.run(test_voice_fix())