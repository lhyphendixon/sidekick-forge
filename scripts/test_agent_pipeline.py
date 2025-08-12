#!/usr/bin/env python3
"""
Direct test of AgentSession pipeline with API keys from platform database.
Tests STT → LLM → TTS without LiveKit involvement.
"""
import asyncio
import os
import sys
from supabase import create_client
from livekit.agents import voice
from livekit.plugins import deepgram, groq, elevenlabs, silero

async def main():
    print("🚀 Starting pipeline test...")
    
    # Connect to platform database
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    if not supabase_url or not supabase_key:
        print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        return
        
    supabase = create_client(supabase_url, supabase_key)
    
    # Get Autonomite client config
    client_id = "11389177-e4d8-49a9-9a00-f77bb4de6592"
    result = supabase.table("clients").select("*").eq("id", client_id).single().execute()
    
    if not result.data:
        print("❌ Failed to load client config")
        return
        
    client_data = result.data
    print(f"✅ Loaded client: {client_data.get('name')}")
    
    # Extract API keys
    groq_key = client_data.get('groq_api_key')
    deepgram_key = client_data.get('deepgram_api_key') 
    elevenlabs_key = client_data.get('elevenlabs_api_key')
    
    print(f"API Keys: Groq={bool(groq_key)}, Deepgram={bool(deepgram_key)}, ElevenLabs={bool(elevenlabs_key)}")
    
    if not all([groq_key, deepgram_key, elevenlabs_key]):
        print("❌ Missing required API keys")
        return
    
    # Set keys in environment for plugins
    os.environ['GROQ_API_KEY'] = groq_key
    os.environ['DEEPGRAM_API_KEY'] = deepgram_key
    os.environ['ELEVENLABS_API_KEY'] = elevenlabs_key
    os.environ['ELEVEN_API_KEY'] = elevenlabs_key  # Also set the alternate name
    
    # Initialize components
    print("\n🔧 Initializing components...")
    
    try:
        vad = silero.VAD.load()
        print("✅ VAD loaded")
    except Exception as e:
        print(f"❌ VAD failed: {e}")
        return
    
    try:
        stt = deepgram.STT(model="nova-2")
        print("✅ STT initialized (Deepgram)")
    except Exception as e:
        print(f"❌ STT failed: {e}")
        return
    
    try:
        llm = groq.LLM(model="llama-3.3-70b-versatile")
        print("✅ LLM initialized (Groq)")
    except Exception as e:
        print(f"❌ LLM failed: {e}")
        return
    
    try:
        tts = elevenlabs.TTS(voice_id="21m00Tcm4TlvDq8ikWAM")
        print("✅ TTS initialized (ElevenLabs)")
    except Exception as e:
        print(f"❌ TTS failed: {e}")
        return
    
    # Create agent and session
    print("\n🤖 Creating agent session...")
    agent = voice.Agent(instructions="You are a helpful test agent. Say 'Hello, test successful!' when you start.")
    
    session = voice.AgentSession(
        vad=vad,
        stt=stt,
        llm=llm,
        tts=tts,
    )
    
    # Track events
    events_received = []
    
    @session.on("user_speech_committed")
    def on_speech(event):
        print(f"💬 User said: {event.text}")
        events_received.append(("user_speech", event.text))
    
    @session.on("agent_speech_committed") 
    def on_agent_speech(event):
        print(f"🤖 Agent said: {event.text}")
        events_received.append(("agent_speech", event.text))
    
    @session.on("transcription_received")
    def on_transcription(event):
        print(f"📝 Transcription: {event.text} (final: {event.is_final})")
        events_received.append(("transcription", event.text))
    
    print("✅ Event handlers registered")
    
    # Start the session
    print("\n🎯 Starting session...")
    try:
        await session.start(agent=agent)
        print("✅ Session started")
    except Exception as e:
        print(f"❌ Failed to start session: {e}")
        return
    
    # Let agent process its initial greeting
    print("\n⏳ Waiting for agent greeting...")
    await asyncio.sleep(5)
    
    # Check if we got any events
    print(f"\n📊 Events received: {len(events_received)}")
    for event_type, text in events_received:
        print(f"  - {event_type}: {text[:50]}...")
    
    print("\n✅ Test complete!")

if __name__ == "__main__":
    asyncio.run(main())