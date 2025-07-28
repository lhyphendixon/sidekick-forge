#!/usr/bin/env python3
"""
Test VAD functionality inside the agent container
"""

import asyncio
import numpy as np
import torch
from livekit.plugins import silero
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_vad_with_audio():
    """Test VAD with synthetic audio"""
    
    # Load Silero VAD
    vad = silero.VAD.load()
    logger.info("✅ Silero VAD loaded successfully")
    
    # Test 1: Silence should not trigger VAD
    sample_rate = 16000
    silence = np.zeros(16000, dtype=np.float32)  # 1 second of silence
    
    # Process in 512-sample frames (32ms at 16kHz)
    frame_size = 512
    silence_detected = False
    
    for i in range(0, len(silence) - frame_size, frame_size):
        frame = silence[i:i + frame_size]
        frame_tensor = torch.from_numpy(frame).float()
        speech_prob = vad(frame_tensor, sample_rate)
        if speech_prob > 0.5:
            silence_detected = True
            
    if not silence_detected:
        logger.info("✅ VAD correctly ignored silence")
    else:
        logger.warning("❌ VAD incorrectly detected speech in silence")
    
    # Test 2: Generate synthetic speech-like audio
    duration = 1.0
    samples = int(sample_rate * duration)
    t = np.linspace(0, duration, samples)
    
    # Create a complex waveform that mimics speech patterns
    synthetic = (
        0.3 * np.sin(2 * np.pi * 150 * t) +  # Fundamental frequency
        0.2 * np.sin(2 * np.pi * 300 * t) +  # Harmonic
        0.1 * np.sin(2 * np.pi * 450 * t)    # Harmonic
    )
    
    # Add amplitude modulation to simulate speech rhythm
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)  # 4 Hz modulation
    synthetic = synthetic * envelope
    
    # Add some noise to make it more realistic
    noise = np.random.normal(0, 0.01, samples)
    synthetic += noise
    
    # Normalize
    synthetic = synthetic.astype(np.float32)
    synthetic = synthetic / np.max(np.abs(synthetic)) * 0.8  # Leave some headroom
    
    # Test with synthetic audio
    speech_frames = 0
    total_frames = 0
    
    for i in range(0, len(synthetic) - frame_size, frame_size):
        frame = synthetic[i:i + frame_size]
        frame_tensor = torch.from_numpy(frame).float()
        speech_prob = vad(frame_tensor, sample_rate)
        total_frames += 1
        if speech_prob > 0.5:
            speech_frames += 1
            
    speech_percentage = (speech_frames / total_frames) * 100
    logger.info(f"Synthetic audio: {speech_frames}/{total_frames} frames detected as speech ({speech_percentage:.1f}%)")
    
    if speech_frames > 0:
        logger.info("✅ VAD detected activity in synthetic audio")
    else:
        logger.warning("⚠️ VAD did not detect synthetic audio as speech")
    
    # Test 3: Check VAD configuration
    logger.info("\n=== VAD Configuration Check ===")
    logger.info(f"VAD model type: {type(vad)}")
    logger.info(f"VAD attributes: {dir(vad)}")
    
    # Test 4: Create loud, clear synthetic speech
    logger.info("\n=== Testing with Loud Clear Audio ===")
    
    # Generate a more speech-like signal with formants
    loud_synthetic = np.zeros(samples, dtype=np.float32)
    
    # Add multiple formants typical of vowel sounds
    formants = [700, 1220, 2600]  # Formant frequencies for 'a' sound
    for f in formants:
        loud_synthetic += 0.3 * np.sin(2 * np.pi * f * t)
    
    # Apply speech-like envelope with attack and decay
    speech_envelope = np.zeros_like(t)
    # Create bursts of "speech"
    for burst_start in [0.1, 0.4, 0.7]:
        burst_mask = (t >= burst_start) & (t < burst_start + 0.2)
        speech_envelope[burst_mask] = np.exp(-5 * (t[burst_mask] - burst_start))
    
    loud_synthetic = loud_synthetic * speech_envelope
    loud_synthetic = loud_synthetic / np.max(np.abs(loud_synthetic)) * 0.9
    
    # Test loud synthetic
    loud_speech_frames = 0
    loud_total_frames = 0
    max_prob = 0.0
    
    for i in range(0, len(loud_synthetic) - frame_size, frame_size):
        frame = loud_synthetic[i:i + frame_size]
        frame_tensor = torch.from_numpy(frame).float()
        speech_prob = vad(frame_tensor, sample_rate)
        loud_total_frames += 1
        max_prob = max(max_prob, speech_prob.item() if hasattr(speech_prob, 'item') else float(speech_prob))
        if speech_prob > 0.5:
            loud_speech_frames += 1
            
    loud_percentage = (loud_speech_frames / loud_total_frames) * 100
    logger.info(f"Loud synthetic: {loud_speech_frames}/{loud_total_frames} frames detected ({loud_percentage:.1f}%)")
    logger.info(f"Maximum VAD probability seen: {max_prob:.3f}")
    
    # Summary
    logger.info("\n=== VAD Test Summary ===")
    vad_working = not silence_detected and (speech_frames > 0 or loud_speech_frames > 0)
    
    if vad_working:
        logger.info("✅ VAD module is functioning")
        if speech_percentage < 20 and loud_percentage < 20:
            logger.info("⚠️ But detection threshold seems very high")
            logger.info("   The pre-recorded audio file might need:")
            logger.info("   - Normalization to higher amplitude")
            logger.info("   - Different frame size/timing")
            logger.info("   - Pre-processing to match VAD expectations")
    else:
        logger.error("❌ VAD module has issues")
        
    return vad_working

if __name__ == "__main__":
    test_vad_with_audio()