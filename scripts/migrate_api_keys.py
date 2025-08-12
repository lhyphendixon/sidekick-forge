#!/usr/bin/env python3
"""
Migrate API keys from Autonomite's database to the platform database
"""
import os
import sys
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

# Platform database credentials
PLATFORM_URL = os.getenv('SUPABASE_URL')
PLATFORM_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

print("🔄 Migrating API Keys from Autonomite Database to Platform Database\n")

# Autonomite's Supabase credentials
AUTONOMITE_URL = "https://yuowazxcxwhczywurmmw.supabase.co"
AUTONOMITE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"

try:
    # Connect to both databases
    platform_client = create_client(PLATFORM_URL, PLATFORM_KEY)
    autonomite_client = create_client(AUTONOMITE_URL, AUTONOMITE_KEY)
    
    print("📊 Step 1: Fetching API keys from Autonomite database...")
    
    # Get the latest agent configuration
    agent_config_result = autonomite_client.table('agent_configurations').select('*').order('last_updated', desc=True).limit(1).execute()
    
    api_keys = {}
    
    if agent_config_result.data and len(agent_config_result.data) > 0:
        config = agent_config_result.data[0]
        print(f"   Found agent configuration from: {config.get('last_updated', 'unknown')}")
        
        # Extract API keys from agent configuration
        key_mappings = {
            'openai_api_key': 'openai_api_key',
            'groq_api_key': 'groq_api_key',
            'deepgram_api_key': 'deepgram_api_key',
            'elevenlabs_api_key': 'elevenlabs_api_key',
            'cartesia_api_key': 'cartesia_api_key',
            'deepinfra_api_key': 'deepinfra_api_key',
            'replicate_api_key': 'replicate_api_key',
            'anthropic_api_key': 'anthropic_api_key',
            'speechify_api_key': 'speechify_api_key',
            'novita_api_key': 'novita_api_key',
            'cohere_api_key': 'cohere_api_key',
            'siliconflow_api_key': 'siliconflow_api_key',
            'jina_api_key': 'jina_api_key'
        }
        
        for db_key, api_key_name in key_mappings.items():
            if db_key in config and config[db_key]:
                api_keys[api_key_name] = config[db_key]
                print(f"   ✓ Found {api_key_name}: {config[db_key][:10]}...{config[db_key][-4:]}")
    
    # Also check global_settings table
    print("\n📊 Step 2: Checking global_settings table...")
    global_settings_result = autonomite_client.table('global_settings').select('*').execute()
    
    if global_settings_result.data:
        global_settings_dict = {}
        for setting in global_settings_result.data:
            setting_key = setting.get('setting_key', '')
            setting_value = setting.get('setting_value', '')
            if setting_key:
                global_settings_dict[setting_key] = setting_value
        
        # Check for any API keys in global settings
        for key_name in key_mappings.values():
            if key_name in global_settings_dict and global_settings_dict[key_name]:
                if key_name not in api_keys or not api_keys[key_name]:
                    api_keys[key_name] = global_settings_dict[key_name]
                    print(f"   ✓ Found {key_name} in global_settings: {global_settings_dict[key_name][:10]}...{global_settings_dict[key_name][-4:]}")
    
    if not api_keys:
        print("\n❌ No API keys found in Autonomite database!")
        sys.exit(1)
    
    print(f"\n✅ Found {len(api_keys)} API keys to migrate")
    
    # Update platform database for both Autonomite clients
    print("\n📊 Step 3: Updating platform database...")
    
    client_ids = [
        'df91fd06-816f-4273-a903-5a4861277040',  # Original Autonomite
        '11389177-e4d8-49a9-9a00-f77bb4de6592'   # Duplicate Autonomite
    ]
    
    for client_id in client_ids:
        print(f"\n   Updating client {client_id}...")
        
        result = platform_client.table('clients').update(api_keys).eq('id', client_id).execute()
        
        if result.data:
            print(f"   ✅ Successfully updated client: {result.data[0]['name']}")
        else:
            print(f"   ❌ Failed to update client {client_id}")
    
    print("\n🎉 Migration complete!")
    print("\n📄 Migrated API keys:")
    for key, value in api_keys.items():
        print(f"   {key}: {value[:10]}...{value[-4:]}")
    
    print("\n🔄 Please restart services to apply changes:")
    print("   docker-compose restart")
    
except Exception as e:
    print(f"\n❌ Error during migration: {e}")
    import traceback
    traceback.print_exc()