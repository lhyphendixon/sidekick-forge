#!/usr/bin/env python3
"""
Test short-term buffer memory functionality
"""

import asyncio
import httpx
import json
import uuid
from datetime import datetime, UTC

API_BASE_URL = "http://localhost:8000"

async def test_buffer_memory():
    """Test that recent conversation history is included in context"""
    conversation_id = str(uuid.uuid4())
    session_id = f"buffer_test_{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    print(f"🧪 SHORT-TERM BUFFER MEMORY TEST")
    print(f"=" * 60)
    print(f"Conversation ID: {conversation_id}")
    print(f"Session ID: {session_id}")
    print(f"Timestamp: {timestamp}")
    print(f"=" * 60)
    
    async with httpx.AsyncClient() as client:
        # Send multiple messages to build conversation history
        messages = [
            "Hello! My name is TestUser123.",
            "I'm interested in learning about Python programming.",
            "What are the best resources for beginners?",
            "Also, can you explain what a decorator is?",
            "And finally, do you remember my name?"  # This tests buffer memory
        ]
        
        for i, message in enumerate(messages, 1):
            print(f"\n📤 Message {i}/{len(messages)}: {message}")
            
            payload = {
                "message": message,
                "agent_slug": "clarence-coherence",
                "session_id": session_id,
                "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
                "conversation_id": conversation_id,
                "mode": "text",
                "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592"
            }
            
            response = await client.post(
                f"{API_BASE_URL}/api/v1/trigger-agent",
                json=payload,
                timeout=30.0
            )
            
            if response.status_code == 200:
                result = response.json()
                data = result.get("data", {})
                agent_response = data.get("response", "No response")
                
                print(f"📥 Response: {agent_response[:150]}...")
                
                # Check if the final response remembers the name
                if i == len(messages):
                    if "TestUser123" in agent_response:
                        print("\n✅ SUCCESS: Agent remembered the name from conversation history!")
                        print("   Buffer memory is working correctly.")
                    else:
                        print("\n⚠️  WARNING: Agent might not have used buffer memory")
                        print("   (Name 'TestUser123' not found in response)")
                
                # Small delay between messages
                if i < len(messages):
                    await asyncio.sleep(1)
            else:
                print(f"❌ Request failed: {response.status_code}")
                print(f"Error: {response.text}")
                break
        
        print(f"\n📊 VERIFICATION:")
        print(f"=" * 60)
        print(f"Check FastAPI logs for '📚 Loaded X recent messages for buffer memory'")
        print(f"The last request should show it loaded previous messages.")
        print(f"\nCheck Supabase conversation_transcripts table:")
        print(f"SELECT role, content FROM conversation_transcripts")
        print(f"WHERE conversation_id = '{conversation_id}'")
        print(f"ORDER BY created_at;")
        print(f"\nExpected: 10 rows (5 user + 5 assistant messages)")
        print(f"=" * 60)

if __name__ == "__main__":
    asyncio.run(test_buffer_memory())