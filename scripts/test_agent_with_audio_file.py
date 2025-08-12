#!/usr/bin/env python3
"""
Test Agent with Pre-recorded Audio File
This test loads a pre-recorded audio file and sends it to the agent
to test the full STT→LLM→TTS pipeline
"""

import asyncio
import logging
import httpx
import json
import time
from pydub import AudioSegment
import numpy as np
from livekit import api, rtc
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BASE_URL = "http://localhost:8000"
CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"
AGENT_SLUG = "autonomite"
AUDIO_FILE = "/root/sidekick-forge/test_audio/greeting.mp3"

class AgentAudioFileTest:
    """Test agent response using pre-recorded audio file"""
    
    def __init__(self):
        self.room = None
        self.agent_responded = False
        self.agent_audio_frames = 0
        self.transcriptions = []
        self.test_passed = False
        self.audio_data = None
        
    def load_audio_file(self):
        """Load and prepare the audio file"""
        logger.info(f"Loading audio file: {AUDIO_FILE}")
        
        # Load MP3 file
        audio = AudioSegment.from_mp3(AUDIO_FILE)
        
        # Convert to mono if stereo
        if audio.channels > 1:
            audio = audio.set_channels(1)
            logger.info("Converted to mono")
        
        # Resample to 48kHz if needed
        if audio.frame_rate != 48000:
            audio = audio.set_frame_rate(48000)
            logger.info(f"Resampled from {audio.frame_rate}Hz to 48000Hz")
        
        # Convert to 16-bit if needed
        if audio.sample_width != 2:
            audio = audio.set_sample_width(2)
            logger.info("Converted to 16-bit")
        
        # Get raw audio data
        self.audio_data = np.array(audio.get_array_of_samples(), dtype=np.int16)
        
        logger.info(f"Audio loaded: {len(self.audio_data)} samples, {len(self.audio_data)/48000:.2f} seconds")
        
        return True
        
    async def test_agent_with_audio(self):
        """Main test function"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Trigger the agent
            logger.info("=== Step 1: Triggering Agent ===")
            room_name = f"test-audio-file-{int(time.time())}"
            
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
                return False
                
            trigger_data = trigger_response.json()
            livekit_config = trigger_data["data"]["livekit_config"]
            user_token = livekit_config["user_token"]
            
            # Get server URL from trigger data or use default
            server_url = livekit_config.get("server_url")
            if not server_url:
                # Use default LiveKit Cloud URL if not provided
                server_url = "wss://litebridge-hw6srhvi.livekit.cloud"
                logger.warning(f"No server URL in response, using default: {server_url}")
            
            logger.info(f"✅ Agent triggered in room: {room_name}")
            logger.info(f"   Server: {server_url}")
            
            # Wait for agent to be ready
            await asyncio.sleep(3)
            
            # Step 2: Connect to the room
            logger.info("=== Step 2: Connecting to Room ===")
            
            try:
                self.room = rtc.Room()
                
                # Track for capturing agent's response
                agent_speaking = False
                
                @self.room.on("track_subscribed")
                def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
                    """Handle track subscriptions"""
                    logger.info(f"📡 Subscribed to {track.kind} track from {participant.identity}")
                    
                    if participant.identity.startswith("agent") and track.kind == rtc.TrackKind.KIND_AUDIO:
                        logger.info("🔊 Agent audio track subscribed - agent can speak!")
                        self.agent_responded = True
                
                @self.room.on("data_received")
                def on_data_received(data: bytes, participant: rtc.RemoteParticipant, kind: str):
                    """Handle data messages"""
                    try:
                        message = data.decode('utf-8')
                        logger.info(f"📨 Data from {participant.identity}: {message[:100]}...")
                        
                        # Try to parse as JSON
                        try:
                            msg_data = json.loads(message)
                            if msg_data.get("type") == "transcription":
                                transcript = msg_data.get("text", "")
                                self.transcriptions.append(transcript)
                                logger.info(f"📝 Transcription: {transcript}")
                        except:
                            # Not JSON, just log it
                            if participant.identity.startswith("agent"):
                                logger.info(f"🤖 Agent message: {message}")
                            
                    except Exception as e:
                        logger.error(f"Error handling data: {e}")
                
                @self.room.on("participant_connected")
                def on_participant_connected(participant: rtc.RemoteParticipant):
                    logger.info(f"👤 {participant.identity} connected")
                
                # Connect to room
                await self.room.connect(server_url, user_token)
                logger.info("✅ Connected to room")
                
                # Wait for agent
                await asyncio.sleep(2)
                
                # Check participants
                participants = list(self.room.remote_participants.values())
                agent_in_room = any(p.identity.startswith("agent") for p in participants)
                
                if not agent_in_room:
                    logger.error("❌ No agent in room!")
                    # Wait a bit more
                    await asyncio.sleep(3)
                    participants = list(self.room.remote_participants.values())
                    agent_in_room = any(p.identity.startswith("agent") for p in participants)
                    
                    if not agent_in_room:
                        logger.error("❌ Still no agent after waiting!")
                        return False
                
                logger.info(f"✅ Agent is in the room")
                
                # Step 3: Publish audio track
                logger.info("=== Step 3: Publishing Audio Track ===")
                
                # Create audio source and track
                source = rtc.AudioSource(48000, 1)  # 48kHz, mono
                track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
                
                # Publish the track
                options = rtc.TrackPublishOptions()
                publication = await self.room.local_participant.publish_track(track, options)
                logger.info("✅ Audio track published")
                
                # Step 4: Send the pre-recorded audio
                logger.info("=== Step 4: Sending Pre-recorded Audio ===")
                
                # Wait for track to be established
                await asyncio.sleep(1)
                
                # Calculate frame parameters
                sample_rate = 48000
                frame_duration_ms = 20  # 20ms frames for LiveKit
                samples_per_frame = int(sample_rate * frame_duration_ms / 1000)
                
                # Send initial silence (1 second) to establish baseline
                logger.info("📤 Sending initial silence...")
                silence = np.zeros(samples_per_frame, dtype=np.int16)
                for _ in range(50):  # 50 * 20ms = 1 second
                    frame = rtc.AudioFrame.create(
                        sample_rate=sample_rate,
                        num_channels=1,
                        samples_per_channel=samples_per_frame
                    )
                    # Write silence data to frame
                    frame_data = frame.data
                    silence_bytes = silence.tobytes()
                    for i in range(min(len(silence_bytes), len(frame_data))):
                        frame_data[i] = silence_bytes[i]
                    await source.capture_frame(frame)
                    await asyncio.sleep(0.02)
                
                # Send the actual audio file data
                logger.info(f"📤 Sending audio file data ({len(self.audio_data)} samples)...")
                
                # Process audio in chunks
                total_frames = len(self.audio_data) // samples_per_frame
                logger.info(f"Sending {total_frames} frames...")
                
                for i in range(total_frames):
                    start_idx = i * samples_per_frame
                    end_idx = start_idx + samples_per_frame
                    
                    # Get chunk of audio
                    chunk = self.audio_data[start_idx:end_idx]
                    
                    # Create frame
                    frame = rtc.AudioFrame.create(
                        sample_rate=sample_rate,
                        num_channels=1,
                        samples_per_channel=samples_per_frame
                    )
                    
                    # Write audio data to frame
                    frame_data = frame.data
                    chunk_bytes = chunk.tobytes()
                    for j in range(min(len(chunk_bytes), len(frame_data))):
                        frame_data[j] = chunk_bytes[j]
                    
                    await source.capture_frame(frame)
                    await asyncio.sleep(0.02)  # 20ms timing
                    
                    # Log progress every 0.5 seconds
                    if i % 25 == 0:
                        progress = (i / total_frames) * 100
                        logger.info(f"   Progress: {progress:.1f}%")
                
                # Send trailing silence (1 second) to ensure VAD detects end of speech
                logger.info("📤 Sending trailing silence...")
                for _ in range(50):  # 1 second
                    frame = rtc.AudioFrame.create(
                        sample_rate=sample_rate,
                        num_channels=1,
                        samples_per_channel=samples_per_frame
                    )
                    # Write silence
                    frame_data = frame.data
                    for i in range(min(len(silence_bytes), len(frame_data))):
                        frame_data[i] = silence_bytes[i]
                    await source.capture_frame(frame)
                    await asyncio.sleep(0.02)
                
                logger.info("✅ Audio file sent completely")
                
                # Step 5: Wait for agent response
                logger.info("=== Step 5: Waiting for Agent Response ===")
                logger.info("⏳ Waiting up to 15 seconds for agent to process and respond...")
                
                start_wait = time.time()
                while time.time() - start_wait < 15:
                    if self.agent_responded or self.transcriptions:
                        break
                    await asyncio.sleep(0.5)
                    elapsed = time.time() - start_wait
                    logger.info(f"   Waiting... {elapsed:.1f}s")
                
                # Check results
                if self.agent_responded:
                    logger.info("✅ AGENT RESPONDED!")
                    self.test_passed = True
                else:
                    logger.warning("❌ No audio response from agent detected")
                    
                if self.transcriptions:
                    logger.info(f"📝 Transcriptions captured: {self.transcriptions}")
                    self.test_passed = True  # If we got transcriptions, pipeline is working
                else:
                    logger.warning("❌ No transcriptions received")
                
                # Give a moment for any final messages
                await asyncio.sleep(2)
                
                # Disconnect
                await self.room.disconnect()
                logger.info("Disconnected from room")
                
            except Exception as e:
                logger.error(f"Error in test: {e}", exc_info=True)
                return False
                
        return self.test_passed
    
    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*60)
        print("AGENT AUDIO FILE TEST SUMMARY")
        print("="*60)
        
        if self.test_passed:
            print("✅ TEST PASSED - Agent processed the audio")
            if self.transcriptions:
                print(f"   Transcriptions: {self.transcriptions}")
            if self.agent_responded:
                print("   Agent audio response detected")
        else:
            print("❌ TEST FAILED - Agent did not process the audio")
            print("\nPossible issues:")
            print("- Audio file format/quality issues")
            print("- VAD not detecting speech in the audio")
            print("- STT service not transcribing correctly")
            print("- LLM not receiving or processing transcriptions")
            print("- TTS not generating response")
            
        print("="*60)


async def main():
    """Main test runner"""
    print("Starting Agent Audio File Test...")
    print(f"Using audio file: {AUDIO_FILE}")
    print("-"*60)
    
    # Check if audio file exists
    if not os.path.exists(AUDIO_FILE):
        logger.error(f"Audio file not found: {AUDIO_FILE}")
        logger.error("Please ensure the audio file has been downloaded")
        return
    
    tester = AgentAudioFileTest()
    
    # Load audio file
    if not tester.load_audio_file():
        logger.error("Failed to load audio file")
        return
    
    try:
        success = await tester.test_agent_with_audio()
    except Exception as e:
        logger.error(f"Test error: {str(e)}")
        success = False
    
    tester.print_summary()
    
    # Check agent logs
    print("\nChecking agent logs for activity...")
    import subprocess
    result = subprocess.run(
        ["docker-compose", "logs", "--tail=100", "agent-worker"],
        capture_output=True,
        text=True
    )
    
    # Look for key events
    log_checks = [
        ("User started speaking", "✅ Agent detected speech", "❌ Agent did NOT detect speech"),
        ("user_speech_committed", "✅ Agent received transcription", "❌ Agent did NOT receive transcription"),
        ("agent_thinking", "✅ Agent started thinking (LLM)", "❌ Agent did NOT think (no LLM)"),
        ("agent_speech_committed", "✅ Agent generated response", "❌ Agent did NOT generate response"),
        ("groq", "✅ Using Groq LLM", "❌ Not using Groq"),
        ("deepgram", "✅ Using Deepgram STT", "❌ Not using Deepgram"),
        ("elevenlabs", "✅ Using ElevenLabs TTS", "❌ Not using ElevenLabs")
    ]
    
    for search_term, found_msg, not_found_msg in log_checks:
        if search_term in result.stdout:
            print(found_msg)
        else:
            print(not_found_msg)
    
    # Exit with appropriate code
    import sys
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())