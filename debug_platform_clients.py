#!/usr/bin/env python3
"""
Debug script to see what's actually in the platform database clients table
"""
import sys
import os
import asyncio
import json

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from supabase import create_client
from app.config import settings

async def main():
    print(f"Connecting to platform database: {settings.supabase_url}")
    print(f"Using service key ending in: ...{settings.supabase_service_role_key[-10:]}")
    
    # Connect to the main Sidekick Forge platform database
    platform_supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
    
    try:
        # Query all clients from the platform database
        print("\nQuerying clients table...")
        result = platform_supabase.table("clients").select("*").execute()
        
        if not result.data:
            print("❌ No clients found in platform database")
            return
        
        print(f"✅ Found {len(result.data)} clients in platform database\n")
        
        for i, client_data in enumerate(result.data, 1):
            client_id = client_data['id']
            client_name = client_data.get('name', 'Unknown')
            
            print(f"=== CLIENT {i}: {client_name} ===")
            print(f"ID: {client_id}")
            print(f"Name: {client_name}")
            print(f"Description: {client_data.get('description')}")
            print(f"Domain: {client_data.get('domain')}")
            print(f"Active: {client_data.get('active')}")
            
            # Print the raw settings field
            settings_raw = client_data.get('settings')
            print(f"Settings type: {type(settings_raw)}")
            print(f"Settings raw: {settings_raw}")
            
            if settings_raw:
                if isinstance(settings_raw, dict):
                    supabase_config = settings_raw.get('supabase')
                    if supabase_config:
                        print(f"  Supabase URL: {supabase_config.get('url', 'NOT SET')}")
                        service_key = supabase_config.get('service_role_key', '')
                        if service_key:
                            print(f"  Service key: ...{service_key[-10:] if len(service_key) > 10 else service_key}")
                        else:
                            print(f"  Service key: NOT SET")
                        anon_key = supabase_config.get('anon_key', '')
                        if anon_key:
                            print(f"  Anon key: ...{anon_key[-10:] if len(anon_key) > 10 else anon_key}")
                        else:
                            print(f"  Anon key: NOT SET")
                    else:
                        print("  ❌ No supabase config in settings")
                elif isinstance(settings_raw, str):
                    print(f"  Settings is a string: {settings_raw[:100]}...")
                    try:
                        parsed_settings = json.loads(settings_raw)
                        supabase_config = parsed_settings.get('supabase')
                        if supabase_config:
                            print(f"  Parsed Supabase URL: {supabase_config.get('url', 'NOT SET')}")
                        else:
                            print("  ❌ No supabase config in parsed settings")
                    except json.JSONDecodeError as e:
                        print(f"  ❌ Could not parse settings JSON: {e}")
                else:
                    print(f"  ❌ Settings has unexpected type: {type(settings_raw)}")
            else:
                print("  ❌ No settings found")
            
            print()
        
    except Exception as e:
        print(f"❌ Error querying platform database: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = asyncio.run(main())