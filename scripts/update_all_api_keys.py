#!/usr/bin/env python3
"""
Update all API keys for a client in the platform database
"""
import os
import sys
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

# Platform database credentials
PLATFORM_URL = os.getenv('SUPABASE_URL')
PLATFORM_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

print("🔧 Update All API Keys for Client\n")

# Check if we have client ID as argument
if len(sys.argv) < 2:
    print("📝 Usage: python update_all_api_keys.py <client_id>")
    print("\nKnown clients:")
    print("  - df91fd06-816f-4273-a903-5a4861277040 (Autonomite)")
    print("  - 11389177-e4d8-49a9-9a00-f77bb4de6592 (Autonomite duplicate)")
    sys.exit(1)

client_id = sys.argv[1]

# API keys to update - you'll need to provide actual values
API_KEYS = {
    'deepgram_api_key': '<your-deepgram-key>',
    'openai_api_key': '<your-openai-key>',
    'groq_api_key': '<your-groq-key>',
    'elevenlabs_api_key': '<your-elevenlabs-key>',
    'cartesia_api_key': '<your-cartesia-key>',
}

print("⚠️  Please edit this script and replace the placeholder API keys with actual values!")
print("   Then run it again.\n")

# Check if placeholders are still there
if any('<your-' in v for v in API_KEYS.values()):
    print("❌ Placeholder API keys detected. Please edit the script first.")
    sys.exit(1)

try:
    # Create platform Supabase client
    platform_supabase = create_client(PLATFORM_URL, PLATFORM_KEY)
    
    # Update all keys at once
    result = platform_supabase.table('clients').update(API_KEYS).eq('id', client_id).execute()
    
    if result.data:
        print(f"✅ Successfully updated all API keys for client!")
        print(f"   Client: {result.data[0]['name']}")
        
        # Show updated keys (masked)
        print("\n📄 Updated keys:")
        for key, value in API_KEYS.items():
            print(f"   {key}: {value[:10]}...{value[-4:]}")
    else:
        print("❌ Failed to update API keys")
        
    print("\n🔄 Restart services to apply changes:")
    print("   docker-compose restart")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    if "401" in str(e):
        print("   Authentication error - check service role key")
    elif "403" in str(e):
        print("   Permission denied - check RLS policies")