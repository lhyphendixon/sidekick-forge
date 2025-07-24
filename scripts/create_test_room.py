#!/usr/bin/env python3
import asyncio
import os
import sys
sys.path.insert(0, '/opt/autonomite-saas')

from dotenv import load_dotenv
load_dotenv()

async def main():
    from livekit import api
    from datetime import datetime
    
    # LiveKit credentials
    url = "wss://litebridge-hw6srhvi.livekit.cloud"
    api_key = "APIUtuiQ47BQBsk"
    api_secret = "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
    
    # Create API client
    lk_api = api.LiveKitAPI(url, api_key, api_secret)
    
    room_name = f"test-agent-dispatch-{int(datetime.now().timestamp())}"
    
    try:
        # Create room with metadata
        from livekit.api import CreateRoomRequest
        room = await lk_api.room.create_room(CreateRoomRequest(
            name=room_name,
            metadata='{"agent_request": {"agent": "session-agent-rag"}}'
        ))
        
        print(f"✅ Created room: {room_name}")
        print(f"   Metadata: {room.metadata}")
        
        # Create user token to join
        token = api.AccessToken(api_key, api_secret) \
            .with_identity("test-dispatch-user") \
            .with_name("Test User") \
            .with_grants(api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True
            )).to_jwt()
        
        print(f"\n📋 User token created")
        print(f"Room: {room_name}")
        print(f"Token: {token[:50]}...")
        
        print("\n⏳ Room is ready for participants. Agent should join when a participant connects.")
        print(f"\nMonitor agent logs with:")
        print(f"docker logs -f agent_df91fd06_gpt_session_ | grep -E '(job|dispatch|{room_name})'")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())