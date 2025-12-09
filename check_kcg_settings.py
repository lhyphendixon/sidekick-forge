#!/usr/bin/env python3
import os
import json
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

platform_url = os.getenv('SUPABASE_URL')
platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

sb = create_client(platform_url, platform_key)
clients = sb.table('clients').select('*').execute()

for client in clients.data:
    name_lower = (client.get('name') or '').lower()
    if 'kimberly' in name_lower or 'carter-gamble' in name_lower:
        print(f"Found KCG client: {client.get('name')}")
        print(f"Client ID: {client.get('id')}")
        print(f"\nSettings structure:")
        settings = client.get('settings')
        if settings:
            print(json.dumps(settings, indent=2, default=str))
        else:
            print("  (No settings)")

        # Check all columns
        print(f"\nAll client data keys:")
        for key in client.keys():
            if 'supabase' in key.lower():
                print(f"  {key}: {str(client.get(key))[:100]}...")
        break
