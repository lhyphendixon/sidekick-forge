#!/usr/bin/env python3
"""
Update client API keys in the platform database
"""
import os
import sys
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

# Platform database credentials
PLATFORM_URL = os.getenv('SUPABASE_URL')
PLATFORM_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

print("🔧 Update Client API Keys in Platform Database\n")

# Check if we have API keys as arguments
if len(sys.argv) < 2:
    print("📝 Usage: python update_client_api_keys.py <key_type> <key_value>")
    print("\nSupported key types:")
    print("  - deepgram_api_key")
    print("  - openai_api_key")
    print("  - groq_api_key")
    print("  - elevenlabs_api_key")
    print("  - cartesia_api_key")
    print("  - all (to see all current keys)")
    print("\nExample:")
    print("  python update_client_api_keys.py deepgram_api_key YOUR_ACTUAL_KEY")
    print("  python update_client_api_keys.py all")
    sys.exit(1)

key_type = sys.argv[1]
client_id = "df91fd06-816f-4273-a903-5a4861277040"  # Autonomite

try:
    # Create platform Supabase client
    platform_supabase = create_client(PLATFORM_URL, PLATFORM_KEY)
    
    if key_type == "all":
        # Show all current API keys
        result = platform_supabase.table('clients').select(
            'name, deepgram_api_key, openai_api_key, groq_api_key, elevenlabs_api_key, cartesia_api_key'
        ).eq('id', client_id).single().execute()
        
        if result.data:
            print(f"📄 Current API keys for {result.data['name']}:\n")
            for key, value in result.data.items():
                if key != 'name' and value:
                    if value == '<needs-actual-key>':
                        print(f"  {key}: ❌ PLACEHOLDER (needs update)")
                    else:
                        print(f"  {key}: ✅ {value[:10]}...{value[-4:]}")
                elif key != 'name':
                    print(f"  {key}: ❌ Not set")
        else:
            print("❌ Client not found")
    
    else:
        # Update specific API key
        if len(sys.argv) < 3:
            print("❌ Please provide the key value")
            sys.exit(1)
            
        key_value = sys.argv[2]
        
        # Validate key type
        valid_keys = ['deepgram_api_key', 'openai_api_key', 'groq_api_key', 
                      'elevenlabs_api_key', 'cartesia_api_key', 'speechify_api_key',
                      'anthropic_api_key', 'replicate_api_key', 'novita_api_key']
        
        if key_type not in valid_keys:
            print(f"❌ Invalid key type: {key_type}")
            print(f"   Valid types: {', '.join(valid_keys)}")
            sys.exit(1)
        
        # Update the key
        result = platform_supabase.table('clients').update({
            key_type: key_value
        }).eq('id', client_id).execute()
        
        if result.data:
            print(f"✅ Successfully updated {key_type}!")
            print(f"   Client: {result.data[0]['name']}")
            print(f"   Key: {key_value[:10]}...{key_value[-4:]}")
        else:
            print(f"❌ Failed to update {key_type}")
            
    print("\n🔄 Restart services to apply changes:")
    print("   docker-compose restart")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    if "401" in str(e):
        print("   Authentication error - check service role key")
    elif "403" in str(e):
        print("   Permission denied - check RLS policies")