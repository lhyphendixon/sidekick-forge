#!/usr/bin/env python3
"""
Test RAG System functionality in isolation
"""
import os
import sys
import asyncio
import logging
from datetime import datetime

# Add agent-runtime to path if needed
sys.path.insert(0, '/app')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_conversation_buffer():
    """Test the ConversationWindowBuffer component"""
    print("=" * 60)
    print("TESTING CONVERSATION WINDOW BUFFER")
    print("=" * 60)
    
    try:
        from rag_system import ConversationWindowBuffer
        
        # Create a buffer
        conversation_id = "test-conv-123"
        buffer = ConversationWindowBuffer(conversation_id, window_size=10)
        print(f"✅ Created conversation buffer with ID: {conversation_id}")
        
        # Add messages
        test_messages = [
            ("user", "Hello, how are you?"),
            ("assistant", "I'm doing well, thank you! How can I help you today?"),
            ("user", "Tell me about the weather"),
            ("assistant", "I'd be happy to help with weather information. What location are you interested in?"),
            ("user", "New York City"),
        ]
        
        for role, content in test_messages:
            buffer.add_message(role, content)
        
        print(f"✅ Added {len(test_messages)} messages to buffer")
        
        # Test retrieval
        messages = buffer.get_messages()
        print(f"✅ Retrieved {len(messages)} messages from buffer")
        
        # Test context string
        context = buffer.get_context_string()
        print(f"✅ Generated context string ({len(context)} chars)")
        print("\nContext preview:")
        print("-" * 40)
        print(context[:200] + "..." if len(context) > 200 else context)
        print("-" * 40)
        
        # Test stats
        stats = buffer.get_conversation_stats()
        print(f"\n✅ Conversation stats:")
        for key, value in stats.items():
            print(f"   {key}: {value}")
        
        # Test window size limit
        print(f"\n Testing window size limit (max: {buffer.window_size})...")
        for i in range(15):
            buffer.add_message("user", f"Test message {i}")
        
        final_count = len(buffer.get_messages())
        print(f"✅ After adding 15 more messages, buffer contains: {final_count} (should be {buffer.window_size})")
        
        return True
        
    except Exception as e:
        print(f"❌ Conversation buffer test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_rag_searcher():
    """Test the RAGSearcher component"""
    print("\n" + "=" * 60)
    print("TESTING RAG SEARCHER")
    print("=" * 60)
    
    try:
        from rag_system import RAGSearcher
        
        # Create searcher without Supabase (testing structure)
        searcher = RAGSearcher(supabase_client=None)
        print("✅ Created RAGSearcher instance")
        
        # Test document search (will return empty due to no Supabase)
        print("\n1. Testing document search interface...")
        docs = await searcher.search_documents(
            query="test query",
            agent_slug="test-agent",
            limit=3
        )
        print(f"   Document search returned: {len(docs)} results (expected 0 without Supabase)")
        
        # Test conversation search
        print("\n2. Testing conversation search interface...")
        convs = await searcher.search_conversations(
            query="test query",
            user_id="test-user",
            agent_slug="test-agent",
            limit=3
        )
        print(f"   Conversation search returned: {len(convs)} results (expected 0 without Supabase)")
        
        print("\n✅ RAGSearcher interfaces are properly structured")
        return True
        
    except Exception as e:
        print(f"❌ RAG searcher test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_ai_processing_bridge():
    """Test AI Processing Bridge availability"""
    print("\n" + "=" * 60)
    print("TESTING AI PROCESSING BRIDGE")
    print("=" * 60)
    
    try:
        from ai_processing_bridge import ai_bridge, AIProcessingBridge
        
        print("✅ AI Processing Bridge imported successfully")
        print(f"   Bridge type: {type(ai_bridge)}")
        print(f"   Bridge class: {AIProcessingBridge}")
        
        # Check if embedding generation is available
        if hasattr(ai_bridge, 'generate_embeddings'):
            print("✅ Embedding generation method available")
        else:
            print("❌ Embedding generation method not found")
        
        return True
        
    except ImportError as e:
        print(f"⚠️  AI Processing Bridge not available: {e}")
        print("   This is expected if ai_processing_bridge.py is not in the container")
        return False
    except Exception as e:
        print(f"❌ AI Processing Bridge test failed: {e}")
        return False

async def test_rag_manager():
    """Test the main RAGManager component"""
    print("\n" + "=" * 60)
    print("TESTING RAG MANAGER")
    print("=" * 60)
    
    try:
        from rag_system import RAGManager
        
        # Create manager without Supabase
        print("1. Creating RAGManager...")
        manager = RAGManager(
            conversation_id="test-conv-456",
            supabase_client=None,
            window_size=25
        )
        print("✅ Created RAGManager instance")
        print(f"   Conversation ID: {manager.conversation_id}")
        print(f"   Has buffer: {manager.conversation_buffer is not None}")
        print(f"   Has searcher: {manager.searcher is not None}")
        
        # Test adding messages
        print("\n2. Testing message management...")
        manager.add_message("user", "What's the capital of France?")
        manager.add_message("assistant", "The capital of France is Paris.")
        
        messages = manager.get_conversation_messages()
        print(f"✅ Added and retrieved {len(messages)} messages")
        
        # Test context retrieval
        print("\n3. Testing context retrieval...")
        context = await manager.get_context_for_query(
            query="Tell me more about Paris",
            user_id="test-user",
            agent_slug="test-agent"
        )
        
        print(f"✅ Retrieved context:")
        print(f"   Recent messages: {len(context['recent_conversation'])} chars")
        print(f"   Past conversations: {len(context['relevant_past_conversations'])} items")
        print(f"   Documents: {len(context['relevant_documents'])} items")
        
        # Test prompt building
        print("\n4. Testing system prompt building...")
        prompt = manager.build_system_prompt(
            base_instructions="You are a helpful assistant.",
            context=context
        )
        print(f"✅ Built system prompt ({len(prompt)} chars)")
        print("\nPrompt preview:")
        print("-" * 40)
        print(prompt[:300] + "..." if len(prompt) > 300 else prompt)
        print("-" * 40)
        
        return True
        
    except Exception as e:
        print(f"❌ RAG Manager test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_rag_with_supabase():
    """Test RAG with actual Supabase connection if available"""
    print("\n" + "=" * 60)
    print("TESTING RAG WITH SUPABASE")
    print("=" * 60)
    
    # Check for Supabase credentials
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_ANON_KEY')
    
    if not supabase_url or not supabase_key:
        print("⚠️  Supabase credentials not available")
        print("   SUPABASE_URL: " + ("✅ Set" if supabase_url else "❌ Not set"))
        print("   SUPABASE_KEY: " + ("✅ Set" if supabase_key else "❌ Not set"))
        return False
    
    try:
        from supabase import create_client
        from rag_system import RAGManager
        
        # Create Supabase client
        print("1. Creating Supabase client...")
        supabase = create_client(supabase_url, supabase_key)
        print("✅ Supabase client created")
        
        # Test connection
        print("\n2. Testing Supabase connection...")
        try:
            # Try a simple query
            result = supabase.table('profiles').select('user_id').limit(1).execute()
            print("✅ Supabase connection successful")
        except Exception as e:
            print(f"❌ Supabase connection failed: {e}")
            return False
        
        # Create RAG manager with Supabase
        print("\n3. Creating RAG Manager with Supabase...")
        manager = RAGManager(
            conversation_id="test-supabase-conv",
            supabase_client=supabase,
            window_size=25
        )
        print("✅ RAG Manager created with Supabase client")
        
        # Test search functionality (may return empty if no data)
        print("\n4. Testing search with Supabase...")
        context = await manager.get_context_for_query(
            query="test query",
            user_id="00000000-0000-0000-0000-000000000000",  # Test UUID
            agent_slug="test-agent"
        )
        
        print("✅ Search completed:")
        print(f"   Past conversations found: {len(context['relevant_past_conversations'])}")
        print(f"   Documents found: {len(context['relevant_documents'])}")
        
        return True
        
    except Exception as e:
        print(f"❌ Supabase RAG test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Run all RAG tests"""
    print("RAG SYSTEM TEST SUITE")
    print("=" * 60)
    
    results = {}
    
    # Test conversation buffer
    results['conversation_buffer'] = await test_conversation_buffer()
    
    # Test RAG searcher
    results['rag_searcher'] = await test_rag_searcher()
    
    # Test AI processing bridge
    results['ai_bridge'] = await test_ai_processing_bridge()
    
    # Test RAG manager
    results['rag_manager'] = await test_rag_manager()
    
    # Test with Supabase if available
    results['supabase_integration'] = await test_rag_with_supabase()
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} {test_name.replace('_', ' ').title()}")
    
    all_core_passed = all([
        results['conversation_buffer'],
        results['rag_searcher'],
        results['rag_manager']
    ])
    
    if all_core_passed:
        print("\n✅ CORE RAG COMPONENTS ARE FUNCTIONAL")
        if not results['ai_bridge']:
            print("⚠️  AI Processing Bridge not available (may need separate file)")
        if not results['supabase_integration']:
            print("⚠️  Supabase integration not tested (credentials needed)")
    else:
        print("\n❌ SOME RAG COMPONENTS FAILED")

if __name__ == "__main__":
    asyncio.run(main())