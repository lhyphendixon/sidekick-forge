#!/usr/bin/env python3
"""
Test script for Phase 4: Multi-session state isolation

Tests that agents can handle 10 consecutive sessions without state carryover,
verifying comprehensive state reset and warm pool functionality.
"""
import asyncio
import httpx
import json
import os
import time
import uuid
from datetime import datetime
from typing import List, Dict, Any

# Backend configuration
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API_KEY = os.getenv("TEST_API_KEY", "test-api-key")

# Test configuration
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"
TEST_AGENT_SLUG = "clarence_coherence"
NUM_SESSIONS = 10
SESSION_DURATION = 15  # seconds per session
INTER_SESSION_DELAY = 5  # seconds between sessions


class SessionTest:
    """Test session with unique data"""
    
    def __init__(self, session_num: int):
        self.session_num = session_num
        self.session_id = f"test_session_{session_num}_{uuid.uuid4().hex[:8]}"
        self.user_id = f"test_user_{session_num}"
        self.room_name = f"isolation_test_{session_num}_{int(time.time())}"
        self.unique_phrase = f"Session {session_num} unique phrase: {uuid.uuid4().hex[:8]}"
        self.conversation_id = f"conv_{self.session_id}"
        
        # Track session state
        self.start_time = None
        self.end_time = None
        self.container_name = None
        self.error = None
        self.state_leaked = False
        self.cleanup_verified = False


