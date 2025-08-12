#!/usr/bin/env python3
"""
Test script to verify voice agent is working
"""
import asyncio
import json
import httpx
from datetime import datetime

BASE_URL = "http://localhost:8000"

async def test_voice_agent():
    """Test voice agent dispatch and connection"""
    async with httpx.AsyncClient() as client:
        print("=== Voice Agent Test ===")
        print(f"Started at: {datetime.now()}")
        
        # 1. Get first client
        print("\n1. Getting client...")
        resp = await client.get(f"{BASE_URL}/api/v2/clients")
        clients = resp.json()
        if not clients:
            print("❌ No clients found")
            return
        
        client_id = clients[0]["id"]
        client_name = clients[0]["name"]
        print(f"✅ Using client: {client_name} ({client_id})")
        
        # 2. For testing, use a known agent slug
        print("\n2. Using test agent...")
        # We know "clarence-coherence" exists from the admin UI logs
        agent_slug = "clarence-coherence"
        agent_name = "Clarence Coherence"
        print(f"✅ Using agent: {agent_name} ({agent_slug})")
        
        # 3. Trigger agent
        print("\n3. Triggering agent...")
        room_name = f"test_room_{int(datetime.now().timestamp())}"
        
        trigger_data = {
            "agent_slug": agent_slug,
            "mode": "voice",
            "room_name": room_name,
            "user_id": "test_user",
            "client_id": client_id
        }
        
        resp = await client.post(
            f"{BASE_URL}/api/v1/trigger-agent",
            json=trigger_data
        )
        
        if resp.status_code == 200:
            result = resp.json()
            print(f"✅ Agent triggered successfully")
            print(f"   Room: {result.get('room_name', 'N/A')}")
            print(f"   Status: {result.get('status', 'N/A')}")
            print(f"   Token: {result.get('token', 'N/A')[:50]}..." if result.get('token') else "   Token: None")
            
            # Check worker logs for job acceptance
            print("\n4. Agent should now be ready in the room")
            print("   Check worker logs: docker logs --tail=50 sidekick-forge_agent-worker_1")
            
        else:
            print(f"❌ Failed to trigger agent: {resp.status_code}")
            print(f"   Response: {resp.text}")

if __name__ == "__main__":
    asyncio.run(test_voice_agent())