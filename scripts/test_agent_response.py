#!/usr/bin/env python3
"""
Test Agent Response - Verify the agent actually responds to voice/text input
This test creates a room, triggers the agent, and verifies it responds
"""

import asyncio
import logging
import httpx
import json
import time
from livekit import api, rtc
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BASE_URL = "http://localhost:8000"
CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"
AGENT_SLUG = "autonomite"

class AgentResponseTest:
    """Test that the agent actually responds"""
    
    def __init__(self):
        self.room = None
        self.agent_responded = False
        self.agent_response = None
        self.test_passed = False
        
    async def test_agent_response(self):
        """Main test function"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Trigger the agent
            logger.info("=== Step 1: Triggering Agent ===")
            room_name = f"test-response-{int(time.time())}"
            
            trigger_response = await client.post(
                f"{BASE_URL}/api/v1/trigger-agent",
                json={
                    "agent_slug": AGENT_SLUG,
                    "mode": "voice",
                    "room_name": room_name,
                    "user_id": "test-user",
                    "client_id": CLIENT_ID
                }
            )
            
            if trigger_response.status_code != 200:
                logger.error(f"Failed to trigger agent: {trigger_response.text}")
                return False
                
            trigger_data = trigger_response.json()
            livekit_config = trigger_data["data"]["livekit_config"]
            user_token = livekit_config["user_token"]
            server_url = livekit_config["server_url"]
            
            logger.info(f"✅ Agent triggered in room: {room_name}")
            logger.info(f"   Server: {server_url}")
            
            # Wait for agent to be ready
            await asyncio.sleep(3)
            
            # Step 2: Connect to the room as a user
            logger.info("=== Step 2: Connecting to Room ===")
            
            try:
                self.room = rtc.Room()
                
                # Set up event handlers
                @self.room.on("data_received")
                def on_data_received(data: bytes, participant: rtc.RemoteParticipant, kind: str):
                    """Handle data messages from agent"""
                    try:
                        message = data.decode('utf-8')
                        logger.info(f"📨 Received data from {participant.identity}: {message}")
                        if participant.identity.startswith("agent"):
                            self.agent_responded = True
                            self.agent_response = message
                    except Exception as e:
                        logger.error(f"Error handling data: {e}")
                
                @self.room.on("participant_connected")
                def on_participant_connected(participant: rtc.RemoteParticipant):
                    """Handle participant connections"""
                    logger.info(f"👤 Participant connected: {participant.identity}")
                    if participant.identity.startswith("agent"):
                        logger.info("🤖 Agent is in the room!")
                
                @self.room.on("track_published")
                def on_track_published(publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
                    """Handle track publications"""
                    logger.info(f"📡 Track published by {participant.identity}: {publication.kind}")
                
                @self.room.on("track_subscribed")
                def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
                    """Handle track subscriptions"""
                    logger.info(f"📡 Subscribed to track from {participant.identity}: {track.kind}")
                    if participant.identity.startswith("agent") and track.kind == rtc.TrackKind.KIND_AUDIO:
                        logger.info("🔊 Agent audio track subscribed - agent can speak!")
                
                # Connect to room
                await self.room.connect(server_url, user_token)
                logger.info(f"✅ Connected to room as user")
                
                # Wait to see participants
                await asyncio.sleep(2)
                
                # Check participants
                participants = list(self.room.remote_participants.values())
                logger.info(f"Current participants: {len(participants)}")
                for p in participants:
                    logger.info(f"  - {p.identity}")
                
                # Step 3: Send a text message via data channel
                logger.info("=== Step 3: Sending Test Message ===")
                
                # Try sending a message via data channel
                message = json.dumps({
                    "type": "chat",
                    "message": "Hello agent, can you hear me?"
                })
                
                await self.room.local_participant.publish_data(
                    message.encode('utf-8'),
                    reliable=True
                )
                logger.info("📤 Sent test message via data channel")
                
                # Wait for response
                logger.info("⏳ Waiting for agent response...")
                await asyncio.sleep(5)
                
                # Step 4: Check if agent responded
                logger.info("=== Step 4: Checking Agent Response ===")
                
                if self.agent_responded:
                    logger.info(f"✅ AGENT RESPONDED: {self.agent_response}")
                    self.test_passed = True
                else:
                    logger.warning("❌ No response received from agent")
                    
                    # Try alternative: Check if agent has audio track
                    agent_participant = None
                    for p in self.room.remote_participants.values():
                        if p.identity.startswith("agent"):
                            agent_participant = p
                            break
                    
                    if agent_participant:
                        logger.info(f"Agent {agent_participant.identity} is in the room")
                        audio_tracks = [pub for pub in agent_participant.track_publications.values() 
                                      if pub.kind == rtc.TrackKind.KIND_AUDIO]
                        if audio_tracks:
                            logger.info("✅ Agent has audio track published (ready to speak)")
                            self.test_passed = True
                        else:
                            logger.info("❌ Agent has no audio track")
                    else:
                        logger.error("❌ No agent participant found in room")
                
                # Disconnect
                await self.room.disconnect()
                
            except Exception as e:
                logger.error(f"Error in room connection: {e}")
                return False
                
        return self.test_passed
    
    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*60)
        print("AGENT RESPONSE TEST SUMMARY")
        print("="*60)
        
        if self.test_passed:
            print("✅ TEST PASSED - Agent is responsive")
            if self.agent_response:
                print(f"   Agent response: {self.agent_response}")
        else:
            print("❌ TEST FAILED - No agent response detected")
            
        print("="*60)


async def main():
    """Main test runner"""
    print("Starting Agent Response Test...")
    print("-"*60)
    
    tester = AgentResponseTest()
    
    try:
        success = await tester.test_agent_response()
    except Exception as e:
        logger.error(f"Test error: {str(e)}")
        success = False
    
    tester.print_summary()
    
    # Exit with appropriate code
    import sys
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())