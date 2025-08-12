#!/usr/bin/env python3
"""
Test Agent Audio Response - Send actual audio to the agent and verify response
Uses TTS to generate test audio and sends it to the agent
"""

import asyncio
import logging
import httpx
import json
import time
import numpy as np
from livekit import api, rtc
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BASE_URL = "http://localhost:8000"
CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"
AGENT_SLUG = "autonomite"

class AgentAudioResponseTest:
    """Test that the agent responds to actual audio input"""
    
    def __init__(self):
        self.room = None
        self.agent_responded = False
        self.agent_speaking = False
        self.transcriptions = []
        self.test_passed = False
        
    def generate_test_audio(self, duration_seconds=3, sample_rate=48000):
        """Generate simple test audio (sine wave)"""
        # Generate a simple sine wave as test audio
        frequency = 440  # A4 note
        t = np.linspace(0, duration_seconds, int(sample_rate * duration_seconds))
        audio_data = np.sin(2 * np.pi * frequency * t)
        
        # Convert to 16-bit PCM
        audio_int16 = (audio_data * 32767).astype(np.int16)
        
        return audio_int16.tobytes()
        
    async def test_agent_audio_response(self):
        """Main test function"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Trigger the agent
            logger.info("=== Step 1: Triggering Agent ===")
            room_name = f"test-audio-{int(time.time())}"
            
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
            server_url = livekit_config["server_url"]
            
            logger.info(f"✅ Agent triggered in room: {room_name}")
            
            # Wait for agent to be ready
            await asyncio.sleep(3)
            
            # Step 2: Connect to the room
            logger.info("=== Step 2: Connecting to Room ===")
            
            try:
                self.room = rtc.Room()
                
                # Track for capturing agent's audio response
                agent_audio_received = False
                
                @self.room.on("track_subscribed")
                def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
                    """Handle track subscriptions"""
                    logger.info(f"📡 Subscribed to {track.kind} track from {participant.identity}")
                    
                    if participant.identity.startswith("agent") and track.kind == rtc.TrackKind.KIND_AUDIO:
                        logger.info("🔊 Agent audio track subscribed!")
                        self.agent_speaking = True
                        
                        # Set up audio stream to detect when agent speaks
                        @track.on("frame_received")
                        def on_audio_frame(frame):
                            nonlocal agent_audio_received
                            if not agent_audio_received:
                                logger.info("🎤 Agent is speaking! Audio frames received.")
                                agent_audio_received = True
                                self.agent_responded = True
                
                @self.room.on("data_received")
                def on_data_received(data: bytes, participant: rtc.RemoteParticipant, kind: str):
                    """Handle data messages"""
                    try:
                        message = data.decode('utf-8')
                        logger.info(f"📨 Data from {participant.identity}: {message}")
                        
                        # Check if it's a transcription
                        try:
                            msg_data = json.loads(message)
                            if msg_data.get("type") == "transcription":
                                self.transcriptions.append(msg_data.get("text", ""))
                                logger.info(f"📝 Transcription: {msg_data.get('text', '')}")
                        except:
                            pass
                            
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
                
                # Step 4: Send test audio
                logger.info("=== Step 4: Sending Test Audio ===")
                
                # Wait a moment for track to be fully established
                await asyncio.sleep(1)
                
                # Generate and send audio frames
                # Let's send silence first, then a tone, then silence (to trigger VAD)
                sample_rate = 48000
                frame_duration_ms = 20  # 20ms frames
                samples_per_frame = int(sample_rate * frame_duration_ms / 1000)
                
                # Send 1 second of silence
                logger.info("📤 Sending initial silence...")
                silence = np.zeros(samples_per_frame, dtype=np.int16)
                for _ in range(50):  # 50 * 20ms = 1 second
                    frame = rtc.AudioFrame.create(
                        sample_rate=sample_rate,
                        num_channels=1,
                        samples_per_channel=samples_per_frame
                    )
                    frame.data = silence.tobytes()
                    await source.capture_frame(frame)
                    await asyncio.sleep(0.02)
                
                # Send speech-like audio (sine wave modulated)
                logger.info("📤 Sending speech-like audio...")
                for i in range(100):  # 2 seconds of "speech"
                    t = np.linspace(0, frame_duration_ms/1000, samples_per_frame)
                    # Modulate frequency to simulate speech patterns
                    freq = 200 + 100 * np.sin(2 * np.pi * 2 * i / 100)
                    audio = np.sin(2 * np.pi * freq * t) * 0.5
                    audio_int16 = (audio * 32767).astype(np.int16)
                    
                    frame = rtc.AudioFrame.create(
                        sample_rate=sample_rate,
                        num_channels=1,
                        samples_per_channel=samples_per_frame
                    )
                    frame.data = audio_int16.tobytes()
                    await source.capture_frame(frame)
                    await asyncio.sleep(0.02)
                
                # Send silence again to trigger end of speech
                logger.info("📤 Sending trailing silence...")
                for _ in range(50):  # 1 second
                    frame = rtc.AudioFrame.create(
                        sample_rate=sample_rate,
                        num_channels=1,
                        samples_per_channel=samples_per_frame
                    )
                    frame.data = silence.tobytes()
                    await source.capture_frame(frame)
                    await asyncio.sleep(0.02)
                
                logger.info("✅ Test audio sent")
                
                # Step 5: Wait for agent response
                logger.info("=== Step 5: Waiting for Agent Response ===")
                logger.info("⏳ Waiting up to 10 seconds for agent to respond...")
                
                for i in range(10):
                    if self.agent_responded or agent_audio_received:
                        break
                    await asyncio.sleep(1)
                    logger.info(f"   {i+1}s...")
                
                # Check results
                if self.agent_responded or agent_audio_received:
                    logger.info("✅ AGENT RESPONDED WITH AUDIO!")
                    self.test_passed = True
                else:
                    logger.warning("❌ No audio response from agent")
                    
                if self.transcriptions:
                    logger.info(f"📝 Transcriptions received: {self.transcriptions}")
                
                # Disconnect
                await self.room.disconnect()
                
            except Exception as e:
                logger.error(f"Error in test: {e}", exc_info=True)
                return False
                
        return self.test_passed
    
    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*60)
        print("AGENT AUDIO RESPONSE TEST SUMMARY")
        print("="*60)
        
        if self.test_passed:
            print("✅ TEST PASSED - Agent responded to audio input")
        else:
            print("❌ TEST FAILED - No agent audio response detected")
            print("\nPossible issues:")
            print("- VAD (Voice Activity Detection) may not be triggering")
            print("- STT (Speech-to-Text) may not be processing audio")
            print("- LLM may not be receiving transcriptions")
            print("- TTS may not be generating response audio")
            
        if self.transcriptions:
            print(f"\nTranscriptions: {self.transcriptions}")
            
        print("="*60)


async def main():
    """Main test runner"""
    print("Starting Agent Audio Response Test...")
    print("This test sends actual audio to the agent")
    print("-"*60)
    
    tester = AgentAudioResponseTest()
    
    try:
        success = await tester.test_agent_audio_response()
    except Exception as e:
        logger.error(f"Test error: {str(e)}")
        success = False
    
    tester.print_summary()
    
    # Check agent logs
    print("\nChecking agent logs for clues...")
    import subprocess
    result = subprocess.run(
        ["docker-compose", "logs", "--tail=50", "agent-worker"],
        capture_output=True,
        text=True
    )
    
    # Look for key events in logs
    if "User started speaking" in result.stdout:
        print("✅ Agent detected user speaking")
    else:
        print("❌ Agent did NOT detect user speaking")
        
    if "user_speech_committed" in result.stdout:
        print("✅ Agent received speech transcription")
    else:
        print("❌ Agent did NOT receive speech transcription")
        
    if "agent_thinking" in result.stdout:
        print("✅ Agent started thinking (LLM processing)")
    else:
        print("❌ Agent did NOT start thinking (no LLM call)")
    
    # Exit with appropriate code
    import sys
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    # Check if numpy is available
    try:
        import numpy as np
    except ImportError:
        print("NumPy not installed. Installing...")
        import subprocess
        subprocess.run(["pip", "install", "numpy"])
        import numpy as np
        
    asyncio.run(main())