#!/usr/bin/env python3
"""Test script for clarence-coherence agent dispatch"""
import asyncio
import os
import sys
sys.path.insert(0, '/opt/autonomite-saas')

from dotenv import load_dotenv
load_dotenv()

async def main():
    from livekit import api, rtc
    from datetime import datetime
    
    # LiveKit credentials (backend)
    url = "wss://litebridge-hw6srhvi.livekit.cloud"
    api_key = "APIUtuiQ47BQBsk"
    api_secret = "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
    
    # Create API client
    lk_api = api.LiveKitAPI(url, api_key, api_secret)
    
    room_name = f"test-clarence-{int(datetime.now().timestamp())}"
    
    try:
        # Create room with clarence-coherence metadata
        from livekit.api import CreateRoomRequest
        room = await lk_api.room.create_room(CreateRoomRequest(
            name=room_name,
            metadata='{"agent_request": {"agent": "clarence-coherence", "client_id": "df91fd06-816f-4273-a903-5a4861277040"}}'
        ))
        
        print(f"✅ Created room: {room_name}")
        print(f"   Metadata: {room.metadata}")
        
        # Create user token
        token = api.AccessToken(api_key, api_secret) \
            .with_identity("test-user") \
            .with_name("Test User") \
            .with_grants(api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True
            )).to_jwt()
        
        print(f"\n📋 User token created")
        
        # Connect as user
        user_room = rtc.Room()
        
        agent_joined = asyncio.Event()
        
        @user_room.on("participant_connected")
        def on_participant_connected(participant):
            print(f"✅ Participant connected: {participant.identity}")
            if "agent" in participant.identity.lower() or "clarence" in participant.identity.lower():
                print("🤖 AGENT HAS JOINED THE ROOM!")
                agent_joined.set()
        
        print(f"\n🔗 Connecting to room as user...")
        await user_room.connect(url, token)
        print(f"✅ Connected to room")
        
        # Wait for agent
        print("\n⏳ Waiting for clarence-coherence agent to join...")
        try:
            await asyncio.wait_for(agent_joined.wait(), timeout=10.0)
            print("✅ SUCCESS: Agent joined the room!")
        except asyncio.TimeoutError:
            print("❌ TIMEOUT: Agent did not join within 10 seconds")
            
            # List participants
            print(f"\nCurrent participants ({len(user_room.participants)}):")
            for p in user_room.participants.values():
                print(f"  - {p.identity}")
        
        await user_room.disconnect()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())