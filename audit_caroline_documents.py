#!/usr/bin/env python3
"""
Audit all documents in Caroline Cory's knowledge base for agent_permissions issues
"""
import os
from supabase import create_client
from dotenv import load_dotenv
import json

load_dotenv('/root/sidekick-forge/.env')

platform_url = os.getenv('SUPABASE_URL')
platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
platform_sb = create_client(platform_url, platform_key)

# Find Caroline Cory
clients = platform_sb.table('clients').select('*').execute()
cc_client = next((c for c in clients.data if 'caroline' in (c.get('name') or '').lower()), None)

if not cc_client:
    print("Caroline Cory client not found")
    exit(1)

client_sb = create_client(cc_client.get('supabase_url'), cc_client.get('supabase_service_role_key'))

# Get the agent
agents = client_sb.table('agents').select('*').execute()
if not agents.data:
    print("No agents found")
    exit(1)

agent = agents.data[0]
agent_slug = agent.get('slug')

print("="*100)
print(f"AUDIT REPORT: Caroline Cory Knowledge Base Documents")
print(f"Agent: {agent.get('name')} (slug: {agent_slug})")
print("="*100)

# Get all documents
docs = client_sb.table('documents').select('id, title, status, chunk_count, agent_permissions').execute()

print(f"\nTotal documents: {len(docs.data)}")

# Categorize documents
docs_with_null_perms = []
docs_with_empty_perms = []
docs_with_wrong_perms = []
docs_with_correct_perms = []
docs_missing_chunks = []

for doc in docs.data:
    agent_perms = doc.get('agent_permissions')
    chunk_count = doc.get('chunk_count') or 0

    # Check chunk count
    if chunk_count == 0:
        docs_missing_chunks.append(doc)
        continue

    # Check permissions
    if agent_perms is None:
        docs_with_null_perms.append(doc)
    elif isinstance(agent_perms, list):
        if len(agent_perms) == 0:
            docs_with_empty_perms.append(doc)
        elif agent_slug not in agent_perms:
            docs_with_wrong_perms.append(doc)
        else:
            docs_with_correct_perms.append(doc)
    else:
        # Unknown format
        docs_with_wrong_perms.append(doc)

print("\n" + "-"*100)
print("RESULTS:")
print("-"*100)

print(f"\n✅ Documents with CORRECT permissions ({agent_slug} in agent_permissions): {len(docs_with_correct_perms)}")
if docs_with_correct_perms and len(docs_with_correct_perms) <= 10:
    for doc in docs_with_correct_perms[:10]:
        print(f"   - {doc.get('title')}")

print(f"\n❌ Documents with NULL agent_permissions: {len(docs_with_null_perms)}")
if docs_with_null_perms:
    for doc in docs_with_null_perms[:20]:
        print(f"   - {doc.get('title')} (ID: {doc.get('id')})")
    if len(docs_with_null_perms) > 20:
        print(f"   ... and {len(docs_with_null_perms) - 20} more")

print(f"\n⚠️  Documents with EMPTY agent_permissions array: {len(docs_with_empty_perms)}")
if docs_with_empty_perms:
    for doc in docs_with_empty_perms[:20]:
        print(f"   - {doc.get('title')} (ID: {doc.get('id')})")

print(f"\n⚠️  Documents with WRONG agent permissions (doesn't include {agent_slug}): {len(docs_with_wrong_perms)}")
if docs_with_wrong_perms:
    for doc in docs_with_wrong_perms[:10]:
        perms = doc.get('agent_permissions')
        print(f"   - {doc.get('title')}")
        print(f"     Current permissions: {perms}")

print(f"\n⚠️  Documents with NO chunks: {len(docs_missing_chunks)}")
if docs_missing_chunks:
    for doc in docs_missing_chunks[:10]:
        print(f"   - {doc.get('title')} (status: {doc.get('status')})")

# Calculate total broken documents
total_broken = len(docs_with_null_perms) + len(docs_with_empty_perms) + len(docs_with_wrong_perms)

print("\n" + "="*100)
print("SUMMARY:")
print("="*100)
print(f"Total documents: {len(docs.data)}")
print(f"Documents that WILL appear in RAG: {len(docs_with_correct_perms)}")
print(f"Documents that WON'T appear in RAG: {total_broken}")
print(f"Documents with no chunks (can't appear anyway): {len(docs_missing_chunks)}")
print(f"\nPercentage of usable documents: {len(docs_with_correct_perms) / max(1, len(docs.data) - len(docs_missing_chunks)) * 100:.1f}%")

# Generate fix SQL
if total_broken > 0:
    print("\n" + "="*100)
    print("FIX SQL:")
    print("="*100)
    print(f"\n-- Run this in Caroline Cory's Supabase SQL Editor to fix all {total_broken} documents:")
    print(f"\nUPDATE documents")
    print(f"SET agent_permissions = ARRAY['{agent_slug}']::text[]")
    print(f"WHERE agent_permissions IS NULL")
    print(f"   OR agent_permissions = ARRAY[]::text[]")
    print(f"   OR NOT ('{agent_slug}' = ANY(agent_permissions));")

    print(f"\n-- Verify the fix:")
    print(f"SELECT count(*) as fixed_count")
    print(f"FROM documents")
    print(f"WHERE '{agent_slug}' = ANY(agent_permissions);")

# Check embeddings format on a few documents
print("\n" + "="*100)
print("EMBEDDINGS FORMAT CHECK:")
print("="*100)

sample_docs = docs.data[:3]
for doc in sample_docs:
    chunks = client_sb.table('document_chunks').select('embeddings, embeddings_vec').eq('document_id', doc.get('id')).limit(1).execute()
    if chunks.data:
        chunk = chunks.data[0]
        emb_type = type(chunk.get('embeddings'))
        has_vec = chunk.get('embeddings_vec') is not None

        print(f"\n{doc.get('title')[:60]}...")
        print(f"  embeddings type: {emb_type.__name__}")
        print(f"  embeddings_vec present: {has_vec}")

        if emb_type == str:
            print(f"  ❌ Embeddings stored as JSON string (needs conversion)")
        elif emb_type == list:
            print(f"  ⚠️  Embeddings stored as list (may work but vector type is better)")

print("\n" + "="*100 + "\n")
