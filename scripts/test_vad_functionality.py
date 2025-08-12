#!/usr/bin/env python3
"""
Test VAD (Voice Activity Detection) Functionality
This test verifies that the Silero VAD is working by:
1. Loading it directly
2. Testing it with various audio inputs
3. Checking if it detects speech in our test file
"""

import asyncio
import logging
import numpy as np
from pydub import AudioSegment
import torch
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AUDIO_FILE = "/root/sidekick-forge/test_audio/greeting.mp3"

class VADTester:
    """Test Silero VAD functionality"""
    
    def __init__(self):
        self.vad = None
        self.audio_data = None
        
    async def test_vad(self):
        """Test VAD with various inputs"""
        
        # Step 1: Try to load Silero VAD
        logger.info("=== Step 1: Loading Silero VAD ===")
        try:
            from livekit.plugins import silero
            self.vad = silero.VAD.load()
            logger.info("✅ Silero VAD loaded successfully")
        except Exception as e:
            logger.error(f"❌ Failed to load Silero VAD: {e}")
            return False
            
        # Step 2: Test with silence
        logger.info("\n=== Step 2: Testing VAD with Silence ===")
        sample_rate = 16000  # Silero VAD expects 16kHz
        silence = np.zeros(16000, dtype=np.float32)  # 1 second of silence
        
        # Create frames of 512 samples each (32ms at 16kHz)
        frame_size = 512
        silence_detected_speech = False
        
        for i in range(0, len(silence) - frame_size, frame_size):
            frame = silence[i:i + frame_size]
            # Silero VAD expects tensor input
            frame_tensor = torch.from_numpy(frame).float()
            
            # Get VAD probability
            speech_prob = self.vad(frame_tensor, sample_rate)
            if speech_prob > 0.5:
                silence_detected_speech = True
                
        if silence_detected_speech:
            logger.warning("❌ VAD incorrectly detected speech in silence")
        else:
            logger.info("✅ VAD correctly detected no speech in silence")
            
        # Step 3: Test with synthetic speech (sine wave modulated)
        logger.info("\n=== Step 3: Testing VAD with Synthetic Audio ===")
        
        # Generate a more speech-like signal
        duration = 1.0
        samples = int(sample_rate * duration)
        t = np.linspace(0, duration, samples)
        
        # Create a complex waveform that mimics speech patterns
        # Mix of frequencies common in human speech (100-400 Hz)
        synthetic = (
            0.3 * np.sin(2 * np.pi * 150 * t) +  # Fundamental
            0.2 * np.sin(2 * np.pi * 300 * t) +  # Harmonic
            0.1 * np.sin(2 * np.pi * 450 * t)    # Harmonic
        )
        
        # Add amplitude modulation to simulate speech rhythm
        envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)  # 4 Hz modulation
        synthetic = synthetic * envelope
        
        # Normalize
        synthetic = synthetic.astype(np.float32)
        synthetic = synthetic / np.max(np.abs(synthetic))
        
        synthetic_detected_speech = False
        speech_frames = 0
        total_frames = 0
        
        for i in range(0, len(synthetic) - frame_size, frame_size):
            frame = synthetic[i:i + frame_size]
            frame_tensor = torch.from_numpy(frame).float()
            
            speech_prob = self.vad(frame_tensor, sample_rate)
            total_frames += 1
            if speech_prob > 0.5:
                speech_frames += 1
                synthetic_detected_speech = True
                
        logger.info(f"Synthetic audio: {speech_frames}/{total_frames} frames detected as speech")
        if synthetic_detected_speech:
            logger.info("✅ VAD detected activity in synthetic audio")
        else:
            logger.warning("⚠️ VAD did not detect synthetic audio as speech")
            
        # Step 4: Test with our actual audio file
        logger.info("\n=== Step 4: Testing VAD with Test Audio File ===")
        
        # Load the audio file
        audio = AudioSegment.from_mp3(AUDIO_FILE)
        
        # Convert to mono if needed
        if audio.channels > 1:
            audio = audio.set_channels(1)
            
        # Resample to 16kHz (Silero VAD requirement)
        if audio.frame_rate != 16000:
            logger.info(f"Resampling from {audio.frame_rate}Hz to 16000Hz for VAD")
            audio = audio.set_frame_rate(16000)
            
        # Get audio data as float32
        samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
        # Normalize to [-1, 1]
        samples = samples / 32768.0
        
        logger.info(f"Audio file: {len(samples)} samples, {len(samples)/16000:.2f} seconds")
        
        # Process through VAD
        file_speech_frames = 0
        file_total_frames = 0
        speech_segments = []
        current_segment_start = None
        
        for i in range(0, len(samples) - frame_size, frame_size):
            frame = samples[i:i + frame_size]
            frame_tensor = torch.from_numpy(frame).float()
            
            speech_prob = self.vad(frame_tensor, 16000)
            file_total_frames += 1
            
            if speech_prob > 0.5:
                file_speech_frames += 1
                if current_segment_start is None:
                    current_segment_start = i / 16000  # Convert to seconds
            else:
                if current_segment_start is not None:
                    segment_end = i / 16000
                    speech_segments.append((current_segment_start, segment_end))
                    current_segment_start = None
                    
        # Close final segment if needed
        if current_segment_start is not None:
            speech_segments.append((current_segment_start, len(samples) / 16000))
            
        logger.info(f"Test audio file: {file_speech_frames}/{file_total_frames} frames detected as speech")
        logger.info(f"Speech percentage: {(file_speech_frames/file_total_frames)*100:.1f}%")
        
        if speech_segments:
            logger.info(f"✅ VAD detected {len(speech_segments)} speech segments:")
            for i, (start, end) in enumerate(speech_segments):
                logger.info(f"   Segment {i+1}: {start:.2f}s - {end:.2f}s (duration: {end-start:.2f}s)")
        else:
            logger.warning("❌ VAD detected no speech in the test audio file")
            
        # Step 5: Test VAD state tracking
        logger.info("\n=== Step 5: Testing VAD State Machine ===")
        
        # Simulate the LiveKit VAD usage pattern
        vad_events = []
        is_speaking = False
        
        # Process with state tracking
        for i in range(0, len(samples) - frame_size, frame_size):
            frame = samples[i:i + frame_size]
            frame_tensor = torch.from_numpy(frame).float()
            
            speech_prob = self.vad(frame_tensor, 16000)
            
            if speech_prob > 0.5 and not is_speaking:
                # Speech started
                is_speaking = True
                vad_events.append(("speech_start", i / 16000))
            elif speech_prob <= 0.5 and is_speaking:
                # Speech ended
                is_speaking = False
                vad_events.append(("speech_end", i / 16000))
                
        if is_speaking:
            vad_events.append(("speech_end", len(samples) / 16000))
            
        logger.info(f"VAD events detected: {len(vad_events)}")
        for event_type, timestamp in vad_events:
            logger.info(f"   {event_type} at {timestamp:.2f}s")
            
        # Summary
        logger.info("\n=== VAD Test Summary ===")
        vad_working = False
        
        if not silence_detected_speech:
            logger.info("✅ Correctly ignores silence")
            vad_working = True
        else:
            logger.info("❌ Incorrectly detects speech in silence")
            
        if file_speech_frames > 0:
            logger.info(f"✅ Detects speech in audio file ({(file_speech_frames/file_total_frames)*100:.1f}% of frames)")
            vad_working = True
        else:
            logger.info("❌ Does not detect speech in audio file")
            vad_working = False
            
        return vad_working


