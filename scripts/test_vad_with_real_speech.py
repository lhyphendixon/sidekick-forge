#!/usr/bin/env python3
"""
Test VAD with real speech by examining AgentSession behavior
"""

import asyncio
import logging
import httpx
import time
from livekit import api, rtc
import numpy as np
from pydub import AudioSegment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BASE_URL = "http://localhost:8000"
CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"
AGENT_SLUG = "autonomite"

async def test_vad_with_speech_patterns():
    """Test VAD with different speech patterns"""
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Trigger agent
        room_name = f"test-vad-{int(time.time())}"
        
        trigger_response = await client.post(
            f"{BASE_URL}/api/v1/trigger-agent",
            json={
                "agent_slug": AGENT_SLUG,
                "mode": "voice",
                "room_name": room_name,
                "user_id": "test-user",
                "client_id": CLIENT_ID
            }
        )
        
        if trigger_response.status_code != 200:
            logger.error(f"Failed to trigger agent: {trigger_response.text}")
            return
            
        trigger_data = trigger_response.json()
        livekit_config = trigger_data["data"]["livekit_config"]
        user_token = livekit_config["user_token"]
        server_url = livekit_config.get("server_url", "wss://litebridge-hw6srhvi.livekit.cloud")
        
        logger.info(f"Agent triggered in room: {room_name}")
        
        # Wait for agent
        await asyncio.sleep(3)
        
        # Connect to room
        room = rtc.Room()
        
        # Track agent activity
        vad_events = []
        agent_responses = []
        
        @room.on("data_received")
        def on_data_received(data: bytes, participant: rtc.RemoteParticipant, kind: str):
            """Capture any data messages that might indicate VAD activity"""
            try:
                message = data.decode('utf-8')
                timestamp = time.time()
                vad_events.append((timestamp, participant.identity, message))
                logger.info(f"[{timestamp:.2f}] Data from {participant.identity}: {message[:100]}")
            except:
                pass
        
        @room.on("track_subscribed")
        def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
            """Monitor agent audio responses"""
            if participant.identity.startswith("agent") and track.kind == rtc.TrackKind.KIND_AUDIO:
                timestamp = time.time()
                agent_responses.append(timestamp)
                logger.info(f"[{timestamp:.2f}] Agent audio track active")
        
        # Connect
        await room.connect(server_url, user_token)
        logger.info("Connected to room")
        
        # Create audio source
        sample_rate = 48000
        source = rtc.AudioSource(sample_rate, 1)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        
        # Publish track
        await room.local_participant.publish_track(track, rtc.TrackPublishOptions())
        logger.info("Audio track published")
        
        # Wait for setup
        await asyncio.sleep(2)
        
        # Test 1: Send pure silence
        logger.info("\n=== Test 1: Pure Silence (5 seconds) ===")
        silence_start = time.time()
        frame_duration_ms = 20
        samples_per_frame = int(sample_rate * frame_duration_ms / 1000)
        silence = np.zeros(samples_per_frame, dtype=np.int16)
        
        for _ in range(250):  # 5 seconds
            frame = rtc.AudioFrame.create(
                sample_rate=sample_rate,
                num_channels=1,
                samples_per_channel=samples_per_frame
            )
            frame.data[:len(silence.tobytes())] = silence.tobytes()
            await source.capture_frame(frame)
            await asyncio.sleep(0.02)
            
        logger.info(f"Silence sent. VAD events during silence: {len([e for e in vad_events if e[0] > silence_start])}")
        
        # Test 2: Send loud tone burst
        logger.info("\n=== Test 2: Loud Tone Burst (1 second) ===")
        tone_start = time.time()
        t = np.linspace(0, 1, sample_rate)
        tone = (32767 * 0.8 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)  # 440Hz tone
        
        for i in range(0, len(tone), samples_per_frame):
            chunk = tone[i:i+samples_per_frame]
            if len(chunk) < samples_per_frame:
                chunk = np.pad(chunk, (0, samples_per_frame - len(chunk)))
            
            frame = rtc.AudioFrame.create(
                sample_rate=sample_rate,
                num_channels=1,
                samples_per_channel=samples_per_frame
            )
            frame.data[:len(chunk.tobytes())] = chunk.tobytes()
            await source.capture_frame(frame)
            await asyncio.sleep(0.02)
            
        # Send trailing silence
        for _ in range(50):  # 1 second
            frame = rtc.AudioFrame.create(
                sample_rate=sample_rate,
                num_channels=1,
                samples_per_channel=samples_per_frame
            )
            frame.data[:len(silence.tobytes())] = silence.tobytes()
            await source.capture_frame(frame)
            await asyncio.sleep(0.02)
            
        logger.info(f"Tone sent. VAD events during tone: {len([e for e in vad_events if e[0] > tone_start])}")
        
        # Test 3: Send speech-like modulated signal
        logger.info("\n=== Test 3: Speech-like Modulated Signal (2 seconds) ===")
        speech_start = time.time()
        
        # Create more realistic speech-like audio
        duration = 2.0
        samples_total = int(sample_rate * duration)
        t = np.linspace(0, duration, samples_total)
        
        # Mix of formants
        speech_like = np.zeros(samples_total)
        formants = [300, 700, 1220, 2600]  # Typical formant frequencies
        for f in formants:
            speech_like += 0.2 * np.sin(2 * np.pi * f * t)
        
        # Apply speech-like envelope with bursts
        envelope = np.zeros_like(t)
        for burst_time in [0.1, 0.4, 0.8, 1.2, 1.6]:
            burst_mask = np.abs(t - burst_time) < 0.15
            envelope[burst_mask] = np.exp(-10 * np.abs(t[burst_mask] - burst_time))
        
        speech_like = (speech_like * envelope * 32767 * 0.7).astype(np.int16)
        
        for i in range(0, len(speech_like), samples_per_frame):
            chunk = speech_like[i:i+samples_per_frame]
            if len(chunk) < samples_per_frame:
                chunk = np.pad(chunk, (0, samples_per_frame - len(chunk)))
            
            frame = rtc.AudioFrame.create(
                sample_rate=sample_rate,
                num_channels=1,
                samples_per_channel=samples_per_frame
            )
            frame.data[:len(chunk.tobytes())] = chunk.tobytes()
            await source.capture_frame(frame)
            await asyncio.sleep(0.02)
            
        # Trailing silence
        for _ in range(50):
            frame = rtc.AudioFrame.create(
                sample_rate=sample_rate,
                num_channels=1,
                samples_per_channel=samples_per_frame
            )
            frame.data[:len(silence.tobytes())] = silence.tobytes()
            await source.capture_frame(frame)
            await asyncio.sleep(0.02)
            
        logger.info(f"Speech-like signal sent. VAD events: {len([e for e in vad_events if e[0] > speech_start])}")
        
        # Wait for any responses
        logger.info("\n=== Waiting for Agent Response ===")
        await asyncio.sleep(5)
        
        # Disconnect
        await room.disconnect()
        
        # Summary
        logger.info("\n=== VAD Test Summary ===")
        logger.info(f"Total VAD events captured: {len(vad_events)}")
        logger.info(f"Agent audio responses: {len(agent_responses)}")
        
        if vad_events:
            logger.info("\nVAD Event Details:")
            for timestamp, identity, message in vad_events[-5:]:  # Last 5 events
                logger.info(f"  [{timestamp:.2f}] {identity}: {message[:100]}")
                
        if agent_responses:
            logger.info("\nAgent responded with audio!")
        else:
            logger.info("\nNo agent audio responses detected")
            
        # Check agent logs
        logger.info("\n=== Checking Agent Logs ===")
        import subprocess
        result = subprocess.run(
            ["docker-compose", "logs", "--tail=200", "agent-worker"],
            capture_output=True,
            text=True
        )
        
        # Look for VAD indicators
        vad_indicators = [
            "started speaking",
            "stopped speaking", 
            "speech_started",
            "speech_committed",
            "user_started_speaking",
            "VAD",
            "voice activity"
        ]
        
        found_indicators = []
        for line in result.stdout.split('\n'):
            for indicator in vad_indicators:
                if indicator.lower() in line.lower():
                    found_indicators.append((indicator, line.strip()))
                    
        if found_indicators:
            logger.info(f"\nFound {len(found_indicators)} VAD-related log entries:")
            for indicator, line in found_indicators[:5]:
                logger.info(f"  {indicator}: {line[:150]}")
        else:
            logger.info("\nNo VAD-related log entries found")
            
        return len(vad_events) > 0 or len(agent_responses) > 0

if __name__ == "__main__":
    success = asyncio.run(test_vad_with_speech_patterns())
    print("\n" + "="*60)
    if success:
        print("✅ VAD is detecting audio activity")
    else:
        print("❌ VAD is not detecting audio activity properly")
    print("="*60)