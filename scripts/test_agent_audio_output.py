#!/usr/bin/env python3
"""
Test if agent is publishing audio tracks to LiveKit room
"""
import asyncio
import logging
import os
from livekit import api, rtc
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv('/root/sidekick-forge/.env')

LIVEKIT_URL = os.getenv('LIVEKIT_URL', 'wss://litebridge-hw6srhvi.livekit.cloud')
LIVEKIT_API_KEY = os.getenv('LIVEKIT_API_KEY')
LIVEKIT_API_SECRET = os.getenv('LIVEKIT_API_SECRET')


async def monitor_room(room_name: str):
    """Monitor a room for audio tracks from the agent"""
    logger.info(f"Monitoring room: {room_name}")
    
    # Create room instance
    room = rtc.Room()
    
    # Track what we receive
    tracks_received = []
    
    @room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        logger.info(f"🔊 Track subscribed from {participant.identity}: {track.kind}")
        tracks_received.append({
            'participant': participant.identity,
            'track_kind': track.kind,
            'track_name': track.name
        })
        
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            logger.info("✅ Agent audio track received! Agent can speak.")
        
    @room.on("track_published") 
    def on_track_published(publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        logger.info(f"📢 Track published by {participant.identity}: {publication.kind}")
        
    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        logger.info(f"👤 Participant connected: {participant.identity}")
        
    # Generate token
    token_request = api.VideoGrants(
        room_join=True,
        room=room_name
    )
    
    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET).with_identity(
        "monitor"
    ).with_grants(token_request).to_jwt()
    
    # Connect to room
    try:
        await room.connect(LIVEKIT_URL, token)
        logger.info(f"✅ Connected to room: {room_name}")
        
        # Monitor for 30 seconds
        logger.info("Monitoring for agent audio tracks...")
        await asyncio.sleep(30)
        
        # Report results
        logger.info(f"\n📊 Summary:")
        logger.info(f"Total tracks received: {len(tracks_received)}")
        for track in tracks_received:
            logger.info(f"  - {track['participant']}: {track['track_kind']} ({track['track_name']})")
            
        if not any(t['track_kind'] == rtc.TrackKind.KIND_AUDIO for t in tracks_received):
            logger.error("❌ No audio tracks received from agent!")
        
    finally:
        await room.disconnect()


async def main():
    # Get the most recent room
    livekit_api = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    
    try:
        response = await livekit_api.room.list_rooms(api.ListRoomsRequest())
        if response.rooms:
            # Get the most recent room
            room = response.rooms[-1]
            logger.info(f"Found room: {room.name}")
            await monitor_room(room.name)
        else:
            logger.error("No active rooms found")
    except Exception as e:
        logger.error(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())