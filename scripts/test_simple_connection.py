#!/usr/bin/env python3
"""
Simple test to connect to LiveKit room and publish audio
"""
import asyncio
import logging
import numpy as np
from livekit import rtc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_connection():
    """Test basic connection and audio publishing"""
    
    # Use the token from the trigger response
    server_url = "wss://litebridge-hw6srhvi.livekit.cloud"
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJtZXRhZGF0YSI6InsndXNlcl9pZCc6ICd0ZXN0LXVzZXInLCAnY2xpZW50X2lkJzogJ2RmOTFmZDA2LTgxNmYtNDI3My1hOTAzLTVhNDg2MTI3NzA0MCd9IiwidmlkZW8iOnsicm9vbUpvaW4iOnRydWUsInJvb20iOiJ0ZXN0LWRlYnVnLTE3NTM3MjAxMDciLCJjYW5QdWJsaXNoIjp0cnVlLCJjYW5TdWJzY3JpYmUiOnRydWUsImNhblB1Ymxpc2hEYXRhIjp0cnVlfSwic3ViIjoidXNlcl90ZXN0LXVzZXIiLCJpc3MiOiJBUElyWmFWVkd0cTVQQ1giLCJuYmYiOjE3NTM3MjAxMTEsImV4cCI6MTc1MzcyMzcxMX0.qQroJn4var2WeqUn1oowxu8SZPaSC7Q8kuFVnD9JLKc"
    
    room = rtc.Room()
    
    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        logger.info(f"Participant connected: {participant.identity}")
        
    @room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        logger.info(f"Subscribed to {track.kind} track from {participant.identity}")
        if participant.identity.startswith("agent") and track.kind == rtc.TrackKind.KIND_AUDIO:
            logger.info("🎉 Agent audio track detected! Agent can speak!")
    
    # Connect
    logger.info("Connecting to room...")
    await room.connect(server_url, token)
    logger.info("Connected!")
    
    # Wait to see participants
    await asyncio.sleep(2)
    logger.info(f"Participants in room: {list(room.remote_participants.keys())}")
    
    # Create and publish audio track
    logger.info("Publishing audio track...")
    source = rtc.AudioSource(48000, 1)
    track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
    await room.local_participant.publish_track(track, rtc.TrackPublishOptions())
    logger.info("Audio track published")
    
    # Send a simple tone to test
    logger.info("Sending test audio...")
    sample_rate = 48000
    duration = 2.0
    t = np.linspace(0, duration, int(sample_rate * duration))
    
    # Create a clear speech-like pattern
    audio = np.zeros_like(t)
    # Add speech bursts
    for start_time in [0.2, 0.6, 1.0, 1.4]:
        mask = (t >= start_time) & (t < start_time + 0.2)
        audio[mask] = 0.5 * np.sin(2 * np.pi * 300 * t[mask])  # 300 Hz tone
    
    # Convert to int16
    audio_int16 = (audio * 32767).astype(np.int16)
    
    # Send in frames
    frame_duration = 0.02  # 20ms
    samples_per_frame = int(sample_rate * frame_duration)
    
    for i in range(0, len(audio_int16), samples_per_frame):
        chunk = audio_int16[i:i+samples_per_frame]
        if len(chunk) < samples_per_frame:
            chunk = np.pad(chunk, (0, samples_per_frame - len(chunk)))
            
        frame = rtc.AudioFrame.create(
            sample_rate=sample_rate,
            num_channels=1,
            samples_per_channel=samples_per_frame
        )
        frame.data[:len(chunk.tobytes())] = chunk.tobytes()
        await source.capture_frame(frame)
        await asyncio.sleep(frame_duration)
    
    logger.info("Test audio sent")
    
    # Wait for response
    logger.info("Waiting for agent response...")
    await asyncio.sleep(10)
    
    # Disconnect
    await room.disconnect()
    logger.info("Test complete")

if __name__ == "__main__":
    asyncio.run(test_connection())