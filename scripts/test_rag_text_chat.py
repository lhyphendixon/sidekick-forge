#!/usr/bin/env python3
"""
Test script to verify RAG-powered text chat implementation
"""

import asyncio
import httpx
import json
import uuid
from datetime import datetime

# API configuration
API_BASE_URL = "http://localhost:8000"

# Test configuration - use a real client/agent if available
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"  # Replace with actual client ID
TEST_AGENT_SLUG = "test-agent"  # Replace with actual agent slug
TEST_USER_ID = f"test_user_{uuid.uuid4().hex[:8]}"
TEST_CONVERSATION_ID = f"test_conv_{uuid.uuid4().hex[:8]}"


async def test_rag_text_chat():
    """Test RAG-powered text chat"""
    print("\n🧪 Testing RAG-Powered Text Chat...")
    
    async with httpx.AsyncClient() as client:
        # First message - establish context
        payload1 = {
            "mode": "text",
            "message": "Hi! My name is Alex and I love astronomy.",
            "agent_slug": TEST_AGENT_SLUG,
            "client_id": TEST_CLIENT_ID,
            "user_id": TEST_USER_ID,
            "conversation_id": TEST_CONVERSATION_ID,
            "session_id": f"session_{uuid.uuid4().hex[:8]}"
        }
        
        print(f"\n📤 Sending first message: '{payload1['message']}'")
        
        response1 = await client.post(
            f"{API_BASE_URL}/api/v1/trigger-agent",
            json=payload1,
            timeout=30.0
        )
        
        if response1.status_code == 200:
            result1 = response1.json()
            print(f"✅ Response received:")
            print(f"   Agent: {result1.get('response', 'No response')[:150]}...")
            print(f"   RAG Enabled: {result1.get('rag_enabled', False)}")
            print(f"   LLM Provider: {result1.get('llm_provider')}")
            
            # Wait a moment for storage
            await asyncio.sleep(2)
            
            # Second message - test context recall
            payload2 = {
                "mode": "text",
                "message": "What was my name again? And what did I say I was interested in?",
                "agent_slug": TEST_AGENT_SLUG,
                "client_id": TEST_CLIENT_ID,
                "user_id": TEST_USER_ID,
                "conversation_id": TEST_CONVERSATION_ID,
                "session_id": f"session_{uuid.uuid4().hex[:8]}"
            }
            
            print(f"\n📤 Sending context test message: '{payload2['message']}'")
            
            response2 = await client.post(
                f"{API_BASE_URL}/api/v1/trigger-agent",
                json=payload2,
                timeout=30.0
            )
            
            if response2.status_code == 200:
                result2 = response2.json()
                print(f"✅ Context-aware response:")
                print(f"   Agent: {result2.get('response', 'No response')}")
                
                # Check if the agent remembered the context
                response_text = result2.get('response', '').lower()
                remembered_name = 'alex' in response_text
                remembered_interest = 'astronomy' in response_text
                
                print(f"\n🔍 Context Verification:")
                print(f"   Remembered name (Alex): {'✅ YES' if remembered_name else '❌ NO'}")
                print(f"   Remembered interest (Astronomy): {'✅ YES' if remembered_interest else '❌ NO'}")
                
                if remembered_name and remembered_interest:
                    print("\n🎉 SUCCESS: RAG context is working! The agent remembered previous conversation details.")
                else:
                    print("\n⚠️  WARNING: RAG context might not be fully working. Check logs for details.")
                    
            else:
                print(f"❌ Second request failed: {response2.status_code}")
                print(f"   Error: {response2.text}")
        else:
            print(f"❌ First request failed: {response1.status_code}")
            print(f"   Error: {response1.text}")
            
            # Check if it's an agent not found error
            if response1.status_code == 404:
                print("\n💡 TIP: Make sure to use a valid agent slug. You can list agents with:")
                print("   curl http://localhost:8000/api/v1/clients/{client_id}/agents")


async def main():
    """Run the RAG text chat test"""
    print("="*60)
    print("🚀 RAG-POWERED TEXT CHAT TEST")
    print("="*60)
    print(f"Client ID: {TEST_CLIENT_ID}")
    print(f"Agent Slug: {TEST_AGENT_SLUG}")
    print(f"User ID: {TEST_USER_ID}")
    
    await test_rag_text_chat()
    
    print("\n" + "="*60)
    print("✅ Test completed. Check the results above.")
    print("\nNote: For this test to fully work, you need:")
    print("1. A valid client ID with Supabase credentials")
    print("2. A valid agent slug for that client")
    print("3. Valid API keys configured for the LLM provider")
    print("4. The conversation_transcripts table in the client's Supabase")


if __name__ == "__main__":
    asyncio.run(main())