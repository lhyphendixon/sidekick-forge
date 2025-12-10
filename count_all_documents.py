#!/usr/bin/env python3
"""
Count all documents in Caroline Cory's database with different filters
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

print("="*100)
print("Document Count Investigation - Caroline Cory Knowledge Base")
print("="*100)

# Count total documents (no filters)
total_docs = client_sb.table('documents').select('id', count='exact').execute()
print(f"\nTotal documents (no filters): {total_docs.count}")

# Count by status
statuses = ['ready', 'processing', 'failed', 'pending', 'uploaded']
print("\nBreakdown by status:")
for status in statuses:
    count_result = client_sb.table('documents').select('id', count='exact').eq('status', status).execute()
    if count_result.count > 0:
        print(f"  {status}: {count_result.count}")

# Count by document_type
print("\nBreakdown by document_type:")
doc_types = client_sb.table('documents').select('document_type').execute()
type_counts = {}
for doc in doc_types.data:
    dt = doc.get('document_type') or 'null'
    type_counts[dt] = type_counts.get(dt, 0) + 1

for dt, count in sorted(type_counts.items()):
    print(f"  {dt}: {count}")

# Count documents with chunks
with_chunks = client_sb.table('documents').select('id', count='exact').gt('chunk_count', 0).execute()
without_chunks = client_sb.table('documents').select('id', count='exact').eq('chunk_count', 0).execute()
null_chunks = client_sb.table('documents').select('id', count='exact').is_('chunk_count', 'null').execute()

print(f"\nBreakdown by chunk_count:")
print(f"  With chunks (chunk_count > 0): {with_chunks.count}")
print(f"  Without chunks (chunk_count = 0): {without_chunks.count}")
print(f"  NULL chunk_count: {null_chunks.count}")

# Get agent info
agents = client_sb.table('agents').select('*').execute()
agent = agents.data[0] if agents.data else None

if agent:
    print(f"\n{'='*100}")
    print(f"Agent: {agent.get('name')} (ID: {agent.get('id')})")
    print("="*100)

    # Count documents in agent_documents
    agent_docs_count = client_sb.table('agent_documents').select('document_id', count='exact').eq('agent_id', agent.get('id')).execute()
    print(f"\nDocuments in agent_documents table: {agent_docs_count.count}")

    # Count enabled vs disabled
    enabled = client_sb.table('agent_documents').select('document_id', count='exact').eq('agent_id', agent.get('id')).eq('enabled', True).execute()
    disabled = client_sb.table('agent_documents').select('document_id', count='exact').eq('agent_id', agent.get('id')).eq('enabled', False).execute()

    print(f"  Enabled: {enabled.count}")
    print(f"  Disabled: {disabled.count}")

    # Get all document IDs
    all_doc_ids = set(d.get('id') for d in client_sb.table('documents').select('id').execute().data)
    agent_doc_ids = set(ad.get('document_id') for ad in client_sb.table('agent_documents').select('document_id').eq('agent_id', agent.get('id')).execute().data)

    unassigned_count = len(all_doc_ids - agent_doc_ids)

    print(f"\n{'='*100}")
    print("SUMMARY:")
    print("="*100)
    print(f"Total documents in database: {total_docs.count}")
    print(f"Documents assigned to agent: {agent_docs_count.count}")
    print(f"Documents NOT assigned to agent: {unassigned_count}")
    print(f"Documents that will appear in RAG search: {enabled.count}")

# Check if my previous count was limited
print(f"\n{'='*100}")
print("Checking previous query limits:")
print("="*100)

# Simulate my previous query
prev_query = client_sb.table('documents').select('id, title, status, chunk_count, agent_id').execute()
print(f"Documents returned by .select().execute(): {len(prev_query.data)}")
print(f"Was this limited by default pagination? {'YES - data was truncated!' if len(prev_query.data) < total_docs.count else 'NO'}")

# Get the actual limit
if len(prev_query.data) < total_docs.count:
    print(f"\nDefault limit appears to be: {len(prev_query.data)} rows")
    print(f"Missing documents: {total_docs.count - len(prev_query.data)}")
    print(f"\nTo get all documents, I should use:")
    print(f"  .select('*').limit({total_docs.count + 100}).execute()")
    print(f"  or .select('*', count='exact').execute() and check .count")

print("\n" + "="*100 + "\n")
