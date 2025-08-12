#!/usr/bin/env python3
"""
Test LiveKit connection directly
"""
import asyncio
import os
import sys
sys.path.insert(0, '/root/sidekick-forge')

from app.integrations.livekit_client import livekit_manager

async def test_livekit():
    print("Testing LiveKit connection...")
    
    try:
        # Initialize if needed
        if not livekit_manager._initialized:
            print("Initializing LiveKit manager...")
            await livekit_manager.initialize()
        
        print(f"LiveKit URL: {livekit_manager.url}")
        print(f"LiveKit API Key: {livekit_manager.api_key[:10]}...")
        
        # Create a test token
        room_name = "test_room_123"
        user_token = livekit_manager.create_token(
            identity="test_user",
            room_name=room_name
        )
        
        print(f"Room Name: {room_name}")
        print(f"Token created successfully!")
        print(f"Token length: {len(user_token)}")
        print(f"Token preview: {user_token[:50]}...")
        
        # Create the HTML that would be sent to browser
        html = f"""
        <script>
        const serverUrl = '{livekit_manager.url}';
        const userToken = '{user_token}';
        const roomName = '{room_name}';
        
        console.log('LiveKit Config:', {{
            serverUrl: serverUrl,
            userTokenLength: userToken.length,
            roomName: roomName
        }});
        </script>
        """
        
        print("\nHTML output preview:")
        print(html)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_livekit())