#!/usr/bin/env python3
"""
Audit Caroline Cory's agent_documents junction table
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
print(f"Caroline Cory Knowledge Base - Agent Document Assignment Audit")
print(f"Agent: {agent.get('name')} (ID: {agent.get('id')})")
print("="*100)

# Get all documents
all_docs = client_sb.table('documents').select('id, title, status, chunk_count, agent_id').execute()
print(f"\nTotal documents in knowledge base: {len(all_docs.data)}")

# Get Divine Plan document
divine_doc = client_sb.table('documents').select('*').ilike('title', '%Divine Plan%printer%').execute()
if divine_doc.data:
    divine_doc = divine_doc.data[0]
    print(f"\nDivine Plan Document:")
    print(f"  ID: {divine_doc.get('id')}")
    print(f"  Title: {divine_doc.get('title')}")
    print(f"  agent_id field: {divine_doc.get('agent_id')}")
    divine_doc_id = divine_doc.get('id')
else:
    print("\n❌ Divine Plan document not found!")
    divine_doc_id = None

# Check agent_documents table
print(f"\n{'='*100}")
print("Agent Documents Junction Table Analysis:")
print("="*100)

agent_docs = client_sb.table('agent_documents').select('*').eq('agent_id', agent.get('id')).execute()
print(f"\nDocuments assigned to agent '{agent.get('name')}': {len(agent_docs.data)}")

if divine_doc_id:
    divine_assigned = any(ad.get('document_id') == divine_doc_id for ad in agent_docs.data)

    if divine_assigned:
        print(f"✅ Divine Plan document IS assigned to agent")
        # Get the assignment details
        for ad in agent_docs.data:
            if ad.get('document_id') == divine_doc_id:
                print(f"   Assignment details: enabled={ad.get('enabled')}, access_type={ad.get('access_type')}")
    else:
        print(f"❌ Divine Plan document NOT assigned to agent!")
        print(f"   This is why it doesn't appear in RAG searches!")

# Count how many documents are assigned vs total
docs_with_agent_id = sum(1 for d in all_docs.data if d.get('agent_id') is not None)
docs_in_junction_table = len(set(ad.get('document_id') for ad in agent_docs.data))

print(f"\n{'='*100}")
print("Summary:")
print("="*100)
print(f"Total documents: {len(all_docs.data)}")
print(f"Documents with agent_id set: {docs_with_agent_id}")
print(f"Documents in agent_documents table: {docs_in_junction_table}")
print(f"Documents searchable by RAG: {docs_in_junction_table} (only these will appear)")

# Calculate percentage
if len(all_docs.data) > 0:
    percentage = (docs_in_junction_table / len(all_docs.data)) * 100
    print(f"\nPercentage of documents searchable: {percentage:.1f}%")

# Get list of unassigned documents
unassigned_doc_ids = set(d.get('id') for d in all_docs.data) - set(ad.get('document_id') for ad in agent_docs.data)
print(f"\nUnassigned documents (won't appear in RAG): {len(unassigned_doc_ids)}")

if len(unassigned_doc_ids) > 0 and len(unassigned_doc_ids) <= 30:
    print("\nUnassigned documents:")
    for doc_id in list(unassigned_doc_ids)[:30]:
        doc = next((d for d in all_docs.data if d.get('id') == doc_id), None)
        if doc:
            print(f"  - {doc.get('title')}")
            if 'divine plan' in doc.get('title', '').lower():
                print(f"    ⚠️  THIS IS THE DIVINE PLAN DOCUMENT!")
elif len(unassigned_doc_ids) > 30:
    print(f"\n(Too many to list - showing first 30):")
    for doc_id in list(unassigned_doc_ids)[:30]:
        doc = next((d for d in all_docs.data if d.get('id') == doc_id), None)
        if doc:
            title = doc.get('title', '')
            if 'divine plan' in title.lower():
                print(f"  - {title} ⚠️  THIS IS THE DIVINE PLAN DOCUMENT!")
            else:
                print(f"  - {title}")

# Generate fix SQL
if divine_doc_id and not divine_assigned:
    print(f"\n{'='*100}")
    print("FIX SQL:")
    print("="*100)
    print(f"\n-- Add Divine Plan document to agent_documents table:")
    print(f"INSERT INTO agent_documents (agent_id, document_id, access_type, enabled)")
    print(f"VALUES ('{agent.get('id')}', '{divine_doc_id}', 'read', true);")

if len(unassigned_doc_ids) > 1:
    print(f"\n-- To assign ALL {len(unassigned_doc_ids)} unassigned documents to the agent:")
    print(f"INSERT INTO agent_documents (agent_id, document_id, access_type, enabled)")
    print(f"SELECT '{agent.get('id')}', id, 'read', true")
    print(f"FROM documents")
    print(f"WHERE id NOT IN (")
    print(f"  SELECT document_id FROM agent_documents WHERE agent_id = '{agent.get('id')}'")
    print(f");")

print("\n" + "="*100 + "\n")
