#!/usr/bin/env python3
"""
Client-specific LiveKit Agent
Runs in an isolated container per WordPress site
"""

import os
import asyncio
import logging
import json
from livekit import agents, rtc
from livekit.agents import JobContext, WorkerOptions, cli
from typing import Optional

from .config import AgentConfig
from .voice_assistant import VoiceAssistant
from .health_server import HealthServer

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load global configuration from environment for worker setup
config = AgentConfig()

async def entrypoint(ctx: JobContext):
    """Main entry point for LiveKit agent jobs"""
    logger.info(f"Received job for room: {ctx.room.name}, job id: {ctx.job.id}")

    metadata = {}
    if ctx.job.metadata:
        try:
            metadata = json.loads(ctx.job.metadata)
        except json.JSONDecodeError:
            logger.error("Failed to parse job metadata", exc_info=True)
            return

    try:
        # Create a job-specific configuration from the dispatched metadata,
        # falling back to the worker's environment config for base settings.
        job_config = AgentConfig(
            agent_slug=metadata.get("agent_slug"),
            agent_name=metadata.get("agent_name"),
            system_prompt=metadata.get("system_prompt"),
            model=metadata.get("model"),
            temperature=float(metadata.get("temperature", config.temperature)),
            max_tokens=int(metadata.get("max_tokens", config.max_tokens)),
            
            # Voice and STT/TTS settings from nested dicts
            voice_id=metadata.get("voice_settings", {}).get("voice_id"),
            stt_provider=metadata.get("voice_settings", {}).get("stt_provider"),
            stt_model=metadata.get("voice_settings", {}).get("stt_model"),
            tts_provider=metadata.get("voice_settings", {}).get("tts_provider"),
            tts_model=metadata.get("voice_settings", {}).get("tts_model"),
            
            # API keys from nested dict
            openai_api_key=metadata.get("api_keys", {}).get("openai_api_key"),
            groq_api_key=metadata.get("api_keys", {}).get("groq_api_key"),
            elevenlabs_api_key=metadata.get("api_keys", {}).get("elevenlabs_api_key"),
            deepgram_api_key=metadata.get("api_keys", {}).get("deepgram_api_key"),
            
            # Base worker settings are inherited from the environment
            site_id=config.site_id,
            container_name=config.container_name,
            livekit_url=config.livekit_url,
            livekit_api_key=config.livekit_api_key,
            livekit_api_secret=config.livekit_api_secret,
        )
        logger.info(f"Created job-specific config for agent: {job_config.agent_slug}")
    except Exception as e:
        logger.error(f"Failed to create job-specific config from metadata: {e}", exc_info=True)
        return

    # Create and run voice assistant with the job-specific config
    assistant = VoiceAssistant(job_config, ctx)
    await assistant.run()

    logger.info(f"Job completed for room: {ctx.room.name}")


async def main():
    """Main function to run the agent worker"""
    
    logger.info(f"Starting LiveKit Agent Worker")
    logger.info(f"Container: {config.container_name}")
    logger.info(f"Site ID: {config.site_id}")
    
    # Start health check server
    health_server = HealthServer(config)
    health_task = asyncio.create_task(health_server.start())
    
    # Configure worker options for a general worker pool.
    # This worker will accept any job dispatched by the backend.
    worker_options = WorkerOptions(
        entrypoint_fnc=entrypoint,
        api_key=config.livekit_api_key,
        api_secret=config.livekit_api_secret,
        ws_url=config.livekit_url,
        worker_type=agents.WorkerType.ROOM, # Listen for room-based jobs
    )
    
    # Run the worker
    try:
        await cli.run_app(worker_options)
    finally:
        # Stop health server
        health_server.stop()
        await health_task

if __name__ == "__main__":
    asyncio.run(main())