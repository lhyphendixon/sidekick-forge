#!/usr/bin/env python3
"""
Comprehensive test to verify text chat storage is working
"""

import asyncio
import httpx
import json
import uuid
from datetime import datetime, UTC

API_BASE_URL = "http://localhost:8000"

async def test_and_verify_storage():
    """Test text chat and verify storage with unique identifiers"""
    # Generate unique identifiers
    unique_conv_id = str(uuid.uuid4())
    unique_session_id = f"verify_{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    unique_message = f"VERIFY TEST at {timestamp} - Please confirm this exact message is stored."
    
    print(f"🧪 STORAGE VERIFICATION TEST")
    print(f"=" * 60)
    print(f"Conversation ID: {unique_conv_id}")
    print(f"Session ID: {unique_session_id}")
    print(f"Timestamp: {timestamp}")
    print(f"Test Message: {unique_message}")
    print(f"=" * 60)
    
    payload = {
        "message": unique_message,
        "agent_slug": "clarence-coherence",
        "session_id": unique_session_id,
        "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
        "conversation_id": unique_conv_id,
        "mode": "text",
        "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592"
    }
    
    async with httpx.AsyncClient() as client:
        print("\n📤 Sending request...")
        response = await client.post(
            f"{API_BASE_URL}/api/v1/trigger-agent",
            json=payload,
            timeout=30.0
        )
        
        print(f"📥 Response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            data = result.get("data", {})
            agent_info = result.get("agent_info", {})
            
            if data.get("response") and "error" not in data.get("response", "").lower():
                print("✅ Text chat API call successful")
                print(f"\n🤖 Agent response preview: {data.get('response', 'No response')[:150]}...")
                
                print(f"\n📊 STORAGE VERIFICATION CHECKLIST:")
                print(f"=" * 60)
                print(f"Database: Autonomite's Supabase (yuowazxcxwhczywurmmw.supabase.co)")
                print(f"Table: conversation_transcripts")
                print(f"\nSearch for these EXACT values:")
                print(f"  conversation_id = '{unique_conv_id}'")
                print(f"  session_id = '{unique_session_id}'")
                print(f"\nYou should find EXACTLY 2 rows:")
                print(f"\n  Row 1 (User Message):")
                print(f"    - role = 'user'")
                print(f"    - content = '{unique_message[:50]}...'")
                print(f"    - user_id = '351bb07b-03fc-4fb4-b09b-748ef8a72084'")
                print(f"    - agent_id = '460f8e47-3115-4a8b-a5b2-5db9b5c2cec0'")
                print(f"\n  Row 2 (Assistant Response):")
                print(f"    - role = 'assistant'")
                print(f"    - content = <agent's response>")
                print(f"    - user_id = '351bb07b-03fc-4fb4-b09b-748ef8a72084'")
                print(f"    - agent_id = '460f8e47-3115-4a8b-a5b2-5db9b5c2cec0'")
                print(f"\n⚠️  If you don't see these rows, storage is NOT working!")
                print(f"=" * 60)
            else:
                print("❌ FAILED: Got error response")
                print(f"Response: {json.dumps(result, indent=2)}")
        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Error: {response.text}")

if __name__ == "__main__":
    asyncio.run(test_and_verify_storage())