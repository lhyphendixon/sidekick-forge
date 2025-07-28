#!/usr/bin/env python3
"""
Test the context system integration
"""
import os
import sys
import json
import asyncio
from supabase import create_client

# Add agent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'docker/agent'))

from context import AgentContextManager


async def test_context_with_real_data():
    """Test context system with real Supabase data"""
    
    # Platform credentials
    platform_url = os.getenv("SUPABASE_URL", "https://eukudpgfpihxsypulopm.supabase.co")
    platform_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    if not platform_key:
        print("❌ Missing SUPABASE_SERVICE_ROLE_KEY - cannot test with real data")
        return
    
    try:
        # Connect to platform
        platform_supabase = create_client(platform_url, platform_key)
        print(f"✅ Connected to platform Supabase")
        
        # Get Autonomite client
        result = platform_supabase.table("clients").select("*").eq("name", "Autonomite").single().execute()
        
        if not result.data:
            print("❌ No Autonomite client found")
            return
            
        client = result.data
        print(f"✅ Found client: {client['name']} (ID: {client['id']})")
        
        # Check for Supabase credentials in additional_settings
        additional_settings = client.get('additional_settings', {})
        if isinstance(additional_settings, str):
            additional_settings = json.loads(additional_settings)
            
        supabase_url = client.get('supabase_url') or additional_settings.get('supabase_url')
        supabase_anon_key = client.get('supabase_anon_key') or additional_settings.get('supabase_anon_key')
        
        if not supabase_url or not supabase_anon_key:
            print("❌ Client missing Supabase credentials")
            print(f"   URL: {supabase_url}")
            print(f"   Key: {'Present' if supabase_anon_key else 'Missing'}")
            return
            
        # Connect to client's Supabase
        client_supabase = create_client(supabase_url, supabase_anon_key)
        print("✅ Connected to client's Supabase")
        
        # Mock agent config
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
            user_id="test-user-123",
            client_id=client['id']
        )
        print("✅ Created context manager")
        
        # Test building context
        print("\n" + "="*60)
        print("Testing context building...")
        print("="*60)
        
        result = await context_manager.build_complete_context("How do I set up webhooks?")
        
        print("\n📊 Context Metadata:")
        print(json.dumps(result["context_metadata"], indent=2))
        
        print("\n📝 Enhanced System Prompt (first 500 chars):")
        prompt = result["enhanced_system_prompt"]
        print(prompt[:500] + "..." if len(prompt) > 500 else prompt)
        
        if result["raw_context_data"].get("context_markdown"):
            print("\n📄 Generated Context Markdown:")
            print(result["raw_context_data"]["context_markdown"])
            
        print("\n✅ Context system test completed successfully!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()


async def test_basic_formatting():
    """Test basic context formatting without database"""
    
    print("\n" + "="*60)
    print("Testing basic context formatting...")
    print("="*60)
    
    # Mock Supabase
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
    
    # Create context manager
    context_manager = AgentContextManager(
        supabase_client=MockSupabase(),
        agent_config={"system_prompt": "You are a helpful assistant."},
        user_id="test-user",
        client_id="test-client"
    )
    
    # Test markdown formatting
    test_profile = {
        "name": "John Doe",
        "email": "john@example.com",
        "tags": ["premium", "technical"],
        "goals": "Improve workflow automation"
    }
    
    test_knowledge = [
        {
            "title": "API Integration Guide",
            "excerpt": "To use our API, you need to authenticate using OAuth 2.0...",
            "relevance": 0.92,
            "document_id": "doc-123"
        },
        {
            "title": "Webhook Setup",
            "excerpt": "Webhooks allow you to receive real-time notifications...",
            "relevance": 0.87,
            "document_id": "doc-456"
        }
    ]
    
    test_conversations = [
        {
            "user_message": "How do I authenticate?",
            "agent_response": "You can authenticate using API keys or OAuth tokens.",
            "relevance": 0.85,
            "timestamp": "2024-01-15T10:30:00Z"
        }
    ]
    
    # Generate markdown
    markdown = context_manager._format_context_as_markdown(
        test_profile,
        test_knowledge,
        test_conversations
    )
    
    print("\n📄 Formatted Context Markdown:")
    print(markdown)
    
    # Test prompt merging
    original = "You are a helpful AI assistant."
    enhanced = context_manager._merge_system_prompts(original, markdown)
    
    print("\n📝 Enhanced System Prompt:")
    print(enhanced)
    
    print("\n✅ Basic formatting test completed!")


if __name__ == "__main__":
    print("🚀 Starting context system integration tests...")
    
    # Run basic formatting test first
    asyncio.run(test_basic_formatting())
    
    # Then try real data test if credentials available
    if os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        asyncio.run(test_context_with_real_data())
    else:
        print("\n⚠️ Skipping real data test - set SUPABASE_SERVICE_ROLE_KEY to test with real data")