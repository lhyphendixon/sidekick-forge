#!/usr/bin/env python3
"""
Final test of text chat storage with UUID conversation ID
"""

import asyncio
import httpx
import json
import uuid
from datetime import datetime, UTC

API_BASE_URL = "http://localhost:8000"

async def test_text_chat_storage():
    """Test text chat with UUID conversation ID for storage verification"""
    unique_conv_id = str(uuid.uuid4())  # Proper UUID
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    print(f"🧪 Testing Text Chat Storage")
    print(f"   Conversation ID: {unique_conv_id}")
    print(f"   Timestamp: {timestamp}")
    
    payload = {
        "message": f"Storage test at {timestamp}. Please confirm you received this message.",
        "agent_slug": "clarence-coherence",
        "session_id": f"test_session_{uuid.uuid4().hex[:8]}",
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
                print(f"\n🔍 Check Supabase conversation_transcripts table for:")
                print(f"   conversation_id = '{unique_conv_id}'")
                print(f"   Expected: 2 rows")
                print(f"   - role='user' with your test message")
                print(f"   - role='assistant' with the agent's response")
            else:
                print("❌ FAILED: Got error response")
                print(f"Response: {json.dumps(result, indent=2)}")
        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Error: {response.text}")

if __name__ == "__main__":
    asyncio.run(test_text_chat_storage())