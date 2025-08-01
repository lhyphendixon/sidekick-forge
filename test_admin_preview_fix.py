#!/usr/bin/env python3
"""
Test admin preview storage fix - simulating admin preview behavior
"""

import asyncio
import httpx
import json
import uuid
from datetime import datetime, UTC

API_BASE_URL = "http://localhost:8000"

async def test_admin_preview_simulation():
    """Simulate admin preview with proper UUID conversation_id"""
    # Simulate admin preview session_id format
    session_id = f"preview_{uuid.uuid4().hex[:8]}"
    
    # This is what the admin preview NOW does - generates proper UUID
    conversation_id = str(uuid.uuid4())
    
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    print(f"🧪 ADMIN PREVIEW FIX TEST")
    print(f"=" * 60)
    print(f"Session ID (for UI display): {session_id}")
    print(f"Conversation ID (for database): {conversation_id}")
    print(f"Timestamp: {timestamp}")
    print(f"=" * 60)
    
    payload = {
        "message": f"Admin preview test at {timestamp}. Testing UUID fix for storage.",
        "agent_slug": "clarence-coherence",
        "session_id": session_id,  # Preview format for UI
        "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
        "conversation_id": conversation_id,  # Proper UUID for database
        "mode": "text",
        "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592"
    }
    
    async with httpx.AsyncClient() as client:
        print("\n📤 Sending request (simulating admin preview)...")
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
                print("✅ SUCCESS: Text chat working!")
                print(f"\n🤖 Agent response: {data.get('response', 'No response')[:150]}...")
                
                print(f"\n📊 STORAGE VERIFICATION:")
                print(f"=" * 60)
                print(f"✅ Session ID for UI: {session_id}")
                print(f"✅ Conversation ID for DB: {conversation_id}")
                print(f"\nCheck Supabase conversation_transcripts table:")
                print(f"  SELECT * FROM conversation_transcripts")
                print(f"  WHERE conversation_id = '{conversation_id}';")
                print(f"\nExpected: 2 rows (user + assistant)")
                print(f"=" * 60)
            else:
                print("❌ FAILED: Got error response")
                print(f"Response: {json.dumps(result, indent=2)}")
        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Error: {response.text}")

if __name__ == "__main__":
    asyncio.run(test_admin_preview_simulation())