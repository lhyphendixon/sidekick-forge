#!/usr/bin/env python3
"""
Test to verify audio flow in LiveKit room
- Check if user audio is being published
- Check if agent can subscribe to user audio
- Monitor audio activity
"""
import asyncio
import logging
import json
from livekit import rtc, api
import os
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def monitor_room(room_name: str):
    """Monitor a LiveKit room for audio activity"""
    
    # Get LiveKit credentials
    url = os.getenv("LIVEKIT_URL", "wss://litebridge-hw6srhvi.livekit.cloud")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    
    # Create API client to get room info
    livekit_api = api.LiveKitAPI(url, api_key, api_secret)
    
    # List rooms to find our test room
    logger.info(f"Looking for room: {room_name}")
    rooms = await livekit_api.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
    
    if not rooms.rooms:
        logger.error(f"Room {room_name} not found")
        return
        
    room_info = rooms.rooms[0]
    logger.info(f"Found room: {room_info.name} (SID: {room_info.sid})")
    logger.info(f"Active participants: {room_info.num_participants}")
    
    # List participants
    participants = await livekit_api.room.list_participants(
        api.ListParticipantsRequest(room=room_name)
    )
    
    logger.info(f"\nParticipants in room:")
    for p in participants.participants:
        logger.info(f"  - {p.identity} (SID: {p.sid})")
        logger.info(f"    State: {p.state}")
        logger.info(f"    Is Publisher: {p.is_publisher}")
        logger.info(f"    Tracks:")
        for track in p.tracks:
            logger.info(f"      - {track.type}: {track.name} (SID: {track.sid})")
            logger.info(f"        Muted: {track.muted}, Simulcast: {track.simulcast}")
            if track.type == "AUDIO":
                logger.info(f"        📢 AUDIO TRACK DETECTED from {p.identity}")
    
    # Connect as a monitoring participant
    logger.info("\nConnecting as monitor to observe audio activity...")
    monitor_room = rtc.Room()
    
    audio_activity = {}
    
    @monitor_room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        logger.info(f"🟢 Participant connected: {participant.identity}")
        audio_activity[participant.identity] = {"connected": True, "has_audio": False, "audio_active": False}
        
    @monitor_room.on("track_published")
    def on_track_published(publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        logger.info(f"📡 Track published by {participant.identity}: {publication.kind}")
        if publication.kind == rtc.TrackKind.KIND_AUDIO:
            audio_activity[participant.identity]["has_audio"] = True
            logger.info(f"🎤 AUDIO TRACK PUBLISHED by {participant.identity}")
            
    @monitor_room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        logger.info(f"✅ Subscribed to {track.kind} track from {participant.identity}")
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            logger.info(f"🔊 Now receiving audio from {participant.identity}")
            
            # Monitor audio frames
            @track.on("frame_received")
            def on_frame_received(frame):
                # Log only first frame to avoid spam
                if participant.identity not in audio_activity or not audio_activity[participant.identity].get("first_frame_logged"):
                    logger.info(f"🎵 Receiving audio frames from {participant.identity}")
                    audio_activity[participant.identity]["first_frame_logged"] = True
                    audio_activity[participant.identity]["audio_active"] = True
    
    # Generate token for monitoring
    token = api.AccessToken(api_key, api_secret) \
        .with_identity("monitor") \
        .with_name("Audio Monitor") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name
        )).to_jwt()
    
    # Connect
    await monitor_room.connect(url, token)
    logger.info("✅ Monitor connected to room")
    
    # Wait and report
    logger.info("\nMonitoring for 30 seconds...")
    for i in range(30):
        await asyncio.sleep(1)
        if i % 5 == 0:
            logger.info(f"\n[{i}s] Audio Activity Summary:")
            for identity, info in audio_activity.items():
                status = []
                if info.get("connected"): status.append("Connected")
                if info.get("has_audio"): status.append("Has Audio Track")
                if info.get("audio_active"): status.append("Audio Active")
                logger.info(f"  {identity}: {' | '.join(status) if status else 'No activity'}")
    
    # Disconnect
    await monitor_room.disconnect()
    logger.info("\nMonitor disconnected")
    
    # Final summary
    logger.info("\n=== FINAL AUDIO FLOW ANALYSIS ===")
    user_count = sum(1 for id in audio_activity if id.startswith("user_"))
    agent_count = sum(1 for id in audio_activity if "agent" in id.lower())
    
    logger.info(f"Total participants seen: {len(audio_activity)}")
    logger.info(f"  - Users: {user_count}")
    logger.info(f"  - Agents: {agent_count}")
    
    # Check for issues
    issues = []
    user_has_audio = any(info.get("has_audio") for id, info in audio_activity.items() if id.startswith("user_"))
    if not user_has_audio:
        issues.append("❌ No audio track published by user")
    
    agent_present = any("agent" in id.lower() for id in audio_activity)
    if not agent_present:
        issues.append("❌ No agent participant found in room")
    
    if issues:
        logger.error("\n🚨 ISSUES DETECTED:")
        for issue in issues:
            logger.error(f"  {issue}")
    else:
        logger.info("\n✅ Audio flow appears normal")

async def main():
    # Load environment
    from dotenv import load_dotenv
    load_dotenv('/root/sidekick-forge/.env')
    
    # Get room name from command line or use default
    room_name = sys.argv[1] if len(sys.argv) > 1 else "test-debug-1753720107"
    
    logger.info(f"Testing audio flow for room: {room_name}")
    await monitor_room(room_name)

if __name__ == "__main__":
    asyncio.run(main())