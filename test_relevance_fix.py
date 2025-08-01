#!/usr/bin/env python3
"""
Test that the relevance field fix works correctly
"""

import asyncio
import httpx
import json
import uuid
from datetime import datetime, UTC

API_BASE_URL = "http://localhost:8000"

async def test_relevance_fix():
    """Test that RAG context building works without KeyError"""
    conversation_id = str(uuid.uuid4())
    session_id = f"relevance_test_{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    print(f"🧪 RELEVANCE FIELD FIX TEST")
    print(f"=" * 60)
    print(f"Conversation ID: {conversation_id}")
    print(f"Session ID: {session_id}")
    print(f"Timestamp: {timestamp}")
    print(f"=" * 60)
    
    payload = {
        "message": "Tell me about machine learning algorithms and their applications.",
        "agent_slug": "clarence-coherence",
        "session_id": session_id,
        "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
        "conversation_id": conversation_id,
        "mode": "text",
        "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592"
    }
    
    async with httpx.AsyncClient() as client:
        print("\n📤 Sending test message...")
        response = await client.post(
            f"{API_BASE_URL}/api/v1/trigger-agent",
            json=payload,
            timeout=30.0
        )
        
        print(f"📥 Response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            data = result.get("data", {})
            
            if data.get("response") and "error" not in data.get("response", "").lower():
                print("✅ SUCCESS: RAG context building completed without errors!")
                print(f"\n🤖 Agent response: {data.get('response', '')[:200]}...")
                print("\n✅ The relevance field fix is working correctly!")
            else:
                print("❌ FAILED: Got error response")
                print(f"Response: {json.dumps(result, indent=2)}")
        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Error: {response.text}")
        
        print(f"\n📊 VERIFICATION:")
        print(f"=" * 60)
        print(f"Check FastAPI logs - should NOT see 'KeyError: relevance'")
        print(f"Should see successful RAG context building logs")
        print(f"=" * 60)

if __name__ == "__main__":
    asyncio.run(test_relevance_fix())