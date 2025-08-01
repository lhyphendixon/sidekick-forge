#!/usr/bin/env python3
"""
Test text chat storage with unique conversation ID
"""

import asyncio
import httpx
import json
import uuid
from datetime import datetime

API_BASE_URL = "http://localhost:8000"

async def test_text_chat_storage():
    """Test text chat with unique conversation ID for storage verification"""
    unique_conv_id = f"test_storage_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    print(f"🧪 Testing Text Chat Storage with conversation_id: {unique_conv_id}")
    
    payload = {
        "message": "Hello! This is a test message for storage verification.",
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
        
        print(f"Response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            data = result.get("data", {})
            if data.get("response") and "error" not in data.get("response", "").lower():
                print("✅ SUCCESS: Text chat is working!")
                print(f"Agent response: {data.get('response', 'No response')[:150]}...")
                print(f"\n📝 Check Supabase for conversation_id: {unique_conv_id}")
                print("   Look for 2 rows in conversation_transcripts table:")
                print("   - One with role='user' and your message")
                print("   - One with role='assistant' and the agent's response")
            else:
                print("❌ FAILED: Got error response")
                print(f"Response: {json.dumps(result, indent=2)}")
        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Error: {response.text}")

if __name__ == "__main__":
    asyncio.run(test_text_chat_storage())