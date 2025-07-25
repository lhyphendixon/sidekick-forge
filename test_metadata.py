#!/usr/bin/env python3
"""Test script to verify room metadata handling"""

import json

# Test metadata structure that would be created by trigger.py
test_metadata = {
    "agent_slug": "test-agent",
    "agent_name": "Test Agent",
    "system_prompt": "You are a helpful test assistant.",
    "voice_settings": {
        "llm_provider": "openai",
        "stt_provider": "deepgram", 
        "tts_provider": "cartesia",
        "voice_id": "test-voice"
    },
    "user_id": "test-user-123",
    "session_id": "test-session-456",
    "api_keys": {
        "openai_api_key": "sk-test-key",
        "deepgram_api_key": "test_key",
        "cartesia_api_key": "test_key"
    },
    "created_by": "autonomite_backend",
    "created_at": "2025-07-25T00:00:00"
}

# Test what the worker would see
print("Test metadata as JSON string (what LiveKit stores):")
metadata_json = json.dumps(test_metadata)
print(metadata_json)
print("\n" + "="*50 + "\n")

# Test parsing
print("Parsed metadata (what worker should extract):")
parsed = json.loads(metadata_json)
print(f"System prompt: {parsed.get('system_prompt', 'DEFAULT')}")
print(f"Voice settings: {parsed.get('voice_settings', {})}")
print(f"API keys present: {list(parsed.get('api_keys', {}).keys())}")