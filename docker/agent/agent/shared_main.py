#!/usr/bin/env python3
"""
Shared LiveKit Agent for Sidekick Forge Platform
Handles session-agent-rag jobs with full STT/LLM/TTS capabilities
Adapted from production agent for shared worker pool architecture
"""

import os
import asyncio
import logging
import json
from livekit import agents, rtc
from livekit.agents import JobContext, WorkerOptions, cli, AutoSubscribe
from typing import Optional

from .voice_assistant import VoiceAssistant
from .health_server import HealthServer

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Agent configuration from environment
AGENT_NAME = os.getenv("AGENT_NAME", "session-agent-rag")
AGENT_SLUG = os.getenv("AGENT_SLUG", "session-agent-rag")

class AgentConfig:
    """Simplified configuration for shared worker pool"""
    
    def __init__(self):
        # Agent identification
        self.agent_slug = AGENT_SLUG
        self.site_id = os.getenv("SITE_ID", "default")
        
        # LiveKit configuration
        self.livekit_url = os.getenv("LIVEKIT_URL", "")
        self.livekit_api_key = os.getenv("LIVEKIT_API_KEY", "")
        self.livekit_api_secret = os.getenv("LIVEKIT_API_SECRET", "")
        
        # Agent configuration
        self.agent_name = os.getenv("AGENT_NAME", "Assistant")
        self.system_prompt = os.getenv("SYSTEM_PROMPT", "You are a helpful AI assistant for voice conversations.")
        self.model = os.getenv("MODEL", "gpt-4-turbo-preview")
        self.temperature = float(os.getenv("TEMPERATURE", "0.7"))
        self.max_tokens = int(os.getenv("MAX_TOKENS", "4096"))
        
        # Voice configuration
        self.voice_id = os.getenv("VOICE_ID", "alloy")
        self.stt_provider = os.getenv("STT_PROVIDER", "groq")
        self.stt_model = os.getenv("STT_MODEL", "whisper-large-v3-turbo")
        self.tts_provider = os.getenv("TTS_PROVIDER", "openai")
        self.tts_model = os.getenv("TTS_MODEL", "tts-1")
        
        # API Keys
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        self.cartesia_api_key = os.getenv("CARTESIA_API_KEY")
        self.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        
        # Monitoring
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.enable_metrics = os.getenv("ENABLE_METRICS", "true").lower() == "true"
    
    def validate(self):
        """Validate required configuration"""
        required_fields = [
            "livekit_url", "livekit_api_key", "livekit_api_secret"
        ]
        
        missing = []
        for field in required_fields:
            if not getattr(self, field):
                missing.append(field)
        
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")
        
        # Note: API key validation is relaxed here because keys can come from job metadata
        # The actual validation happens at runtime when creating providers

