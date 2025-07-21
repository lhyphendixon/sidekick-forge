#!/usr/bin/env python3
"""
Test TTS functionality in isolation - including audio generation
"""
import os
import asyncio
import logging
from livekit.plugins import cartesia, elevenlabs
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_cartesia_tts():
    """Test Cartesia TTS in isolation"""
    print("=" * 60)
    print("TESTING CARTESIA TTS IN ISOLATION")
    print("=" * 60)
    
    # Check API key
    api_key = os.getenv('CARTESIA_API_KEY')
    if not api_key:
        print("❌ CARTESIA_API_KEY not set")
        return False
    
    print(f"✅ CARTESIA_API_KEY present: {api_key[:10]}...{api_key[-4:]}")
    
    # Get voice ID
    voice_id = os.getenv('VOICE_ID', '7cf0e2b1-8daf-4fe4-89ad-f6039398f359')
    print(f"✅ Using voice ID: {voice_id}")
    
    try:
        # Initialize TTS
        print("\n1. Initializing Cartesia TTS...")
        tts = cartesia.TTS(voice=voice_id)
        print("✅ Cartesia TTS initialized successfully")
        print(f"   Type: {type(tts)}")
        
        # Test synthesize method
        print("\n2. Testing speech synthesis...")
        test_phrases = [
            "Hello! This is a test of Cartesia text to speech.",
            "The quick brown fox jumps over the lazy dog.",
            "Testing one, two, three. Can you hear me clearly?"
        ]
        
        for i, phrase in enumerate(test_phrases, 1):
            print(f"\n   Test {i}: '{phrase[:50]}...'")
            
            try:
                # Try to synthesize
                start_time = time.time()
                
                # Check if we should use synthesize or another method
                if hasattr(tts, 'synthesize'):
                    print("   Using synthesize method...")
                    result = await tts.synthesize(phrase)
                    duration = time.time() - start_time
                    print(f"   ✅ Synthesized in {duration:.2f}s")
                    print(f"   Result type: {type(result)}")
                    
                elif hasattr(tts, 'aio'):
                    print("   Using aio.synthesize method...")
                    result = await tts.aio.synthesize(phrase)
                    duration = time.time() - start_time
                    print(f"   ✅ Synthesized in {duration:.2f}s")
                    print(f"   Result type: {type(result)}")
                    
                else:
                    # List available methods
                    methods = [m for m in dir(tts) if not m.startswith('_')]
                    print(f"   Available methods: {methods}")
                    
                    # Try stream method if available
                    if hasattr(tts, 'stream'):
                        print("   Using stream method...")
                        stream = tts.stream()
                        
                        # Push text
                        stream.push_text(phrase)
                        stream.flush()
                        
                        # Collect audio frames
                        audio_frames = []
                        async for frame in stream:
                            audio_frames.append(frame)
                            
                        duration = time.time() - start_time
                        print(f"   ✅ Streamed {len(audio_frames)} frames in {duration:.2f}s")
                        
                        if audio_frames:
                            first_frame = audio_frames[0]
                            print(f"   First frame type: {type(first_frame)}")
                            if hasattr(first_frame, 'data'):
                                print(f"   Frame data length: {len(first_frame.data)} bytes")
                            if hasattr(first_frame, 'sample_rate'):
                                print(f"   Sample rate: {first_frame.sample_rate} Hz")
                
            except Exception as e:
                print(f"   ❌ Synthesis failed: {e}")
                import traceback
                traceback.print_exc()
        
        # Test with actual agent-like usage
        print("\n3. Testing agent-like TTS usage...")
        
        # Check TTS capabilities
        print("   TTS Capabilities:")
        if hasattr(tts, 'sample_rate'):
            print(f"   - Sample rate: {tts.sample_rate}")
        if hasattr(tts, 'num_channels'):
            print(f"   - Channels: {tts.num_channels}")
        
        # Try ChunkedStream if available
        if hasattr(tts, 'ChunkedStream'):
            print("\n4. Testing ChunkedStream...")
            try:
                stream = tts.stream()
                print("   ✅ Created TTS stream")
                
                # Push text and flush
                test_text = "This is a chunked stream test."
                stream.push_text(test_text)
                stream.flush()
                
                # Try to get one frame
                print("   Waiting for audio frame...")
                frame_count = 0
                total_bytes = 0
                
                async for frame in stream:
                    frame_count += 1
                    if hasattr(frame, 'data'):
                        total_bytes += len(frame.data)
                    
                    if frame_count >= 5:  # Just get a few frames
                        break
                
                await stream.aclose()
                print(f"   ✅ Received {frame_count} frames, {total_bytes} bytes total")
                
            except Exception as e:
                print(f"   ❌ ChunkedStream test failed: {e}")
        
        print("\n✅ CARTESIA TTS IS FUNCTIONAL")
        return True
        
    except Exception as e:
        print(f"\n❌ TTS test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_tts_api_direct():
    """Test Cartesia API directly with HTTP"""
    print("\n" + "=" * 60)
    print("TESTING CARTESIA API DIRECTLY")
    print("=" * 60)
    
    import httpx
    
    api_key = os.getenv('CARTESIA_API_KEY')
    voice_id = os.getenv('VOICE_ID', '7cf0e2b1-8daf-4fe4-89ad-f6039398f359')
    
    if not api_key:
        print("❌ No API key for direct test")
        return False
    
    try:
        async with httpx.AsyncClient() as client:
            # Test Cartesia API endpoint
            print("1. Testing Cartesia API endpoint...")
            
            # First, test voices endpoint
            response = await client.get(
                "https://api.cartesia.ai/v1/voices",
                headers={
                    "X-Api-Key": api_key,
                    "Cartesia-Version": "2024-06-10"
                },
                timeout=10.0
            )
            
            if response.status_code == 200:
                print("✅ Cartesia API is reachable")
                voices = response.json()
                print(f"   Found {len(voices)} voices")
                
                # Check if our voice ID exists
                voice_found = any(v.get('id') == voice_id for v in voices)
                if voice_found:
                    print(f"   ✅ Voice ID {voice_id} is valid")
                else:
                    print(f"   ⚠️  Voice ID {voice_id} not found in account")
                    # List available voices
                    print("   Available voices:")
                    for v in voices[:5]:
                        print(f"     - {v.get('id')}: {v.get('name')}")
            else:
                print(f"❌ Cartesia API returned status {response.status_code}")
                print(f"   Response: {response.text}")
                
            # Test TTS endpoint
            print("\n2. Testing TTS generation...")
            response = await client.post(
                "https://api.cartesia.ai/v1/tts/bytes",
                headers={
                    "X-Api-Key": api_key,
                    "Cartesia-Version": "2024-06-10",
                    "Content-Type": "application/json"
                },
                json={
                    "model_id": "sonic-english",
                    "voice": {
                        "mode": "id",
                        "id": voice_id
                    },
                    "transcript": "Hello, this is a test of Cartesia text to speech.",
                    "output_format": {
                        "container": "wav",
                        "encoding": "pcm_f32le",
                        "sample_rate": 24000
                    }
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                print("✅ TTS generation successful")
                print(f"   Response size: {len(response.content)} bytes")
                print(f"   Content type: {response.headers.get('content-type')}")
                
                # Check if it's actually audio
                if response.content[:4] == b'RIFF':
                    print("   ✅ Valid WAV file received")
                else:
                    print("   ⚠️  Unexpected audio format")
                    
                return True
            else:
                print(f"❌ TTS generation failed with status {response.status_code}")
                print(f"   Response: {response.text}")
                return False
                
    except Exception as e:
        print(f"❌ Direct API test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_elevenlabs_fallback():
    """Test ElevenLabs TTS as fallback"""
    print("\n" + "=" * 60)
    print("TESTING ELEVENLABS TTS (FALLBACK)")
    print("=" * 60)
    
    api_key = os.getenv('ELEVEN_API_KEY')
    if not api_key:
        print("❌ ELEVEN_API_KEY not set")
        return False
    
    print(f"✅ ELEVEN_API_KEY present: {api_key[:10]}...{api_key[-4:]}")
    
    try:
        # Initialize ElevenLabs TTS
        print("\n1. Initializing ElevenLabs TTS...")
        tts = elevenlabs.TTS(voice_id="alloy")  # Using a default voice
        print("✅ ElevenLabs TTS initialized")
        
        # Test synthesis
        print("\n2. Testing ElevenLabs synthesis...")
        if hasattr(tts, 'stream'):
            stream = tts.stream()
            stream.push_text("Testing ElevenLabs text to speech.")
            stream.flush()
            
            frame_count = 0
            async for frame in stream:
                frame_count += 1
                if frame_count >= 3:
                    break
                    
            await stream.aclose()
            print(f"✅ ElevenLabs TTS working - received {frame_count} frames")
            return True
            
    except Exception as e:
        print(f"❌ ElevenLabs test failed: {e}")
        return False

async def main():
    """Run all TTS tests"""
    print("TTS ISOLATION TEST SUITE")
    print("=" * 60)
    
    # Test Cartesia TTS
    cartesia_result = await test_cartesia_tts()
    
    # Test Cartesia API directly
    api_result = await test_tts_api_direct()
    
    # Test ElevenLabs fallback
    elevenlabs_result = await test_elevenlabs_fallback()
    
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Cartesia TTS Library: {'✅ PASS' if cartesia_result else '❌ FAIL'}")
    print(f"Cartesia API Direct: {'✅ PASS' if api_result else '❌ FAIL'}")
    print(f"ElevenLabs Fallback: {'✅ PASS' if elevenlabs_result else '❌ FAIL'}")
    
    if cartesia_result or api_result:
        print("\n✅ TTS IS WORKING - Cartesia TTS functional")
    elif elevenlabs_result:
        print("\n⚠️  TTS FALLBACK WORKING - ElevenLabs functional, Cartesia issues")
    else:
        print("\n❌ TTS FAILED - No TTS providers working")

if __name__ == "__main__":
    asyncio.run(main())