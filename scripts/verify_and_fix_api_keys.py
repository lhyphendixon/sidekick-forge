#!/usr/bin/env python3
"""
Verify and fix API key loading from Supabase
"""
import os
import sys
import json
import asyncio
from typing import Dict, Any

sys.path.append('/root/sidekick-forge')

from supabase import create_client
from app.services.client_service_supabase import ClientService
from app.core.dependencies import get_client_service

# Known test/dummy keys to detect
TEST_KEYS = {
    'APIUtuiQ47BQBsk',  # Expired LiveKit key
    'test_key',
    'dummy',
    'placeholder',
    'sk_test',
    'test',
    ''
}

async def verify_client_api_keys(client_id: str = 'df91fd06-816f-4273-a903-5a4861277040'):
    """Verify API keys are loading correctly from Supabase"""
    print(f"\n{'='*60}")
    print("API KEY VERIFICATION REPORT")
    print(f"{'='*60}\n")
    
    # Get client service
    client_service = get_client_service()
    
    # Get client
    client = await client_service.get_client(client_id)
    
    if not client:
        print(f"❌ Client {client_id} not found!")
        return
    
    print(f"✅ Client found: {client.name}")
    
    # Check settings
    if not client.settings:
        print("❌ No settings found for client!")
        return
        
    print("✅ Settings found")
    
    # Check API keys
    if not client.settings.api_keys:
        print("❌ No API keys found in settings!")
        return
        
    api_keys = client.settings.api_keys
    print(f"✅ API keys object found\n")
    
    # Analyze each API key
    print("API KEY ANALYSIS:")
    print("-" * 60)
    
    valid_keys = []
    dummy_keys = []
    missing_keys = []
    
    # Get all API key fields
    api_key_fields = [
        'openai_api_key', 'groq_api_key', 'deepinfra_api_key', 'replicate_api_key',
        'deepgram_api_key', 'elevenlabs_api_key', 'cartesia_api_key', 'speechify_api_key',
        'novita_api_key', 'cohere_api_key', 'siliconflow_api_key', 'jina_api_key',
        'anthropic_api_key'
    ]
    
    for field in api_key_fields:
        value = getattr(api_keys, field, None)
        if not value:
            missing_keys.append(field)
        elif value in TEST_KEYS or 'test' in str(value).lower() or 'dummy' in str(value).lower():
            dummy_keys.append(field)
            print(f"⚠️  {field}: DUMMY/TEST KEY DETECTED")
        else:
            valid_keys.append(field)
            # Show partial key for verification
            if len(value) > 10:
                print(f"✅ {field}: {value[:8]}...{value[-4:]}")
            else:
                print(f"✅ {field}: [key too short]")
    
    print("\nSUMMARY:")
    print(f"  Valid keys: {len(valid_keys)}")
    print(f"  Dummy/test keys: {len(dummy_keys)}")
    print(f"  Missing keys: {len(missing_keys)}")
    
    if dummy_keys:
        print(f"\n⚠️  DUMMY KEYS FOUND: {dummy_keys}")
    
    # Check LiveKit configuration
    print(f"\n{'='*60}")
    print("LIVEKIT CONFIGURATION:")
    print("-" * 60)
    
    if hasattr(client.settings, 'livekit') and client.settings.livekit:
        lk = client.settings.livekit
        # Handle both dict and object types
        if isinstance(lk, dict):
            url = lk.get('server_url', '')
            api_key = lk.get('api_key', '')
            api_secret = lk.get('api_secret', '')
        else:
            url = getattr(lk, 'server_url', '')
            api_key = getattr(lk, 'api_key', '')
            api_secret = getattr(lk, 'api_secret', '')
        
        print(f"URL: {url}")
        
        if api_key == 'APIUtuiQ47BQBsk':
            print(f"❌ API Key: EXPIRED TEST KEY (APIUtuiQ47BQBsk)")
        elif api_key:
            print(f"✅ API Key: {api_key[:8]}...{api_key[-4:]}")
        else:
            print(f"❌ API Key: MISSING")
            
        if api_secret:
            print(f"✅ API Secret: {'*' * 20}")
        else:
            print(f"❌ API Secret: MISSING")
            
        if api_key == 'APIUtuiQ47BQBsk':
            print("\n🚨 CRITICAL: LiveKit is configured with EXPIRED TEST CREDENTIALS!")
            print("   These credentials no longer work and must be updated.")
    else:
        print("❌ No LiveKit configuration found!")
    
    # Test actual loading in agent context
    print(f"\n{'='*60}")
    print("TESTING API KEY LOADING IN AGENT CONTEXT:")
    print("-" * 60)
    
    # Add the agent path
    sys.path.append('/root/sidekick-forge/docker/agent')
    # Simulate what happens in the agent
    from api_key_loader import APIKeyLoader
    
    # Create metadata like trigger endpoint does
    metadata = {
        'client_id': client_id,
        'api_keys': {}
    }
    
    # Add API keys from client settings
    if client.settings and client.settings.api_keys:
        for field in api_key_fields:
            value = getattr(client.settings.api_keys, field, None)
            if value:
                metadata['api_keys'][field] = value
    
    # Load API keys as agent would
    loaded_keys = APIKeyLoader.load_api_keys(metadata)
    
    print(f"\nKeys passed to agent: {len(metadata['api_keys'])}")
    print(f"Keys loaded by agent: {len([k for k, v in loaded_keys.items() if v])}")
    
    # Check for validation failures
    for key, value in loaded_keys.items():
        if value and not APIKeyLoader.validate_api_key(key, value):
            print(f"⚠️  {key}: Failed validation (likely test/dummy key)")
    
    print(f"\n{'='*60}\n")

async def main():
    """Main function"""
    await verify_client_api_keys()

if __name__ == "__main__":
    asyncio.run(main())