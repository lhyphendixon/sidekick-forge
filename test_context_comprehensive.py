#!/usr/bin/env python3
"""Comprehensive test of context building with multiple scenarios"""

import asyncio
import sys
import os
sys.path.append('/root/sidekick-forge/docker/agent')

from supabase import create_client
from context import AgentContextManager as ContextManager
import logging
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_context_scenarios():
    """Test various context building scenarios"""
    
    # Test configurations for different scenarios
    test_scenarios = [
        {
            "name": "User with profile and documents",
            "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
            "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592",
            "agent_config": {
                "slug": "clarence-coherence",
                "name": "Clarence Coherence",
                "system_prompt": "You are Clarence Coherence, an AI assistant."
            },
            "test_message": "Tell me about Coherence Education and learning",
            "expected": {
                "has_profile": True,
                "has_knowledge": True,
                "has_conversations": True
            }
        },
        {
            "name": "User without agent documents",
            "user_id": "test-user-no-docs",
            "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592",
            "agent_config": {
                "slug": "non-existent-agent",
                "name": "Test Agent",
                "system_prompt": "You are a test agent."
            },
            "test_message": "What can you help me with?",
            "expected": {
                "has_profile": False,
                "has_knowledge": False,
                "has_conversations": False
            }
        }
    ]
    
    # Autonomite's Supabase credentials
    supabase_url = "https://yuowazxcxwhczywurmmw.supabase.co"
    supabase_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"
    
    # Create Supabase client
    supabase_client = create_client(supabase_url, supabase_key)
    
    # Run tests
    for scenario in test_scenarios:
        logger.info(f"\n{'='*60}")
        logger.info(f"🧪 Testing: {scenario['name']}")
        logger.info(f"{'='*60}")
        
        try:
            # Create context manager
            context_manager = ContextManager(
                supabase_client=supabase_client,
                agent_config=scenario["agent_config"],
                user_id=scenario["user_id"],
                client_id=scenario["client_id"],
                api_keys={}
            )
            
            # Show detected schema
            logger.info(f"📊 Schema detection results:")
            logger.info(f"   - agent_documents table: {context_manager.has_agent_documents_table}")
            logger.info(f"   - documents with agent_slug: {context_manager.has_documents_with_agent_slug}")
            logger.info(f"   - match_documents function: {context_manager.has_match_documents_function}")
            logger.info(f"   - conversation foreign key: {context_manager.conversation_fkey_suffix}")
            
            # Build context
            context_result = await context_manager.build_complete_context(scenario["test_message"])
            
            metadata = context_result.get('context_metadata', {})
            
            # Verify results
            logger.info(f"\n✅ Context built successfully!")
            logger.info(f"   - User profile found: {metadata.get('user_profile_found')} (expected: {scenario['expected']['has_profile']})")
            logger.info(f"   - Knowledge results: {metadata.get('knowledge_results_count')} (expected: {'> 0' if scenario['expected']['has_knowledge'] else '0'})")
            logger.info(f"   - Conversation results: {metadata.get('conversation_results_count')} (expected: {'> 0' if scenario['expected']['has_conversations'] else '0'})")
            logger.info(f"   - Context length: {metadata.get('context_length')} chars")
            logger.info(f"   - Total prompt length: {metadata.get('total_prompt_length')} chars")
            
            # Show raw context data
            raw_data = context_result.get('raw_context_data', {})
            if raw_data.get('user_profile'):
                logger.info(f"\n📋 User Profile:")
                profile = raw_data['user_profile']
                logger.info(f"   - Name: {profile.get('name', 'N/A')}")
                logger.info(f"   - Email: {profile.get('email', 'N/A')}")
                
            if raw_data.get('knowledge_results'):
                logger.info(f"\n📚 Knowledge Results:")
                for i, kb in enumerate(raw_data['knowledge_results'][:2]):
                    logger.info(f"   {i+1}. {kb.get('title', 'Untitled')} (relevance: {kb.get('relevance', 0)})")
                    
            if raw_data.get('conversation_results'):
                logger.info(f"\n💬 Conversation Results:")
                for i, conv in enumerate(raw_data['conversation_results'][:2]):
                    logger.info(f"   {i+1}. User: {conv.get('user_message', '')[:50]}...")
                    
        except Exception as e:
            logger.error(f"❌ Test failed: {e}", exc_info=True)
            
    logger.info(f"\n{'='*60}")
    logger.info("🎉 All tests completed!")

if __name__ == "__main__":
    asyncio.run(test_context_scenarios())