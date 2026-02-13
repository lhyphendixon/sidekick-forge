#!/usr/bin/env python3
"""
Sync client configurations from platform database using correct schema
"""
import sys
import os
import asyncio
import redis
import json
from datetime import datetime

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from supabase import create_client
from app.config import settings

async def main():
    # Connect to Redis
    redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    # Connect to the main Sidekick Forge platform database
    platform_supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
    
    print("Syncing clients from platform database with correct schema...")
    
    try:
        # Query all clients with individual columns
        result = platform_supabase.table("clients").select("*").execute()
        
        if not result.data:
            print("No clients found in platform database")
            return False
        
        print(f"Found {len(result.data)} clients in platform database")
        
        for client_data in result.data:
            client_id = client_data['id']
            client_name = client_data.get('name', 'Unknown')
            
            print(f"\nProcessing client: {client_name} (ID: {client_id})")
            
            # Get additional settings (contains supabase_anon_key)
            additional_settings = client_data.get('additional_settings', {})
            if isinstance(additional_settings, str):
                try:
                    additional_settings = json.loads(additional_settings)
                except json.JSONDecodeError:
                    additional_settings = {}
            
            # Build the client settings in the expected format
            client_settings = {
                "supabase": {
                    "url": client_data.get('supabase_url', ''),
                    "anon_key": additional_settings.get('supabase_anon_key', ''),
                    "service_role_key": client_data.get('supabase_service_role_key', '')
                },
                "livekit": {
                    "server_url": client_data.get('livekit_url', ''),
                    "api_key": client_data.get('livekit_api_key', ''),
                    "api_secret": client_data.get('livekit_api_secret', '')
                },
                "api_keys": {
                    "openai_api_key": client_data.get('openai_api_key'),
                    "groq_api_key": client_data.get('groq_api_key'),
                    "deepinfra_api_key": client_data.get('deepinfra_api_key'),
                    "replicate_api_key": client_data.get('replicate_api_key'),
                    "deepgram_api_key": client_data.get('deepgram_api_key'),
                    "elevenlabs_api_key": client_data.get('elevenlabs_api_key'),
                    "cartesia_api_key": client_data.get('cartesia_api_key'),
                    "speechify_api_key": client_data.get('speechify_api_key'),
                    "novita_api_key": client_data.get('novita_api_key'),
                    "cohere_api_key": client_data.get('cohere_api_key'),
                    "siliconflow_api_key": client_data.get('siliconflow_api_key'),
                    "jina_api_key": client_data.get('jina_api_key'),
                    "anthropic_api_key": client_data.get('anthropic_api_key'),
                    "cerebras_api_key": client_data.get('cerebras_api_key')
                },
                "embedding": additional_settings.get('embedding', {
                    "provider": "novita",
                    "document_model": "Qwen/Qwen2.5-72B-Instruct",
                    "conversation_model": "Qwen/Qwen2.5-72B-Instruct"
                }),
                "rerank": additional_settings.get('rerank', {
                    "enabled": False,
                    "provider": None,
                    "model": None,
                    "top_k": 3,
                    "candidates": 20
                }),
                "performance_monitoring": False,
                "license_key": None
            }
            
            # Create the Redis client entry
            redis_client_data = {
                "id": client_id,
                "name": client_name,
                "description": additional_settings.get('description'),
                "domain": additional_settings.get('domain'),
                "settings": client_settings,
                "active": additional_settings.get('active', True),
                "created_at": client_data.get('created_at', datetime.utcnow().isoformat()),
                "updated_at": client_data.get('updated_at', datetime.utcnow().isoformat())
            }
            
            # Cache the client in Redis
            cache_key = f"client:{client_id}"
            redis_client.setex(cache_key, 86400, json.dumps(redis_client_data))
            
            # Validate the Supabase configuration
            supabase_url = client_settings["supabase"]["url"]
            service_key = client_settings["supabase"]["service_role_key"]
            anon_key = client_settings["supabase"]["anon_key"]
            
            print(f"  ✓ Cached client {client_name}")
            print(f"  - Supabase URL: {supabase_url}")
            
            if service_key and service_key != 'Project access token required':
                print(f"  - Service key: ✓ (ends with ...{service_key[-10:]})")
            else:
                print(f"  - Service key: ❌ (missing or placeholder)")
                
            if anon_key and anon_key != 'Project access token required':
                print(f"  - Anon key: ✓ (ends with ...{anon_key[-10:]})")
            else:
                print(f"  - Anon key: ❌ (missing or placeholder)")
        
        # Update the client list cache
        client_ids = [client['id'] for client in result.data]
        redis_client.setex("clients:all", 86400, json.dumps(client_ids))
        print(f"\n✓ Updated client list cache with {len(client_ids)} clients")
        
        # Clear agent caches to force refresh
        print("\nClearing agent caches to force refresh...")
        for pattern in ["agents:client:*", "agent:*", "agent_config:*"]:
            for key in redis_client.scan_iter(match=pattern):
                redis_client.delete(key)
        print("✓ Cleared agent caches")
        
    except Exception as e:
        print(f"Error syncing clients: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = asyncio.run(main())
    if success:
        print("\n🎉 Client synchronization completed successfully!")
        print("✨ All clients should now be able to load their individual agents!")
    else:
        print("\n❌ Client synchronization failed!")