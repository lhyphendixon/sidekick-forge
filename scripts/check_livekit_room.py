#!/usr/bin/env python3
import asyncio
import os
import sys
sys.path.insert(0, '/opt/autonomite-saas')

from dotenv import load_dotenv
load_dotenv()

async def main():
    from livekit import api
    
    # LiveKit credentials
    url = "wss://litebridge-hw6srhvi.livekit.cloud"
    api_key = "APIUtuiQ47BQBsk"
    api_secret = "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
    
    # Create API client
    lk_api = api.LiveKitAPI(url, api_key, api_secret)
    
    try:
        # List rooms
        from livekit.api import ListRoomsRequest
        rooms = await lk_api.room.list_rooms(ListRoomsRequest())
        
        print(f"Found {len(rooms.rooms)} rooms:")
        for room in rooms.rooms:
            print(f"\n- Room: {room.name}")
            print(f"  Created: {room.creation_time}")
            print(f"  Participants: {room.num_participants}")
            print(f"  Metadata: {room.metadata}")
            
            # Check participants
            if room.num_participants > 0:
                from livekit.api import ListParticipantsRequest
                participants = await lk_api.room.list_participants(ListParticipantsRequest(room=room.name))
                for p in participants.participants:
                    print(f"    - {p.identity} ({p.state})")
    
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())