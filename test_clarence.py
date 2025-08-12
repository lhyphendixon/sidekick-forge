#!/usr/bin/env python3
"""
Test text chat with clarence-coherence agent
"""

import asyncio
import httpx
import json

API_BASE_URL = "http://localhost:8000"

async def test_text_chat():
    """Test text chat with clarence-coherence agent"""
    print("🧪 Testing Text Chat with clarence-coherence...")
    
    payload = {
        "message": "Hello! How are you today?",
        "agent_slug": "clarence-coherence",
        "session_id": "test_session_123",
        "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",  # Valid UUID
        "conversation_id": "test_conv_789",
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
            if result.get("response") and "error" not in result.get("response", "").lower():
                print("✅ SUCCESS: Text chat is working!")
                print(f"Agent response: {result.get('response', 'No response')}")
                print(f"RAG enabled: {result.get('rag_enabled', False)}")
                print(f"LLM provider: {result.get('llm_provider', 'unknown')}")
            else:
                print("❌ FAILED: Got error response")
                print(f"Response: {json.dumps(result, indent=2)}")
        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Error: {response.text}")

if __name__ == "__main__":
    asyncio.run(test_text_chat())