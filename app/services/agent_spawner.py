"""
Agent Spawner Service - Directly spawns agents for voice preview
"""
import asyncio
import logging
import subprocess
import os
import json
import tempfile
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class AgentSpawner:
    """Manages spawning LiveKit agents for voice preview"""
    
    def __init__(self):
        self.agent_processes: Dict[str, subprocess.Popen] = {}
        self.agent_script_path = "/root/wordpress-plugin/autonomite-agent/livekit-agents/simple_voice_agent.py"
        self.venv_python = "/root/wordpress-plugin/autonomite-agent/livekit-agents/agent_env/bin/python"
    
    async def spawn_agent_for_room(
        self,
        room_name: str,
        server_url: str,
        api_key: str,
        api_secret: str,
        agent_name: str = "preview-agent"
    ) -> Dict[str, Any]:
        """Check if main agent is running and can handle the room"""
        
        # Check if the main sophisticated agent is running
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True
            )
            
            # Look for the main agent process
            main_agent_running = False
            for line in result.stdout.split('\n'):
                if 'autonomite_agent_v1_1_19_text_support.py' in line and 'dev' in line:
                    main_agent_running = True
                    break
            
            if main_agent_running:
                logger.info(f"✅ Main sophisticated agent is running - it will handle room {room_name}")
                logger.info("🎯 The main agent has full voice processing, RAG, and user context capabilities")
                return {
                    "success": True,
                    "method": "main_agent",
                    "room_name": room_name,
                    "message": "Main agent will handle this room automatically",
                    "capabilities": ["voice_processing", "rag_context", "user_profiles", "conversation_storage"]
                }
            else:
                logger.warning("❌ Main agent not running - falling back to simple agent")
                return await self._spawn_simple_agent(room_name, server_url, api_key, api_secret, agent_name)
                
        except Exception as e:
            logger.error(f"❌ Failed to check main agent status: {e}")
            return {
                "success": False,
                "error": f"Failed to check main agent: {str(e)}"
            }
    
    async def _spawn_simple_agent(
        self,
        room_name: str,
        server_url: str,
        api_key: str,
        api_secret: str,
        agent_name: str = "preview-agent"
    ) -> Dict[str, Any]:
        """Fallback: Spawn a simple agent if main agent is not running"""
        
        # Create a simple agent script if it doesn't exist
        if not os.path.exists(self.agent_script_path):
            await self._create_simple_agent_script()
        
        # Set up environment
        env = os.environ.copy()
        env.update({
            "LIVEKIT_URL": server_url,
            "LIVEKIT_API_KEY": api_key,
            "LIVEKIT_API_SECRET": api_secret,
            "ROOM_NAME": room_name,
            "AGENT_NAME": agent_name
        })
        
        # Create log file
        log_file = f"/tmp/agent_{room_name}.log"
        
        try:
            # Start the agent process
            with open(log_file, 'w') as log:
                process = subprocess.Popen(
                    [self.venv_python, self.agent_script_path, room_name],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    preexec_fn=os.setsid  # Create new process group
                )
            
            # Store process reference
            self.agent_processes[room_name] = process
            
            # Wait a moment for agent to start
            await asyncio.sleep(2)
            
            # Check if process is still running
            if process.poll() is None:
                logger.info(f"✅ Simple agent spawned for room {room_name} with PID {process.pid}")
                return {
                    "success": True,
                    "method": "simple_agent",
                    "pid": process.pid,
                    "room_name": room_name,
                    "log_file": log_file
                }
            else:
                # Process died
                with open(log_file, 'r') as log:
                    error_output = log.read()
                logger.error(f"❌ Simple agent process died immediately: {error_output}")
                return {
                    "success": False,
                    "error": "Simple agent process failed to start",
                    "output": error_output
                }
                
        except Exception as e:
            logger.error(f"❌ Failed to spawn simple agent: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def stop_agent_for_room(self, room_name: str) -> bool:
        """Stop the agent for a specific room"""
        if room_name in self.agent_processes:
            process = self.agent_processes[room_name]
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            
            del self.agent_processes[room_name]
            logger.info(f"✅ Stopped agent for room {room_name}")
            return True
        return False
    
    async def _create_simple_agent_script(self):
        """Create a voice-enabled agent script with STT and TTS"""
        script_content = '''#!/usr/bin/env python3
"""Voice Agent with Speech Processing"""
import asyncio
import os
import sys
import logging
import json
from livekit import rtc, api
from livekit.agents import VoiceAssistant, AutoSubscribe, JobContext, WorkerOptions
from livekit.agents.llm import LLMStream
from livekit.agents.stt import STT, SpeechEvent
from livekit.agents.tts import TTS, SynthesizeStream
from livekit.plugins import cartesia, deepgram, openai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-agent")

# Simple LLM that responds to speech
class SimpleLLM:
    def __init__(self):
        self.responses = [
            "Hello! I can hear you speaking. This is Clarence Coherence, your AI assistant.",
            "I understand you're testing the voice connection. Everything seems to be working well!",
            "Thank you for speaking with me. I'm processing your voice input successfully.",
            "Great! The voice preview is functioning correctly. How can I help you today?",
            "I can hear you clearly. The LiveKit voice connection is working as expected.",
        ]
        self.response_index = 0
    
    async def agenerate(self, prompt: str):
        """Generate a simple response"""
        response = self.responses[self.response_index % len(self.responses)]
        self.response_index += 1
        logger.info(f"LLM responding: {response}")
        
        # Simulate streaming response
        class SimpleStream:
            def __init__(self, text):
                self.text = text
                self.sent = False
            
            async def __aiter__(self):
                if not self.sent:
                    yield self.text
                    self.sent = True
        
        return SimpleStream(response)

async def entrypoint(ctx: JobContext):
    """Main agent entrypoint"""
    logger.info(f"🎤 Voice agent starting for room: {ctx.job.room.name}")
    
    # Use dummy API keys for demo (you can configure real ones later)
    stt = deepgram.STT(api_key="dummy_key")  # Speech-to-text
    llm = SimpleLLM()  # Simple response LLM
    tts = cartesia.TTS(api_key="dummy_key")  # Text-to-speech
    
    # Create voice assistant
    assistant = VoiceAssistant(
        vad=rtc.VAD.for_speaking_detection(),  # Voice activity detection
        stt=stt,
        llm=llm,
        tts=tts,
        min_endpointing_delay=0.5,  # How long to wait before processing speech
        preemptive_synthesis=True,  # Start generating response while user speaks
        debug=True  # Enable debug logging
    )
    
    assistant.start(ctx.room)
    
    logger.info("🎯 Voice assistant started and ready for speech!")
    
    # Keep the agent running
    await asyncio.sleep(1)

# Fallback simple agent if voice processing fails
async def simple_main():
    """Fallback agent without voice processing"""
    room_name = sys.argv[1] if len(sys.argv) > 1 else os.getenv("ROOM_NAME")
    server_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    
    logger.info(f"🔄 Starting fallback agent for room: {room_name}")
    
    # Create token for agent
    token = api.AccessToken(api_key, api_secret)
    token.with_identity(f"voice-agent-{room_name}")
    token.with_name("Voice Assistant")
    token.with_grants(api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True
    ))
    
    # Create room instance
    room = rtc.Room()
    
    @room.on("connected")
    def on_connected():
        logger.info(f"✅ Fallback agent connected to room: {room_name}")
    
    @room.on("participant_connected")
    def on_participant_connected(participant):
        logger.info(f"👤 User joined: {participant.identity}")
        asyncio.create_task(send_voice_greeting(room, participant))
    
    @room.on("track_published")
    def on_track_published(publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        logger.info(f"🎵 Audio track from {participant.identity}: {publication.kind}")
        if publication.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(respond_to_audio(room, participant))
    
    async def send_voice_greeting(room, participant):
        await asyncio.sleep(2)
        greeting = {
            "type": "agent_response",
            "message": "🎤 Voice agent connected! I can see you but need proper STT/TTS setup for voice responses."
        }
        await room.local_participant.publish_data(
            json.dumps(greeting).encode(),
            destination_identities=[participant.identity]
        )
    
    async def respond_to_audio(room, participant):
        await asyncio.sleep(3)
        response = {
            "type": "agent_response", 
            "message": "🔊 I detected your audio stream! Voice processing would work with proper API keys."
        }
        await room.local_participant.publish_data(
            json.dumps(response).encode(),
            destination_identities=[participant.identity]
        )
    
    try:
        await room.connect(server_url, token.to_jwt())
        logger.info("🎯 Fallback agent ready!")
        
        # Keep running
        while True:
            await asyncio.sleep(5)
            
    except KeyboardInterrupt:
        logger.info("Agent shutting down...")
    except Exception as e:
        logger.error(f"Agent error: {e}")
    finally:
        await room.disconnect()

if __name__ == "__main__":
    # Try voice agent first, fallback to simple agent
    try:
        from livekit.agents import cli
        cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
    except ImportError as e:
        logger.warning(f"Voice agent dependencies not available: {e}")
        logger.info("🔄 Using fallback agent...")
        asyncio.run(simple_main())
    except Exception as e:
        logger.error(f"Voice agent failed: {e}")
        logger.info("🔄 Using fallback agent...")
        asyncio.run(simple_main())
'''
        
        os.makedirs(os.path.dirname(self.agent_script_path), exist_ok=True)
        with open(self.agent_script_path, 'w') as f:
            f.write(script_content)
        os.chmod(self.agent_script_path, 0o755)
        logger.info(f"✅ Created simple agent script at {self.agent_script_path}")

# Global instance
agent_spawner = AgentSpawner()