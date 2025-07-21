#!/usr/bin/env python3
"""
Test LLM functionality in isolation
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
        
        # Create a chat context
        print("\n2. Creating chat context...")
        chat_ctx = lk_llm.ChatContext()
        chat_ctx.append(
            role="system",
            text="You are a helpful assistant. Keep responses brief."
        )
        chat_ctx.append(
            role="user", 
            text="Say hello and tell me the capital of France in one sentence."
        )
        print("✅ Chat context created")
        
        # Test synchronous completion
        print("\n3. Testing synchronous chat completion...")
        response = llm.chat(chat_ctx)
        print(f"✅ Response: {response}")
        
        # Test streaming
        print("\n4. Testing streaming chat completion...")
        chat_ctx.append(
            role="user",
            text="Count from 1 to 5."
        )
        
        stream = llm.chat(chat_ctx)
        full_response = ""
        print("Streaming response: ", end="", flush=True)
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                print(content, end="", flush=True)
                full_response += content
        print("\n✅ Streaming completed")
        
        # Test with agent-like context
        print("\n5. Testing agent-like conversation...")
        agent_ctx = lk_llm.ChatContext()
        agent_ctx.append(
            role="system",
            text="You are a voice assistant. Be conversational and friendly."
        )
        agent_ctx.append(
            role="user",
            text="Hello! How are you doing today?"
        )
        
        response = llm.chat(agent_ctx)
        print(f"✅ Agent response: {response}")
        
        print("\n✅ ALL LLM TESTS PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ LLM test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_llm_in_container():
    """Test LLM from within a container context"""
    print("\n" + "=" * 60)
    print("TESTING LLM IN CONTAINER CONTEXT")
    print("=" * 60)
    
    # Check if we're in a container
    if os.path.exists('/.dockerenv'):
        print("✅ Running inside Docker container")
    else:
        print("⚠️  Not running in container, testing local environment")
    
    # List environment variables
    print("\n📋 LLM-related environment variables:")
    for key in ['GROQ_API_KEY', 'OPENAI_API_KEY', 'AGENT_NAME', 'VOICE_ID']:
        value = os.getenv(key)
        if value:
            if 'KEY' in key:
                print(f"  {key}: {'*' * 10}{value[-4:]}")
            else:
                print(f"  {key}: {value}")
        else:
            print(f"  {key}: Not set")
    
    # Test with actual container environment
    result = await test_groq_llm()
    return result

async def main():
    """Run all tests"""
    # Test basic LLM
    basic_result = await test_groq_llm()
    
    # Test in container context
    container_result = await test_llm_in_container()
    
    if basic_result and container_result:
        print("\n✅ ALL TESTS PASSED - LLM is working correctly")
    else:
        print("\n❌ SOME TESTS FAILED - Check output above")

if __name__ == "__main__":
    asyncio.run(main())