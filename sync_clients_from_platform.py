#!/usr/bin/env python3
"""
Sync client configurations from the Sidekick Forge platform database to Redis
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
    
    print("Fetching client configurations from Sidekick Forge platform database...")
    
    try:
        # Query all clients from the platform database
        result = platform_supabase.table("clients").select("*").execute()
        
        if not result.data:
            print("No clients found in platform database")
            return
        
        print(f"Found {len(result.data)} clients in platform database")
        
        for client_data in result.data:
            client_id = client_data['id']
            client_name = client_data.get('name', 'Unknown')
            
            print(f"\nProcessing client: {client_name} (ID: {client_id})")
            
            # Cache the client in Redis
            cache_key = f"client:{client_id}"
            
            # Ensure the client has a proper structure
            redis_client_data = {
                "id": client_id,
                "name": client_data.get('name', ''),
                "description": client_data.get('description'),
                "domain": client_data.get('domain'),
                "settings": client_data.get('settings'),
                "active": client_data.get('active', True),
                "created_at": client_data.get('created_at', datetime.utcnow().isoformat()),
                "updated_at": client_data.get('updated_at', datetime.utcnow().isoformat())
            }
            
            # Store in Redis with a long TTL
            redis_client.setex(cache_key, 86400, json.dumps(redis_client_data))
            print(f"✓ Cached client {client_name} in Redis")
            
            # Check if client has Supabase settings
            if client_data.get('settings') and client_data['settings'].get('supabase'):
                supabase_config = client_data['settings']['supabase']
                supabase_url = supabase_config.get('url', '')
                print(f"  - Supabase URL: {supabase_url}")
                
                if supabase_config.get('service_role_key') and supabase_config.get('service_role_key') != 'Project access token required':
                    print(f"  - Has valid service key: ✓")
                else:
                    print(f"  - Service key: ❌ (needs configuration)")
            else:
                print(f"  - Supabase settings: ❌ (not configured)")
        
        # Update the client list cache
        client_ids = [client['id'] for client in result.data]
        redis_client.setex("clients:all", 86400, json.dumps(client_ids))
        print(f"\n✓ Updated client list cache with {len(client_ids)} clients")
        
        # Clear any stale caches to force refresh
        print("\nClearing stale caches...")
        for pattern in ["agents:client:*", "agent:*"]:
            for key in redis_client.scan_iter(match=pattern):
                redis_client.delete(key)
        print("✓ Cleared agent caches")
        
    except Exception as e:
        print(f"Error syncing clients: {e}")
        return False
    
    return True

if __name__ == "__main__":
    success = asyncio.run(main())
    if success:
        print("\n🎉 Client synchronization completed successfully!")
    else:
        print("\n❌ Client synchronization failed!")