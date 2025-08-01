#!/usr/bin/env python3
"""
Comprehensive RAG System Test
Tests all RAG search strategies in the context system
"""
import asyncio
import os
import json
import sys
from datetime import datetime
from typing import Dict, Any, List
import logging

# Add the agent directory to the path
sys.path.insert(0, '/root/sidekick-forge/docker/agent')

from supabase import create_client
from context import AgentContextManager
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

async def test_rag_system(user_id: str, agent_slug: str, client_id: str):
    """Test all RAG search functionalities"""
    
    # First, get client credentials from platform database
    platform_url = os.getenv('SUPABASE_URL')
    platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    platform_supabase = create_client(platform_url, platform_key)
    
    # Get client settings
    client_result = platform_supabase.table('clients').select('*').eq('id', client_id).execute()
    if not client_result.data:
        logger.error(f"Client not found: {client_id}")
        return
    
    client_data = client_result.data[0]
    
    # Get Supabase credentials directly from client record
    client_url = client_data.get('supabase_url')
    client_service_key = client_data.get('supabase_service_role_key')
    
    if not client_url or not client_service_key:
        logger.error("Missing Supabase credentials")
        return
    
    logger.info(f"Connecting to client database: {client_url[:50]}...")
    
    # Create client Supabase connection
    client_supabase = create_client(client_url, client_service_key)
    
    # Get agent configuration
    agent_result = client_supabase.table('agents').select('*').eq('slug', agent_slug).execute()
    if not agent_result.data:
        logger.error(f"Agent not found: {agent_slug}")
        return
    
    agent_config = agent_result.data[0]
    logger.info(f"Found agent: {agent_config['name']} (ID: {agent_config['id']})")
    
    # Create context manager
    context_manager = AgentContextManager(
        supabase_client=client_supabase,
        agent_config=agent_config,
        user_id=user_id,
        client_id=client_id,
        api_keys={}  # API keys would come from client_data in real scenario
    )
    
    # Test queries
    test_queries = [
        "Tell me about my setup",
        "What are my goals?",
        "How do I configure webhooks?",
        "What did we discuss last time?",
        "Show me documentation about integrations"
    ]
    
    print("\n" + "="*80)
    print("RAG SYSTEM COMPREHENSIVE TEST")
    print("="*80)
    print(f"User ID: {user_id}")
    print(f"Agent: {agent_config['name']} ({agent_slug})")
    print(f"Client: {client_id}")
    print("="*80 + "\n")
    
    # Test schema detection
    print("\n🔍 SCHEMA DETECTION TEST")
    print("-" * 40)
    print(f"Has agent_documents table: {context_manager.has_agent_documents_table}")
    print(f"Has documents with agent_slug: {context_manager.has_documents_with_agent_slug}")
    print(f"Has document_chunks table: {getattr(context_manager, 'has_document_chunks_table', False)}")
    print(f"Has chunk embeddings: {getattr(context_manager, 'has_chunk_embeddings', False)}")
    if getattr(context_manager, 'has_chunk_embeddings', False):
        print(f"Embedding dimension: {getattr(context_manager, 'embedding_dimension', 'Unknown')}")
    print(f"Has match_documents function: {context_manager.has_match_documents_function}")
    print(f"Conversation foreign key: {context_manager.conversation_fkey_suffix}")
    
    # Test each query
    for i, query in enumerate(test_queries, 1):
        print(f"\n\n{'='*80}")
        print(f"TEST {i}: {query}")
        print("="*80)
        
        try:
            # Build complete context
            result = await context_manager.build_complete_context(query)
            
            # Display metadata
            metadata = result['context_metadata']
            print("\n📊 CONTEXT METADATA:")
            print(f"  - Duration: {metadata.get('duration_seconds', 'N/A')}s")
            print(f"  - User profile found: {metadata.get('user_profile_found', False)}")
            print(f"  - Knowledge results: {metadata.get('knowledge_results_count', 0)}")
            print(f"  - Conversation results: {metadata.get('conversation_results_count', 0)}")
            print(f"  - Context length: {metadata.get('context_length', 0)} chars")
            print(f"  - Total prompt length: {metadata.get('total_prompt_length', 0)} chars")
            
            # Display raw context data
            raw_data = result['raw_context_data']
            
            # User Profile
            if raw_data.get('user_profile'):
                print("\n👤 USER PROFILE:")
                profile = raw_data['user_profile']
                print(f"  - Name: {profile.get('name', profile.get('full_name', 'Unknown'))}")
                print(f"  - Email: {profile.get('email', 'N/A')}")
                print(f"  - Tags: {profile.get('tags', profile.get('Tags', []))}")
                print(f"  - Goals: {profile.get('goals', 'N/A')}")
                print(f"  - Preferences: {profile.get('preferences', 'N/A')}")
            
            # Knowledge Results
            if raw_data.get('knowledge_results'):
                print(f"\n📚 KNOWLEDGE RESULTS ({len(raw_data['knowledge_results'])} found):")
                for j, doc in enumerate(raw_data['knowledge_results'][:3], 1):
                    print(f"\n  Document {j}:")
                    print(f"    - Title: {doc.get('title', 'Untitled')}")
                    print(f"    - Relevance: {doc.get('relevance', 0)}")
                    print(f"    - Excerpt: {doc.get('excerpt', '')[:200]}...")
            
            # Conversation Results
            if raw_data.get('conversation_results'):
                print(f"\n💬 CONVERSATION RESULTS ({len(raw_data['conversation_results'])} found):")
                for j, conv in enumerate(raw_data['conversation_results'][:2], 1):
                    print(f"\n  Conversation {j}:")
                    print(f"    - User: {conv.get('user_message', '')[:100]}...")
                    print(f"    - Agent: {conv.get('agent_response', '')[:100]}...")
                    print(f"    - Relevance: {conv.get('relevance', 0)}")
                    print(f"    - Timestamp: {conv.get('timestamp', 'N/A')}")
            
            # Show a snippet of the enhanced prompt
            print("\n📝 ENHANCED PROMPT PREVIEW:")
            print("..." + result['enhanced_system_prompt'][-500:])
            
        except Exception as e:
            print(f"\n❌ ERROR: {str(e)}")
            logger.error(f"Test failed for query '{query}': {e}", exc_info=True)
    
    print("\n\n" + "="*80)
    print("TEST COMPLETE")
    print("="*80)

async def main():
    # Test parameters
    USER_ID = "351bb07b-03fc-4fb4-b09b-748ef8a72084"  # Your correct user ID (l-dixon@autonomite.net)
    AGENT_SLUG = "clarence-coherence"
    CLIENT_ID = "11389177-e4d8-49a9-9a00-f77bb4de6592"  # Autonomite client
    
    await test_rag_system(USER_ID, AGENT_SLUG, CLIENT_ID)

if __name__ == "__main__":
    asyncio.run(main())