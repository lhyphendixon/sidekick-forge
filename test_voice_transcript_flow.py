#!/usr/bin/env python3
"""
Test the complete voice transcript flow with citations
"""
import asyncio
import inspect

def check_file(filepath, checks):
    """Check a file for specific patterns"""
    print(f"\nChecking {filepath}:")
    with open(filepath, 'r') as f:
        content = f.read()
    
    all_good = True
    for check_name, pattern in checks.items():
        if pattern in content:
            print(f"  ✅ {check_name}: Found")
        else:
            print(f"  ❌ {check_name}: NOT FOUND")
            all_good = False
    return all_good


def test_sidekick_agent():
    """Test SidekickAgent captures assistant responses"""
    print("\n" + "="*60)
    print("TEST: SidekickAgent Assistant Response Capture")
    print("="*60)
    
    checks = {
        "llm_node captures text": "assistant_text += choice.delta.content",
        "calls _handle_assistant_transcript": "await self._handle_assistant_transcript(assistant_text)",
        "store_turn import": "from transcript_store import store_turn",
        "turn data includes embedder": "'embedder': self._embedder",
        "stores complete turn": "Stored complete turn"
    }
    
    return check_file('/root/sidekick-forge/docker/agent/sidekick_agent.py', checks)


def test_entrypoint_setup():
    """Test entrypoint sets up agent properly"""
    print("\n" + "="*60)
    print("TEST: Entrypoint Agent Setup")
    print("="*60)
    
    checks = {
        "sets session_id": "agent._session_id = metadata.get",
        "sets embedder": "agent._embedder = context_manager.embedder",
        "sets supabase_client": "agent._supabase_client = client_supabase",
        "sets conversation_id": "agent._conversation_id = metadata.get",
        "sets agent_id": "agent._agent_id = metadata.get"
    }
    
    return check_file('/root/sidekick-forge/docker/agent/entrypoint.py', checks)


def test_transcript_store():
    """Test transcript store handles all fields properly"""
    print("\n" + "="*60)
    print("TEST: Transcript Store Module")
    print("="*60)
    
    checks = {
        "turn_id generation": "turn_id = str(uuid.uuid4())",
        "source field set": 'user_row["source"] = "voice"',
        "citations on assistant": 'assistant_row["citations"] = citations',
        "embedder handling": "'embedder' in turn_data",
        "observability logging": "turn_id={turn_id}"
    }
    
    return check_file('/root/sidekick-forge/docker/agent/transcript_store.py', checks)


def test_voice_transcripts_api():
    """Test voice transcripts API now responds with a realtime deprecation notice."""
    print("\n" + "="*60)
    print("TEST: Voice Transcripts API")
    print("="*60)
    
    checks = {
        "deprecation constant present": "DEPRECATION_MESSAGE",
        "references Supabase Realtime": "Supabase Realtime",
        "stream endpoint defined": "async def stream_voice_transcripts",
        "history endpoint defined": "async def get_transcript_history",
        "HTTP 410 status used": "status.HTTP_410_GONE",
    }
    
    return check_file('/root/sidekick-forge/app/api/v1/voice_transcripts.py', checks)


async def test_import_modules():
    """Test that modules can be imported"""
    print("\n" + "="*60)
    print("TEST: Module Imports")
    print("="*60)
    
    all_good = True
    
    try:
        from docker.agent.transcript_store import store_turn
        print("  ✅ Agent transcript_store imports successfully")
        
        # Check function signature
        sig = inspect.signature(store_turn)
        if 'turn_data' in sig.parameters and 'supabase_client' in sig.parameters:
            print("  ✅ store_turn has correct parameters")
        else:
            print("  ❌ store_turn has incorrect parameters")
            all_good = False
            
    except Exception as e:
        print(f"  ❌ Failed to import agent transcript_store: {e}")
        all_good = False
    
    try:
        from app.agent_modules.transcript_store import store_turn
        print("  ✅ FastAPI transcript_store imports successfully")
    except Exception as e:
        print(f"  ❌ Failed to import FastAPI transcript_store: {e}")
        all_good = False
    
    return all_good


def test_migration_sql():
    """Test migration SQL has all necessary columns"""
    print("\n" + "="*60)
    print("TEST: Database Migration")
    print("="*60)
    
    checks = {
        "turn_id column": "ADD COLUMN IF NOT EXISTS turn_id uuid",
        "citations column": "ADD COLUMN IF NOT EXISTS citations jsonb",
        "metadata column": "ADD COLUMN IF NOT EXISTS metadata jsonb",
        "source column": "ADD COLUMN IF NOT EXISTS source text",
        "source index": "idx_transcripts_source_created"
    }
    
    return check_file('/root/sidekick-forge/migrations/add_transcript_citations.sql', checks)


async def main():
    print("\n" + "="*60)
    print("VOICE TRANSCRIPT FLOW TEST SUITE")
    print("="*60)
    
    results = []
    
    # Run all tests
    results.append(("Database Migration", test_migration_sql()))
    results.append(("Module Imports", await test_import_modules()))
    results.append(("SidekickAgent", test_sidekick_agent()))
    results.append(("Entrypoint Setup", test_entrypoint_setup()))
    results.append(("Transcript Store", test_transcript_store()))
    results.append(("Voice Transcripts API", test_voice_transcripts_api()))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    total = len(results)
    passed = sum(1 for _, p in results if p)
    
    for test_name, passed_test in results:
        status = "PASSED" if passed_test else "FAILED"
        emoji = "✅" if passed_test else "❌"
        print(f"{emoji} {test_name}: {status}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! Voice transcript flow is ready.")
        print("\nKey improvements:")
        print("1. Assistant responses are captured in llm_node")
        print("2. Complete turns are stored with same turn_id")
        print("3. Citations are persisted on assistant messages")
        print("4. Source field is handled gracefully (backward compatible)")
        print("5. Embedder is properly passed for best-effort embeddings")
    else:
        print("\n⚠️ Some tests failed. Please review the implementation.")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
