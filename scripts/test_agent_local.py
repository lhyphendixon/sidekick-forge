#!/usr/bin/env python3
"""
Runs the same AgentSession in console mode (no LiveKit) to verify
STT → LLM → TTS loop with a local WAV clip.
"""
import asyncio
import pathlib
import os
import sys
from livekit.agents import Agent, AgentSession, cli, voice
from livekit.plugins import deepgram, groq, elevenlabs, silero

# Add the app directory to the Python path
sys.path.insert(0, '/app')

async def main():
    print("🚀 Starting local agent test...")
    
    # Load API keys from Supabase as per architecture
    try:
        from app.integrations.supabase_client import supabase_manager
        await supabase_manager.initialize()
        
        # Get client config (using Autonomite's client ID)
        client_id = "11389177-e4d8-49a9-9a00-f77bb4de6592"
        client_data = await supabase_manager.get_client_config(client_id)
        
        if client_data:
            # Set API keys from client config
            os.environ['GROQ_API_KEY'] = client_data.get('groq_api_key', '')
            os.environ['DEEPGRAM_API_KEY'] = client_data.get('deepgram_api_key', '')
            os.environ['ELEVENLABS_API_KEY'] = client_data.get('elevenlabs_api_key', '')
            print("✅ Loaded API keys from Supabase")
        else:
            print("❌ Failed to load client config from Supabase")
            return
    except Exception as e:
        print(f"❌ Failed to connect to Supabase: {e}")
        return
    
    # Create test audio file if it doesn't exist
    test_audio_path = pathlib.Path("/tmp/test_hello.wav")
    if not test_audio_path.exists():
        print("❌ Test audio file not found. Creating a simple test...")
        # Create a simple test by recording silence
        import wave
        with wave.open(str(test_audio_path), 'wb') as wav:
            wav.setnchannels(1)  # mono
            wav.setsampwidth(2)  # 16-bit
            wav.setframerate(16000)  # 16kHz
            # Write 3 seconds of silence
            wav.writeframes(b'\x00\x00' * 16000 * 3)
        print("📝 Created silent test file. For real test, record 'Hello, how are you?' to /tmp/test_hello.wav")
    
    print("📂 Using test audio:", test_audio_path)
    
    # Initialize components
    print("🔧 Initializing components...")
    
    try:
        vad = silero.VAD.load()
        print("✅ VAD loaded")
    except Exception as e:
        print(f"❌ VAD failed: {e}")
        return
    
    try:
        stt = deepgram.STT(model="nova-2")
        print("✅ STT initialized (Deepgram nova-2)")
    except Exception as e:
        print(f"❌ STT failed: {e}")
        return
    
    try:
        llm = groq.LLM(model="llama-3.3-70b-versatile")
        print("✅ LLM initialized (Groq llama-3.3)")
    except Exception as e:
        print(f"❌ LLM failed: {e}")
        return
    
    try:
        tts = elevenlabs.TTS(voice_id="21m00Tcm4TlvDq8ikWAM")  # Default voice
        print("✅ TTS initialized (ElevenLabs)")
    except Exception as e:
        print(f"❌ TTS failed: {e}")
        return
    
    # Create agent and session
    print("\n🤖 Creating agent session...")
    agent = voice.Agent(instructions="You are a helpful test agent. Respond briefly.")
    
    session = voice.AgentSession(
        vad=vad,
        stt=stt,
        llm=llm,
        tts=tts,
    )
    
    # Register event handlers to see what's happening
    @session.on("user_speech_committed")
    async def on_speech(event):
        print(f"💬 User said: {event.text}")
    
    @session.on("agent_speech_committed")
    async def on_agent_speech(event):
        print(f"🤖 Agent said: {event.text}")
    
    @session.on("transcription_received")
    async def on_transcription(event):
        print(f"📝 Transcription: {event.text} (final: {event.is_final})")
    
    print("✅ Event handlers registered")
    
    # Start the session
    print("\n🎯 Starting session...")
    await session.start(agent=agent)
    
    # Load and process audio
    wav_data = test_audio_path.read_bytes()
    print(f"🎵 Loaded {len(wav_data)} bytes of audio")
    
    # Push audio to the session
    print("📤 Pushing audio to session...")
    if hasattr(session, 'push_audio'):
        await session.push_audio(wav_data)
    else:
        print("⚠️  Session doesn't have push_audio method, trying input stream...")
        if hasattr(session, 'input'):
            # Try to write to input stream
            session.input.write(wav_data)
            
    # Wait for processing
    print("⏳ Waiting for processing...")
    await asyncio.sleep(5)
    
    print("\n✅ Test complete!")

if __name__ == "__main__":
    # Run directly
    asyncio.run(main())