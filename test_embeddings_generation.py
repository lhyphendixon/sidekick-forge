#!/usr/bin/env python3
"""
Test that embeddings are generated for conversation transcripts
"""

import asyncio
import httpx
import json
import uuid
from datetime import datetime, UTC

API_BASE_URL = "http://localhost:8000"

async def test_embeddings_generation():
    """Test conversation with embedding generation"""
    conversation_id = str(uuid.uuid4())
    session_id = f"embed_test_{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    print(f"🧪 EMBEDDINGS GENERATION TEST")
    print(f"=" * 60)
    print(f"Conversation ID: {conversation_id}")
    print(f"Session ID: {session_id}")
    print(f"Timestamp: {timestamp}")
    print(f"=" * 60)
    
    # Test with a meaningful message (not trivial)
    payload = {
        "message": f"Can you explain how machine learning works and what are the main types of algorithms used in practice?",
        "agent_slug": "clarence-coherence",
        "session_id": session_id,
        "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
        "conversation_id": conversation_id,
        "mode": "text",
        "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592"
    }
    
    async with httpx.AsyncClient() as client:
        print("\n📤 Sending meaningful message (should generate embeddings)...")
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
                print("✅ Text chat successful!")
                print(f"\n🤖 Agent response: {data.get('response', '')[:100]}...")
                
                print(f"\n📊 EMBEDDING VERIFICATION:")
                print(f"=" * 60)
                print(f"Check in Supabase conversation_transcripts table:")
                print(f"\nSELECT id, role, content, embeddings IS NOT NULL as has_embedding")
                print(f"FROM conversation_transcripts")
                print(f"WHERE conversation_id = '{conversation_id}'")
                print(f"ORDER BY created_at;")
                print(f"\nExpected: 2 rows, both with has_embedding = true")
                print(f"=" * 60)
                
                # Wait a moment for async embedding generation
                await asyncio.sleep(2)
                
                # Test with trivial message (should NOT generate embeddings)
                print(f"\n\n🧪 Testing trivial message (should skip embeddings)...")
                trivial_payload = {
                    **payload,
                    "message": "hi",
                    "conversation_id": str(uuid.uuid4())  # New conversation
                }
                
                response2 = await client.post(
                    f"{API_BASE_URL}/api/v1/trigger-agent",
                    json=trivial_payload,
                    timeout=30.0
                )
                
                if response2.status_code == 200:
                    print("✅ Trivial message processed")
                    print(f"Check conversation_id '{trivial_payload['conversation_id']}' - should have has_embedding = false")
                
            else:
                print("❌ FAILED: Got error response")
                print(f"Response: {json.dumps(result, indent=2)}")
        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Error: {response.text}")

if __name__ == "__main__":
    asyncio.run(test_embeddings_generation())