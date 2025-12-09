#!/usr/bin/env python3
"""
Check the actual schema of the documents table in Caroline Cory's database
"""
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

platform_url = os.getenv('SUPABASE_URL')
platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
platform_sb = create_client(platform_url, platform_key)

# Find Caroline Cory
clients = platform_sb.table('clients').select('*').execute()
cc_client = next((c for c in clients.data if 'caroline' in (c.get('name') or '').lower()), None)

client_sb = create_client(cc_client.get('supabase_url'), cc_client.get('supabase_service_role_key'))

# Get one document to see its structure
doc = client_sb.table('documents').select('*').limit(1).execute()

if doc.data:
    print("Documents table columns:")
    print("="*80)
    for key in sorted(doc.data[0].keys()):
        value = doc.data[0][key]
        value_type = type(value).__name__
        value_preview = str(value)[:100] if value is not None else "NULL"
        print(f"{key:30} {value_type:15} {value_preview}")

    print("\n" + "="*80)
    print("Full sample document (first one):")
    print("="*80)
    import json
    print(json.dumps(doc.data[0], indent=2, default=str))

# Check if there's an agent_documents junction table
print("\n" + "="*80)
print("Checking for agent_documents table...")
print("="*80)
try:
    agent_docs = client_sb.table('agent_documents').select('*').limit(5).execute()
    print(f"Found agent_documents table with {len(agent_docs.data)} rows (showing first 5)")
    if agent_docs.data:
        print("\nSample rows:")
        for row in agent_docs.data[:5]:
            print(f"  agent_id: {row.get('agent_id')}, document_id: {row.get('document_id')}, enabled: {row.get('enabled')}")
except Exception as e:
    print(f"No agent_documents table or error: {e}")