async def entrypoint(ctx: JobContext):
    """Main entry point for LiveKit agent jobs"""
    
    # Log job start
    logger.info(f"[{AGENT_NAME}] Starting voice AI job for room: {ctx.room.name}")
    
    # Extract metadata from job request (LiveKit Agent dispatch metadata)
    metadata = {}
    try:
        # Debug: Log available context attributes
        logger.info(f"[{AGENT_NAME}] JobContext attributes: {[attr for attr in dir(ctx) if not attr.startswith('_')]}")
        
        # Debug: Check job metadata value
        if hasattr(ctx, 'job'):
            logger.info(f"[{AGENT_NAME}] Job attributes: {[attr for attr in dir(ctx.job) if not attr.startswith('_')]}")
            if hasattr(ctx.job, 'metadata'):
                raw_metadata = ctx.job.metadata
                logger.info(f"[{AGENT_NAME}] Raw job metadata type: {type(raw_metadata)}")
                logger.info(f"[{AGENT_NAME}] Raw job metadata value: {raw_metadata}")
                logger.info(f"[{AGENT_NAME}] Raw job metadata length: {len(raw_metadata) if raw_metadata else 0}")
            else:
                logger.warning(f"[{AGENT_NAME}] Job has no metadata attribute")
        
        # Try job metadata first (from agent dispatch)
        if hasattr(ctx, 'job') and hasattr(ctx.job, 'metadata') and ctx.job.metadata:
            metadata = json.loads(ctx.job.metadata) if isinstance(ctx.job.metadata, str) else ctx.job.metadata
            logger.info(f"[{AGENT_NAME}] Using job metadata: {list(metadata.keys())}")
        # Fallback to room metadata
        elif hasattr(ctx.room, 'metadata') and ctx.room.metadata:
            metadata = json.loads(ctx.room.metadata) if isinstance(ctx.room.metadata, str) else ctx.room.metadata
            logger.info(f"[{AGENT_NAME}] Using room metadata: {list(metadata.keys())}")
        else:
            logger.warning(f"[{AGENT_NAME}] No metadata found in job or room")
    except Exception as e:
        logger.warning(f"[{AGENT_NAME}] Failed to parse metadata: {e}")
    
    # Extract client configuration from job metadata if available
    client_id = metadata.get("client_id", "unknown")
    agent_config_data = metadata.get("agent_config", {})
    
    logger.info(f"[{AGENT_NAME}] Job metadata - Client: {client_id}")
    logger.info(f"[{AGENT_NAME}] Raw metadata keys: {list(metadata.keys())}")
    logger.info(f"[{AGENT_NAME}] Agent config keys: {list(agent_config_data.keys())}")
    
    # Debug: Log presence of API keys in agent_config_data
    if agent_config_data:
        api_key_status = {}
        for key in ["openai_api_key", "groq_api_key", "elevenlabs_api_key", "deepgram_api_key", "cartesia_api_key"]:
            has_key = key in agent_config_data
            value = agent_config_data.get(key, "")
            api_key_status[key] = f"Present: {has_key}, Length: {len(value) if value else 0}"
        logger.info(f"[{AGENT_NAME}] API key status in agent_config: {api_key_status}")
    else:
        logger.warning(f"[{AGENT_NAME}] agent_config_data is empty or None!")
    
    # Create configuration object
    config = AgentConfig()
    
    # Override config with job-specific API keys if provided
    if agent_config_data:
        logger.info(f"[{AGENT_NAME}] Applying client-specific configuration overrides...")
        
        if "openai_api_key" in agent_config_data:
            config.openai_api_key = agent_config_data["openai_api_key"]
            logger.info(f"[{AGENT_NAME}] ✅ Set OpenAI API key from job metadata (length: {len(config.openai_api_key) if config.openai_api_key else 0})")
        if "groq_api_key" in agent_config_data:
            config.groq_api_key = agent_config_data["groq_api_key"]
            logger.info(f"[{AGENT_NAME}] ✅ Set Groq API key from job metadata (length: {len(config.groq_api_key) if config.groq_api_key else 0})")
        if "elevenlabs_api_key" in agent_config_data:
            config.elevenlabs_api_key = agent_config_data["elevenlabs_api_key"]
            logger.info(f"[{AGENT_NAME}] ✅ Set ElevenLabs API key from job metadata (length: {len(config.elevenlabs_api_key) if config.elevenlabs_api_key else 0})")
        if "deepgram_api_key" in agent_config_data:
            config.deepgram_api_key = agent_config_data["deepgram_api_key"]
            logger.info(f"[{AGENT_NAME}] ✅ Set Deepgram API key from job metadata (length: {len(config.deepgram_api_key) if config.deepgram_api_key else 0})")
        if "cartesia_api_key" in agent_config_data:
            config.cartesia_api_key = agent_config_data["cartesia_api_key"]
            logger.info(f"[{AGENT_NAME}] ✅ Set Cartesia API key from job metadata (length: {len(config.cartesia_api_key) if config.cartesia_api_key else 0})")
        if "system_prompt" in agent_config_data:
            config.system_prompt = agent_config_data["system_prompt"]
            logger.info(f"[{AGENT_NAME}] ✅ Set system prompt from job metadata")
        if "model" in agent_config_data:
            config.model = agent_config_data["model"]
            logger.info(f"[{AGENT_NAME}] ✅ Set model from job metadata: {config.model}")
        if "voice_id" in agent_config_data:
            config.voice_id = agent_config_data["voice_id"]
            logger.info(f"[{AGENT_NAME}] ✅ Set voice_id from job metadata: {config.voice_id}")
        
        # Log final API key status after overrides
        final_key_status = {
            "openai": len(config.openai_api_key) if config.openai_api_key else 0,
            "groq": len(config.groq_api_key) if config.groq_api_key else 0,
            "elevenlabs": len(config.elevenlabs_api_key) if config.elevenlabs_api_key else 0,
            "deepgram": len(config.deepgram_api_key) if config.deepgram_api_key else 0,
            "cartesia": len(config.cartesia_api_key) if config.cartesia_api_key else 0,
        }
        logger.info(f"[{AGENT_NAME}] Final API key lengths after override: {final_key_status}")
    else:
        logger.warning(f"[{AGENT_NAME}] No agent_config_data available for overrides - using environment defaults")
    
    try:
        # Create and run voice assistant
        assistant = VoiceAssistant(config, ctx)
        await assistant.run()
        
    except Exception as e:
        logger.error(f"[{AGENT_NAME}] Voice assistant error: {e}", exc_info=True)
        # Still wait briefly to allow debugging
        await asyncio.sleep(5)
    
    logger.info(f"[{AGENT_NAME}] Job completed for room: {ctx.room.name}")

