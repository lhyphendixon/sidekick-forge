#!/usr/bin/env python3
"""
Diagnose and report the specific issues with Divine Plan document RAG search
"""
import os
import asyncio
from supabase import create_client
import logging
import json

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

async def main():
    from dotenv import load_dotenv
    load_dotenv('/root/sidekick-forge/.env')

    platform_url = os.getenv('SUPABASE_URL')
    platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    platform_sb = create_client(platform_url, platform_key)

    # Find Caroline Cory
    clients = platform_sb.table('clients').select('*').execute()
    cc_client = next((c for c in clients.data if 'caroline' in (c.get('name') or '').lower()), None)

    client_sb = create_client(cc_client.get('supabase_url'), cc_client.get('supabase_service_role_key'))

    print("\n" + "="*80)
    print("DIAGNOSTIC REPORT: Divine Plan Document RAG Search Issue")
    print("="*80)

    # Get the document
    doc_result = client_sb.table('documents').select('*').ilike('title', '%Divine Plan%printer%').execute()
    doc = doc_result.data[0]

    print(f"\nDocument: {doc.get('title')}")
    print(f"Document ID: {doc.get('id')}")
    print(f"Status: {doc.get('status')}")
    print(f"Chunk Count: {doc.get('chunk_count')}")

    # Get the agent
    agent_result = client_sb.table('agents').select('*').execute()
    agent = agent_result.data[0]

    print(f"\nAgent: {agent.get('name')} (slug: {agent.get('slug')})")

    print("\n" + "-"*80)
    print("ISSUES FOUND:")
    print("-"*80)

    issue_count = 0

    # Issue 1: Agent permissions
    agent_perms = doc.get('agent_permissions')
    print(f"\n1. Agent Permissions Check:")
    print(f"   Document agent_permissions field: {agent_perms}")
    print(f"   Agent slug: {agent.get('slug')}")

    if agent_perms is None:
        issue_count += 1
        print(f"   ❌ ISSUE: agent_permissions is NULL")
        print(f"   IMPACT: The match_documents RPC will filter out this document")
        print(f"   FIX: Set agent_permissions to include the agent slug")
    elif isinstance(agent_perms, list) and agent.get('slug') not in agent_perms:
        issue_count += 1
        print(f"   ❌ ISSUE: Agent slug '{agent.get('slug')}' not in agent_permissions")
        print(f"   IMPACT: The match_documents RPC will filter out this document")
        print(f"   FIX: Add agent slug to the agent_permissions array")
    else:
        print(f"   ✅ Agent permissions OK")

    # Issue 2: Embeddings format
    chunks = client_sb.table('document_chunks').select('*').eq('document_id', doc.get('id')).limit(1).execute()
    if chunks.data:
        chunk = chunks.data[0]
        embeddings = chunk.get('embeddings')
        embeddings_vec = chunk.get('embeddings_vec')

        print(f"\n2. Embeddings Format Check:")
        print(f"   embeddings field type: {type(embeddings)}")
        print(f"   embeddings_vec field: {'Present' if embeddings_vec else 'None'}")

        if isinstance(embeddings, str):
            issue_count += 1
            print(f"   ❌ ISSUE: Embeddings stored as JSON string, not vector type")
            print(f"   IMPACT: The match_documents RPC cannot perform vector similarity search")
            print(f"   FIX: Convert embeddings to vector type or use embeddings_vec column")

        if not embeddings_vec:
            issue_count += 1
            print(f"   ⚠️  WARNING: No embeddings_vec column")
            print(f"   IMPACT: Must use string-to-vector conversion in RPC")
            print(f"   RECOMMENDATION: Migrate embeddings to vector type")

    # Check the match_documents function
    print(f"\n3. Testing match_documents RPC:")
    try:
        # Test with a dummy embedding
        import random
        dummy_emb = [random.random() for _ in range(1024)]

        result = client_sb.rpc('match_documents', {
            'p_query_embedding': dummy_emb,
            'p_agent_slug': agent.get('slug'),
            'p_match_threshold': 0.0,
            'p_match_count': 5
        }).execute()

        print(f"   RPC executed successfully")
        print(f"   Results returned: {len(result.data)}")

        divine_plan_found = False
        for r in result.data:
            if 'divine plan' in r.get('title', '').lower():
                divine_plan_found = True
                break

        if not divine_plan_found:
            print(f"   ❌ Divine Plan document NOT in results")
            print(f"   This confirms the agent_permissions issue")
        else:
            print(f"   ✅ Divine Plan document found in results")
    except Exception as e:
        issue_count += 1
        print(f"   ❌ RPC FAILED: {e}")

    print("\n" + "-"*80)
    print(f"SUMMARY: Found {issue_count} critical issue(s)")
    print("-"*80)

    print("\n" + "="*80)
    print("RECOMMENDED FIXES:")
    print("="*80)

    print(f"\n1. Fix agent_permissions:")
    print(f"   Run this SQL in Caroline Cory's Supabase:")
    print(f"""
   UPDATE documents
   SET agent_permissions = ARRAY['{agent.get('slug')}']::text[]
   WHERE id = '{doc.get('id')}';
""")

    print(f"\n2. Verify the match_documents RPC function:")
    print(f"   The function should handle both string and vector embeddings")
    print(f"   Or migrate all embeddings to vector type first")

    print("\n" + "="*80 + "\n")

if __name__ == '__main__':
    asyncio.run(main())
