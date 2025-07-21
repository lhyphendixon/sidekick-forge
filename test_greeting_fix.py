#!/usr/bin/env python3
"""
Test the greeting fix by triggering an agent with the new image
"""
import asyncio
import httpx
import json
import time
import subprocess

async def test_greeting():
    print("=" * 60)
    print("TESTING GREETING FIX")
    print("=" * 60)
    
    # Update container manager to use the fixed image
    print("\n1. Updating default agent image...")
    subprocess.run([
        "sed", "-i", 
        's|DEFAULT_AGENT_IMAGE = "autonomite/agent-runtime:.*"|DEFAULT_AGENT_IMAGE = "autonomite/agent-runtime:greeting-fix-v2"|',
        "/opt/autonomite-saas/app/services/container_manager.py"
    ])
    
    # Trigger an agent
    print("\n2. Triggering agent with fixed greeting...")
    
    trigger_data = {
        "agent_slug": "clarence-coherence",
        "mode": "voice",
        "room_name": f"greeting-test-{int(time.time())}",
        "user_id": "test-user",
        "client_id": "df91fd06-816f-4273-a903-5a4861277040"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/v1/trigger-agent",
            json=trigger_data,
            timeout=30.0
        )
        
        if response.status_code in [200, 201]:
            data = response.json()
            print(f"\n✅ Agent triggered successfully")
            print(f"   Container: {data['data']['container_info']['container_name']}")
            print(f"   Status: {data['data']['container_info']['status']}")
            
            container_name = data['data']['container_info']['container_name']
            
            # Wait for agent to start
            print("\n3. Waiting for agent to initialize...")
            await asyncio.sleep(5)
            
            # Check logs for greeting behavior
            print("\n4. Checking greeting behavior...")
            result = subprocess.run(
                ["docker", "logs", "--tail", "100", container_name],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                logs = result.stdout + result.stderr
                
                # Check for key indicators
                checks = {
                    "on_enter called": "Agent entering - on_enter() called" in logs,
                    "No greeting in on_enter": "Don't send greeting here" not in logs or "Greeting will be sent after session starts" in logs,
                    "Session started": "Agent session started successfully" in logs,
                    "Greeting after session": "participant(s) in room, sending greeting" in logs or "Sending greeting to" in logs,
                    "No session error": "No agent session available for greeting" not in logs
                }
                
                print("\n📋 Greeting Fix Verification:")
                print("-" * 40)
                
                all_passed = True
                for check, passed in checks.items():
                    status = "✅" if passed else "❌"
                    print(f"{status} {check}")
                    if not passed:
                        all_passed = False
                
                # Extract greeting-related logs
                print("\n📜 Greeting-Related Logs:")
                print("-" * 40)
                for line in logs.split('\n'):
                    if any(keyword in line for keyword in ["greeting", "Greeting", "on_enter", "session started", "participant"]):
                        print(line)
                
                if all_passed:
                    print("\n✅ GREETING FIX VERIFIED - Agent waits for session before greeting")
                else:
                    print("\n⚠️  Some checks failed - Review logs above")
                
                # Stop the container
                print(f"\n5. Cleaning up container {container_name}...")
                subprocess.run(["docker", "stop", container_name], capture_output=True)
                
            else:
                print(f"❌ Failed to get container logs")
        else:
            print(f"❌ Failed to trigger agent: {response.status_code}")
            print(response.text)

if __name__ == "__main__":
    asyncio.run(test_greeting())