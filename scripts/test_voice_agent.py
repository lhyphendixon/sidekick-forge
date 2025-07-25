#!/usr/bin/env python3
"""
Test voice agent functionality
"""
import requests
import time
import json

BASE_URL = "http://localhost:8000"
CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"
AGENT_SLUG = "clarence-coherence"

def test_voice_agent():
    print("🧪 Testing Voice Agent")
    print("=" * 50)
    
    # 1. Trigger agent
    print("\n1️⃣ Triggering voice agent...")
    room_name = f"test_voice_{int(time.time())}"
    
    start_time = time.time()
    response = requests.post(
        f"{BASE_URL}/api/v1/trigger-agent",
        json={
            "agent_slug": AGENT_SLUG,
            "client_id": CLIENT_ID,
            "mode": "voice",
            "room_name": room_name,
            "user_id": "test_user",
            "session_id": "test_session",
        }
    )
    elapsed = time.time() - start_time
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Agent triggered in {elapsed:.2f} seconds")
        print(f"   Room: {room_name}")
        print(f"   Container: {data['data']['container_info']['container_name']}")
        print(f"   Status: {data['data']['container_info']['status']}")
        
        # Check container logs
        container_name = data['data']['container_info']['container_name']
        time.sleep(3)  # Give agent time to initialize
        
        print(f"\n2️⃣ Checking container logs...")
        import subprocess
        logs = subprocess.run(
            f"docker logs {container_name} 2>&1 | tail -50 | grep -E '(greeting|session|ERROR|WARN)'",
            shell=True,
            capture_output=True,
            text=True
        )
        
        if logs.stdout:
            print("📋 Recent logs:")
            for line in logs.stdout.strip().split('\n')[-10:]:
                print(f"   {line}")
        
        # Get user token
        user_token = data['data']['livekit_config']['user_token']
        server_url = data['data']['livekit_config']['server_url']
        
        print(f"\n3️⃣ Connection info:")
        print(f"   Server: {server_url}")
        print(f"   Token length: {len(user_token)}")
        print(f"   Room: {room_name}")
        
    else:
        print(f"❌ Failed to trigger agent: {response.status_code}")
        print(response.text)
    
    print("\n" + "=" * 50)

if __name__ == "__main__":
    test_voice_agent()