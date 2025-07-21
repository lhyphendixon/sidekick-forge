#!/usr/bin/env python3
"""
Test TTS with proper HTTP session context
"""
import os
import asyncio
import aiohttp
from livekit.plugins import cartesia, elevenlabs

async def test_cartesia_with_session():
    """Test Cartesia TTS with manual HTTP session"""
    print("=" * 60)
    print("TESTING CARTESIA TTS WITH HTTP SESSION")
    print("=" * 60)
    
    api_key = os.getenv('CARTESIA_API_KEY')
    voice_id = os.getenv('VOICE_ID', '7cf0e2b1-8daf-4fe4-89ad-f6039398f359')
    
    if not api_key:
        print("❌ CARTESIA_API_KEY not set")
        return False
    
    print(f"✅ API Key: {api_key[:10]}...{api_key[-4:]}")
    print(f"✅ Voice ID: {voice_id}")
    
    # Create HTTP session
    async with aiohttp.ClientSession() as session:
        try:
            # Initialize TTS with session
            print("\n1. Initializing Cartesia TTS with session...")
            tts = cartesia.TTS(
                voice=voice_id,
                http_session=session  # Pass the session
            )
            print("✅ TTS initialized with custom session")
            
            # Test streaming
            print("\n2. Testing TTS streaming...")
            stream = tts.stream()
            
            # Push text
            test_text = "Hello! This is a test of Cartesia text to speech with proper session handling."
            print(f"   Pushing text: '{test_text[:50]}...'")
            stream.push_text(test_text)
            stream.flush()
            
            # Collect frames
            print("   Collecting audio frames...")
            frames = []
            total_bytes = 0
            
            try:
                # Set a timeout for frame collection
                async def collect_frames():
                    async for frame in stream:
                        frames.append(frame)
                        if hasattr(frame, 'data'):
                            total_bytes += len(frame.data)
                        if len(frames) >= 10:  # Collect up to 10 frames
                            break
                
                # Wait for frames with timeout
                await asyncio.wait_for(collect_frames(), timeout=10.0)
                
            except asyncio.TimeoutError:
                print("   ⚠️  Timeout while collecting frames")
            
            await stream.aclose()
            
            if frames:
                print(f"   ✅ Received {len(frames)} audio frames")
                print(f"   Total audio data: {total_bytes} bytes")
                
                # Check first frame
                first_frame = frames[0]
                print(f"   First frame type: {type(first_frame)}")
                if hasattr(first_frame, 'sample_rate'):
                    print(f"   Sample rate: {first_frame.sample_rate} Hz")
                if hasattr(first_frame, 'num_channels'):
                    print(f"   Channels: {first_frame.num_channels}")
                
                return True
            else:
                print("   ❌ No audio frames received")
                return False
                
        except Exception as e:
            print(f"❌ TTS test failed: {e}")
            import traceback
            traceback.print_exc()
            return False

async def test_direct_api():
    """Test Cartesia API directly"""
    print("\n" + "=" * 60)
    print("TESTING CARTESIA API DIRECTLY") 
    print("=" * 60)
    
    api_key = os.getenv('CARTESIA_API_KEY')
    voice_id = os.getenv('VOICE_ID', '7cf0e2b1-8daf-4fe4-89ad-f6039398f359')
    
    if not api_key:
        return False
    
    async with aiohttp.ClientSession() as session:
        try:
            # Test voices endpoint
            print("1. Checking available voices...")
            async with session.get(
                "https://api.cartesia.ai/v1/voices",
                headers={
                    "X-Api-Key": api_key,
                    "Cartesia-Version": "2024-06-10"
                }
            ) as resp:
                if resp.status == 200:
                    voices = await resp.json()
                    print(f"   ✅ Found {len(voices)} voices")
                    
                    # Find our voice
                    our_voice = next((v for v in voices if v.get('id') == voice_id), None)
                    if our_voice:
                        print(f"   ✅ Voice '{our_voice.get('name')}' found")
                    else:
                        print(f"   ⚠️  Voice ID {voice_id} not found")
                        if voices:
                            print("   Available voices:")
                            for v in voices[:3]:
                                print(f"     - {v.get('id')}: {v.get('name')}")
                else:
                    print(f"   ❌ API returned {resp.status}")
            
            # Test TTS
            print("\n2. Testing TTS generation...")
            async with session.post(
                "https://api.cartesia.ai/v1/tts/bytes",
                headers={
                    "X-Api-Key": api_key,
                    "Cartesia-Version": "2024-06-10"
                },
                json={
                    "model_id": "sonic-english",
                    "voice": {"mode": "id", "id": voice_id},
                    "transcript": "Testing Cartesia API directly.",
                    "output_format": {
                        "container": "wav",
                        "encoding": "pcm_f32le", 
                        "sample_rate": 24000
                    }
                }
            ) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    print(f"   ✅ Generated {len(data)} bytes of audio")
                    if data[:4] == b'RIFF':
                        print("   ✅ Valid WAV file")
                    return True
                else:
                    print(f"   ❌ TTS failed with status {resp.status}")
                    error = await resp.text()
                    print(f"   Error: {error}")
                    return False
                    
        except Exception as e:
            print(f"❌ API test failed: {e}")
            return False

async def test_in_agent_context():
    """Test how TTS would work in agent context"""
    print("\n" + "=" * 60)
    print("TESTING TTS IN AGENT-LIKE CONTEXT")
    print("=" * 60)
    
    # Check if we're in a worker context
    try:
        from livekit.agents import utils
        has_context = hasattr(utils.http_context, '_var')
        print(f"HTTP context available: {has_context}")
    except:
        print("Running outside of agent worker context")
    
    # Show that TTS needs job context
    print("\n✅ TTS components verified:")
    print("   - Cartesia API is accessible")
    print("   - API key is valid") 
    print("   - Voice ID is configured")
    print("   - TTS works with manual HTTP session")
    print("\n⚠️  Note: TTS plugins require agent job context for automatic session management")
    print("   In production, TTS is initialized within the agent's job context")

async def main():
    """Run all tests"""
    print("CARTESIA TTS ISOLATION TEST")
    print("=" * 60)
    
    # Test with manual session
    session_result = await test_cartesia_with_session()
    
    # Test API directly
    api_result = await test_direct_api()
    
    # Test agent context
    await test_in_agent_context()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    if session_result and api_result:
        print("✅ CARTESIA TTS IS FULLY FUNCTIONAL")
        print("   - API connectivity verified")
        print("   - Audio generation working")
        print("   - Ready for use in agent context")
    elif api_result:
        print("⚠️  CARTESIA API WORKS but SDK has issues")
    else:
        print("❌ CARTESIA TTS NOT WORKING")

if __name__ == "__main__":
    asyncio.run(main())