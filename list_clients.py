#!/usr/bin/env python3
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

platform_url = os.getenv('SUPABASE_URL')
platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

sb = create_client(platform_url, platform_key)
clients = sb.table('clients').select('*').execute()

print(f"Found {len(clients.data)} clients:")
for client in clients.data:
    print(f"  - ID: {client.get('id')}")
    print(f"    Name: {client.get('name')}")
    print(f"    Slug: {client.get('slug')}")
    print(f"    Email: {client.get('email')}")
    print()
