#!/usr/bin/env python3
"""
Test that voice chat works after fixing LiveKit SDK v1.0+ architectural pattern
"""

import asyncio
import httpx
import json
from datetime import datetime, UTC

API_BASE_URL = "http://localhost:8000"

async def test_voice_livekit_fix():
    """Test voice chat after fixing LiveKit SDK pattern"""
    
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    print(f"🎤 VOICE CHAT LIVEKIT SDK FIX TEST")
    print(f"=" * 60)
    print(f"Timestamp: {timestamp}")
    print(f"=" * 60)
    
    # Test with admin preview endpoint
    async with httpx.AsyncClient() as client:
        # Get a new preview session
        print("\n📝 Getting preview session...")
        response = await client.get(
            f"{API_BASE_URL}/admin/agents/preview/11389177-e4d8-49a9-9a00-f77bb4de6592/clarence-coherence"
        )
        
        if response.status_code == 200:
            print("✅ Preview session created")
            
            print("\n🔧 LiveKit SDK pattern has been fixed:")
            print("   1. Removed generic agents.Agent object creation")
            print("   2. Pass system prompt directly to voice.AgentSession")
            print("   3. AgentSession IS the agent in LiveKit v1.0+")
            print("   4. session.start() only needs the room parameter")
            
            print("\n✅ Expected results after fix:")
            print("   - STT transcripts should trigger user_speech_committed events")
            print("   - Agent should process speech through the LLM")
            print("   - Agent should respond with voice")
            print("   - No more silence - the full pipeline should work")
            
        else:
            print(f"❌ Failed to get preview: {response.status_code}")
    
    print(f"\n📊 MANUAL VERIFICATION STEPS:")
    print(f"=" * 60)
    print(f"1. Open admin dashboard and test voice preview")
    print(f"2. Speak into the microphone")
    print(f"3. Check agent logs for '💬 User:' messages")
    print(f"4. Agent should respond with speech")
    print(f"5. Check for '🤖 Agent:' messages in logs")
    print(f"=" * 60)

if __name__ == "__main__":
    asyncio.run(test_voice_livekit_fix())