async def test_vad_in_agent_context():
    """Test how VAD is used in the agent context"""
    logger.info("\n=== Testing VAD in Agent Context ===")
    
    # Check if the agent's VAD configuration
    import subprocess
    result = subprocess.run(
        ["docker-compose", "logs", "--tail=200", "agent-worker"],
        capture_output=True,
        text=True
    )
    
    vad_logs = []
    for line in result.stdout.split('\n'):
        if 'VAD' in line or 'vad' in line or 'Silero' in line or 'silero' in line:
            vad_logs.append(line)
            
    if vad_logs:
        logger.info("Found VAD-related logs in agent:")
        for log in vad_logs[:5]:  # Show first 5
            logger.info(f"  {log}")
    else:
        logger.info("No VAD-specific logs found in agent")
        
    # Check for speech detection events
    speech_events = []
    for line in result.stdout.split('\n'):
        if 'speech' in line.lower() or 'speaking' in line.lower():
            speech_events.append(line)
            
    if speech_events:
        logger.info(f"\nFound {len(speech_events)} speech-related events")
        for event in speech_events[:3]:
            logger.info(f"  {event}")


async def main():
    """Main test runner"""
    print("Starting VAD Functionality Test...")
    print("="*60)
    
    # Check if audio file exists
    if not os.path.exists(AUDIO_FILE):
        logger.error(f"Audio file not found: {AUDIO_FILE}")
        return
        
    tester = VADTester()
    
    try:
        vad_working = await tester.test_vad()
        
        # Also check agent context
        await test_vad_in_agent_context()
        
        print("\n" + "="*60)
        print("VAD TEST CONCLUSION")
        print("="*60)
        
        if vad_working:
            print("✅ VAD module is working")
            print("\nHowever, if the agent isn't responding to the test audio, it could be because:")
            print("1. The VAD threshold in AgentSession might be different")
            print("2. The audio needs preprocessing (normalization, filtering)")
            print("3. The frame size/timing doesn't match agent expectations")
            print("4. The agent might need continuous audio stream, not discrete frames")
        else:
            print("❌ VAD module has issues")
            print("The VAD is not detecting speech in the audio file.")
            
    except Exception as e:
        logger.error(f"Test error: {str(e)}", exc_info=True)
        print("\n❌ VAD test failed with error")
        
    import sys
    sys.exit(0 if vad_working else 1)


if __name__ == "__main__":
    # Install torch if needed for Silero
    try:
        import torch
    except ImportError:
        logger.info("Installing torch for Silero VAD...")
        import subprocess
        subprocess.run(["pip", "install", "torch", "--break-system-packages"], check=True)
        import torch
        
    asyncio.run(main())