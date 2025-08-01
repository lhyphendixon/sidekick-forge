#!/usr/bin/env python3
"""Direct test of context building with fixed code"""

import asyncio
import sys
import os
sys.path.append('/root/sidekick-forge/docker/agent')

from supabase import create_client
from context import AgentContextManager as ContextManager
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_context():
    # Test with real user ID and Clarence Coherence
    test_config = {
        "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
        "client_id": "11389177-e4d8-49a9-9a00-f77bb4de6592",
        "agent_config": {
            "slug": "clarence-coherence",
            "name": "Clarence Coherence",
            "system_prompt": "You are Clarence Coherence..."
        }
    }
    
    # Autonomite's Supabase credentials
    supabase_url = "https://yuowazxcxwhczywurmmw.supabase.co"
    supabase_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"
    
    logger.info("🚀 Testing context manager with real user ID...")
    
    # Create Supabase client
    supabase_client = create_client(supabase_url, supabase_key)
    
    # Create context manager
    context_manager = ContextManager(
        supabase_client=supabase_client,
        agent_config=test_config["agent_config"],
        user_id=test_config["user_id"],
        client_id=test_config["client_id"],
        api_keys={}
    )
    
    # Build context
    test_message = "Hello, can you tell me about Coherence Education?"
    context_result = await context_manager.build_complete_context(test_message)
    
    logger.info(f"✅ Context built successfully!")
    
    # Check what we actually got
    metadata = context_result.get('context_metadata', {})
    logger.info(f"   - User profile found: {metadata.get('user_profile_found', 'N/A')}")
    logger.info(f"   - Knowledge results: {metadata.get('knowledge_results_count', 'N/A')}")
    logger.info(f"   - Conversation results: {metadata.get('conversation_results_count', 'N/A')}")
    logger.info(f"   - Context length: {metadata.get('context_length', 'N/A')}")
    logger.info(f"   - Total prompt length: {metadata.get('total_prompt_length', 'N/A')}")
    
    # Show a sample of the enhanced prompt
    enhanced_prompt = context_result.get('enhanced_system_prompt', '')
    if enhanced_prompt:
        logger.info("\n📄 Enhanced prompt preview (first 500 chars):")
        logger.info(enhanced_prompt[:500] + "...")

if __name__ == "__main__":
    asyncio.run(test_context())