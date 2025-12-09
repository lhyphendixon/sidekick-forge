#!/usr/bin/env python3
"""
Debug why Divine Plan document isn't appearing in RAG even though it's assigned
"""
import os
import asyncio
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

async def main():
    platform_sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY'))
    clients = platform_sb.table('clients').select('*').execute()
    cc_client = next((c for c in clients.data if 'caroline' in (c.get('name') or '').lower()), None)
    client_sb = create_client(cc_client.get('supabase_url'), cc_client.get('supabase_service_role_key'))

    print("="*100)
    print("DEBUG: Why Divine Plan document isn't appearing in RAG")
    print("="*100)

    # Get agent
    agent = client_sb.table('agents').select('*').execute().data[0]
    print(f"\nAgent: {agent.get('name')} (ID: {agent.get('id')})")

    # Get Divine Plan document
    divine = client_sb.table('documents').select('*').ilike('title', '%Divine Plan%printer%').execute()
    if not divine.data:
        print("❌ Divine Plan document not found!")
        return

    doc = divine.data[0]
    print(f"\nDocument: {doc.get('title')}")
    print(f"Document ID: {doc.get('id')}")
    print(f"Status: {doc.get('status')}")
    print(f"Chunk count: {doc.get('chunk_count')}")

    # Check agent_documents assignment
    assignment = client_sb.table('agent_documents').select('*').eq('agent_id', agent.get('id')).eq('document_id', doc.get('id')).execute()

    print(f"\nAgent Assignment Check:")
    if assignment.data:
        print(f"  ✅ Document IS in agent_documents table")
        print(f"  Enabled: {assignment.data[0].get('enabled')}")
        print(f"  Access type: {assignment.data[0].get('access_type')}")
    else:
        print(f"  ❌ Document NOT in agent_documents table")
        return

    # Check document chunks
    chunks = client_sb.table('document_chunks').select('*').eq('document_id', doc.get('id')).limit(5).execute()
    print(f"\nChunks:")
    print(f"  Total chunks: {doc.get('chunk_count')}")
    print(f"  Chunks retrieved: {len(chunks.data)}")

    if chunks.data:
        chunk = chunks.data[0]
        print(f"\n  Sample chunk:")
        print(f"    ID: {chunk.get('id')}")
        print(f"    Content length: {len(chunk.get('content', ''))}")
        print(f"    Has embeddings: {chunk.get('embeddings') is not None}")
        print(f"    Has embeddings_vec: {chunk.get('embeddings_vec') is not None}")

        if chunk.get('embeddings'):
            emb = chunk.get('embeddings')
            print(f"    Embeddings type: {type(emb).__name__}")
            if isinstance(emb, str):
                print(f"    ⚠️  Embeddings are stored as JSON string!")
                print(f"    String length: {len(emb)}")

    # Test the match_documents RPC with a real query
    print(f"\n{'='*100}")
    print("Testing match_documents RPC:")
    print("="*100)

    # Get embedding config
    settings = agent.get('settings') or {}
    embedding_config = settings.get('embedding') or {}

    print(f"\nEmbedding config: {embedding_config}")

    if not embedding_config.get('provider'):
        print("❌ No embedding provider configured!")
        return

    # Generate embedding for test query
    query = "Does divine intervention interfere with human free will"
    print(f"\nTest query: '{query}'")

    provider = embedding_config.get('provider')
    model = embedding_config.get('model') or embedding_config.get('document_model')
    api_keys = settings.get('api_keys') or {}

    if provider == 'siliconflow':
        api_key = api_keys.get('siliconflow_api_key')
        if not api_key:
            print("❌ No SiliconFlow API key!")
            return

        import httpx
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
            print(f"❌ Embedding API error: {response.status_code}")
            return

        query_embedding = response.json()['data'][0]['embedding']
        print(f"✅ Generated embedding (dimension: {len(query_embedding)})")

        # Call match_documents RPC
        print(f"\nCalling match_documents RPC...")
        try:
            result = client_sb.rpc('match_documents', {
                'p_query_embedding': query_embedding,
                'p_agent_slug': agent.get('slug'),
                'p_match_threshold': 0.3,
                'p_match_count': 20
            }).execute()

            print(f"✅ RPC returned {len(result.data)} results")

            # Check if Divine Plan is in results
            found_divine = False
            for i, match in enumerate(result.data):
                title = match.get('title', '')
                if 'divine plan' in title.lower() and 'printer' in title.lower():
                    found_divine = True
                    print(f"\n✅ FOUND Divine Plan document in results!")
                    print(f"  Position: #{i+1}")
                    print(f"  Similarity: {match.get('similarity')}")
                    break

            if not found_divine:
                print(f"\n❌ Divine Plan document NOT in results!")
                print(f"\nTop 5 results returned:")
                for i, match in enumerate(result.data[:5]):
                    print(f"  {i+1}. {match.get('title')} (similarity: {match.get('similarity')})")

                # Check if any chunk from Divine Plan doc appears
                print(f"\nChecking if RPC is filtering out Divine Plan chunks...")

        except Exception as e:
            print(f"❌ RPC failed: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "="*100 + "\n")

if __name__ == '__main__':
    asyncio.run(main())
