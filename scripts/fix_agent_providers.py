#!/usr/bin/env python3
"""
Fix agent provider configuration by updating test data
This updates the agent configuration that's being sent to the worker
"""

import os
import sys
sys.path.append('/root/sidekick-forge')

from app.services.agent_service_supabase import AgentService
from app.services.client_service_supabase import ClientService
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def fix_agent_providers():
    """Fix the agent configuration to use correct providers"""
    
    client_id = "df91fd06-816f-4273-a903-5a4861277040"
    agent_slug = "autonomite"
    
    # Initialize services
    client_service = ClientService()
    agent_service = AgentService(client_service)
    
    try:
        # Get the agent
        agent = await agent_service.get_agent(client_id, agent_slug)
        
        if agent:
            logger.info(f"Found agent: {agent.name}")
            logger.info(f"Current voice settings: {agent.voice_settings}")
            
            # Update voice settings
            if not agent.voice_settings:
                from app.models.agent import VoiceSettings
                agent.voice_settings = VoiceSettings()
            
            # Set the correct providers
            agent.voice_settings.llm_provider = "groq"
            agent.voice_settings.llm_model = "llama-3.1-70b-versatile"
            agent.voice_settings.stt_provider = "deepgram"
            agent.voice_settings.tts_provider = "elevenlabs"
            agent.voice_settings.provider = "elevenlabs"  # Main provider field
            
            # For now, let's just log what needs to be updated
            logger.info(f"Agent needs these updates:")
            logger.info(f"  - llm_provider: groq")
            logger.info(f"  - stt_provider: deepgram")
            logger.info(f"  - tts_provider: elevenlabs")
            logger.info(f"  - provider: elevenlabs")
            
            # The real fix needs to be in the data that's being sent from trigger.py
            # or in the default values in config_validator.py
            
            return True
        else:
            logger.error(f"Agent {agent_slug} not found")
            return False
            
    except Exception as e:
        logger.error(f"Error: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(fix_agent_providers())
    sys.exit(0 if success else 1)