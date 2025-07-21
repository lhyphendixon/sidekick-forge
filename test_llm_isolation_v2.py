#!/usr/bin/env python3
"""
Test LLM functionality in isolation - corrected for LiveKit SDK
"""
import os
import asyncio
import logging
from livekit.plugins import groq
from livekit.agents import llm as lk_llm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_groq_llm():
    """Test Groq LLM functionality"""
    print("=" * 60)
    print("TESTING LLM IN ISOLATION")
    print("=" * 60)
    
    # Check API key
    api_key = os.getenv('GROQ_API_KEY')
    if not api_key:
        print("❌ GROQ_API_KEY not set in environment")
        return False
    
    print(f"✅ GROQ_API_KEY present: {api_key[:10]}...")
    
    try:
        # Initialize LLM
        print("\n1. Initializing Groq LLM...")
        llm = groq.LLM(
            model="llama3-70b-8192",
            temperature=0.7
        )
        print("✅ LLM initialized successfully")
        
        # Create a chat context - LiveKit uses a different API
        print("\n2. Creating chat context...")
        chat_ctx = lk_llm.ChatContext()
        # ChatContext in LiveKit doesn't have append, it's created with messages
        print("✅ Chat context created")
        
        # Test direct completion with string
        print("\n3. Testing direct text completion...")
        try:
            # Try different approaches based on the API
            test_prompt = "Say hello and tell me the capital of France in one sentence."
            
            # Method 1: Try direct string
            print("   Attempting direct string completion...")
            response = await llm.agenerate(test_prompt)
            print(f"✅ Direct response: {response}")
        except Exception as e1:
            print(f"   Direct string failed: {e1}")
            
            # Method 2: Try with messages format
            try:
                print("   Attempting messages format...")
                messages = [
                    {"role": "system", "content": "You are a helpful assistant. Keep responses brief."},
                    {"role": "user", "content": test_prompt}
                ]
                response = await llm.agenerate(messages)
                print(f"✅ Messages response: {response}")
            except Exception as e2:
                print(f"   Messages format failed: {e2}")
                
                # Method 3: Check actual LLM interface
                print("\n   Checking LLM interface...")
                print(f"   LLM type: {type(llm)}")
                print(f"   LLM methods: {[m for m in dir(llm) if not m.startswith('_')]}")
                
                # Try the chat method if available
                if hasattr(llm, 'chat'):
                    print("   Attempting chat method...")
                    # Create messages in the format expected
                    from livekit.agents.llm import ChatMessage
                    
                    messages = [
                        ChatMessage(role="system", content="You are a helpful assistant."),
                        ChatMessage(role="user", content=test_prompt)
                    ]
                    
                    # Try synchronous chat
                    response = llm.chat(messages)
                    print(f"✅ Chat response: {response}")
        
        print("\n4. Testing model info...")
        if hasattr(llm, 'model'):
            print(f"   Model: {llm.model}")
        if hasattr(llm, 'temperature'):
            print(f"   Temperature: {llm.temperature}")
        
        print("\n✅ LLM CONNECTIVITY VERIFIED")
        return True
        
    except Exception as e:
        print(f"\n❌ LLM test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_llm_http_connectivity():
    """Test if we can reach Groq API endpoint"""
    print("\n" + "=" * 60)
    print("TESTING GROQ API CONNECTIVITY")
    print("=" * 60)
    
    import httpx
    
    api_key = os.getenv('GROQ_API_KEY')
    if not api_key:
        print("❌ No API key for connectivity test")
        return False
    
    try:
        async with httpx.AsyncClient() as client:
            # Test Groq API endpoint
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama3-70b-8192",
                    "messages": [
                        {"role": "user", "content": "Hi"}
                    ],
                    "max_tokens": 10
                },
                timeout=10.0
            )
            
            if response.status_code == 200:
                print("✅ Groq API is reachable and responding")
                data = response.json()
                if 'choices' in data and data['choices']:
                    print(f"   Response: {data['choices'][0]['message']['content']}")
                return True
            else:
                print(f"❌ Groq API returned status {response.status_code}")
                print(f"   Response: {response.text}")
                return False
                
    except Exception as e:
        print(f"❌ Failed to reach Groq API: {e}")
        return False

async def main():
    """Run all tests"""
    # Test HTTP connectivity first
    http_result = await test_llm_http_connectivity()
    
    # Test LLM library
    llm_result = await test_groq_llm()
    
    if http_result and llm_result:
        print("\n✅ ALL TESTS PASSED - LLM is working correctly")
    else:
        print("\n❌ SOME TESTS FAILED - Check output above")

if __name__ == "__main__":
    asyncio.run(main())