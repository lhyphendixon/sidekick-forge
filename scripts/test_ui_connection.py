#!/usr/bin/env python3
"""
Test UI connection to LiveKit room with full E2E verification
"""
import asyncio
import httpx
import time
import docker
import json
from datetime import datetime
from livekit import rtc, api

# Configuration
API_URL = "http://localhost:8000"
CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"
AGENT_SLUG = "clarence-coherence"

class UIConnectionTest:
    def __init__(self):
        self.docker_client = docker.from_env()
        self.evidence = []
        
    def log(self, message, data=None):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "message": message,
            "data": data or {}
        }
        self.evidence.append(entry)
        print(f"[{entry['timestamp']}] {message}")
        if data:
            print(f"  Data: {json.dumps(data, indent=2)}")
    
    async def test_full_flow(self):
        """Test complete UI flow with user connection"""
        print("\n=== TESTING FULL UI CONNECTION FLOW ===\n")
        
        # Step 1: Start voice preview (simulate UI click)
        self.log("Step 1: Starting voice preview via UI endpoint")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{API_URL}/admin/agents/preview/{CLIENT_ID}/{AGENT_SLUG}/voice-start",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "HX-Request": "true"
                },
                data={"session_id": f"ui-test-{int(time.time())}"}
            )
            
            if response.status_code != 200:
                self.log("ERROR: Voice start failed", {"status": response.status_code})
                return False
            
            # Extract connection details from HTML response
            html = response.text
            import re
            
            # Look for hardcoded values in room.connect() call
            connect_match = re.search(r"room\.connect\('([^']+)',\s*'([^']+)'\)", html)
            room_name_match = re.search(r"room_name['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", html)
            
            if not connect_match:
                self.log("ERROR: Could not find room.connect() call in response")
                return False
            
            server_url = connect_match.group(1)
            user_token = connect_match.group(2)
            room_name = room_name_match.group(1) if room_name_match else "unknown"
            
            self.log("Extracted connection details", {
                "server_url": server_url,
                "room_name": room_name,
                "token_length": len(user_token)
            })
            
            # Step 2: Monitor container for agent activity
            self.log("Step 2: Monitoring agent container")
            
            container = self.docker_client.containers.get("agent_df91fd06_clarence_coherence")
            initial_logs = container.logs(tail=100).decode()
            
            # Step 3: Simulate UI connecting to LiveKit
            self.log("Step 3: Simulating UI connection to LiveKit")
            
            room = rtc.Room()
            events_received = []
            
            @room.on("participant_connected")
            def on_participant_connected(participant):
                self.log(f"Participant connected: {participant.identity}")
                events_received.append(("participant_connected", participant.identity))
            
            @room.on("track_published")
            def on_track_published(publication, participant):
                self.log(f"Track published by {participant.identity}: {publication.kind}")
                events_received.append(("track_published", f"{participant.identity}:{publication.kind}"))
            
            @room.on("track_subscribed")
            def on_track_subscribed(track, publication, participant):
                self.log(f"Subscribed to track from {participant.identity}: {track.kind}")
                events_received.append(("track_subscribed", f"{participant.identity}:{track.kind}"))
                
                if track.kind == rtc.TrackKind.KIND_AUDIO:
                    self.log("🎵 AGENT AUDIO DETECTED - Agent is speaking!")
            
            @room.on("data_received")
            def on_data_received(data, participant):
                self.log(f"Data received from {participant.identity}: {data.decode()}")
                events_received.append(("data_received", data.decode()))
            
            try:
                # Connect to room
                await room.connect(server_url, user_token)
                self.log("✅ Successfully connected to LiveKit room")
                
                # Wait a moment for agent to detect us
                await asyncio.sleep(2)
                
                # Step 4: Publish audio track (simulate microphone)
                self.log("Step 4: Publishing audio track to trigger agent")
                
                source = rtc.AudioSource(sample_rate=48000, num_channels=1)
                track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
                
                options = rtc.TrackPublishOptions()
                publication = await room.local_participant.publish_track(track, options)
                self.log(f"✅ Audio track published: {publication.sid}")
                
                # Step 5: Monitor for agent response
                self.log("Step 5: Waiting for agent response...")
                
                start_time = time.time()
                agent_responded = False
                
                while time.time() - start_time < 10:  # Wait up to 10 seconds
                    # Check container logs for activity
                    current_logs = container.logs(tail=200).decode()
                    new_logs = current_logs.replace(initial_logs, "")
                    
                    if "participant_connected" in new_logs:
                        self.log("✅ Agent detected participant connection")
                    
                    if "Greeting sent successfully" in new_logs:
                        self.log("✅ GREETING SENT SUCCESSFULLY!")
                        agent_responded = True
                        break
                    
                    if "USER STARTED SPEAKING EVENT" in new_logs:
                        self.log("✅ Agent detected user speaking")
                    
                    # Check LiveKit events
                    if any("agent" in str(e[1]).lower() for e in events_received):
                        self.log("✅ Agent activity detected in LiveKit")
                        agent_responded = True
                    
                    await asyncio.sleep(0.5)
                
                # Step 6: Analyze results
                self.log("Step 6: Analyzing results")
                
                # Get final logs
                final_logs = container.logs(tail=300).decode()
                agent_logs = final_logs.replace(initial_logs, "")
                
                # Count key events
                results = {
                    "room_connected": True,
                    "audio_published": True,
                    "events_received": len(events_received),
                    "agent_detected_participant": "participant_connected" in agent_logs,
                    "greeting_attempted": "Attempting to send greeting" in agent_logs,
                    "greeting_sent": "Greeting sent successfully" in agent_logs,
                    "agent_responded": agent_responded,
                    "session_say_called": "About to call session.say()" in agent_logs,
                    "audio_tracks_from_agent": sum(1 for e in events_received if e[0] == "track_subscribed" and "audio" in str(e[1]))
                }
                
                self.log("Test results", results)
                
                # Disconnect
                await room.disconnect()
                
                # Final verdict
                if results["greeting_sent"] and results["audio_tracks_from_agent"] > 0:
                    self.log("✅ FULL E2E TEST PASSED - Agent responded with audio!")
                    return True
                else:
                    self.log("❌ E2E TEST FAILED - Missing agent audio response")
                    self.log("Container logs excerpt:", {"logs": agent_logs[-500:]})
                    return False
                    
            except Exception as e:
                self.log(f"ERROR during connection: {str(e)}")
                return False

async def main():
    test = UIConnectionTest()
    success = await test.test_full_flow()
    
    # Save evidence
    evidence_file = f"/tmp/ui_connection_test_{int(time.time())}.json"
    with open(evidence_file, 'w') as f:
        json.dump(test.evidence, f, indent=2)
    
    print(f"\n📄 Evidence saved to: {evidence_file}")
    
    if success:
        print("\n✅ UI CONNECTION TEST PASSED - Agent responds with audio")
    else:
        print("\n❌ UI CONNECTION TEST FAILED - No agent audio response")
        print("\n🔍 Check the evidence file for details")

if __name__ == "__main__":
    asyncio.run(main())