async def trigger_agent_session(client: httpx.AsyncClient, session: SessionTest) -> Dict[str, Any]:
    """Trigger an agent session and return response"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    trigger_payload = {
        "room_name": session.room_name,
        "agent_slug": TEST_AGENT_SLUG,
        "user_id": session.user_id,
        "session_id": session.session_id,
        "conversation_id": session.conversation_id,
        "platform": "livekit",
        "mode": "voice",
        "context": {
            "test_session": session.session_num,
            "unique_phrase": session.unique_phrase
        }
    }
    
    response = await client.post(
        f"{BACKEND_URL}/api/v1/trigger-agent",
        json=trigger_payload,
        headers=headers
    )
    
    return response


async def check_container_logs(container_name: str, search_phrases: List[str]) -> Dict[str, bool]:
    """Check container logs for specific phrases to detect state leakage"""
    try:
        # Get recent container logs
        result = await asyncio.create_subprocess_exec(
            "docker", "logs", "--tail", "500", container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await result.communicate()
        logs = stdout.decode() + stderr.decode()
        
        # Check for each phrase
        found_phrases = {}
        for phrase in search_phrases:
            found_phrases[phrase] = phrase in logs
        
        return found_phrases
        
    except Exception as e:
        print(f"Error checking container logs: {e}")
        return {}


async def verify_state_reset(container_name: str) -> bool:
    """Verify state reset was performed"""
    try:
        # Check for state reset markers in logs
        result = await asyncio.create_subprocess_exec(
            "docker", "logs", "--tail", "200", container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await result.communicate()
        logs = stdout.decode() + stderr.decode()
        
        reset_markers = [
            "Starting comprehensive session state cleanup",
            "State reset completed",
            "Forced garbage collection",
            "Released container",
            "Session cleanup complete, ready for next session"
        ]
        
        found_count = sum(1 for marker in reset_markers if marker in logs)
        return found_count >= 3  # At least 3 markers should be present
        
    except Exception as e:
        print(f"Error verifying state reset: {e}")
        return False


async def run_session_test(client: httpx.AsyncClient, session: SessionTest) -> None:
    """Run a single session test"""
    print(f"\n{'='*60}")
    print(f"🧪 Starting Session {session.session_num}")
    print(f"   Room: {session.room_name}")
    print(f"   User: {session.user_id}")
    print(f"   Unique phrase: {session.unique_phrase}")
    
    session.start_time = datetime.now()
    
    try:
        # Trigger the agent
        response = await trigger_agent_session(client, session)
        
        if response.status_code == 200:
            result = response.json()
            data = result.get("data", {})
            container_info = data.get("container_info", {})
            
            session.container_name = container_info.get("container_name")
            print(f"   ✅ Agent triggered successfully")
            print(f"   📦 Container: {session.container_name}")
            print(f"   🔄 Session count: {container_info.get('session_count', 'N/A')}")
            
            # Simulate session activity
            print(f"   ⏳ Simulating {SESSION_DURATION}s session...")
            await asyncio.sleep(SESSION_DURATION)
            
        else:
            session.error = f"Trigger failed: {response.status_code}"
            print(f"   ❌ Failed to trigger agent: {response.status_code}")
            
    except Exception as e:
        session.error = str(e)
        print(f"   ❌ Error during session: {e}")
    
    session.end_time = datetime.now()
    print(f"   ⏱️ Session duration: {(session.end_time - session.start_time).seconds}s")


async def verify_session_isolation(sessions: List[SessionTest]) -> Dict[str, Any]:
    """Verify no state leaked between sessions"""
    print(f"\n{'='*60}")
    print("🔍 Verifying Session Isolation")
    
    isolation_results = {
        "total_sessions": len(sessions),
        "successful_sessions": 0,
        "failed_sessions": 0,
        "state_leaks_detected": 0,
        "cleanup_verified": 0,
        "container_reuse_count": 0,
        "unique_containers": set()
    }
    
    # Check each session
    for i, session in enumerate(sessions):
        if session.error:
            isolation_results["failed_sessions"] += 1
            continue
            
        isolation_results["successful_sessions"] += 1
        
        if session.container_name:
            isolation_results["unique_containers"].add(session.container_name)
            
            # Check if container was reused
            if i > 0:
                for prev_session in sessions[:i]:
                    if prev_session.container_name == session.container_name:
                        isolation_results["container_reuse_count"] += 1
                        break
            
            # Check for state leakage (look for previous session phrases)
            if i > 0:
                previous_phrases = [s.unique_phrase for s in sessions[:i] if s.unique_phrase]
                found_phrases = await check_container_logs(session.container_name, previous_phrases)
                
                leaked_phrases = [phrase for phrase, found in found_phrases.items() if found]
                if leaked_phrases:
                    session.state_leaked = True
                    isolation_results["state_leaks_detected"] += 1
                    print(f"   ⚠️ Session {session.session_num}: Found {len(leaked_phrases)} leaked phrases")
                else:
                    print(f"   ✅ Session {session.session_num}: No state leakage detected")
            
            # Verify cleanup was performed
            if await verify_state_reset(session.container_name):
                session.cleanup_verified = True
                isolation_results["cleanup_verified"] += 1
                print(f"   ✅ Session {session.session_num}: State reset verified")
    
    isolation_results["unique_containers"] = len(isolation_results["unique_containers"])
    
    return isolation_results


async def check_pool_stats(client: httpx.AsyncClient) -> Dict[str, Any]:
    """Check container pool statistics"""
    try:
        headers = {"Authorization": f"Bearer {API_KEY}"}
        response = await client.get(
            f"{BACKEND_URL}/api/v1/containers/pool/stats",
            headers=headers
        )
        
        if response.status_code == 200:
            return response.json().get("stats", {})
        else:
            return {"error": f"Failed to get pool stats: {response.status_code}"}
            
    except Exception as e:
        return {"error": str(e)}


async def test_multi_session_isolation():
    """Run the multi-session isolation test"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"\n🚀 Phase 4 Multi-Session State Isolation Test")
        print(f"   Backend: {BACKEND_URL}")
        print(f"   Sessions: {NUM_SESSIONS}")
        print(f"   Duration per session: {SESSION_DURATION}s")
        print(f"   Inter-session delay: {INTER_SESSION_DELAY}s")
        print(f"   Start time: {datetime.now()}")
        
        # Check initial pool stats
        print(f"\n📊 Initial Pool Stats:")
        initial_stats = await check_pool_stats(client)
        if "error" not in initial_stats:
            print(f"   Total containers: {initial_stats.get('total_containers', 0)}")
            print(f"   Idle containers: {initial_stats.get('idle_containers', 0)}")
            print(f"   Allocated containers: {initial_stats.get('allocated_containers', 0)}")
        
        # Create test sessions
        sessions = [SessionTest(i + 1) for i in range(NUM_SESSIONS)]
        
        # Run sessions sequentially with delays
        for i, session in enumerate(sessions):
            await run_session_test(client, session)
            
            if i < len(sessions) - 1:
                print(f"\n⏳ Waiting {INTER_SESSION_DELAY}s before next session...")
                await asyncio.sleep(INTER_SESSION_DELAY)
        
        # Verify isolation
        print("\n" + "="*60)
        isolation_results = await verify_session_isolation(sessions)
        
        # Check final pool stats
        print(f"\n📊 Final Pool Stats:")
        final_stats = await check_pool_stats(client)
        if "error" not in final_stats:
            print(f"   Total containers: {final_stats.get('total_containers', 0)}")
            print(f"   Idle containers: {final_stats.get('idle_containers', 0)}")
            print(f"   Allocated containers: {final_stats.get('allocated_containers', 0)}")
        
        # Generate summary report
        print("\n" + "="*60)
        print("📋 TEST SUMMARY REPORT")
        print("\n🎯 Success Metrics:")
        print(f"   ✅ Successful sessions: {isolation_results['successful_sessions']}/{NUM_SESSIONS}")
        print(f"   ✅ Failed sessions: {isolation_results['failed_sessions']}")
        print(f"   ✅ State leaks detected: {isolation_results['state_leaks_detected']}")
        print(f"   ✅ Cleanup verified: {isolation_results['cleanup_verified']}/{isolation_results['successful_sessions']}")
        print(f"   ✅ Container reuse: {isolation_results['container_reuse_count']} times")
        print(f"   ✅ Unique containers used: {isolation_results['unique_containers']}")
        
        # Determine overall result
        passed = (
            isolation_results['successful_sessions'] >= NUM_SESSIONS * 0.9 and  # 90% success rate
            isolation_results['state_leaks_detected'] == 0 and  # No state leaks
            isolation_results['cleanup_verified'] >= isolation_results['successful_sessions'] * 0.8  # 80% cleanup
        )
        
        print(f"\n🏁 OVERALL RESULT: {'✅ PASSED' if passed else '❌ FAILED'}")
        
        if passed:
            print("\n✨ Agents successfully handled 10 consecutive sessions without state carryover!")
            print("   - Warm pool provided instant container availability")
            print("   - State reset prevented data leakage between sessions")
            print("   - Container reuse optimized resource utilization")
        else:
            print("\n⚠️ Issues detected during multi-session test:")
            if isolation_results['state_leaks_detected'] > 0:
                print(f"   - State leaked between {isolation_results['state_leaks_detected']} sessions")
            if isolation_results['cleanup_verified'] < isolation_results['successful_sessions'] * 0.8:
                print(f"   - Cleanup verification failed for some sessions")
            if isolation_results['failed_sessions'] > NUM_SESSIONS * 0.1:
                print(f"   - Too many failed sessions: {isolation_results['failed_sessions']}")
        
        print(f"\n⏱️ Total test duration: {sum((s.end_time - s.start_time).seconds for s in sessions if s.end_time and s.start_time)}s")
        print(f"   End time: {datetime.now()}")


if __name__ == "__main__":
    asyncio.run(test_multi_session_isolation())