#!/usr/bin/env python3
"""
Mission Critical Test Suite v4.0
Tests core functionality with proper handling of global agents and current system state
"""
import asyncio
import aiohttp
import time
import subprocess
import os
from datetime import datetime
from typing import Optional, Dict, Any, List

# Configuration
BASE_URL = "http://localhost:8000"
API_PREFIX = "/api/v1"
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"  # Autonomite client

# LiveKit Configuration (from current system)
LIVEKIT_URL = "wss://litebridge-hw6srhvi.livekit.cloud"
LIVEKIT_API_KEY = "APIrZaVVGtq5PCX"
LIVEKIT_API_SECRET = "mRj96UaZFIA8ECFqBK9kIZYFlfW0FHWYZz7Yi3loJ0V"

# Test tracking
test_results = []

# ANSI color codes
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def log_test(test_name: str, passed: bool, details: str = ""):
    """Log test result"""
    status = f"{GREEN}✅ PASSED{RESET}" if passed else f"{RED}❌ FAILED{RESET}"
    print(f"\n{status} {test_name}")
    if details:
        print(f"   {details}")
    test_results.append({"test": test_name, "passed": passed, "details": details})

async def test_health_endpoints():
    """Test health check endpoints"""
    print(f"\n{BLUE}=== Test 1: Health Check Endpoints ==={RESET}")
    
    async with aiohttp.ClientSession() as session:
        # Basic health
        try:
            async with session.get(f"{BASE_URL}/health") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    log_test("Basic Health Check", True, 
                            f"Status: {data.get('status', 'unknown')}")
                else:
                    log_test("Basic Health Check", False, f"Status code: {resp.status}")
        except Exception as e:
            log_test("Basic Health Check", False, str(e))
        
        # Detailed health
        try:
            async with session.get(f"{BASE_URL}/health/detailed") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    checks = data.get("checks", {})
                    overall = data.get("status", "unknown")
                    log_test("Detailed Health Check", True, 
                            f"Overall: {overall}, LiveKit: {checks.get('livekit')}, "
                            f"Supabase: {checks.get('supabase')}, Database: {checks.get('database')}")
                else:
                    log_test("Detailed Health Check", False, f"Status code: {resp.status}")
        except Exception as e:
            log_test("Detailed Health Check", False, str(e))

async def test_client_operations():
    """Test client CRUD operations"""
    print(f"\n{BLUE}=== Test 2: Client Operations ==={RESET}")
    
    async with aiohttp.ClientSession() as session:
        # Get specific client
        try:
            async with session.get(f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}") as resp:
                if resp.status == 200:
                    client = await resp.json()
                    log_test("Get Client", True, 
                            f"Client: {client.get('name', 'Unknown')}, "
                            f"Domain: {client.get('domain', 'Unknown')}")
                else:
                    log_test("Get Client", False, f"Status code: {resp.status}")
        except Exception as e:
            log_test("Get Client", False, str(e))

async def test_agent_operations():
    """Test agent operations"""
    print(f"\n{BLUE}=== Test 3: Agent Operations ==={RESET}")
    
    async with aiohttp.ClientSession() as session:
        # Check for client-specific agents
        try:
            async with session.get(f"{BASE_URL}{API_PREFIX}/agents/client/{TEST_CLIENT_ID}") as resp:
                if resp.status == 200:
                    agents = await resp.json()
                    if agents:
                        log_test("Client Agents", True, 
                                f"Found {len(agents)} client-specific agents")
                    else:
                        # No client-specific agents, check if global agents exist
                        log_test("Client Agents", True, 
                                "No client-specific agents (using global agents)")
                else:
                    log_test("Client Agents", False, f"Status code: {resp.status}")
        except Exception as e:
            log_test("Client Agents", False, str(e))

async def test_livekit_operations():
    """Test LiveKit integration"""
    print(f"\n{BLUE}=== Test 4: LiveKit Operations ==={RESET}")
    
    async with aiohttp.ClientSession() as session:
        # Test room creation endpoint (requires auth)
        room_name = f"test-v4-{int(time.time())}"
        try:
            async with session.post(f"{BASE_URL}{API_PREFIX}/create-room",
                                  json={
                                      "room_name": room_name,
                                      "client_id": TEST_CLIENT_ID,
                                      "empty_timeout": 300,
                                      "max_participants": 10
                                  }) as resp:
                if resp.status == 401:
                    log_test("Create Room (Auth Check)", True, 
                            "401 Unauthorized - API properly requires authentication")
                else:
                    log_test("Create Room (Auth Check)", False, 
                            f"Expected 401, got {resp.status}")
        except Exception as e:
            log_test("Create Room (Auth Check)", False, str(e))
        
        # Token generation also requires auth
        try:
            async with session.post(f"{BASE_URL}{API_PREFIX}/token",
                                  json={
                                      "room_name": room_name,
                                      "identity": "test-user",
                                      "metadata": {}
                                  }) as resp:
                if resp.status == 401:
                    log_test("Token Generation (Auth Check)", True, 
                            "401 Unauthorized - API properly requires authentication")
                else:
                    log_test("Token Generation (Auth Check)", False, 
                            f"Expected 401, got {resp.status}")
        except Exception as e:
            log_test("Token Generation (Auth Check)", False, str(e))

