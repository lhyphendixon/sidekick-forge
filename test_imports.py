#!/usr/bin/env python3
"""Test script to check available LiveKit imports"""

try:
    import livekit
    print(f"LiveKit version: {livekit.__version__}")
except Exception as e:
    print(f"Error importing livekit: {e}")

try:
    import livekit.plugins
    print("Available in livekit.plugins:")
    for attr in dir(livekit.plugins):
        if not attr.startswith('_'):
            print(f"  - {attr}")
except Exception as e:
    print(f"Error with livekit.plugins: {e}")

try:
    from livekit import voice
    print("\nAvailable in livekit.voice:")
    for attr in dir(voice):
        if not attr.startswith('_'):
            print(f"  - {attr}")
except Exception as e:
    print(f"Error with livekit.voice: {e}")

# Check for turn detection in voice module
try:
    from livekit.voice import TurnDetector, MultilingualModel
    print("\nTurn detection imports successful!")
except ImportError as e:
    print(f"\nTurn detection import error: {e}")

# Check if it's part of agents
try:
    from livekit import agents
    print("\nChecking livekit.agents for turn detection...")
    if hasattr(agents, 'TurnDetector'):
        print("  - Found agents.TurnDetector")
    if hasattr(agents, 'turn_detector'):
        print("  - Found agents.turn_detector")
except Exception as e:
    print(f"Error checking agents: {e}")