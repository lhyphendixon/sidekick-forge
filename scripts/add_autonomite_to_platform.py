#!/usr/bin/env python3
"""
Add Autonomite as a client in the Sidekick Forge platform database
"""
import os
import json
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

# Platform database credentials
PLATFORM_URL = os.getenv('SUPABASE_URL')
PLATFORM_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

print(f"📦 Adding Autonomite to Sidekick Forge Platform Database\n")

# Note: Since we only have the anon key, we might have limited permissions
print(f"Platform URL: {PLATFORM_URL}")
print(f"Using key: {PLATFORM_KEY[:20]}...{PLATFORM_KEY[-10:]}")

try:
    # Create platform Supabase client
    platform_supabase = create_client(PLATFORM_URL, PLATFORM_KEY)
    
    # Check if Autonomite already exists
    result = platform_supabase.table('clients').select('id, name').eq('id', 'df91fd06-816f-4273-a903-5a4861277040').execute()
    
    if result.data:
        print(f"\n✅ Autonomite already exists in platform database: {result.data[0]}")
    else:
        print("\n📝 Creating Autonomite client entry...")
        
        # Add Autonomite to platform database
        # Note: These are placeholder values - actual credentials should be added via admin UI
        client_data = {
            "id": "df91fd06-816f-4273-a903-5a4861277040",
            "name": "Autonomite",
            "supabase_url": "https://yuowazxcxwhczywurmmw.supabase.co",
            "supabase_service_role_key": "<needs-actual-key>",
            "livekit_url": "wss://litebridge-hw6srhvi.livekit.cloud",
            "livekit_api_key": "<needs-actual-key>",
            "livekit_api_secret": "<needs-actual-key>",
            "deepgram_api_key": "<needs-actual-key>",
            "openai_api_key": "<needs-actual-key>",
            "groq_api_key": "<needs-actual-key>",
            "elevenlabs_api_key": "<needs-actual-key>",
            "cartesia_api_key": "<needs-actual-key>",
            "additional_settings": {
                "description": "Autonomite AI Platform",
                "domain": "autonomite.ai",
                "active": True
            }
        }
        
        result = platform_supabase.table('clients').insert(client_data).execute()
        
        if result.data:
            print(f"\n✅ Successfully added Autonomite to platform database!")
            print(f"   ID: {result.data[0]['id']}")
            print(f"   Name: {result.data[0]['name']}")
        else:
            print(f"\n❌ Failed to add Autonomite")
            
except Exception as e:
    print(f"\n❌ Error: {e}")
    if "permission" in str(e).lower() or "policy" in str(e).lower():
        print("\n⚠️  This appears to be a permissions issue.")
        print("   The anon key may not have write permissions to the clients table.")
        print("   You may need to:")
        print("   1. Use the actual service role key (not anon key)")
        print("   2. Or add the client via the Supabase dashboard")
        print("   3. Or update RLS policies to allow anon key to insert clients")

print("\n📝 Next steps:")
print("1. Update the client entry with actual API keys via admin dashboard")
print("2. Ensure the service role key in .env is the actual service role key")
print("3. Restart services to load updated configuration")