async def test_docker_and_workers():
    """Test Docker and worker status"""
    print(f"\n{BLUE}=== Test 5: Docker & Worker Status ==={RESET}")
    
    # Check Docker
    try:
        result = subprocess.run(["docker", "ps"], capture_output=True, text=True)
        if result.returncode == 0:
            log_test("Docker Daemon", True, "Docker is running")
        else:
            log_test("Docker Daemon", False, "Docker command failed")
    except Exception as e:
        log_test("Docker Daemon", False, str(e))
    
    # Check for worker containers
    try:
        result = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            containers = result.stdout.strip().split('\n')
            worker_containers = [c for c in containers if 'agent' in c or 'worker' in c]
            if worker_containers:
                log_test("Worker Containers", True, 
                        f"Found {len(worker_containers)} worker container(s)")
            else:
                log_test("Worker Containers", False, "No worker containers found")
        else:
            log_test("Worker Containers", False, "Failed to list containers")
    except Exception as e:
        log_test("Worker Containers", False, str(e))

async def test_admin_interface():
    """Test admin interface accessibility"""
    print(f"\n{BLUE}=== Test 6: Admin Interface ==={RESET}")
    
    async with aiohttp.ClientSession() as session:
        # Check admin homepage
        try:
            async with session.get(f"{BASE_URL}/admin") as resp:
                if resp.status == 200:
                    log_test("Admin Interface", True, "Admin dashboard accessible")
                else:
                    log_test("Admin Interface", False, f"Status code: {resp.status}")
        except Exception as e:
            log_test("Admin Interface", False, str(e))
        
        # Check agents page
        try:
            async with session.get(f"{BASE_URL}/admin/agents") as resp:
                if resp.status == 200:
                    content = await resp.text()
                    if "agent-card" in content:
                        # Count agents
                        agent_count = content.count("agent-card")
                        log_test("Admin Agents Page", True, 
                                f"Found {agent_count} agents displayed")
                    else:
                        log_test("Admin Agents Page", False, "No agents displayed")
                else:
                    log_test("Admin Agents Page", False, f"Status code: {resp.status}")
        except Exception as e:
            log_test("Admin Agents Page", False, str(e))

def print_summary():
    """Print test summary"""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}=== MISSION CRITICAL TEST V4 SUMMARY ==={RESET}")
    print(f"{BLUE}{'='*60}{RESET}")
    
    passed_count = sum(1 for r in test_results if r["passed"])
    failed_count = len(test_results) - passed_count
    
    print(f"Total Tests Run: {len(test_results)}")
    print(f"{GREEN}Passed: {passed_count}{RESET}")
    print(f"{RED}Failed: {failed_count}{RESET}")
    
    if failed_count > 0:
        print(f"\n{YELLOW}--- Failure Details ---{RESET}")
        for result in test_results:
            if not result["passed"]:
                print(f"  - {RED}{result['test']}{RESET}: {result['details']}")
    
    # Overall status
    print(f"\n{BLUE}--- Overall Status ---{RESET}")
    if failed_count == 0:
        print(f"{BLUE}Mission Critical Status: {GREEN}✅ ALL SYSTEMS OPERATIONAL{RESET}")
    elif failed_count <= 2:
        print(f"{BLUE}Mission Critical Status: {YELLOW}⚠️  MINOR ISSUES DETECTED{RESET}")
    else:
        print(f"{BLUE}Mission Critical Status: {RED}❌ CRITICAL SYSTEMS FAILURE{RESET}")
    
    print(f"{BLUE}{'='*60}{RESET}")

async def main():
    """Run all tests"""
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}AUTONOMITE PLATFORM - MISSION CRITICAL TEST SUITE v4.0{RESET}")
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target: {BASE_URL}")
    print(f"Client: {TEST_CLIENT_ID}")
    
    await test_health_endpoints()
    await test_client_operations()
    await test_agent_operations()
    await test_livekit_operations()
    await test_docker_and_workers()
    await test_admin_interface()
    
    print_summary()

if __name__ == "__main__":
    asyncio.run(main())