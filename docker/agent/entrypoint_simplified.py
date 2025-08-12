#!/usr/bin/env python3
"""
Simplified LiveKit Agent Worker Entrypoint
Reduces startup time and fixes initialization timeout issues
"""

import asyncio
import os
import json
import logging
import sys
from typing import Dict, Any

# Configure logging first
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Lazy imports to reduce startup time
from livekit import agents
from livekit.agents import JobContext, JobRequest, WorkerOptions, cli

# Global flag to track if plugins are loaded
_plugins_loaded = False
_plugin_instances = {}

def lazy_load_plugins():
    """Lazy load plugins only when needed to reduce startup time"""
    global _plugins_loaded, _plugin_instances
    if _plugins_loaded:
        return _plugin_instances
    
    logger.info("Loading LiveKit plugins...")
    from livekit.agents import llm, voice
    from livekit.plugins import deepgram, elevenlabs, openai, groq, silero, cartesia
    
    _plugin_instances = {
        'llm': llm,
        'voice': voice,
        'deepgram': deepgram,
        'elevenlabs': elevenlabs,
        'openai': openai,
        'groq': groq,
        'silero': silero,
        'cartesia': cartesia
    }
    _plugins_loaded = True
    logger.info("Plugins loaded successfully")
    return _plugin_instances


async def agent_job_handler(ctx: JobContext):
    """
    Simplified agent job handler with better error handling and reduced startup time
    """
    logger.info(f"[JOB START] Received job for room: {ctx.room.name}")
    
    try:
        # Lazy load plugins
        plugins = lazy_load_plugins()
        voice = plugins['voice']
        llm = plugins['llm']
        
        # Connect to room immediately
        logger.info("Connecting to room...")
        await ctx.connect(auto_subscribe="audio_only")
        logger.info("✅ Connected to room")
        
        # Get metadata from room
        metadata = {}
        try:
            from livekit import api
            
            livekit_url = os.getenv("LIVEKIT_URL")
            livekit_api_key = os.getenv("LIVEKIT_API_KEY")
            livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")
            
            if all([livekit_url, livekit_api_key, livekit_api_secret]):
                livekit_api = api.LiveKitAPI(
                    url=livekit_url,
                    api_key=livekit_api_key,
                    api_secret=livekit_api_secret
                )
                
                rooms = await livekit_api.room.list_rooms(
                    api.ListRoomsRequest(names=[ctx.room.name])
                )
                
                if rooms.rooms and rooms.rooms[0].metadata:
                    metadata = json.loads(rooms.rooms[0].metadata)
                    logger.info(f"Loaded metadata with {len(metadata)} fields")
        except Exception as e:
            logger.warning(f"Could not load room metadata: {e}")
        
        # Load API keys from metadata or environment
        api_keys = metadata.get('api_keys', {})
        
        # Log what we have
        logger.info(f"API keys in metadata: {list(api_keys.keys())}")
        
        # Configure providers from metadata
        system_prompt = metadata.get("system_prompt", "You are a helpful AI assistant. Start with a friendly greeting.")
        voice_settings = metadata.get("voice_settings", {})
        
        # LLM setup - respect configured provider
        llm_provider = voice_settings.get("llm_provider", metadata.get("llm_provider", "openai"))
        if llm_provider == "groq":
            groq_key = api_keys.get('groq_api_key')
            if not groq_key:
                raise ValueError("Groq API key required but not found")
            groq = plugins['groq']
            llm_plugin = groq.LLM(
                model="llama-3.3-70b-versatile",
                api_key=groq_key
            )
        else:
            openai_key = api_keys.get('openai_api_key')
            if not openai_key:
                raise ValueError("OpenAI API key required but not found")
            openai = plugins['openai']
            llm_plugin = openai.LLM(
                model="gpt-4o-mini",
                api_key=openai_key
            )
        
        # STT setup - respect configured provider
        stt_provider = voice_settings.get("stt_provider", "deepgram")
        if stt_provider == "cartesia":
            cartesia_key = api_keys.get('cartesia_api_key')
            if not cartesia_key:
                raise ValueError("Cartesia API key required for STT but not found")
            cartesia = plugins['cartesia']
            stt_plugin = cartesia.STT(api_key=cartesia_key)
        else:
            deepgram_key = api_keys.get('deepgram_api_key')
            if not deepgram_key:
                raise ValueError("Deepgram API key required for STT but not found")
            deepgram = plugins['deepgram']
            stt_plugin = deepgram.STT(api_key=deepgram_key)
        
        # TTS setup - respect configured provider (NO FALLBACK)
        tts_provider = voice_settings.get("tts_provider", "cartesia")
        if tts_provider == "elevenlabs":
            elevenlabs_key = api_keys.get('elevenlabs_api_key')
            if not elevenlabs_key:
                raise ValueError("ElevenLabs API key required for TTS but not found")
            elevenlabs = plugins['elevenlabs']
            tts_plugin = elevenlabs.TTS(
                voice_id=voice_settings.get("voice_id", "Xb7hH8MSUJpSbSDYk0k2"),
                api_key=elevenlabs_key
            )
        else:  # cartesia
            cartesia_key = api_keys.get('cartesia_api_key')
            if not cartesia_key:
                raise ValueError("Cartesia API key required for TTS but not found")
            cartesia = plugins['cartesia']
            # Set environment variable as Cartesia plugin requires it
            os.environ['CARTESIA_API_KEY'] = cartesia_key
            tts_plugin = cartesia.TTS(
                voice=voice_settings.get("voice_id", "248be419-c632-4f23-adf1-5324ed7dbf1d")
            )
        
        # Simple VAD
        silero = plugins['silero']
        vad = silero.VAD.load()
        
        # Create minimal agent
        logger.info("Creating voice agent...")
        agent = voice.Agent(instructions=system_prompt)
        
        # Create session with minimal configuration
        session = voice.AgentSession(
            vad=vad,
            stt=stt_plugin,
            llm=llm_plugin,
            tts=tts_plugin
        )
        
        # Add basic event handlers (must be sync, not async)
        @session.on("user_speech_committed")
        def on_user_speech(msg: llm.ChatMessage):
            logger.info(f"💬 User: {msg.content}")
        
        @session.on("agent_speech_committed")
        def on_agent_speech(msg: llm.ChatMessage):
            logger.info(f"🤖 Agent: {msg.content}")
        
        @session.on("user_started_speaking")
        def on_user_started():
            logger.info("🎤 User started speaking")
        
        @session.on("agent_started_speaking")
        def on_agent_started():
            logger.info("🔊 Agent started speaking")
        
        # Start the session
        logger.info("Starting agent session...")
        await session.start(room=ctx.room, agent=agent)
        logger.info("✅ Agent session started successfully")
        
        # Check for participants
        if hasattr(ctx.room, 'remote_participants'):
            participant_count = len(ctx.room.remote_participants)
            logger.info(f"Current participants: {participant_count}")
        
    except Exception as e:
        logger.error(f"❌ Error in agent job: {e}", exc_info=True)
        raise


