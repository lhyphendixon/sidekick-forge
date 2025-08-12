#!/usr/bin/env python3
"""
Test if agent is responding to audio input
"""
import asyncio
import logging
from livekit import api
from dotenv import load_dotenv
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv('/root/sidekick-forge/.env')

LIVEKIT_URL = os.getenv('LIVEKIT_URL')
LIVEKIT_API_KEY = os.getenv('LIVEKIT_API_KEY')
LIVEKIT_API_SECRET = os.getenv('LIVEKIT_API_SECRET')

async def check_recent_rooms():
    """Check recent rooms and their participants"""
    livekit_api = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    
    try:
        response = await livekit_api.room.list_rooms(api.ListRoomsRequest())
        if response.rooms:
            for room in response.rooms[-3:]:  # Last 3 rooms
                logger.info(f"\nRoom: {room.name}")
                logger.info(f"  Created: {room.creation_time}")
                logger.info(f"  Participants: {room.num_participants}")
                
                # List participants
                participants = await livekit_api.room.list_participants(
                    api.ListParticipantsRequest(room=room.name)
                )
                
                for p in participants.participants:
                    logger.info(f"  - {p.identity} (joined: {p.joined_at})")
                    logger.info(f"    Tracks: {len(p.tracks)}")
                    for track in p.tracks:
                        logger.info(f"      - {track.type}: {track.name} (muted: {track.muted})")
        else:
            logger.info("No active rooms")
            
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(check_recent_rooms())