#!/usr/bin/env python3
"""
Simulate a user joining a LiveKit room to test agent response
"""
import asyncio
import os
from livekit import api, rtc
import logging
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LiveKit configuration
LIVEKIT_URL = "wss://litebridge-hw6srhvi.livekit.cloud"
LIVEKIT_API_KEY = "APIUtuiQ47BQBsk"
LIVEKIT_API_SECRET = "qLhQa9NP5J7XtKOsm7b1rH04idgdxQFJRJ4IzwIxQcjM"

async def simulate_user(room_name: str, user_id: str = "test-user"):
    """Simulate a user joining the room"""
    
    # Create a token for the user
    token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    token.with_identity(user_id)\
         .with_name(user_id)\
         .with_grants(api.VideoGrants(
             room_join=True,
             room=room_name,
             can_publish=True,
             can_subscribe=True
         ))
    
    jwt_token = token.to_jwt()
    logger.info(f"🎫 Generated user token for room {room_name}")
    
    # Create room instance
    room = rtc.Room()
    
    # Track events
    events = []
    
    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        logger.info(f"👤 Participant connected: {participant.identity}")
        events.append(f"participant_connected: {participant.identity}")
    
    @room.on("track_published")
    def on_track_published(publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        logger.info(f"📢 Track published by {participant.identity}: {publication.kind}")
        events.append(f"track_published: {participant.identity} - {publication.kind}")
    
    @room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        logger.info(f"🔊 Subscribed to track from {participant.identity}: {track.kind}")
        events.append(f"track_subscribed: {participant.identity} - {track.kind}")
        
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            logger.info("🎵 Agent is speaking!")
    
    @room.on("data_received")
    def on_data_received(data: bytes, participant: rtc.RemoteParticipant):
        logger.info(f"📊 Data received from {participant.identity}: {data.decode()}")
        events.append(f"data_received: {data.decode()}")
    
    try:
        # Connect to room
        logger.info(f"🔌 Connecting to room {room_name}...")
        await room.connect(LIVEKIT_URL, jwt_token)
        logger.info(f"✅ Connected to room as {user_id}")
        
        # List participants
        participants = list(room.remote_participants.values())
        logger.info(f"👥 Participants in room: {[p.identity for p in participants]}")
        
        # Publish audio track to trigger agent
        logger.info("🎤 Publishing audio track...")
        source = rtc.AudioSource(sample_rate=48000, num_channels=1)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        
        options = rtc.TrackPublishOptions()
        publication = await room.local_participant.publish_track(track, options)
        logger.info(f"✅ Audio track published: {publication.sid}")
        
        # Wait for agent response
        logger.info("⏳ Waiting for agent response...")
        await asyncio.sleep(10)
        
        # Check what happened
        logger.info("\n📋 Event Summary:")
        for event in events:
            logger.info(f"  - {event}")
        
        # Disconnect
        await room.disconnect()
        logger.info("👋 Disconnected from room")
        
        return events
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return []

async def main():
    if len(sys.argv) > 1:
        room_name = sys.argv[1]
    else:
        room_name = "preview_clarence-coherence_debug"
    
    logger.info(f"🚀 Starting user simulation for room: {room_name}")
    events = await simulate_user(room_name)
    
    # Check if agent responded
    agent_events = [e for e in events if "minimal-agent" in e or "agent" in e.lower()]
    if agent_events:
        logger.info(f"\n✅ Agent responded! Events: {agent_events}")
    else:
        logger.error("\n❌ No agent response detected")

if __name__ == "__main__":
    asyncio.run(main())