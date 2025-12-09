#!/usr/bin/env python3
"""
Get the accurate list of unassigned documents
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

# Get agent
agents = client_sb.table('agents').select('*').execute()
agent = agents.data[0]

print("="*100)
print(f"CORRECTED AUDIT: Caroline Cory Knowledge Base")
print(f"Agent: {agent.get('name')} (ID: {agent.get('id')})")
print("="*100)

# Get ALL documents (not limited to 1000)
all_docs = client_sb.table('documents').select('id, title, status, chunk_count').limit(1500).execute()
print(f"\nTotal documents: {len(all_docs.data)}")

# Get ALL agent_documents
agent_docs = client_sb.table('agent_documents').select('document_id').eq('agent_id', agent.get('id')).limit(1500).execute()
print(f"Documents assigned to agent: {len(agent_docs.data)}")

# Find unassigned documents
all_doc_ids = set(d.get('id') for d in all_docs.data)
assigned_doc_ids = set(ad.get('document_id') for ad in agent_docs.data)
unassigned_doc_ids = all_doc_ids - assigned_doc_ids

print(f"\n❌ Documents NOT assigned to agent: {len(unassigned_doc_ids)}")

# Check if Divine Plan is in the unassigned list
divine_doc = next((d for d in all_docs.data if 'divine plan' in d.get('title', '').lower() and 'printer' in d.get('title', '').lower()), None)

if divine_doc:
    divine_id = divine_doc.get('id')
    is_unassigned = divine_id in unassigned_doc_ids
    print(f"\nDivine Plan Document:")
    print(f"  Title: {divine_doc.get('title')}")
    print(f"  ID: {divine_id}")
    print(f"  Status: {'❌ NOT ASSIGNED' if is_unassigned else '✅ ASSIGNED'}")

# List all unassigned documents
if unassigned_doc_ids:
    print(f"\n{'='*100}")
    print(f"List of all {len(unassigned_doc_ids)} unassigned documents:")
    print("="*100)

    unassigned_docs = [d for d in all_docs.data if d.get('id') in unassigned_doc_ids]
    unassigned_docs.sort(key=lambda x: x.get('title', ''))

    for i, doc in enumerate(unassigned_docs, 1):
        title = doc.get('title', 'Untitled')
        status_marker = "⚠️ " if 'divine' in title.lower() else ""
        print(f"{i:3}. {status_marker}{title}")

# Generate corrected fix SQL
print(f"\n{'='*100}")
print("CORRECTED FIX SQL:")
print("="*100)

if divine_doc and divine_doc.get('id') in unassigned_doc_ids:
    print(f"\n-- Fix just the Divine Plan document:")
    print(f"INSERT INTO agent_documents (agent_id, document_id, access_type, enabled)")
    print(f"VALUES ('{agent.get('id')}', '{divine_doc.get('id')}', 'read', true)")
    print(f"ON CONFLICT DO NOTHING;")

print(f"\n-- Fix ALL {len(unassigned_doc_ids)} unassigned documents:")
print(f"INSERT INTO agent_documents (agent_id, document_id, access_type, enabled)")
print(f"SELECT '{agent.get('id')}', id, 'read', true")
print(f"FROM documents")
print(f"WHERE id NOT IN (")
print(f"  SELECT document_id FROM agent_documents WHERE agent_id = '{agent.get('id')}'")
print(f")")
print(f"ON CONFLICT DO NOTHING;")

print(f"\n-- Verify the fix:")
print(f"SELECT COUNT(*) as total_assigned")
print(f"FROM agent_documents")
print(f"WHERE agent_id = '{agent.get('id')}';")
print(f"-- Should return: 1280")

print("\n" + "="*100 + "\n")
