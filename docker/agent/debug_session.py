#!/usr/bin/env python3
"""Debug AgentSession behavior"""

import asyncio
import logging
from livekit.agents import voice, llm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Check AgentSession initialization
logger.info("Checking AgentSession initialization options...")

# Try to create a minimal session
try:
    # Check what parameters AgentSession accepts
    import inspect
    sig = inspect.signature(voice.AgentSession.__init__)
    logger.info(f"AgentSession.__init__ signature: {sig}")
    
    # Check for session.start signature
    if hasattr(voice.AgentSession, 'start'):
        start_sig = inspect.signature(voice.AgentSession.start)
        logger.info(f"AgentSession.start signature: {start_sig}")
except Exception as e:
    logger.error(f"Error inspecting AgentSession: {e}")

# Check if there's a way to configure auto-subscribe
logger.info("\nChecking for auto-subscribe options...")
if hasattr(voice, 'VoiceSessionOptions'):
    logger.info("VoiceSessionOptions found!")
    logger.info(f"VoiceSessionOptions attributes: {dir(voice.VoiceSessionOptions)}")
    
# Check room connection options
logger.info("\nChecking room connection options...")
from livekit import rtc
if hasattr(rtc, 'RoomOptions'):
    logger.info("RoomOptions found!")
    sig = inspect.signature(rtc.RoomOptions.__init__)
    logger.info(f"RoomOptions.__init__ signature: {sig}")