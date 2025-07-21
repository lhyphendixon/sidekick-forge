#!/usr/bin/env python3

import os
import asyncio
from livekit import api
from livekit.api import AccessToken, VideoGrants, LiveKitAPI, ListRoomsRequest, ListParticipantsRequest
from datetime import datetime
import json
import base64
import time

# LiveKit credentials from settings
LIVEKIT_URL = "wss://litebridge-hw6srhvi.livekit.cloud"
LIVEKIT_API_KEY = "APIUtuiQ47BQBsk"
LIVEKIT_API_SECRET = "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
ROOM_NAME = "test-sdk-fix"

async def test_livekit_room():
    print(f"[{datetime.now()}] Testing LiveKit room: {ROOM_NAME}")
    print(f"[{datetime.now()}] LiveKit URL: {LIVEKIT_URL}")
    
    # 1. Check if the room exists using RoomService API
    livekit_api = LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    
    try:
        print(f"\n[{datetime.now()}] Checking if room exists...")
        response = await livekit_api.room.list_rooms(ListRoomsRequest())
        rooms = response.rooms
        room_exists = False
        
        for room in rooms:
            print(f"  - Found room: {room.name} (SID: {room.sid})")
            if room.name == ROOM_NAME:
                room_exists = True
                print(f"[{datetime.now()}] ✓ Room '{ROOM_NAME}' exists!")
                print(f"  - SID: {room.sid}")
                print(f"  - Participants: {room.num_participants}")
                print(f"  - Max participants: {room.max_participants}")
                print(f"  - Created at: {room.creation_time}")
                
                # Check participants in the room
                response = await livekit_api.room.list_participants(ListParticipantsRequest(room=room.name))
                participants = response.participants
                if participants:
                    print(f"\n[{datetime.now()}] Current participants:")
                    for p in participants:
                        print(f"  - {p.identity} (SID: {p.sid}, State: {p.state})")
                else:
                    print(f"\n[{datetime.now()}] No participants currently in the room")
        
        if not room_exists:
            print(f"[{datetime.now()}] ✗ Room '{ROOM_NAME}' does not exist")
            return
            
    except Exception as e:
        print(f"[{datetime.now()}] Error checking room: {e}")
        return
    
    # 2. Generate a test participant token
    print(f"\n[{datetime.now()}] Generating participant token...")
    
    token = AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    participant_identity = f"test-participant-{int(time.time())}"
    
    # Grant permissions for the participant
    token.with_identity(participant_identity)\
         .with_name("Test Participant")\
         .with_grants(VideoGrants(
             room_join=True,
             room=ROOM_NAME,
             can_publish=True,
             can_subscribe=True,
             can_publish_data=True
         ))
    
    # Add metadata to help agent identify this participant
    metadata = {
        "type": "test_participant",
        "purpose": "testing_agent_trigger",
        "timestamp": datetime.now().isoformat()
    }
    token.with_metadata(json.dumps(metadata))
    
    jwt_token = token.to_jwt()
    print(f"[{datetime.now()}] ✓ Token generated for: {participant_identity}")
    print(f"[{datetime.now()}] Token (first 50 chars): {jwt_token[:50]}...")
    
    # Decode and display token claims for verification
    try:
        # Parse JWT without verification to show claims
        parts = jwt_token.split('.')
        payload = parts[1]
        # Add padding if needed
        payload += '=' * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        claims = json.loads(decoded)
        print(f"\n[{datetime.now()}] Token claims:")
        print(json.dumps(claims, indent=2))
    except Exception as e:
        print(f"[{datetime.now()}] Could not decode token claims: {e}")
    
    # 3. Show what happens when a participant joins
    print(f"\n[{datetime.now()}] What happens when participant joins:")
    print("1. Participant connects with the token to LiveKit Cloud")
    print("2. LiveKit validates the token and grants access to the room")
    print("3. LiveKit sends a 'participant_connected' webhook event")
    print("4. The agent worker receives a job dispatch request")
    print("5. The agent's request_filter function evaluates the job")
    print("6. If accepted, the agent joins the room and handles the participant")
    
    print(f"\n[{datetime.now()}] To test the full flow, use this token in a LiveKit client:")
    print(f"Token: {jwt_token}")
    print(f"\nOr use the LiveKit CLI:")
    print(f"livekit-cli join-room --url {LIVEKIT_URL} --token {jwt_token}")
    
    # Actually connect as a participant to trigger the agent
    print(f"\n[{datetime.now()}] Attempting to connect as participant...")
    from livekit import rtc
    room = rtc.Room()
    try:
        await room.connect(LIVEKIT_URL, jwt_token)
        print(f"[{datetime.now()}] ✓ Successfully connected to room!")
        print(f"[{datetime.now()}] Local participant: {room.local_participant.identity}")
        
        # Wait a moment to let the agent detect us
        print(f"[{datetime.now()}] Waiting for agent to detect participant...")
        await asyncio.sleep(5)
        
        # Check room participants
        print(f"[{datetime.now()}] Room participants:")
        for sid, participant in room.remote_participants.items():
            print(f"  - {participant.identity} (SID: {sid})")
        
        # Disconnect
        await room.disconnect()
        print(f"[{datetime.now()}] Disconnected from room")
    except Exception as e:
        print(f"[{datetime.now()}] Error connecting: {e}")

if __name__ == "__main__":
    asyncio.run(test_livekit_room())