async def main():
    """Main function to run the agent worker"""
    
    logger.info(f"Starting Shared LiveKit Agent Worker: {AGENT_NAME}")
    
    # Load configuration
    try:
        config = AgentConfig()
        logger.info("AgentConfig created successfully")
    except Exception as e:
        logger.error(f"Configuration error: {e}")
        return
    
    logger.info(f"LiveKit URL: {config.livekit_url}")
    logger.info(f"Agent will register with name: {AGENT_NAME}")
    logger.info(f"Explicit dispatch enabled for agent: {AGENT_NAME}")
    logger.info(f"STT Provider: {config.stt_provider}")
    logger.info(f"Groq API Key present: {bool(config.groq_api_key)}")
    
    # Validate core LiveKit configuration only
    try:
        # Only validate LiveKit credentials at startup - API keys validated per job
        required_fields = ["livekit_url", "livekit_api_key", "livekit_api_secret"]
        missing = []
        for field in required_fields:
            if not getattr(config, field):
                missing.append(field)
        
        if missing:
            raise ValueError(f"Missing required LiveKit configuration: {', '.join(missing)}")
            
        logger.info("✅ LiveKit configuration validated")
        logger.info("ℹ️  AI service API keys will be validated per job from client configuration")
            
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return
    
    # Start health check server
    health_server = HealthServer(config)
    health_task = asyncio.create_task(health_server.start())
    
    # Configure worker options
    logger.info(f"[{AGENT_NAME}] Configuring worker with agent name: {AGENT_NAME}")
    worker_options = WorkerOptions(
        entrypoint_fnc=entrypoint,
        api_key=config.livekit_api_key,
        api_secret=config.livekit_api_secret,
        ws_url=config.livekit_url,
        worker_type=agents.WorkerType.ROOM,
        agent_name=AGENT_NAME,  # Enable explicit dispatch for named agent
    )
    logger.info(f"[{AGENT_NAME}] Worker configured for explicit dispatch to agent: {AGENT_NAME}")
    
    # Run the worker
    try:
        logger.info(f"[{AGENT_NAME}] Worker starting...")
        
        # Create and start the worker directly without CLI
        worker = agents.Worker(worker_options)
        await worker.run()
        
    finally:
        # Stop health server
        health_server.stop()
        await health_task

if __name__ == "__main__":
    asyncio.run(main())