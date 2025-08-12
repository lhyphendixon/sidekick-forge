#!/usr/bin/env python3
import asyncio
import httpx
import json
import sys

# Add parent directory to path
sys.path.insert(0, '/root/sidekick-forge')

async def test_trigger():
    url = "http://localhost:8000/api/v1/trigger-agent"
    
    # Test payload with correct model
    payload = {
        "agent_slug": "test-agent",
        "mode": "voice",
        "room_name": f"test-model-fix-{int(time.time())}",
        "user_id": "test-user",
        "client_id": "df91fd06-816f-4273-a903-5a4861277040",
        "voice_settings": {
            "llm_provider": "groq",
            "llm_model": "llama-3.3-70b-versatile",  # Use the correct model
            "stt_provider": "deepgram",
            "tts_provider": "elevenlabs"
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=30.0)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            print(json.dumps(result, indent=2))
        else:
            print(f"Error: {response.text}")

import time
asyncio.run(test_trigger())