#!/usr/bin/env python3
"""
Test script to diagnose why 'Divine Plan_Int_printer_052118 PRINTER' document
isn't being returned for the query 'Does divine intervention interfere with human free will'
"""
import os
import asyncio
import sys
from supabase import create_client
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add the app directory to the path
sys.path.insert(0, '/root/sidekick-forge')

async def main():
    """Test the RAG search for the divine intervention query"""

    # Load environment
    from dotenv import load_dotenv
    load_dotenv('/root/sidekick-forge/.env')

    # Get KCG client credentials
    # We need to find the KCG client ID first
    platform_url = os.getenv('SUPABASE_URL')
    platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

    logger.info(f"Connecting to platform Supabase: {platform_url}")
    platform_sb = create_client(platform_url, platform_key)

    # Find the KCG client
    clients_result = platform_sb.table('clients').select('*').execute()
    kcg_client = None
    for client in clients_result.data:
        name_lower = (client.get('name') or '').lower()
        if 'kimberly' in name_lower or 'carter-gamble' in name_lower or 'kcg' in name_lower:
            kcg_client = client
            break

    if not kcg_client:
        logger.error("Could not find KCG client in platform database")
        logger.info("Available clients:")
        for client in clients_result.data:
            logger.info(f"  - {client.get('name')}")
        return

    logger.info(f"Found client: {kcg_client.get('name')} (slug: {kcg_client.get('slug')})")

    # Get client Supabase credentials (stored directly on client record)
    client_supabase_url = kcg_client.get('supabase_url')
    client_supabase_key = kcg_client.get('supabase_service_role_key')

    if not client_supabase_url or not client_supabase_key:
        logger.error("Client Supabase credentials not found")
        return

    logger.info(f"Connecting to client Supabase: {client_supabase_url}")
    client_sb = create_client(client_supabase_url, client_supabase_key)

    # 1. Check if the document exists
    logger.info("\n=== Step 1: Checking if document exists ===")
    doc_search = client_sb.table('documents').select('*').ilike('title', '%Divine Plan%').execute()

    if doc_search.data:
        for doc in doc_search.data:
            logger.info(f"Found document:")
            logger.info(f"  ID: {doc.get('id')}")
            logger.info(f"  Title: {doc.get('title')}")
            logger.info(f"  Status: {doc.get('status')}")
            logger.info(f"  Chunk count: {doc.get('chunk_count')}")
            logger.info(f"  Agent permissions: {doc.get('agent_permissions')}")
            logger.info(f"  Has embeddings: {doc.get('embeddings') is not None or doc.get('embedding_vec') is not None}")
    else:
        logger.warning("Document 'Divine Plan_Int_printer_052118 PRINTER' not found!")
        logger.info("Listing all documents to find it:")
        all_docs = client_sb.table('documents').select('id, title, status').execute()
        for doc in all_docs.data[:20]:
            logger.info(f"  - {doc.get('title')} (status: {doc.get('status')})")

    # 2. Check document chunks
    if doc_search.data:
        doc_id = doc_search.data[0].get('id')
        logger.info(f"\n=== Step 2: Checking document chunks for doc_id={doc_id} ===")
        chunks = client_sb.table('document_chunks').select('*').eq('document_id', doc_id).execute()
        logger.info(f"Found {len(chunks.data)} chunks")
        if chunks.data:
            chunk = chunks.data[0]
            logger.info(f"Sample chunk:")
            logger.info(f"  ID: {chunk.get('id')}")
            logger.info(f"  Content preview: {chunk.get('content')[:200]}...")
            logger.info(f"  Has embeddings: {chunk.get('embeddings') is not None}")
            logger.info(f"  Has embeddings_vec: {chunk.get('embeddings_vec') is not None}")

            # Check the type of embeddings
            if chunk.get('embeddings'):
                emb = chunk.get('embeddings')
                logger.info(f"  Embeddings type: {type(emb)}")
                if isinstance(emb, list):
                    logger.info(f"  Embeddings dimension: {len(emb)}")

    # 3. Test the match_documents RPC
    logger.info("\n=== Step 3: Testing match_documents RPC ===")

    # We need to generate an embedding for the query
    # First, get the agent configuration to know which embedding provider to use
    agent_result = client_sb.table('agents').select('*').eq('slug', 'able').execute()
    if not agent_result.data:
        logger.error("Agent 'able' not found")
        return

    agent = agent_result.data[0]
    logger.info(f"Agent: {agent.get('name')}")

    # Get embedding config from agent settings
    settings = agent.get('settings') or {}
    embedding_config = settings.get('embedding') or {}
    logger.info(f"Embedding config: {embedding_config}")

    # For now, let's try a simple test with a dummy embedding
    logger.info("Testing with dummy embedding vector...")
    try:
        # Create a dummy 1024-dim vector
        import random
        dummy_embedding = [random.random() for _ in range(1024)]

        result = client_sb.rpc('match_documents', {
            'p_query_embedding': dummy_embedding,
            'p_agent_slug': 'able',
            'p_match_threshold': 0.0,  # Very low threshold to see any results
            'p_match_count': 10
        }).execute()

        logger.info(f"RPC returned {len(result.data)} results")
        for i, match in enumerate(result.data[:5]):
            logger.info(f"  Match {i+1}:")
            logger.info(f"    Title: {match.get('title')}")
            logger.info(f"    Similarity: {match.get('similarity')}")
            logger.info(f"    Content preview: {match.get('content')[:100]}...")
    except Exception as e:
        logger.error(f"RPC call failed: {e}")
        import traceback
        traceback.print_exc()

    # 4. Generate real embedding and test
    logger.info("\n=== Step 4: Testing with real embedding ===")
    query = "Does divine intervention interfere with human free will"

    # Try to use the embedder
    embedding_provider = embedding_config.get('provider')
    embedding_model = embedding_config.get('model') or embedding_config.get('document_model')

    if embedding_provider and embedding_model:
        logger.info(f"Using {embedding_provider} with model {embedding_model}")

        # Get API key from agent settings
        api_keys = agent.get('settings', {}).get('api_keys', {})

        if embedding_provider == 'siliconflow':
            api_key = api_keys.get('siliconflow_api_key')
            if api_key:
                import httpx
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": embedding_model,
                    "input": query
                }

                try:
                    async with httpx.AsyncClient() as http_client:
                        response = await http_client.post(
                            "https://api.siliconflow.com/v1/embeddings",
                            headers=headers,
                            json=data,
                            timeout=30.0
                        )

                    if response.status_code == 200:
                        result = response.json()
                        query_embedding = result['data'][0]['embedding']
                        logger.info(f"Generated embedding with dimension: {len(query_embedding)}")

                        # Now test the RPC with real embedding
                        rpc_result = client_sb.rpc('match_documents', {
                            'p_query_embedding': query_embedding,
                            'p_agent_slug': 'able',
                            'p_match_threshold': 0.4,
                            'p_match_count': 10
                        }).execute()

                        logger.info(f"\n=== RESULTS for query: '{query}' ===")
                        logger.info(f"Found {len(rpc_result.data)} matching chunks")

                        for i, match in enumerate(rpc_result.data):
                            logger.info(f"\n  Result {i+1}:")
                            logger.info(f"    Document ID: {match.get('id')}")
                            logger.info(f"    Title: {match.get('title')}")
                            logger.info(f"    Similarity: {match.get('similarity'):.4f}")
                            logger.info(f"    Content preview: {match.get('content')[:200]}...")

                            # Check if this is the Divine Plan document
                            if 'divine plan' in match.get('title', '').lower():
                                logger.info("    ✅ FOUND THE DIVINE PLAN DOCUMENT!")
                    else:
                        logger.error(f"Embedding API returned {response.status_code}: {response.text}")
                except Exception as e:
                    logger.error(f"Failed to generate embedding: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                logger.error("SiliconFlow API key not found")
        else:
            logger.warning(f"Embedding provider {embedding_provider} not implemented in test script")
    else:
        logger.error("Embedding configuration incomplete")

if __name__ == '__main__':
    asyncio.run(main())
