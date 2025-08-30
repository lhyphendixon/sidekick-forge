#!/usr/bin/env python3
"""
Voice AI Agent for Sidekick Forge Platform
Handles session-agent-rag jobs with full STT/LLM/TTS capabilities
"""

import os
import asyncio
import logging
import json
from livekit import agents, rtc
from livekit.agents import JobContext, WorkerOptions, cli, AutoSubscribe
from livekit.plugins import openai, groq, elevenlabs, deepgram
from typing import Optional

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Agent configuration from environment
AGENT_NAME = os.getenv("AGENT_NAME", "session-agent-rag")
AGENT_SLUG = os.getenv("AGENT_SLUG", "session-agent-rag")
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

async def entrypoint(ctx: JobContext):
    """Main entry point for LiveKit agent jobs with voice AI capabilities"""
    
    # Log job start
    logger.info(f"[{AGENT_NAME}] Starting voice AI job for room: {ctx.room.name}")
    
    # Extract metadata from room and job
    metadata = {}
    try:
        if hasattr(ctx.room, 'metadata') and ctx.room.metadata:
            metadata = json.loads(ctx.room.metadata) if isinstance(ctx.room.metadata, str) else ctx.room.metadata
    except Exception as e:
        logger.warning(f"Failed to parse room metadata: {e}")
    
    # Extract agent configuration from job metadata
    agent_config = metadata.get("agent_config", {})
    if not agent_config:
        logger.error(f"[{AGENT_NAME}] No agent configuration found in metadata")
        return
    
    # Log job details
    client_id = metadata.get("client_id", "unknown")
    logger.info(f"[{AGENT_NAME}] Job metadata - Client: {client_id}")
    logger.info(f"[{AGENT_NAME}] Agent config keys: {list(agent_config.keys())}")
    
    # Connect to room first
    logger.info(f"[{AGENT_NAME}] Connecting to room: {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    logger.info(f"[{AGENT_NAME}] Voice AI agent successfully joined room: {ctx.room.name}")
    
    try:
        # Create voice AI components
        stt = create_stt(agent_config)
        tts = create_tts(agent_config) 
        llm = create_llm(agent_config)
        
        # Create voice assistant
        assistant = agents.VoiceAssistant(
            stt=stt,
            tts=tts,
            llm=llm,
            interrupt_speech_duration=0.5,
            interrupt_min_words=2,
        )
        
        # Start the assistant
        assistant.start(ctx.room)
        logger.info(f"[{AGENT_NAME}] Voice assistant started successfully")
        
        # Set up event handlers
        @ctx.room.on("participant_connected")
        def on_participant_connected(participant: rtc.RemoteParticipant):
            logger.info(f"[{AGENT_NAME}] Participant connected: {participant.identity}")
        
        @ctx.room.on("participant_disconnected") 
        def on_participant_disconnected(participant: rtc.RemoteParticipant):
            logger.info(f"[{AGENT_NAME}] Participant disconnected: {participant.identity}")
            
        @ctx.room.on("track_subscribed")
        def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                logger.info(f"[{AGENT_NAME}] Audio track subscribed from {participant.identity}")
        
        # Wait for the room to close instead of managing participants manually
        logger.info(f"[{AGENT_NAME}] Voice assistant ready - waiting for interactions...")
        await ctx.wait_for_close()
        
    except Exception as e:
        logger.error(f"[{AGENT_NAME}] Voice assistant error: {e}", exc_info=True)
        # Still wait briefly to allow debugging
        await asyncio.sleep(5)
    
    logger.info(f"[{AGENT_NAME}] Job completed for room: {ctx.room.name}")

def create_stt(config):
    """Create STT provider based on configuration"""
    groq_key = config.get("groq_api_key")
    deepgram_key = config.get("deepgram_api_key") 
    openai_key = config.get("openai_api_key")
    
    if groq_key:
        logger.info(f"[{AGENT_NAME}] Using Groq STT")
        return groq.STT(api_key=groq_key, model="whisper-large-v3")
    elif deepgram_key:
        logger.info(f"[{AGENT_NAME}] Using Deepgram STT") 
        return deepgram.STT(api_key=deepgram_key, model="nova-2")
    elif openai_key:
        logger.info(f"[{AGENT_NAME}] Using OpenAI STT")
        return openai.STT(api_key=openai_key, model="whisper-1")
    else:
        raise ValueError("No valid STT API key found")

def create_tts(config):
    """Create TTS provider based on configuration"""
    elevenlabs_key = config.get("elevenlabs_api_key")
    cartesia_key = config.get("cartesia_api_key")
    openai_key = config.get("openai_api_key")
    voice_id = config.get("voice_id", "alloy")
    
    if elevenlabs_key:
        logger.info(f"[{AGENT_NAME}] Using ElevenLabs TTS with voice: {voice_id}")
        return elevenlabs.TTS(api_key=elevenlabs_key, voice=voice_id)
    elif openai_key:
        logger.info(f"[{AGENT_NAME}] Using OpenAI TTS with voice: {voice_id}")
        return openai.TTS(api_key=openai_key, model="tts-1", voice=voice_id)
    else:
        raise ValueError("No valid TTS API key found")

def create_llm(config):
    """Create LLM provider based on configuration"""
    groq_key = config.get("groq_api_key")
    openai_key = config.get("openai_api_key")
    model = config.get("model", "gpt-4-turbo-preview")
    system_prompt = config.get("system_prompt", "You are a helpful AI assistant.")
    
    if groq_key and ("llama" in model.lower() or "mixtral" in model.lower()):
        logger.info(f"[{AGENT_NAME}] Using Groq LLM with model: {model}")
        return groq.LLM(api_key=groq_key, model=model, temperature=0.7)
    elif openai_key:
        logger.info(f"[{AGENT_NAME}] Using OpenAI LLM with model: {model}")
        return openai.LLM(api_key=openai_key, model=model, temperature=0.7)
    else:
        raise ValueError("No valid LLM API key found")

def main():
    """Main function to start the agent worker using CLI"""
    
    logger.info(f"Starting Generic LiveKit Agent Worker: {AGENT_NAME}")
    logger.info(f"LiveKit URL: {LIVEKIT_URL}")
    logger.info(f"Agent will accept jobs for: {AGENT_NAME}")
    
    # Validate configuration
    if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
        logger.error("Missing required LiveKit configuration!")
        logger.error(f"URL: {'SET' if LIVEKIT_URL else 'MISSING'}")
        logger.error(f"API Key: {'SET' if LIVEKIT_API_KEY else 'MISSING'}")
        logger.error(f"API Secret: {'SET' if LIVEKIT_API_SECRET else 'MISSING'}")
        return
    
    # Use the CLI to run the agent worker
    import sys
    sys.argv = ["generic_agent.py", "start"]
    
    # Configure worker options as module-level variable for CLI
    global worker_options
    worker_options = WorkerOptions(
        entrypoint_fnc=entrypoint,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
        ws_url=LIVEKIT_URL,
        worker_type=agents.WorkerType.ROOM,
    )
    
    logger.info(f"[{AGENT_NAME}] Worker starting...")
    cli.run_app(worker_options)

if __name__ == "__main__":
    main()