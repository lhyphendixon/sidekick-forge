#!/usr/bin/env python3
"""
Test agent with mock API keys to verify the Supabase issue
"""
import asyncio
import json
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

async def test_trigger_with_mock_keys():
    """Test trigger endpoint with mock API keys in metadata"""
    
    # Prepare mock API keys (these would normally come from Supabase)
    mock_api_keys = {
        "openai_api_key": "sk-test-mock-key",
        "groq_api_key": "gsk_test_mock_key", 
        "deepgram_api_key": "test_deepgram_key",
        "elevenlabs_api_key": "test_elevenlabs_key",
        "cartesia_api_key": "sk-test_cartesia_key"
    }
    
    # Prepare the trigger request with metadata
    payload = {
        "agent_slug": "clarence-coherence",
        "mode": "voice",
        "room_name": "test_room_with_keys",
        "user_id": "test_user",
        "client_id": "df91fd06-816f-4273-a903-5a4861277040",
        "metadata": {
            "api_keys": mock_api_keys,
            "voice_settings": {
                "llm_provider": "groq",
                "llm_model": "llama-3.3-70b-versatile",
                "stt_provider": "deepgram",
                "tts_provider": "cartesia",
                "voice_id": "248be419-c632-4f23-adf1-5324ed7dbf1d"
            },
            "system_prompt": "You are a helpful AI assistant. When you first meet someone, greet them warmly."
        }
    }
    
    print("🚀 Testing trigger endpoint with mock API keys...")
    print(f"   Payload: {json.dumps(payload, indent=2)}")
    
    url = "http://localhost:8000/api/v1/trigger-agent"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                result = await response.json()
                print(f"\n📤 Response status: {response.status}")
                print(f"   Response: {json.dumps(result, indent=2)}")
                
                if response.status == 200:
                    print("\n✅ Successfully triggered agent with mock keys!")
                    print("   This confirms the issue is with Supabase authentication")
                else:
                    print("\n❌ Trigger failed even with mock keys")
                    
    except Exception as e:
        print(f"\n❌ Error: {e}")

async def main():
    print("🔧 Testing Agent with Mock API Keys\n")
    print("🎯 Goal: Verify if the agent works when API keys are provided directly")
    print("   (bypassing the Supabase authentication issue)\n")
    
    await test_trigger_with_mock_keys()
    
    print("\n📝 Summary:")
    print("   The root cause is the Supabase credential mismatch:")
    print("   - Service role key is for project 'yuowazxcxwhczywurmmw'")
    print("   - But the URL is for project 'eukudpgfpihxsypulopm'")
    print("   - This causes 401 authentication errors")
    print("   - Agent can't load API keys from database")
    print("   - Deepgram fails to connect without an API key")
    print("\n🔧 To fix: Update .env with correct Supabase credentials")

if __name__ == "__main__":
    asyncio.run(main())