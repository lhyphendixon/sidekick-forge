#!/usr/bin/env python3
"""
Investigate why Caroline Cory's last text chat about divine intervention
didn't return the Divine Plan document
"""
import os
import asyncio
import sys
from supabase import create_client
import logging
import json
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sys.path.insert(0, '/root/sidekick-forge')

async def main():
    """Investigate the Caroline Cory chat issue"""

    from dotenv import load_dotenv
    load_dotenv('/root/sidekick-forge/.env')

    platform_url = os.getenv('SUPABASE_URL')
    platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

    logger.info(f"Connecting to platform Supabase")
    platform_sb = create_client(platform_url, platform_key)

    # Find Caroline Cory client
    clients_result = platform_sb.table('clients').select('*').execute()
    cc_client = None
    for client in clients_result.data:
        name_lower = (client.get('name') or '').lower()
        if 'caroline' in name_lower and 'cory' in name_lower:
            cc_client = client
            break

    if not cc_client:
        logger.error("Could not find Caroline Cory client")
        return

    logger.info(f"Found client: {cc_client.get('name')}")

    # Get client Supabase credentials
    client_supabase_url = cc_client.get('supabase_url')
    client_supabase_key = cc_client.get('supabase_service_role_key')

    if not client_supabase_url or not client_supabase_key:
        logger.error("Client Supabase credentials not found")
        return

    logger.info(f"Connecting to Caroline Cory Supabase")
    client_sb = create_client(client_supabase_url, client_supabase_key)

    # Step 1: Find the most recent text chat conversation
    logger.info("\n=== Step 1: Searching for divine intervention query in conversations ===")
    try:
        # Search for conversations containing "divine" - the DB has an issue with ordering/mode filtering
        # So let's just search all recent conversations
        transcripts = client_sb.table('conversation_transcripts').select('*').limit(100).execute()

        logger.info(f"Found {len(transcripts.data)} total conversations")

        # Filter for text mode and divine-related queries
        text_convos = []
        for t in transcripts.data:
            if t.get('mode') == 'text':
                text_convos.append(t)

        logger.info(f"Filtered to {len(text_convos)} text conversations")

        # Sort by created_at manually
        text_convos.sort(key=lambda x: x.get('created_at') or '', reverse=True)

        # Look for divine intervention query
        target_conversation = None
        for i, t in enumerate(text_convos[:20]):
            query_lower = (t.get('query') or '').lower()
            if 'divine' in query_lower:
                logger.info(f"\nConversation {i+1} with 'divine':")
                logger.info(f"  Created: {t.get('created_at')}")
                logger.info(f"  Query: {t.get('query')}")

                if 'intervention' in query_lower or 'free will' in query_lower:
                    logger.info(f"  ✅ FOUND THE DIVINE INTERVENTION QUERY!")
                    target_conversation = t

                    # Check citations
                    citations = t.get('citations') or []
                    logger.info(f"  Citations returned: {len(citations)}")
                    if citations:
                        for j, cite in enumerate(citations):
                            logger.info(f"    Citation {j+1}: {cite.get('title')} (similarity: {cite.get('similarity')})")
                    else:
                        logger.warning("    ❌ NO CITATIONS RETURNED!")
                    break

        if not target_conversation:
            logger.warning("Could not find the divine intervention query in recent conversations")
    except Exception as e:
        logger.error(f"Error fetching conversations: {e}")
        import traceback
        traceback.print_exc()
        return

    # Step 2: Verify the Divine Plan document exists and is assigned to the agent
    logger.info("\n=== Step 2: Checking Divine Plan document ===")
    divine_doc = client_sb.table('documents').select('*').ilike('title', '%Divine Plan%printer%').execute()

    if divine_doc.data:
        doc = divine_doc.data[0]
        logger.info(f"Found document:")
        logger.info(f"  ID: {doc.get('id')}")
        logger.info(f"  Title: {doc.get('title')}")
        logger.info(f"  Status: {doc.get('status')}")
        logger.info(f"  Chunk count: {doc.get('chunk_count')}")
        logger.info(f"  Agent permissions: {doc.get('agent_permissions')}")

        # Check if document has embeddings
        has_embeddings = doc.get('embeddings') is not None or doc.get('embedding_vec') is not None
        logger.info(f"  Has document embeddings: {has_embeddings}")

        doc_id = doc.get('id')

        # Check chunks
        chunks = client_sb.table('document_chunks').select('*').eq('document_id', doc_id).execute()
        logger.info(f"  Total chunks: {len(chunks.data)}")

        chunks_with_embeddings = 0
        for chunk in chunks.data:
            if chunk.get('embeddings') is not None or chunk.get('embeddings_vec') is not None:
                chunks_with_embeddings += 1

        logger.info(f"  Chunks with embeddings: {chunks_with_embeddings}")

        if chunks_with_embeddings == 0:
            logger.error("  ❌ NO CHUNKS HAVE EMBEDDINGS! This is why the document isn't being returned.")

        # Sample a chunk to check embedding format
        if chunks.data:
            sample_chunk = chunks.data[0]
            logger.info(f"\n  Sample chunk analysis:")
            logger.info(f"    Chunk ID: {sample_chunk.get('id')}")
            logger.info(f"    Content preview: {sample_chunk.get('content')[:150]}...")

            # Check embeddings field
            if sample_chunk.get('embeddings'):
                emb = sample_chunk.get('embeddings')
                logger.info(f"    embeddings field type: {type(emb)}")
                if isinstance(emb, list):
                    logger.info(f"    embeddings dimension: {len(emb)}")
                elif isinstance(emb, str):
                    logger.info(f"    embeddings is string (probably JSON): {emb[:100]}...")
            else:
                logger.info(f"    embeddings field: None")

            # Check embeddings_vec field
            if sample_chunk.get('embeddings_vec'):
                logger.info(f"    embeddings_vec field: Present")
            else:
                logger.info(f"    embeddings_vec field: None")

        # Step 3: Check agent configuration
        logger.info("\n=== Step 3: Checking agent configuration ===")

        # Get the agent that should be using this document
        agents = client_sb.table('agents').select('*').execute()
        logger.info(f"Found {len(agents.data)} agents")

        for agent in agents.data:
            agent_perms = doc.get('agent_permissions') or []
            if agent.get('slug') in agent_perms:
                logger.info(f"\nAgent with access: {agent.get('name')} (slug: {agent.get('slug')})")

                # Check embedding configuration
                settings = agent.get('settings') or {}
                embedding_config = settings.get('embedding') or {}
                logger.info(f"  Embedding config: {json.dumps(embedding_config, indent=4)}")

                # Check if agent has API keys
                api_keys = settings.get('api_keys') or {}
                has_embedding_key = False
                if embedding_config.get('provider') == 'siliconflow':
                    has_embedding_key = 'siliconflow_api_key' in api_keys
                elif embedding_config.get('provider') == 'openai':
                    has_embedding_key = 'openai_api_key' in api_keys

                logger.info(f"  Has embedding API key: {has_embedding_key}")

        # Step 4: Test the match_documents RPC with the actual query
        logger.info("\n=== Step 4: Testing match_documents RPC ===")

        query = "Does divine intervention interfere with human free will"

        # Find the agent (assume it's the main one)
        main_agent = None
        for agent in agents.data:
            if agent.get('slug') in (doc.get('agent_permissions') or []):
                main_agent = agent
                break

        if not main_agent:
            logger.warning("Could not find agent with access to Divine Plan document")
            if agents.data:
                main_agent = agents.data[0]
                logger.info(f"Using first agent: {main_agent.get('name')}")

        if main_agent:
            agent_slug = main_agent.get('slug')
            settings = main_agent.get('settings') or {}
            embedding_config = settings.get('embedding') or {}
            embedding_provider = embedding_config.get('provider')
            embedding_model = embedding_config.get('model') or embedding_config.get('document_model')

            if embedding_provider and embedding_model:
                logger.info(f"Generating embedding using {embedding_provider}/{embedding_model}")

                api_keys = settings.get('api_keys') or {}

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

                                # Test match_documents
                                rpc_result = client_sb.rpc('match_documents', {
                                    'p_query_embedding': query_embedding,
                                    'p_agent_slug': agent_slug,
                                    'p_match_threshold': 0.4,
                                    'p_match_count': 10
                                }).execute()

                                logger.info(f"\n=== RPC Results for query: '{query}' ===")
                                logger.info(f"Returned {len(rpc_result.data)} chunks")

                                found_divine_plan = False
                                for i, match in enumerate(rpc_result.data):
                                    logger.info(f"\n  Result {i+1}:")
                                    logger.info(f"    Title: {match.get('title')}")
                                    logger.info(f"    Similarity: {match.get('similarity'):.4f}")
                                    logger.info(f"    Content preview: {match.get('content')[:150]}...")

                                    if 'divine plan' in match.get('title', '').lower():
                                        found_divine_plan = True
                                        logger.info("    ✅ FOUND DIVINE PLAN DOCUMENT!")

                                if not found_divine_plan:
                                    logger.error("\n❌ Divine Plan document NOT in results!")
                                    logger.info("This suggests either:")
                                    logger.info("  1. The chunks don't have embeddings")
                                    logger.info("  2. The similarity scores are too low")
                                    logger.info("  3. The agent_permissions filtering is excluding it")
                            else:
                                logger.error(f"Embedding API error: {response.status_code}")
                        except Exception as e:
                            logger.error(f"Failed to test: {e}")
                            import traceback
                            traceback.print_exc()
                    else:
                        logger.error("No SiliconFlow API key found")
            else:
                logger.error("Embedding configuration incomplete")

    else:
        logger.error("Divine Plan document not found!")

if __name__ == '__main__':
    asyncio.run(main())
