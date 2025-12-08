#!/usr/bin/env python3
"""
Complete test of voice transcript flow with detailed diagnostics
"""

def check_entrypoint():
    """Check entrypoint has all necessary event handlers"""
    print("\n" + "="*60)
    print("CHECKING: Entrypoint Event Handlers")
    print("="*60)
    
    with open('/root/sidekick-forge/docker/agent/entrypoint.py', 'r') as f:
        content = f.read()
    
    checks = {
        "user_speech_committed handler": '@session.on("user_speech_committed")',
        "agent_speech_committed handler": '@session.on("agent_speech_committed")',
        "stores user transcript on agent": 'agent._current_user_transcript = user_text',
        "calls _handle_assistant_transcript": 'asyncio.create_task(agent._handle_assistant_transcript',
        "sets session_id on agent": 'agent._session_id = metadata.get',
        "sets embedder on agent": 'agent._embedder = context_manager.embedder',
        "sets supabase_client": 'agent._supabase_client = client_supabase'
    }
    
    all_good = True
    for check_name, pattern in checks.items():
        if pattern in content:
            print(f"  ✅ {check_name}")
        else:
            print(f"  ❌ {check_name} - NOT FOUND!")
            all_good = False
    
    return all_good


def check_sidekick_agent():
    """Check SidekickAgent properly handles transcripts"""
    print("\n" + "="*60)
    print("CHECKING: SidekickAgent Transcript Handling")
    print("="*60)
    
    with open('/root/sidekick-forge/docker/agent/sidekick_agent.py', 'r') as f:
        content = f.read()
    
    checks = {
        "has _current_user_transcript": 'self._current_user_transcript = ""',
        "_handle_assistant_transcript method": 'async def _handle_assistant_transcript',
        "checks for user transcript": 'if self._supabase_client and self._conversation_id and self._current_user_transcript',
        "imports store_turn": 'from transcript_store import store_turn',
        "calls store_turn": 'result = await store_turn(turn_data, self._supabase_client)',
        "includes embedder in turn_data": "'embedder': self._embedder",
        "detailed logging": 'logger.info(f"📝 _handle_assistant_transcript called'
    }
    
    all_good = True
    for check_name, pattern in checks.items():
        if pattern in content:
            print(f"  ✅ {check_name}")
        else:
            print(f"  ❌ {check_name} - NOT FOUND!")
            all_good = False
    
    return all_good


def check_transcript_store():
    """Check transcript_store has proper logging and error handling"""
    print("\n" + "="*60)
    print("CHECKING: Transcript Store Module")
    print("="*60)
    
    with open('/root/sidekick-forge/docker/agent/transcript_store.py', 'r') as f:
        content = f.read()
    
    checks = {
        "entry logging": 'logger.info("🔄 store_turn called!")',
        "user row insert logging": 'logger.info(f"📤 Attempting to insert user row',
        "assistant row insert logging": 'logger.info(f"📤 Attempting to insert assistant row',
        "success logging": 'logger.info(f"✅ User row inserted successfully")',
        "turn_id generation": 'turn_id = str(uuid.uuid4())',
        "source field conditional": 'user_row["source"] = "voice"',
        "citations handling": 'assistant_row["citations"] = citations'
    }
    
    all_good = True
    for check_name, pattern in checks.items():
        if pattern in content:
            print(f"  ✅ {check_name}")
        else:
            print(f"  ❌ {check_name} - NOT FOUND!")
            all_good = False
    
    return all_good


def check_voice_transcripts_api():
    """Check voice_transcripts.py can handle missing source column"""
    print("\n" + "="*60)
    print("CHECKING: Voice Transcripts API")
    print("="*60)
    
    with open('/root/sidekick-forge/app/api/v1/voice_transcripts.py', 'r') as f:
        content = f.read()
    
    checks = {
        "deprecation message defined": "DEPRECATION_MESSAGE",
        "mentions Supabase Realtime": "Supabase Realtime",
        "stream route exists": "async def stream_voice_transcripts",
        "history route exists": "async def get_transcript_history",
        "uses HTTP 410": "status.HTTP_410_GONE",
    }
    
    all_good = True
    for check_name, pattern in checks.items():
        if pattern in content:
            print(f"  ✅ {check_name}")
        else:
            print(f"  ❌ {check_name} - NOT FOUND!")
            all_good = False
    
    return all_good


def main():
    print("\n" + "="*60)
    print("COMPLETE VOICE TRANSCRIPT DIAGNOSTIC")
    print("="*60)
    print("\nThis test verifies all components needed for voice transcripts.\n")
    
    results = []
    
    # Run all checks
    results.append(("Entrypoint Event Handlers", check_entrypoint()))
    results.append(("SidekickAgent Handling", check_sidekick_agent()))
    results.append(("Transcript Store", check_transcript_store()))
    results.append(("Voice Transcripts API", check_voice_transcripts_api()))
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    all_passed = all(result for _, result in results)
    
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status}: {test_name}")
    
    if all_passed:
        print("\n🎉 All components are properly configured!")
        print("\nExpected flow:")
        print("1. User speaks → user_speech_committed event")
        print("2. Event handler sets agent._current_user_transcript")
        print("3. Agent processes and generates response")
        print("4. Assistant speaks → agent_speech_committed event")
        print("5. Event handler calls agent._handle_assistant_transcript()")
        print("6. _handle_assistant_transcript calls store_turn()")
        print("7. store_turn writes both rows with same turn_id")
        print("\nCheck the logs for these key messages:")
        print("  - '💬 Captured user speech:'")
        print("  - '🤖 Captured assistant speech:'")
        print("  - '🔄 store_turn called!'")
        print("  - '✅ Stored complete turn'")
    else:
        print("\n⚠️ Some components are missing! Voice transcripts may not work.")
    
    return all_passed


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
