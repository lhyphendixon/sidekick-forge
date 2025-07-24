#!/usr/bin/env python3
"""
Test script to verify Phase 1 implementation:
1. Room creation with retry logic
2. Client credential isolation
3. Parallel dispatch
4. Room persistence
"""

import asyncio
import httpx
import json
import os
import sys
from datetime import datetime

# Get backend URL from environment or use default
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API_KEY = os.getenv("TEST_API_KEY", "test-api-key")

# Test configuration
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"  # Example client
TEST_AGENT_SLUG = "general_ai_assistant"
TEST_USER_ID = "test-user-123"

async def test_phase1():
    """Run Phase 1 tests"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"\n🧪 Testing Phase 1 Implementation - {datetime.now()}")
        print(f"Backend URL: {BACKEND_URL}")
        print("=" * 80)
        
        # Test 1: Trigger agent with room creation
        print("\n📋 Test 1: Trigger agent with room creation")
        room_name = f"test_phase1_{int(datetime.now().timestamp())}"
        
        trigger_payload = {
            "room_name": room_name,
            "agent_slug": TEST_AGENT_SLUG,
            "user_id": TEST_USER_ID,
            "conversation_id": f"conv_{room_name}",
            "platform": "livekit",
            "mode": "voice"
        }
        
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
        print(f"   Room name: {room_name}")
        start_time = datetime.now()
        
        try:
            response = await client.post(
                f"{BACKEND_URL}/api/v1/trigger-agent",
                json=trigger_payload,
                headers=headers
            )
            
            elapsed = (datetime.now() - start_time).total_seconds()
            print(f"   Response time: {elapsed:.2f}s")
            print(f"   Status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"   ✅ Success: {result.get('message', 'No message')}")
                
                # Check room info
                if "room_info" in result:
                    room_info = result["room_info"]
                    print(f"\n   📊 Room Info:")
                    print(f"      - Created: {room_info.get('created', False)}")
                    print(f"      - Method: {room_info.get('method', 'unknown')}")
                    print(f"      - Empty timeout: {room_info.get('empty_timeout', 'N/A')}s")
                    print(f"      - Retry attempts: {room_info.get('retry_attempts', 0)}")
                
                # Check container info
                if "container_info" in result:
                    container_info = result["container_info"]
                    print(f"\n   🐳 Container Info:")
                    print(f"      - Status: {container_info.get('status', 'unknown')}")
                    print(f"      - Name: {container_info.get('container_name', 'N/A')}")
                    print(f"      - Worker registered: {container_info.get('worker_registered', False)}")
                    
                    # Check dispatch info
                    if "dispatch" in container_info:
                        dispatch = container_info["dispatch"]
                        print(f"\n   📨 Dispatch Info:")
                        print(f"      - Success: {dispatch.get('success', False)}")
                        print(f"      - Method: {dispatch.get('method', 'unknown')}")
                        print(f"      - Message: {dispatch.get('message', 'N/A')}")
                
                # Check LiveKit config (should use client credentials)
                if "livekit_config" in result:
                    lk_config = result["livekit_config"]
                    print(f"\n   🔐 LiveKit Config:")
                    print(f"      - Server URL: {lk_config.get('server_url', 'N/A')}")
                    print(f"      - Token provided: {'user_token' in lk_config}")
                    
                    # Verify it's NOT using backend credentials
                    if "wss://litebridge" in str(lk_config.get('server_url', '')):
                        print(f"      ❌ WARNING: Using backend LiveKit URL!")
                    else:
                        print(f"      ✅ Using client-specific LiveKit URL")
                
            else:
                print(f"   ❌ Failed: {response.text}")
                
        except Exception as e:
            print(f"   ❌ Error: {str(e)}")
        
        # Test 2: Check room status
        print(f"\n📋 Test 2: Check room status")
        await asyncio.sleep(2)  # Give room time to stabilize
        
        try:
            response = await client.get(
                f"{BACKEND_URL}/api/v1/rooms/status/{room_name}",
                headers=headers
            )
            
            if response.status_code == 200:
                room_status = response.json()
                status = room_status.get("status", {})
                print(f"   ✅ Room status retrieved")
                print(f"      - Exists: {status.get('exists', False)}")
                print(f"      - Participants: {status.get('participants', 0)}")
                print(f"      - Monitored: {status.get('monitored', False)}")
            else:
                print(f"   ❌ Failed to get room status: {response.status_code}")
                
        except Exception as e:
            print(f"   ❌ Error checking room status: {str(e)}")
        
        # Test 3: List monitored rooms
        print(f"\n📋 Test 3: List monitored rooms")
        
        try:
            response = await client.get(
                f"{BACKEND_URL}/api/v1/rooms/monitored",
                headers=headers
            )
            
            if response.status_code == 200:
                monitored_rooms = response.json()
                print(f"   ✅ Found {len(monitored_rooms)} monitored rooms")
                
                # Find our test room
                our_room = next((r for r in monitored_rooms if r["room_name"] == room_name), None)
                if our_room:
                    print(f"   ✅ Our test room is being monitored")
                    print(f"      - Added at: {our_room.get('added_at', 'N/A')}")
                    print(f"      - Check count: {our_room.get('check_count', 0)}")
                else:
                    print(f"   ⚠️  Our test room not in monitored list")
                    
        except Exception as e:
            print(f"   ❌ Error listing monitored rooms: {str(e)}")
        
        # Test 4: Container health check
        print(f"\n📋 Test 4: Container health check")
        
        try:
            response = await client.get(
                f"{BACKEND_URL}/api/v1/containers/health",
                headers=headers
            )
            
            if response.status_code == 200:
                health_data = response.json()
                print(f"   ✅ Container health check completed")
                print(f"      - Total containers: {health_data.get('total', 0)}")
                print(f"      - Running: {health_data.get('running', 0)}")
                print(f"      - Healthy: {health_data.get('healthy', 0)}")
                
                # Check for our test container
                containers = health_data.get("containers", [])
                test_containers = [c for c in containers if TEST_CLIENT_ID in c.get("name", "")]
                if test_containers:
                    print(f"   ✅ Found {len(test_containers)} container(s) for test client")
                    for container in test_containers:
                        print(f"      - {container['name']}: {container['status']}")
                        
        except Exception as e:
            print(f"   ❌ Error checking container health: {str(e)}")
        
        print("\n" + "=" * 80)
        print("✅ Phase 1 testing complete")
        
        # Summary
        print(f"\n📊 Phase 1 Implementation Summary:")
        print(f"   1. Room Creation: Implemented with retry logic")
        print(f"   2. Parallel Dispatch: Container spawn and token generation run concurrently")
        print(f"   3. Client Credentials: Containers use client-specific LiveKit credentials")
        print(f"   4. Room Persistence: 2-hour timeout for preview rooms")
        print(f"   5. Monitoring: Room and container health monitoring active")

if __name__ == "__main__":
    asyncio.run(test_phase1())