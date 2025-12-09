#!/usr/bin/env python3
"""
Test if Divine Plan document appears in RAG search results after the migration
"""
import os
import asyncio
from supabase import create_client
from dotenv import load_dotenv
import httpx

load_dotenv('/root/sidekick-forge/.env')

async def main():
    # Connect to platform
    platform_sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY'))

    # Get Caroline Cory client
    clients = platform_sb.table('clients').select('*').execute()
    cc_client = next((c for c in clients.data if 'caroline' in (c.get('name') or '').lower()), None)

    # Connect to client database
    client_sb = create_client(cc_client['supabase_url'], cc_client['supabase_service_role_key'])

    print("=" * 100)
    print("TEST: Divine Plan Document in RAG Search Results")
    print("=" * 100)

    # Get agent
    agent = client_sb.table('agents').select('*').execute().data[0]
    print(f"\nAgent: {agent['name']} (slug: {agent['slug']})")

    # Get Divine Plan document
    divine_docs = client_sb.table('documents').select('*').ilike('title', '%divine plan%printer%').execute()
    if not divine_docs.data:
        print("\n❌ Divine Plan document not found!")
        return

    divine_doc = divine_docs.data[0]
    print(f"\nDivine Plan Document:")
    print(f"  ID: {divine_doc['id']}")
    print(f"  Title: {divine_doc['title']}")
    print(f"  Chunks: {divine_doc['chunk_count']}")

    # Check if it's assigned to agent
    assignment = client_sb.table('agent_documents').select('*').eq('agent_id', agent['id']).eq('document_id', divine_doc['id']).execute()
    if not assignment.data:
        print(f"\n❌ Divine Plan NOT assigned to agent!")
        return

    print(f"  ✅ Assigned to agent: enabled={assignment.data[0]['enabled']}")

    # Check if chunks have embeddings_vec
    chunks = client_sb.table('document_chunks').select('id, embeddings_vec').eq('document_id', divine_doc['id']).limit(3).execute()
    print(f"\n  Sample chunks:")
    for chunk in chunks.data[:3]:
        has_vec = chunk.get('embeddings_vec') is not None
        print(f"    Chunk {chunk['id']}: embeddings_vec={'✅ YES' if has_vec else '❌ NO'}")

    # Generate embedding for test query
    query = "Does divine intervention interfere with human free will"
    print(f"\n{'='*100}")
    print(f"Testing RAG search with query: '{query}'")
    print("=" * 100)

    # Get embedding config
    settings = agent.get('settings') or {}
    embedding_config = settings.get('embedding') or {}
    api_keys = settings.get('api_keys') or {}

    provider = embedding_config.get('provider')
    model = embedding_config.get('model') or embedding_config.get('document_model')
    api_key = api_keys.get('siliconflow_api_key')

    if not api_key:
        print("\n❌ No SiliconFlow API key configured")
        return

    # Generate query embedding
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(
            "https://api.siliconflow.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "input": query
            },
            timeout=30.0
        )

    if response.status_code != 200:
        print(f"\n❌ Embedding API error: {response.status_code}")
        return

    query_embedding = response.json()['data'][0]['embedding']
    print(f"\n✅ Generated query embedding (dimension: {len(query_embedding)})")

    # Call match_documents RPC
    print(f"\nCalling match_documents RPC...")
    try:
        result = client_sb.rpc('match_documents', {
            'p_query_embedding': query_embedding,
            'p_agent_slug': agent['slug'],
            'p_match_threshold': 0.3,
            'p_match_count': 20
        }).execute()

        print(f"\n✅ RPC returned {len(result.data)} results")

        # Check if Divine Plan is in results
        found_divine = False
        for i, match in enumerate(result.data):
            title = match.get('title', '')
            if 'divine plan' in title.lower() and 'printer' in title.lower():
                found_divine = True
                print(f"\n{'='*100}")
                print(f"✅ SUCCESS! Divine Plan document FOUND in results!")
                print(f"{'='*100}")
                print(f"  Position: #{i+1} out of {len(result.data)}")
                print(f"  Title: {title}")
                print(f"  Chunk ID: {match.get('id')}")
                print(f"  Document ID: {match.get('document_id')}")
                print(f"  Similarity: {match.get('similarity')}")
                print(f"  Content preview: {match.get('content', '')[:200]}...")
                break

        if not found_divine:
            print(f"\n{'='*100}")
            print(f"❌ Divine Plan document NOT found in results")
            print(f"{'='*100}")
            print(f"\nTop 10 results:")
            for i, match in enumerate(result.data[:10]):
                print(f"  {i+1}. {match.get('title')} (similarity: {match.get('similarity'):.4f})")

            # Check if ANY chunk from Divine Plan appears
            divine_chunk_ids = [c['id'] for c in client_sb.table('document_chunks').select('id').eq('document_id', divine_doc['id']).execute().data]
            result_chunk_ids = [m.get('id') for m in result.data]

            matching_chunks = set(divine_chunk_ids) & set(result_chunk_ids)
            if matching_chunks:
                print(f"\n⚠️  Found {len(matching_chunks)} Divine Plan chunks in results, but they weren't ranked high enough")
            else:
                print(f"\n❌ No Divine Plan chunks found in any of the {len(result.data)} results")
                print(f"   This suggests either:")
                print(f"   1. The embeddings_vec migration didn't complete for Divine Plan chunks")
                print(f"   2. The content doesn't semantically match the query")
                print(f"   3. The similarity threshold is too high")

    except Exception as e:
        print(f"\n❌ RPC failed: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*100 + "\n")

if __name__ == '__main__':
    asyncio.run(main())
