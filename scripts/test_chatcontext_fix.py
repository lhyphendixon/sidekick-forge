#!/usr/bin/env python3
"""
Quick test to verify ChatContext implementation works
"""

import asyncio
import httpx
import json
import uuid

API_BASE_URL = "http://localhost:8000"

async def test_text_chat():
    """Test that text chat now works with ChatContext"""
    print("🧪 Testing ChatContext Fix...")
    
    # Use the admin preview endpoint for testing
    payload = {
        "message": "Hello! Can you hear me?",
        "session_id": f"test_{uuid.uuid4().hex[:8]}",
        "mode": "text"
    }
    
    async with httpx.AsyncClient() as client:
        # Try the admin preview endpoint
        response = await client.post(
            f"{API_BASE_URL}/admin/agents/preview/11389177-e4d8-49a9-9a00-f77bb4de6592/clarence-coherence/send",
            json=payload,
            timeout=30.0
        )
        
        print(f"Response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            if result.get("response") and "error processing" not in result.get("response", "").lower():
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