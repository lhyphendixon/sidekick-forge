#!/usr/bin/env python3
"""
Client-specific LiveKit Agent
Runs in an isolated container per WordPress site
"""

import os
import asyncio
import logging
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

# Load configuration from environment
config = AgentConfig()

async def entrypoint(ctx: JobContext):
    """Main entry point for LiveKit agent jobs"""
    
    # Log job start
    logger.info(f"Starting job for room: {ctx.room.name}")
    logger.info(f"Site ID: {config.site_id}, Agent: {config.agent_slug}")
    
    # Extract metadata
    metadata = ctx.room.metadata or {}
    
    # Check if this room is for our site/agent
    room_site_id = metadata.get("site_id")
    room_agent_slug = metadata.get("agent_slug")
    
    if room_site_id != config.site_id or room_agent_slug != config.agent_slug:
        logger.info(f"Room not for this agent. Expected {config.site_id}/{config.agent_slug}, got {room_site_id}/{room_agent_slug}")
        return
    
    # Create and run voice assistant
    assistant = VoiceAssistant(config, ctx)
    await assistant.run()
    
    logger.info(f"Job completed for room: {ctx.room.name}")

async def main():
    """Main function to run the agent worker"""
    
    logger.info(f"Starting LiveKit Agent Worker")
    logger.info(f"Container: {config.container_name}")
    logger.info(f"Site ID: {config.site_id}")
    logger.info(f"Agent: {config.agent_slug}")
    
    # Start health check server
    health_server = HealthServer(config)
    health_task = asyncio.create_task(health_server.start())
    
    # Configure worker options
    worker_options = WorkerOptions(
        entrypoint_fnc=entrypoint,
        api_key=config.livekit_api_key,
        api_secret=config.livekit_api_secret,
        ws_url=config.livekit_url,
        worker_type=agents.WorkerType.ROOM,
        max_idle_time=30.0,  # Disconnect after 30s of no activity
        num_idle_processes=0,  # Don't keep idle processes
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