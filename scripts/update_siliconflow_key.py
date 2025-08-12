#!/usr/bin/env python3
"""
Update siliconflow API key in both platform and Autonomite databases
"""
import sys
from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv('/root/sidekick-forge/.env')

if len(sys.argv) < 2:
    print("Usage: python update_siliconflow_key.py <new_api_key>")
    sys.exit(1)

new_key = sys.argv[1]

# Platform database
PLATFORM_URL = os.getenv('SUPABASE_URL')
PLATFORM_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

# Autonomite database
AUTONOMITE_URL = "https://yuowazxcxwhczywurmmw.supabase.co"
AUTONOMITE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"

print(f"🔄 Updating SiliconFlow API key...")
print(f"   New key: {new_key[:10]}...{new_key[-4:]}")

try:
    # Update platform database for both Autonomite clients
    platform_client = create_client(PLATFORM_URL, PLATFORM_KEY)
    
    for client_id in ['df91fd06-816f-4273-a903-5a4861277040', '11389177-e4d8-49a9-9a00-f77bb4de6592']:
        result = platform_client.table('clients').update({
            'siliconflow_api_key': new_key
        }).eq('id', client_id).execute()
        
        if result.data:
            print(f"✅ Updated platform database for client {client_id[:8]}...")
    
    # Update Autonomite's database
    autonomite_client = create_client(AUTONOMITE_URL, AUTONOMITE_KEY)
    
    # Check if key exists in global_settings
    existing = autonomite_client.table('global_settings').select('*').eq('setting_key', 'siliconflow_api_key').execute()
    
    if existing.data:
        # Update existing
        result = autonomite_client.table('global_settings').update({
            'setting_value': new_key
        }).eq('setting_key', 'siliconflow_api_key').execute()
        print("✅ Updated siliconflow_api_key in Autonomite's global_settings")
    else:
        # Insert new
        result = autonomite_client.table('global_settings').insert({
            'setting_key': 'siliconflow_api_key',
            'setting_value': new_key
        }).execute()
        print("✅ Inserted siliconflow_api_key in Autonomite's global_settings")
    
    print("\n🎉 Success! The siliconflow API key has been updated in both databases.")
    print("   The auto-sync will no longer overwrite your key.")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()