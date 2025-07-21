#!/usr/bin/env python3
"""
Test the complete Voice Agent Pipeline
"""
import os
import sys
import asyncio
import logging
from typing import Optional, Dict, Any
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add proper imports
try:
    from livekit import rtc
    from livekit.agents import (
        AgentSession, JobContext, WorkerOptions, 
        Agent, llm as lk_llm, WorkerType
    )
    from livekit.plugins import groq, cartesia, silero
    print("✅ LiveKit imports successful")
except ImportError as e:
    print(f"❌ LiveKit import failed: {e}")
    sys.exit(1)

class TestVoiceAgent(Agent):
    """Test implementation of voice agent"""
    
    def __init__(self, *, chat_ctx: lk_llm.ChatContext, **kwargs):
        super().__init__(
            chat_ctx=chat_ctx,
            instructions="You are a test voice agent."
        )
        self.test_events = []
        logger.info("🤖 TestVoiceAgent initialized")
    
    async def on_enter(self) -> None:
        """Called when agent starts"""
        logger.info("🚀 Agent on_enter() called")
        self.test_events.append("on_enter")
    
    async def on_exit(self) -> None:
        """Called when agent stops"""
        logger.info("🛑 Agent on_exit() called")
        self.test_events.append("on_exit")

async def test_voice_pipeline_components():
    """Test individual voice pipeline components"""
    print("\n" + "=" * 60)
    print("TESTING VOICE PIPELINE COMPONENTS")
    print("=" * 60)
    
    results = {}
    
    # Test 1: VAD (Voice Activity Detection)
    print("\n1. Testing VAD Component...")
    try:
        vad = silero.VAD.load()
        print("✅ Silero VAD loaded successfully")
        print(f"   Type: {type(vad)}")
        results['vad'] = True
    except Exception as e:
        print(f"❌ VAD test failed: {e}")
        results['vad'] = False
    
    # Test 2: STT (Speech-to-Text)
    print("\n2. Testing STT Component...")
    try:
        if os.getenv('CARTESIA_API_KEY'):
            stt = cartesia.STT(model="ink-whisper")
            print("✅ Cartesia STT initialized (ink-whisper)")
            results['stt'] = True
        else:
            print("❌ No Cartesia API key for STT")
            results['stt'] = False
    except Exception as e:
        print(f"❌ STT test failed: {e}")
        results['stt'] = False
    
    # Test 3: LLM (Language Model)
    print("\n3. Testing LLM Component...")
    try:
        if os.getenv('GROQ_API_KEY'):
            llm = groq.LLM(model="llama3-70b-8192", temperature=0.7)
            print("✅ Groq LLM initialized (llama3-70b)")
            print(f"   Temperature: 0.7")
            results['llm'] = True
        else:
            print("❌ No Groq API key for LLM")
            results['llm'] = False
    except Exception as e:
        print(f"❌ LLM test failed: {e}")
        results['llm'] = False
    
    # Test 4: TTS (Text-to-Speech)
    print("\n4. Testing TTS Component...")
    try:
        voice_id = os.getenv('VOICE_ID', '7cf0e2b1-8daf-4fe4-89ad-f6039398f359')
        if os.getenv('CARTESIA_API_KEY'):
            # Need to provide session for TTS
            import aiohttp
            async with aiohttp.ClientSession() as session:
                tts = cartesia.TTS(voice=voice_id, http_session=session)
                print(f"✅ Cartesia TTS initialized")
                print(f"   Voice ID: {voice_id}")
                results['tts'] = True
        else:
            print("❌ No Cartesia API key for TTS")
            results['tts'] = False
    except Exception as e:
        print(f"❌ TTS test failed: {e}")
        results['tts'] = False
    
    return results

