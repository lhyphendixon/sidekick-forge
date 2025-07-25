#!/usr/bin/env python3
"""
End-to-end test for voice preview functionality
Captures the exact flow: UI click → agent response with audio
"""
import asyncio
import httpx
import time
import docker
import json
from datetime import datetime

# ANSI colors
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'

API_URL = "http://localhost:8000/api/v1"
ADMIN_URL = "http://localhost:8000/admin"
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"
TEST_AGENT_SLUG = "clarence-coherence"

class PreviewE2ETest:
    def __init__(self):
        self.docker_client = docker.from_env()
        self.evidence = {}
        
    def log_evidence(self, step: str, data: dict):
        """Log evidence for each step"""
        self.evidence[step] = {
            "timestamp": datetime.now().isoformat(),
            "data": data
        }
        print(f"{BLUE}[EVIDENCE]{RESET} {step}: {json.dumps(data, indent=2)}")
    
    async def test_preview_flow(self):
        """Test the complete preview flow"""
        print(f"\n{BOLD}Testing Voice Preview E2E Flow{RESET}")
        print("=" * 60)
        
        # Step 1: Check container state
        print(f"\n{YELLOW}1. Checking Container State{RESET}")
        containers = self.docker_client.containers.list(filters={"name": "agent_"})
        container_info = []
        
        for container in containers:
            info = {
                "name": container.name,
                "status": container.status,
                "created": container.attrs["Created"],
                "image": container.image.tags[0] if container.image.tags else "unknown"
            }
            container_info.append(info)
            
            # Get recent logs
            logs = container.logs(tail=20).decode()
            if "preview_" in logs:
                info["has_preview_activity"] = True
            
        self.log_evidence("container_state", {"containers": container_info})
        
        # Step 2: Create preview room
        print(f"\n{YELLOW}2. Creating Preview Room{RESET}")
        room_name = f"preview_{TEST_AGENT_SLUG}_{int(time.time())}"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Trigger agent for preview
            response = await client.post(
                f"{API_URL}/trigger-agent",
                json={
                    "agent_slug": TEST_AGENT_SLUG,
                    "mode": "voice",
                    "room_name": room_name,
                    "user_id": "preview-user",
                    "client_id": TEST_CLIENT_ID,
                    "is_preview": True  # Mark as preview
                }
            )
            
            if response.status_code != 200:
                self.log_evidence("trigger_error", {
                    "status": response.status_code,
                    "response": response.text
                })
                print(f"{RED}✗ Failed to trigger agent{RESET}")
                return False
            
            trigger_data = response.json()
            self.log_evidence("trigger_success", trigger_data)
            
            # Extract key info
            user_token = trigger_data.get("data", {}).get("livekit_config", {}).get("user_token")
            server_url = trigger_data.get("data", {}).get("livekit_config", {}).get("server_url")
            container_name = trigger_data.get("data", {}).get("container_info", {}).get("container_name")
            
            print(f"{GREEN}✓ Room created: {room_name}{RESET}")
            print(f"  Token: {user_token[:50]}..." if user_token else "  No token!")
            
            # Step 3: Monitor container logs
            print(f"\n{YELLOW}3. Monitoring Container Response{RESET}")
            
            if container_name:
                # Start log monitoring
                start_time = time.time()
                greeting_sent = False
                participant_connected = False
                error_found = False
                
                while time.time() - start_time < 15:  # Monitor for 15 seconds
                    try:
                        container = self.docker_client.containers.get(container_name)
                        logs = container.logs(tail=100).decode()
                        
                        # Check for critical events
                        if "Greeting sent successfully" in logs:
                            greeting_sent = True
                            print(f"{GREEN}✓ Greeting sent!{RESET}")
                            
                        if "participant_connected" in logs or "USER STARTED SPEAKING EVENT" in logs:
                            participant_connected = True
                            print(f"{GREEN}✓ Participant activity detected{RESET}")
                            
                        if "ERROR" in logs or "Failed" in logs:
                            error_found = True
                            error_lines = [l for l in logs.split('\n') if 'ERROR' in l or 'Failed' in l]
                            print(f"{RED}✗ Errors found: {error_lines[-1] if error_lines else 'Unknown'}{RESET}")
                        
                        # Check current activity
                        recent_logs = container.logs(since=int(time.time() - 5)).decode()
                        if recent_logs.strip():
                            print(f"{BLUE}Recent activity:{RESET} {len(recent_logs)} bytes")
                            
                    except Exception as e:
                        print(f"{RED}Error monitoring container: {e}{RESET}")
                    
                    await asyncio.sleep(1)
                
                # Final log analysis
                final_logs = container.logs(tail=200).decode()
                
                self.log_evidence("container_monitoring", {
                    "greeting_sent": greeting_sent,
                    "participant_connected": participant_connected,
                    "errors_found": error_found,
                    "greeting_attempts": final_logs.count("Attempting to send greeting"),
                    "session_say_calls": final_logs.count("About to call session.say()"),
                    "greeting_confirmations": final_logs.count("Greeting sent successfully")
                })
                
                # Step 4: Check for HTMX/UI issues
                print(f"\n{YELLOW}4. Checking UI Integration{RESET}")
                
                # Try to access the preview endpoint
                preview_response = await client.post(
                    f"{ADMIN_URL}/agents/{TEST_CLIENT_ID}/{TEST_AGENT_SLUG}/preview",
                    headers={"HX-Request": "true"}  # Simulate HTMX request
                )
                
                self.log_evidence("preview_ui", {
                    "status": preview_response.status_code,
                    "has_htmx_response": "HX-Redirect" in preview_response.headers or "HX-Trigger" in preview_response.headers,
                    "content_length": len(preview_response.text)
                })
                
                # Step 5: Analyze results
                print(f"\n{YELLOW}5. Analysis{RESET}")
                
                issues_found = []
                
                if not greeting_sent:
                    issues_found.append("Greeting never sent - session.say() may be blocking")
                    
                if not participant_connected:
                    issues_found.append("No participant connection detected - agent waiting for user")
                    
                if error_found:
                    issues_found.append("Errors found in container logs")
                    
                if not user_token:
                    issues_found.append("No user token generated")
                    
                if greeting_sent and not participant_connected:
                    issues_found.append("Greeting sent but no user to receive it")
                
                self.log_evidence("analysis", {
                    "issues": issues_found,
                    "probable_cause": "Agent is ready but no user joins the room" if not participant_connected else "Unknown"
                })
                
                # Summary
                print(f"\n{BOLD}Summary{RESET}")
                if issues_found:
                    print(f"{RED}✗ Issues found:{RESET}")
                    for issue in issues_found:
                        print(f"  - {issue}")
                else:
                    print(f"{GREEN}✓ Preview flow working correctly{RESET}")
                    
                return len(issues_found) == 0
            
            else:
                print(f"{RED}✗ No container name in response{RESET}")
                return False

async def main():
    test = PreviewE2ETest()
    success = await test.test_preview_flow()
    
    # Save evidence
    evidence_file = f"/tmp/preview_e2e_evidence_{int(time.time())}.json"
    with open(evidence_file, 'w') as f:
        json.dump(test.evidence, f, indent=2)
    
    print(f"\n{BLUE}Evidence saved to: {evidence_file}{RESET}")
    
    if not success:
        print(f"\n{RED}❌ Preview E2E test failed - no audio response detected{RESET}")
        print(f"\n{YELLOW}Root cause: The agent is ready and attempts to greet, but there's no user in the room.{RESET}")
        print(f"{YELLOW}The UI needs to actually connect a user to the LiveKit room using the provided token.{RESET}")
    else:
        print(f"\n{GREEN}✅ Preview E2E test passed{RESET}")

if __name__ == "__main__":
    asyncio.run(main())