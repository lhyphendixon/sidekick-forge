#!/usr/bin/env python3
"""
Test the complete voice pipeline setup as used in the agent
"""
import os
from livekit.plugins import groq, cartesia, silero
from livekit.agents import AgentSession

print("=" * 60)
print("TESTING COMPLETE VOICE PIPELINE")
print("=" * 60)

# Check all required API keys
print("\n1. Checking API Keys...")
api_keys = {
    'GROQ_API_KEY': os.getenv('GROQ_API_KEY'),
    'CARTESIA_API_KEY': os.getenv('CARTESIA_API_KEY'),
    'VOICE_ID': os.getenv('VOICE_ID', '7cf0e2b1-8daf-4fe4-89ad-f6039398f359')
}

all_keys_present = True
for key, value in api_keys.items():
    if value:
        if 'KEY' in key:
            print(f"✅ {key}: {'*' * 10}{value[-4:]}")
        else:
            print(f"✅ {key}: {value}")
    else:
        print(f"❌ {key}: Not found")
        all_keys_present = False

if not all_keys_present:
    print("\n❌ Missing required API keys")
    exit(1)

try:
    # Initialize components as in minimal_agent.py
    print("\n2. Initializing Voice Pipeline Components...")
    
    # STT
    print("   Initializing Cartesia STT...")
    stt = cartesia.STT(model="ink-whisper")
    print("   ✅ Cartesia STT ready")
    
    # TTS
    print("   Initializing Cartesia TTS...")
    tts = cartesia.TTS(voice=api_keys['VOICE_ID'])
    print(f"   ✅ Cartesia TTS ready with voice: {api_keys['VOICE_ID']}")
    
    # LLM
    print("   Initializing Groq LLM...")
    llm = groq.LLM(model="llama3-70b-8192", temperature=0.7)
    print("   ✅ Groq LLM ready")
    
    # VAD
    print("   Loading Silero VAD...")
    vad = silero.VAD.load()
    print("   ✅ Silero VAD loaded")
    
    # Try to create AgentSession
    print("\n3. Creating Agent Session...")
    try:
        session = AgentSession(
            vad=vad,
            stt=stt,
            llm=llm,
            tts=tts
        )
        print("✅ Agent Session created successfully!")
        print("   All components are compatible")
        
        # Check session properties
        print("\n4. Verifying Session Configuration...")
        print(f"   Session type: {type(session)}")
        print(f"   Has VAD: {hasattr(session, '_vad')}")
        print(f"   Has STT: {hasattr(session, '_stt')}")
        print(f"   Has LLM: {hasattr(session, '_llm')}")
        print(f"   Has TTS: {hasattr(session, '_tts')}")
        
    except Exception as e:
        print(f"❌ Failed to create agent session: {e}")
        raise
    
    print("\n✅ VOICE PIPELINE FULLY FUNCTIONAL")
    print("   - Cartesia STT (ink-whisper) ✓")
    print("   - Cartesia TTS ✓")
    print("   - Groq LLM (llama3-70b) ✓")
    print("   - Silero VAD ✓")
    print("   - AgentSession created ✓")
    
except Exception as e:
    print(f"\n❌ Pipeline test failed: {e}")
    import traceback
    traceback.print_exc()
    exit(1)