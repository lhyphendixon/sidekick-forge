#!/usr/bin/env python3
"""
Test LiveKit connection to verify user can join room
"""
import asyncio
import json
from livekit import api

async def test_connection():
    # Backend LiveKit credentials
    url = "wss://litebridge-hw6srhvi.livekit.cloud"
    api_key = "APIUtuiQ47BQBsk"
    api_secret = "ooIq2MX49GkOJvCaZJCMTXsIGnrJPJmZPaHxnJCMQcjM"
    
    lk_api = api.LiveKitAPI(url, api_key, api_secret)
    
    # List rooms
    print("🏠 Listing active rooms...")
    try:
        request = api.ListRoomsRequest()
        response = await lk_api.room.list_rooms(request)
        for room in response.rooms:
            print(f"   - {room.name}: {room.num_participants} participants")
            
            # Get participants in room
            if room.num_participants > 0:
                p_request = api.ListParticipantsRequest(room=room.name)
                p_response = await lk_api.room.list_participants(p_request)
                participants = p_response.participants
                for p in participants:
                    print(f"     • {p.identity} (sid: {p.sid})")
    except Exception as e:
        print(f"❌ Error listing rooms: {e}")
    
    print("\nDone!")

if __name__ == "__main__":
    asyncio.run(test_connection())