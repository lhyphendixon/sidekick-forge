#!/usr/bin/env python3
"""
Final test of text chat storage with all required fields
"""

import asyncio
import httpx
import json
import uuid
from datetime import datetime, UTC

API_BASE_URL = "http://localhost:8000"

async def test_text_chat_storage():
    """Test text chat with all required fields for storage verification"""
    unique_conv_id = str(uuid.uuid4())  # Proper UUID
    unique_session_id = f"session_{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    print(f"🧪 Testing Text Chat Storage - Final Test")
    print(f"   Conversation ID: {unique_conv_id}")
    print(f"   Session ID: {unique_session_id}")
    print(f"   Timestamp: {timestamp}")
    
    payload = {
        "message": f"Final storage test at {timestamp}. This message should be stored in the database.",
        "agent_slug": "clarence-coherence",
        "session_id": unique_session_id,
        "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",  # Valid UUID
        "conversation_id": unique_conv_id,
        "mode": "text",
        "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE_URL}/api/v1/trigger-agent",
            json=payload,
            timeout=30.0
        )
        
        print(f"\nResponse status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            data = result.get("data", {})
            if data.get("response") and "error" not in data.get("response", "").lower():
                print("✅ SUCCESS: Text chat is working!")
                print(f"\n📝 Agent response: {data.get('response', 'No response')[:200]}...")
                print(f"\n🔍 CHECK SUPABASE DATABASE:")
                print(f"   Table: conversation_transcripts")
                print(f"   conversation_id = '{unique_conv_id}'")
                print(f"   session_id = '{unique_session_id}'")
                print(f"\n   Expected: 2 rows")
                print(f"   1. role='user' with the test message")
                print(f"   2. role='assistant' with the agent's response")
                print(f"\n   Both rows should have:")
                print(f"   - user_id = '351bb07b-03fc-4fb4-b09b-748ef8a72084'")
                print(f"   - agent_id = '{data.get('agent_info', {}).get('id', 'check logs')}'")
            else:
                print("❌ FAILED: Got error response")
                print(f"Response: {json.dumps(result, indent=2)}")
        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Error: {response.text}")

if __name__ == "__main__":
    asyncio.run(test_text_chat_storage())