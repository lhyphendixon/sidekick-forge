#!/usr/bin/env python3
"""Test API key loading from platform database"""
import asyncio
import sys
sys.path.append('/app')

from app.docker.agent.api_key_loader import APIKeyLoader
from app.services.client_service_supabase import ClientService
from app.config import settings

async def test_api_key_loading():
    # Test the API key loader
    loader = APIKeyLoader()
    
    # Load keys for Autonomite client
    client_id = "df91fd06-816f-4273-a903-5a4861277040"
    print(f"Testing API key loading for client: {client_id}\n")
    
    api_keys = await loader.load_api_keys(client_id)
    
    print("API Keys loaded from platform database:")
    for key, value in api_keys.items():
        if value and value != "<needs-actual-key>":
            print(f"  ✅ {key}: {value[:10]}...{value[-4:]}")
        else:
            print(f"  ❌ {key}: Not set or placeholder")
    
    # Test specific providers
    print("\nProvider availability:")
    if api_keys.get("deepgram_api_key") and api_keys["deepgram_api_key"] != "<needs-actual-key>":
        print("  ✅ Deepgram STT: Available")
    else:
        print("  ❌ Deepgram STT: Missing")
        
    if api_keys.get("groq_api_key") and api_keys["groq_api_key"] != "<needs-actual-key>":
        print("  ✅ Groq LLM: Available")
    else:
        print("  ❌ Groq LLM: Missing")
        
    if api_keys.get("elevenlabs_api_key") and api_keys["elevenlabs_api_key"] != "<needs-actual-key>":
        print("  ✅ ElevenLabs TTS: Available")
    else:
        print("  ❌ ElevenLabs TTS: Missing")

if __name__ == "__main__":
    asyncio.run(test_api_key_loading())