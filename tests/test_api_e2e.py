#!/usr/bin/env python3
"""
API-based End-to-End Test for Voice Preview
Tests the complete flow through API endpoints
"""

import asyncio
import aiohttp
import json
import time
import sys
from datetime import datetime
from pathlib import Path

# Configuration
BASE_URL = "http://localhost:8000"
API_PREFIX = "/api/v1"
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"
TEST_AGENT_SLUG = "clarence-coherence"

# Color codes
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

test_results = []

def log_result(test_name: str, passed: bool, details: str = "", evidence: dict = None):
    """Log test result with evidence"""
    status = f"{GREEN}✅ PASSED{RESET}" if passed else f"{RED}❌ FAILED{RESET}"
    print(f"\n{status} {test_name}")
    if details:
        print(f"   {details}")
    if evidence:
        print(f"   Evidence: {json.dumps(evidence, indent=2)}")
    
    test_results.append({
        "test": test_name,
        "passed": passed,
        "details": details,
        "evidence": evidence or {},
        "timestamp": datetime.now().isoformat()
    })

async def test_health_check(session):
    """Test if API is accessible"""
    try:
        async with session.get(f"{BASE_URL}/health") as resp:
            if resp.status == 200:
                data = await resp.json()
                log_result("API Health Check", True, f"Service: {data.get('service')}")
                return True
            else:
                log_result("API Health Check", False, f"Status: {resp.status}")
                return False
    except Exception as e:
        log_result("API Health Check", False, str(e))
        return False

