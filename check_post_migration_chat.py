#!/usr/bin/env python3
"""
Check the most recent text chat after migration to diagnose citation issues
"""
import os
import json
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv('/root/sidekick-forge/.env')

platform_sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY'))
clients = platform_sb.table('clients').select('*').execute()
cc_client = next((c for c in clients.data if 'caroline' in (c.get('name') or '').lower()), None)
client_sb = create_client(cc_client.get('supabase_url'), cc_client.get('supabase_service_role_key'))

print("="*100)
print("POST-MIGRATION CHAT ANALYSIS")
print("="*100)

# Get the most recent conversations
print("\nFetching recent conversations...")
recent_convos = client_sb.table('conversation_transcripts').select('*').limit(500).execute()

# Sort by created_at
convos_sorted = sorted(recent_convos.data, key=lambda x: x.get('created_at') or '', reverse=True)

print(f"Total conversations retrieved: {len(convos_sorted)}")

# Find the most recent one (should be after migration)
print("\nMost recent conversations:")
print("-"*100)

for i, convo in enumerate(convos_sorted[:5]):
    created = convo.get('created_at', 'Unknown')
    query = convo.get('query', '')
    mode = convo.get('mode', '')
    citations = convo.get('citations', [])

    print(f"\n{i+1}. Created: {created}")
    print(f"   Mode: {mode}")
    print(f"   Query: {query[:100]}...")
    print(f"   Citations: {len(citations) if isinstance(citations, list) else 'N/A'}")

    # Check if this is the divine intervention query
    if 'divine' in query.lower() and 'intervention' in query.lower():
        print(f"   🎯 THIS IS THE POST-MIGRATION QUERY!")

        print(f"\n{'='*100}")
        print("DETAILED ANALYSIS OF POST-MIGRATION QUERY")
        print("="*100)

        print(f"\nQuery: {query}")
        print(f"\nAgent Response (first 500 chars):")
        print(convo.get('agent_response', '')[:500])

        print(f"\nCitations received: {len(citations) if isinstance(citations, list) else 0}")

        if citations and isinstance(citations, list):
            print(f"\nCitation details:")
            for j, cite in enumerate(citations[:10]):
                if isinstance(cite, dict):
                    title = cite.get('title', 'N/A')
                    similarity = cite.get('similarity', 'N/A')
                    print(f"  {j+1}. {title}")
                    print(f"     Similarity: {similarity}")
                else:
                    print(f"  {j+1}. {cite}")
        else:
            print(f"\n❌ NO CITATIONS!")

        # Check the raw response
        print(f"\nFull conversation record:")
        print(json.dumps(convo, indent=2, default=str))
        break

# Check if migration completed successfully
print(f"\n{'='*100}")
print("MIGRATION VERIFICATION")
print("="*100)

migration_check = client_sb.table('document_chunks').select(
    'id',
    count='exact'
).execute()

chunks_with_vec = client_sb.table('document_chunks').select(
    'id',
    count='exact'
).not_.is_('embeddings_vec', 'null').execute()

print(f"\nTotal chunks: {migration_check.count}")
print(f"Chunks with embeddings_vec: {chunks_with_vec.count}")
print(f"Migration coverage: {chunks_with_vec.count / migration_check.count * 100:.1f}%")

# Check Divine Plan document specifically
print(f"\n{'='*100}")
print("DIVINE PLAN DOCUMENT CHECK")
print("="*100)

divine = client_sb.table('documents').select('id, title').ilike('title', '%Divine Plan%printer%').execute()
if divine.data:
    doc_id = divine.data[0]['id']
    print(f"\nDocument: {divine.data[0]['title']}")
    print(f"Document ID: {doc_id}")

    # Check its chunks
    divine_chunks = client_sb.table('document_chunks').select(
        'id, embeddings_vec'
    ).eq('document_id', doc_id).limit(5).execute()

    chunks_with_vec_count = sum(1 for c in divine_chunks.data if c.get('embeddings_vec') is not None)
    print(f"\nSample chunks checked: {len(divine_chunks.data)}")
    print(f"Chunks with embeddings_vec: {chunks_with_vec_count}/{len(divine_chunks.data)}")

    if chunks_with_vec_count == 0:
        print("❌ Divine Plan chunks don't have embeddings_vec!")
    else:
        print("✅ Divine Plan chunks have embeddings_vec")

# Test match_documents RPC
print(f"\n{'='*100}")
print("TESTING match_documents RPC")
print("="*100)

agent = client_sb.table('agents').select('*').execute().data[0]
print(f"\nAgent: {agent.get('name')} (slug: {agent.get('slug')})")

try:
    # Test with dummy embedding
    import random
    dummy_emb = [random.random() for _ in range(1024)]

    result = client_sb.rpc('match_documents', {
        'p_query_embedding': dummy_emb,
        'p_agent_slug': agent.get('slug'),
        'p_match_threshold': 0.0,
        'p_match_count': 10
    }).execute()

    print(f"\n✅ RPC executed successfully")
    print(f"Results returned: {len(result.data)}")

    if result.data:
        print(f"\nSample results:")
        for i, r in enumerate(result.data[:5]):
            print(f"  {i+1}. {r.get('title')} (similarity: {r.get('similarity')})")

        # Check if Divine Plan is in results
        divine_found = any('divine plan' in r.get('title', '').lower() for r in result.data)
        if divine_found:
            print(f"\n✅ Divine Plan document FOUND in RPC results")
        else:
            print(f"\n❌ Divine Plan document NOT in RPC results")
    else:
        print(f"\n❌ RPC returned NO results!")

except Exception as e:
    print(f"\n❌ RPC failed: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*100 + "\n")