async def request_filter(job_request: JobRequest) -> None:
    """Simple request filter - accept all jobs for our agent"""
    our_agent_name = os.getenv("AGENT_NAME", "sidekick-agent")
    
    if job_request.agent_name == our_agent_name:
        logger.info(f"✅ Accepting job {job_request.job.id}")
        await job_request.accept()
    else:
        logger.info(f"❌ Rejecting job - wrong agent name")
        await job_request.reject()


if __name__ == "__main__":
    # Simplified startup
    logger.info("=== SIDEKICK AGENT STARTING ===")
    
    # Check required environment variables
    url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    
    if not all([url, api_key, api_secret]):
        logger.critical("Missing LiveKit credentials")
        sys.exit(1)
    
    agent_name = os.getenv("AGENT_NAME", "sidekick-agent")
    logger.info(f"Agent name: {agent_name}")
    logger.info(f"LiveKit URL: {url}")
    
    # Create worker with increased timeouts
    worker_options = WorkerOptions(
        entrypoint_fnc=agent_job_handler,
        request_fnc=request_filter,
        agent_name=agent_name,
        num_idle_processes=1,  # Keep one process ready
        shutdown_process_timeout=30.0,  # Increase shutdown timeout
        initialize_process_timeout=30.0  # Increase init timeout
    )
    
    # Start the worker
    try:
        cli.run_app(worker_options)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Worker failed: {e}", exc_info=True)
        sys.exit(1)