async def test_trigger_agent(session):
    """Test the complete agent trigger flow"""
    try:
        # Prepare trigger payload
        room_name = f"e2e_test_{int(time.time())}"
        payload = {
            "agent_slug": TEST_AGENT_SLUG,
            "mode": "voice",
            "room_name": room_name,
            "user_id": "e2e-test-user",
            "client_id": TEST_CLIENT_ID
        }
        
        # Trigger agent
        async with session.post(
            f"{BASE_URL}{API_PREFIX}/trigger-agent",
            json=payload
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                
                evidence = {
                    "room_name": room_name,
                    "success": data.get("success"),
                    "has_token": bool(data.get("data", {}).get("livekit_config", {}).get("user_token")),
                    "room_created": data.get("data", {}).get("room_info", {}).get("status") == "created",
                    "container_status": data.get("data", {}).get("container_info", {}).get("status")
                }
                
                if evidence["success"] and evidence["has_token"] and evidence["room_created"]:
                    log_result("Trigger Agent API", True, 
                              f"Room: {room_name}, Container: {evidence['container_status']}", 
                              evidence)
                    return True, room_name, data
                else:
                    log_result("Trigger Agent API", False, "Missing required fields", evidence)
                    return False, None, None
            else:
                log_result("Trigger Agent API", False, f"Status: {resp.status}")
                return False, None, None
                
    except Exception as e:
        log_result("Trigger Agent API", False, str(e))
        return False, None, None

async def test_container_status(session, room_name):
    """Check if container is processing the room"""
    try:
        # Get container status
        async with session.get(f"{BASE_URL}{API_PREFIX}/containers") as resp:
            if resp.status == 200:
                data = await resp.json()
                
                # Look for our room in container logs
                containers = data.get("containers", [])
                room_found = False
                
                for container in containers:
                    if container.get("name", "").startswith("agent_"):
                        # Check container logs endpoint
                        container_id = container.get("id")
                        if container_id:
                            async with session.get(
                                f"{BASE_URL}{API_PREFIX}/containers/{container_id}/logs?tail=100"
                            ) as log_resp:
                                if log_resp.status == 200:
                                    logs = await log_resp.text()
                                    if room_name in logs:
                                        room_found = True
                                        
                                        # Check for key events
                                        evidence = {
                                            "container_id": container_id[:12],
                                            "room_found": True,
                                            "job_accepted": f"Job accepted for room '{room_name}'" in logs,
                                            "connected": f"Connected to room: {room_name}" in logs,
                                            "greeting_sent": "Greeting sent successfully" in logs
                                        }
                                        
                                        log_result("Container Processing", True, 
                                                  f"Room {room_name} being processed", 
                                                  evidence)
                                        return True
                
                if not room_found:
                    log_result("Container Processing", False, 
                              f"Room {room_name} not found in any container")
                    return False
                    
            else:
                log_result("Container Processing", False, f"Status: {resp.status}")
                return False
                
    except Exception as e:
        log_result("Container Processing", False, str(e))
        return False

async def test_greeting_sent(session, room_name):
    """Verify agent sent greeting"""
    try:
        # Wait a bit for agent to process
        await asyncio.sleep(3)
        
        # Check containers for greeting evidence
        async with session.get(f"{BASE_URL}{API_PREFIX}/containers") as resp:
            if resp.status == 200:
                data = await resp.json()
                
                for container in data.get("containers", []):
                    container_id = container.get("id")
                    if container_id:
                        async with session.get(
                            f"{BASE_URL}{API_PREFIX}/containers/{container_id}/logs?tail=200"
                        ) as log_resp:
                            if log_resp.status == 200:
                                logs = await log_resp.text()
                                
                                # Look for greeting in this specific room context
                                if room_name in logs:
                                    lines = logs.split('\n')
                                    room_index = -1
                                    
                                    # Find where our room starts in logs
                                    for i, line in enumerate(lines):
                                        if room_name in line and "Job request" in line:
                                            room_index = i
                                            break
                                    
                                    if room_index >= 0:
                                        # Check subsequent lines for greeting
                                        greeting_found = False
                                        greeting_message = None
                                        
                                        for line in lines[room_index:room_index+20]:
                                            if "Attempting to send greeting:" in line:
                                                greeting_message = line.split("Attempting to send greeting:")[-1].strip()
                                                greeting_found = True
                                            elif "Greeting sent successfully" in line:
                                                log_result("Greeting Verification", True,
                                                          f"Greeting sent: {greeting_message or 'Success'}",
                                                          {"room": room_name, "greeting": greeting_message})
                                                return True
                                        
                                        if greeting_found:
                                            log_result("Greeting Verification", False,
                                                      "Greeting attempted but not confirmed sent",
                                                      {"room": room_name, "attempted": True})
                                            return False
                
                log_result("Greeting Verification", False, 
                          f"No greeting found for room {room_name}")
                return False
                
            else:
                log_result("Greeting Verification", False, f"Status: {resp.status}")
                return False
                
    except Exception as e:
        log_result("Greeting Verification", False, str(e))
        return False

async def run_api_e2e_tests():
    """Run API-based E2E tests"""
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}API END-TO-END TEST{RESET}")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target: {BASE_URL}")
    print(f"{BLUE}{'='*60}{RESET}")
    
    async with aiohttp.ClientSession() as session:
        tests_passed = 0
        total_tests = 4
        
        # Test 1: Health check
        if await test_health_check(session):
            tests_passed += 1
        else:
            print(f"\n{RED}API not accessible - stopping tests{RESET}")
            return False
        
        # Test 2: Trigger agent
        success, room_name, trigger_data = await test_trigger_agent(session)
        if success:
            tests_passed += 1
            
            # Test 3: Container processing
            if await test_container_status(session, room_name):
                tests_passed += 1
                
            # Test 4: Greeting verification
            if await test_greeting_sent(session, room_name):
                tests_passed += 1
        
    # Print summary
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}TEST SUMMARY{RESET}")
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"Total Tests: {total_tests}")
    print(f"{GREEN}Passed: {tests_passed}{RESET}")
    print(f"{RED}Failed: {total_tests - tests_passed}{RESET}")
    
    # Save report
    report_path = f"/tmp/api_e2e_report_{int(time.time())}.json"
    with open(report_path, 'w') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "base_url": BASE_URL,
            "results": test_results,
            "summary": {
                "total": total_tests,
                "passed": tests_passed,
                "failed": total_tests - tests_passed
            }
        }, f, indent=2)
    
    print(f"\nReport saved to: {report_path}")
    
    if tests_passed == total_tests:
        print(f"\n{GREEN}✅ ALL TESTS PASSED - E2E FLOW VERIFIED{RESET}")
        return True
    else:
        print(f"\n{RED}❌ TESTS FAILED - E2E FLOW HAS ISSUES{RESET}")
        return False

if __name__ == "__main__":
    success = asyncio.run(run_api_e2e_tests())
    sys.exit(0 if success else 1)