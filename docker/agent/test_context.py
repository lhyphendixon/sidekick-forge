#!/usr/bin/env python3
"""
Test script for the Agent Context System
"""
import asyncio
import os
import sys
import logging
import json
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from context import AgentContextManager
from supabase import create_client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_context_system():
    """Test the context system with real data"""
    
    # Platform Supabase credentials (from environment)
    platform_url = os.getenv("SUPABASE_URL")
    platform_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    if not platform_url or not platform_key:
        logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variables")
        return
    
    try:
        # Connect to platform database
        platform_supabase = create_client(platform_url, platform_key)
        logger.info("Connected to platform Supabase")
        
        # Get a test client (Autonomite)
        client_result = platform_supabase.table("clients").select("*").eq("name", "Autonomite").single().execute()
        
        if not client_result.data:
            logger.error("No Autonomite client found")
            return
        
        client = client_result.data
        logger.info(f"Found client: {client['name']} (ID: {client['id']})")
        
        # Check if client has Supabase credentials
        if not client.get('supabase_url') or not client.get('supabase_anon_key'):
            logger.error("Client missing Supabase credentials")
            logger.info(f"Client data: {json.dumps(client, indent=2)}")
            return
        
        # Connect to client's Supabase
        client_supabase = create_client(client['supabase_url'], client['supabase_anon_key'])
        logger.info("Connected to client's Supabase")
        
        # Mock agent configuration
        agent_config = {
            "id": "test-agent",
            "agent_id": "test-agent",
            "system_prompt": "You are a helpful AI assistant specialized in technical support.",
            "name": "Test Agent"
        }
        
        # Create context manager
        context_manager = AgentContextManager(
            supabase_client=client_supabase,
            agent_config=agent_config,
            user_id="test-user-123",  # You may need to use a real user ID from the client's database
            client_id=client['id'],
            api_keys={}  # Would normally come from platform
        )
        
        logger.info("Created context manager")
        
        # Test building context
        test_queries = [
            "How do I set up webhooks?",
            "What are the API rate limits?",
            "Can you help me debug an authentication error?"
        ]
        
        for query in test_queries:
            logger.info(f"\n{'='*60}")
            logger.info(f"Testing query: {query}")
            logger.info(f"{'='*60}")
            
            # Build context
            result = await context_manager.build_complete_context(query)
            
            # Display results
            logger.info("\nContext Metadata:")
            logger.info(json.dumps(result["context_metadata"], indent=2))
            
            logger.info("\nEnhanced System Prompt Preview:")
            prompt_preview = result["enhanced_system_prompt"][:500] + "..." if len(result["enhanced_system_prompt"]) > 500 else result["enhanced_system_prompt"]
            logger.info(prompt_preview)
            
            # If in development mode, show full context
            if os.getenv("DEVELOPMENT_MODE", "false").lower() == "true":
                logger.info("\nFull Context Markdown:")
                logger.info(result["raw_context_data"].get("context_markdown", ""))
        
        logger.info("\n✅ Context system test completed successfully!")
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)


async def test_basic_functionality():
    """Test basic context manager functionality without real database"""
    
    logger.info("\n" + "="*60)
    logger.info("Testing basic context manager functionality")
    logger.info("="*60)
    
    # Mock Supabase client
    class MockSupabase:
        def table(self, name):
            return self
        
        def select(self, *args):
            return self
        
        def eq(self, field, value):
            return self
        
        def gte(self, field, value):
            return self
        
        def order(self, field, desc=False):
            return self
        
        def limit(self, n):
            return self
        
        def single(self):
            return self
        
        def execute(self):
            class Result:
                data = None
            return Result()
    
    # Create context manager with mock
    context_manager = AgentContextManager(
        supabase_client=MockSupabase(),
        agent_config={"system_prompt": "Test prompt"},
        user_id="test-user",
        client_id="test-client"
    )
    
    # Test markdown formatting
    test_profile = {
        "name": "John Doe",
        "email": "john@example.com",
        "tags": ["premium", "technical"],
        "goals": "Improve automation"
    }
    
    test_knowledge = [
        {
            "title": "API Guide",
            "excerpt": "This is how to use the API...",
            "relevance": 0.92,
            "document_id": "doc-1"
        }
    ]
    
    test_conversations = [
        {
            "user_message": "How do I set up webhooks?",
            "agent_response": "To set up webhooks, you need to...",
            "relevance": 0.85,
            "timestamp": datetime.now().isoformat()
        }
    ]
    
    # Test formatting
    markdown = context_manager._format_context_as_markdown(
        test_profile,
        test_knowledge,
        test_conversations
    )
    
    logger.info("\nFormatted Context Markdown:")
    logger.info(markdown)
    
    # Test prompt merging
    original = "You are a helpful assistant."
    enhanced = context_manager._merge_system_prompts(original, markdown)
    
    logger.info("\nEnhanced System Prompt:")
    logger.info(enhanced)
    
    logger.info("\n✅ Basic functionality test completed!")


if __name__ == "__main__":
    # Run tests
    logger.info("Starting context system tests...")
    
    # First test basic functionality
    asyncio.run(test_basic_functionality())
    
    # Then test with real database if available
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        asyncio.run(test_context_system())
    else:
        logger.warning("\nSkipping database tests - SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY not set")