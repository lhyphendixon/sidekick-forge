#!/usr/bin/env python3
import asyncio
import os
import sys
sys.path.insert(0, '/opt/autonomite-saas')

from dotenv import load_dotenv
load_dotenv()

async def main():
    from livekit import api, rtc
    
    # LiveKit credentials
    url = "wss://litebridge-hw6srhvi.livekit.cloud"
    api_key = "APIUtuiQ47BQBsk"
    api_secret = "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
    
    room_name = "test-room-final"
    
    # Create token for test user
    token = api.AccessToken(api_key, api_secret) \
        .with_identity("test-user-python") \
        .with_name("Test User") \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True
        )).to_jwt()
    
    print(f"Connecting to room {room_name}...")
    
    # Connect to room
    room = rtc.Room()
    
    @room.on("participant_connected")
    def on_participant_connected(participant):
        print(f"✅ Participant connected: {participant.identity}")
        if participant.identity.startswith("agent-"):
            print("🤖 AGENT HAS JOINED THE ROOM!")
    
    try:
        await room.connect(url, token)
        print(f"✅ Connected to room as test-user-python")
        print(f"   Room: {room.name}")
        print(f"   Participants: {len(room.participants)}")
        
        # List current participants
        for p in room.participants.values():
            print(f"   - {p.identity}")
        
        # Wait a bit to see if agent joins
        print("\nWaiting for agent to join...")
        await asyncio.sleep(10)
        
        # Check again
        print(f"\nAfter waiting:")
        print(f"   Participants: {len(room.participants)}")
        for p in room.participants.values():
            print(f"   - {p.identity}")
            
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await room.disconnect()
        print("Disconnected from room")

if __name__ == "__main__":
    asyncio.run(main())