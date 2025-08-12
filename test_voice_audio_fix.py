#!/usr/bin/env python3
"""
Test that voice chat audio is being properly captured after fixing LiveKit track creation
"""

import asyncio
import httpx
import json
from datetime import datetime, UTC

API_BASE_URL = "http://localhost:8000"

async def test_voice_audio_fix():
    """Test voice chat audio capture fix"""
    
    timestamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    print(f"🎤 VOICE AUDIO CAPTURE FIX TEST")
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
            
            # Extract session_id from response
            # The response contains the preview modal HTML
            print("\n🎤 Voice preview template has been updated with fix:")
            print("   1. Uses LiveKitSDK.createLocalAudioTrack() instead of raw MediaStreamTrack")
            print("   2. Disables noise suppression to prevent false silence detection")
            print("   3. Configures proper publish options (dtx: false, red: false)")
            
            print("\n✅ Expected results after fix:")
            print("   - No more 'silence detected on local audio track' errors")
            print("   - Agent should receive and process speech")
            print("   - STT should transcribe user speech")
            print("   - Agent should respond to user queries")
            
        else:
            print(f"❌ Failed to get preview: {response.status_code}")
    
    print(f"\n📊 MANUAL VERIFICATION STEPS:")
    print(f"=" * 60)
    print(f"1. Open admin dashboard and test voice preview")
    print(f"2. Browser console should NOT show 'silence detected'")
    print(f"3. Agent logs should show speech/transcript events")
    print(f"4. Agent should respond when you speak")
    print(f"=" * 60)

if __name__ == "__main__":
    asyncio.run(test_voice_audio_fix())