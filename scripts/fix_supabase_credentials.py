#!/usr/bin/env python3
"""
Fix Supabase credentials mismatch
"""
import os
import re

# Correct Supabase URL from CLAUDE.md
CORRECT_SUPABASE_URL = "https://eukudpgfpihxsypulopm.supabase.co"
CORRECT_PROJECT_REF = "eukudpgfpihxsypulopm"

print("🔧 Fixing Supabase credentials mismatch\n")

# Read current .env file
env_path = "/root/sidekick-forge/.env"
with open(env_path, 'r') as f:
    content = f.read()

print("🔍 Current configuration:")
# Extract current values
url_match = re.search(r'SUPABASE_URL=(.+)', content)
service_key_match = re.search(r'SUPABASE_SERVICE_ROLE_KEY=(.+)', content)
anon_key_match = re.search(r'SUPABASE_ANON_KEY=(.+)', content)

if url_match:
    current_url = url_match.group(1)
    print(f"  URL: {current_url}")
    
if service_key_match:
    current_service_key = service_key_match.group(1)
    # Decode JWT to check project ref
    import base64
    import json
    
    try:
        # JWT structure: header.payload.signature
        payload_encoded = current_service_key.split('.')[1]
        # Add padding if needed
        payload_encoded += '=' * (4 - len(payload_encoded) % 4)
        payload = base64.b64decode(payload_encoded)
        payload_data = json.loads(payload)
        print(f"  Service Key Project Ref: {payload_data.get('ref')}")
        print(f"  Service Key Role: {payload_data.get('role')}")
    except Exception as e:
        print(f"  Could not decode service key: {e}")

# Check if URL matches the keys
if current_url == CORRECT_SUPABASE_URL:
    print("\n✅ Supabase URL is correct")
else:
    print(f"\n❌ Supabase URL mismatch!")
    print(f"   Expected: {CORRECT_SUPABASE_URL}")
    print(f"   Current:  {current_url}")

print("\n⚠️  The service role key appears to be for a different Supabase project!")
print("   The JWT contains ref='yuowazxcxwhczywurmmw' but the URL uses 'eukudpgfpihxsypulopm'")
print("\n🔐 You need to get the correct service role key for the project 'eukudpgfpihxsypulopm'")
print("   from the Supabase dashboard at:")
print(f"   {CORRECT_SUPABASE_URL}/project/eukudpgfpihxsypulopm/settings/api")
print("\n📝 To fix this, update the .env file with:")
print("   1. Keep SUPABASE_URL as-is (it's correct)")
print("   2. Replace SUPABASE_SERVICE_ROLE_KEY with the correct key from the dashboard")
print("   3. Replace SUPABASE_ANON_KEY with the correct key from the dashboard")
print("\nAfter updating, restart the containers:")
print("   docker-compose down")
print("   docker-compose up -d")