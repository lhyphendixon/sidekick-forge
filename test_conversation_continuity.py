#!/usr/bin/env python3
"""
Test conversation continuity in admin preview
"""

import asyncio
import httpx
import json
import uuid
from datetime import datetime, UTC

API_BASE_URL = "http://localhost:8000"

async def test_conversation_continuity():
    """Test that conversation continuity is maintained across messages"""
    # Use the same session_id for all messages (simulating a preview session)
    session_id = f"preview_{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    print(f"🧪 CONVERSATION CONTINUITY TEST")
    print(f"=" * 60)
    print(f"Session ID: {session_id}")
    print(f"Timestamp: {timestamp}")
    print(f"=" * 60)
    
    async with httpx.AsyncClient() as client:
        # Send first message with location info
        print("\n📤 Message 1: Introducing location")
        payload1 = {
            "message": "Hi! I'm testing from San Francisco, California.",
            "agent_slug": "clarence-coherence",
            "session_id": session_id,
            "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
            "mode": "text",
            "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592"
        }
        
        # Note: Admin preview doesn't use conversation_id in the API call
        # It's generated internally
        response1 = await client.post(
            f"{API_BASE_URL}/api/v1/trigger-agent",
            json=payload1,
            timeout=30.0
        )
        
        if response1.status_code == 200:
            result1 = response1.json()
            print(f"✅ Response received")
            print(f"   Agent: {result1.get('data', {}).get('response', '')[:150]}...")
            
            # Wait a bit
            await asyncio.sleep(2)
            
            # Send second message asking about location
            print("\n📤 Message 2: Asking about remembered location")
            payload2 = {
                "message": "Where did I say I was from?",
                "agent_slug": "clarence-coherence", 
                "session_id": session_id,  # SAME session_id
                "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
                "mode": "text",
                "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592"
            }
            
            response2 = await client.post(
                f"{API_BASE_URL}/api/v1/trigger-agent",
                json=payload2,
                timeout=30.0
            )
            
            if response2.status_code == 200:
                result2 = response2.json()
                agent_response = result2.get('data', {}).get('response', '')
                print(f"✅ Response received")
                print(f"   Agent: {agent_response[:200]}...")
                
                # Check if location was remembered
                if "San Francisco" in agent_response or "California" in agent_response:
                    print("\n✅ SUCCESS: Agent remembered the location from previous message!")
                    print("   Conversation continuity is working correctly.")
                else:
                    print("\n⚠️  WARNING: Agent did not mention the location")
                    print("   Check if conversation_id is being maintained")
                    
                # Check conversation_id from response
                conv_id1 = result1.get('data', {}).get('conversation_id', 'N/A')
                conv_id2 = result2.get('data', {}).get('conversation_id', 'N/A') 
                print(f"\n📊 Conversation IDs:")
                print(f"   Message 1: {conv_id1}")
                print(f"   Message 2: {conv_id2}")
                print(f"   Same ID: {'✅ YES' if conv_id1 == conv_id2 else '❌ NO'}")
            else:
                print(f"❌ Second request failed: {response2.status_code}")
        else:
            print(f"❌ First request failed: {response1.status_code}")
        
        print(f"\n📊 VERIFICATION:")
        print(f"=" * 60)
        print(f"Check FastAPI logs for:")
        print(f"- Buffer memory loading messages")
        print(f"- Same conversation_id being used")
        print(f"- RAG context including previous messages")
        print(f"=" * 60)

if __name__ == "__main__":
    asyncio.run(test_conversation_continuity())