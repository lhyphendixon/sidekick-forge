#!/usr/bin/env python3
"""
Test text chat functionality after ChatContext fix
"""

import asyncio
import httpx
import json
import uuid

API_BASE_URL = "http://localhost:8000"

async def test_text_chat():
    """Test that text chat now works with ChatContext"""
    print("🧪 Testing Text Chat with ChatContext Fix...")
    
    payload = {
        "message": "Hello! Can you hear me?",
        "agent_slug": "clarence-coherence",
        "session_id": f"test_session_{uuid.uuid4().hex[:8]}",
        "user_id": str(uuid.uuid4()),  # Use a valid UUID
        "conversation_id": f"test_conv_{uuid.uuid4().hex[:8]}",
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
            if "response" in result and "error" not in result.get("response", "").lower():
                print("✅ SUCCESS: Text chat is working!")
                print(f"Agent response: {result.get('response', 'No response')[:200]}...")
            else:
                print("❌ FAILED: Got error response")
                print(f"Response: {result}")
        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Error: {response.text}")

if __name__ == "__main__":
    asyncio.run(test_text_chat())