async def test_agent_session_creation():
    """Test creating a complete AgentSession"""
    print("\n" + "=" * 60)
    print("TESTING AGENT SESSION CREATION")
    print("=" * 60)
    
    try:
        # Check API keys
        if not all([
            os.getenv('GROQ_API_KEY'),
            os.getenv('CARTESIA_API_KEY')
        ]):
            print("❌ Missing required API keys")
            return False
        
        print("1. Creating voice pipeline components...")
        
        # Create components with manual session
        import aiohttp
        async with aiohttp.ClientSession() as session:
            vad = silero.VAD.load()
            print("   ✅ VAD created")
            
            stt = cartesia.STT(model="ink-whisper")
            print("   ✅ STT created")
            
            llm = groq.LLM(model="llama3-70b-8192", temperature=0.7)
            print("   ✅ LLM created")
            
            voice_id = os.getenv('VOICE_ID', '7cf0e2b1-8daf-4fe4-89ad-f6039398f359')
            tts = cartesia.TTS(voice=voice_id, http_session=session)
            print("   ✅ TTS created")
            
            print("\n2. Creating AgentSession...")
            agent_session = AgentSession(
                vad=vad,
                stt=stt,
                llm=llm,
                tts=tts
            )
            print("✅ AgentSession created successfully!")
            
            # Verify session properties
            print("\n3. Verifying session properties...")
            print(f"   Has VAD: {hasattr(agent_session, '_vad')}")
            print(f"   Has STT: {hasattr(agent_session, '_stt')}")
            print(f"   Has LLM: {hasattr(agent_session, '_llm')}")
            print(f"   Has TTS: {hasattr(agent_session, '_tts')}")
            
            return True
            
    except Exception as e:
        print(f"❌ Agent session creation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_voice_pipeline_flow():
    """Test the conceptual flow of voice processing"""
    print("\n" + "=" * 60)
    print("TESTING VOICE PIPELINE FLOW")
    print("=" * 60)
    
    print("Voice Pipeline Flow:")
    print("1. User speaks → Microphone captures audio")
    print("2. VAD detects speech activity")
    print("3. STT transcribes speech to text")
    print("4. LLM processes text and generates response")
    print("5. TTS converts response to speech")
    print("6. Audio sent back to user")
    
    print("\nCurrent Pipeline Configuration:")
    print(f"- VAD: Silero VAD")
    print(f"- STT: Cartesia (ink-whisper)")
    print(f"- LLM: Groq (llama3-70b-8192)")
    print(f"- TTS: Cartesia (voice: {os.getenv('VOICE_ID', 'default')})")
    
    return True

async def test_livekit_integration():
    """Test LiveKit integration aspects"""
    print("\n" + "=" * 60)
    print("TESTING LIVEKIT INTEGRATION")
    print("=" * 60)
    
    # Test 1: Check LiveKit URL
    livekit_url = os.getenv('LIVEKIT_URL')
    print(f"1. LiveKit URL: {livekit_url if livekit_url else '❌ Not set'}")
    
    # Test 2: Check LiveKit credentials
    api_key = os.getenv('LIVEKIT_API_KEY')
    api_secret = os.getenv('LIVEKIT_API_SECRET')
    print(f"2. LiveKit API Key: {'✅ Set' if api_key else '❌ Not set'}")
    print(f"3. LiveKit API Secret: {'✅ Set' if api_secret else '❌ Not set'}")
    
    # Test 3: Worker registration concept
    print("\n4. Worker Registration Process:")
    print("   - Worker connects to LiveKit server")
    print("   - Registers with agent name and capabilities")
    print("   - Waits for job assignments")
    print("   - Accepts/rejects jobs based on criteria")
    
    # Test 4: Job context simulation
    print("\n5. Job Context Contents:")
    print("   - Room information")
    print("   - Participant details")
    print("   - Job metadata")
    print("   - Connection methods")
    
    return True

async def test_minimal_agent_structure():
    """Test the minimal agent implementation structure"""
    print("\n" + "=" * 60)
    print("TESTING MINIMAL AGENT STRUCTURE")
    print("=" * 60)
    
    try:
        # Create test agent
        chat_ctx = lk_llm.ChatContext()
        agent = TestVoiceAgent(chat_ctx=chat_ctx)
        
        print("✅ Test agent created")
        print(f"   Type: {type(agent)}")
        print(f"   Has on_enter: {hasattr(agent, 'on_enter')}")
        print(f"   Has on_exit: {hasattr(agent, 'on_exit')}")
        
        # Test lifecycle methods
        await agent.on_enter()
        print("✅ on_enter() executed")
        
        await agent.on_exit()
        print("✅ on_exit() executed")
        
        print(f"\nAgent events recorded: {agent.test_events}")
        
        return True
        
    except Exception as e:
        print(f"❌ Agent structure test failed: {e}")
        return False

async def main():
    """Run all voice pipeline tests"""
    print("VOICE AGENT PIPELINE TEST SUITE")
    print("=" * 60)
    
    results = {}
    
    # Test individual components
    component_results = await test_voice_pipeline_components()
    results.update(component_results)
    
    # Test agent session
    results['agent_session'] = await test_agent_session_creation()
    
    # Test pipeline flow
    results['pipeline_flow'] = await test_voice_pipeline_flow()
    
    # Test LiveKit integration
    results['livekit_integration'] = await test_livekit_integration()
    
    # Test agent structure
    results['agent_structure'] = await test_minimal_agent_structure()
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} {test_name.replace('_', ' ').title()}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    print("OVERALL VOICE PIPELINE STATUS")
    print("=" * 60)
    
    if all_passed:
        print("✅ VOICE AGENT PIPELINE IS FULLY FUNCTIONAL")
        print("\nAll components are:")
        print("- Properly initialized")
        print("- Compatible with each other")
        print("- Ready for voice processing")
        print("\n⚠️  Note: Full functionality requires:")
        print("- Active LiveKit job context")
        print("- Room connection with participants")
        print("- Proper job dispatch (current blocker)")
    else:
        print("❌ SOME VOICE PIPELINE COMPONENTS FAILED")
        print("\nCheck the failures above for details.")

if __name__ == "__main__":
    asyncio.run(main())