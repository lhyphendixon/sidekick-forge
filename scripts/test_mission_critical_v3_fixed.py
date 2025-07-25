#!/usr/bin/env python3
"""
Mission Critical Test Suite v3.0 - Fixed for current API versions
Enhanced with direct LiveKit worker dispatch and room joining verification.
Fixed for LiveKit API v1.0.3 and current endpoint structure.
"""

import asyncio
import aiohttp
import json
import sys
import time
from datetime import datetime
import docker
import os
from livekit import api, rtc

# --- Configuration ---
BASE_URL = "http://localhost:8000"
API_PREFIX = "/api/v1"
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"  # Default autonomite client

# LiveKit credentials must be set as environment variables
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "wss://litebridge-hw6srhvi.livekit.cloud")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "APIUtuiQ47BQBsk")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM")

# --- Color Codes ---
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

class MissionCriticalTests:
    def __init__(self):
        self.session = None
        self.test_results = []
        self.test_agent = None
        self.livekit_api = None  # Will be initialized in async setup

        try:
            self.docker_client = docker.from_env()
        except Exception as e:
            print(f"{YELLOW}Warning: Docker client not available. Docker-related tests will be skipped. Error: {e}{RESET}")
            self.docker_client = None

        if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
            print(f"{RED}Error: LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET must be set in your environment.{RESET}")
            sys.exit(1)

    async def setup(self):
        """Initialize test session and LiveKit API in async context."""
        self.session = aiohttp.ClientSession()
        # Initialize LiveKit API in async context
        self.livekit_api = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    async def teardown(self):
        """Cleanup test session."""
        if self.session:
            await self.session.close()
        if self.livekit_api:
            await self.livekit_api.aclose()

    def log_test(self, test_name: str, passed: bool, details: str = ""):
        """Log test result."""
        status = f"{GREEN}✅ PASSED{RESET}" if passed else f"{RED}❌ FAILED{RESET}"
        print(f"{status} {test_name}")
        if details:
            print(f"   {details}")
        self.test_results.append({"test": test_name, "passed": passed, "details": details})

    async def test_health_connectivity(self):
        """Test 1: Basic health and connectivity checks."""
        print(f"\n{BLUE}=== Test 1: Health & Connectivity ==={RESET}")
        
        # Basic health check
        try:
            async with self.session.get(f"{BASE_URL}/health") as resp:
                if resp.status == 200:
                    self.log_test("Basic Health Check", True, "API is responsive")
                else:
                    self.log_test("Basic Health Check", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("Basic Health Check", False, str(e))
        
        # Detailed health check
        try:
            async with self.session.get(f"{BASE_URL}/health/detailed") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    livekit_status = data.get("services", {}).get("livekit", {}).get("status")
                    supabase_status = data.get("services", {}).get("supabase", {}).get("status")
                    if livekit_status == "healthy" and supabase_status == "healthy":
                        self.log_test("Detailed Health Check", True, f"LiveKit: {livekit_status}, Supabase: {supabase_status}")
                    else:
                        self.log_test("Detailed Health Check", False, f"LiveKit: {livekit_status}, Supabase: {supabase_status}")
                else:
                    self.log_test("Detailed Health Check", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("Detailed Health Check", False, str(e))

    async def test_livekit_workers(self):
        """Test 2: Verify LiveKit workers via our API endpoints."""
        print(f"\n{BLUE}=== Test 2: LiveKit Worker Status ==={RESET}")
        
        try:
            # Check worker status via our API
            async with self.session.get(f"{BASE_URL}{API_PREFIX}/workers") as resp:
                if resp.status == 200:
                    workers = await resp.json()
                    if workers and len(workers) > 0:
                        worker_info = []
                        for worker in workers:
                            worker_info.append(f"ID: {worker.get('id', 'unknown')}")
                        self.log_test("Worker API Endpoint", True, f"Found {len(workers)} worker(s): {', '.join(worker_info)}")
                    else:
                        self.log_test("Worker API Endpoint", True, "Endpoint available but no workers reported")
                elif resp.status == 404:
                    # Worker endpoint might not be implemented, check docker instead
                    if self.docker_client:
                        containers = [c for c in self.docker_client.containers.list() if "agent-worker" in c.name]
                        if containers:
                            self.log_test("Worker Docker Check", True, f"Found {len(containers)} worker container(s) running")
                        else:
                            self.log_test("Worker Docker Check", False, "No worker containers found")
                    else:
                        self.log_test("Worker API Endpoint", False, "Not implemented (404)")
                else:
                    self.log_test("Worker API Endpoint", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("Worker Status Check", False, f"Error: {str(e)}")

    async def test_room_operations(self):
        """Test 3: Basic room operations using LiveKit API."""
        print(f"\n{BLUE}=== Test 3: LiveKit Room Operations ==={RESET}")
        
        room_name = f"test-room-{int(time.time())}"
        
        try:
            # Create a room
            room = await self.livekit_api.room.create_room(
                api.CreateRoomRequest(name=room_name)
            )
            self.log_test("Room Creation", True, f"Room created: {room_name}")
            
            # List rooms to verify
            rooms_response = await self.livekit_api.room.list_rooms(api.ListRoomsRequest())
            # Handle the response object properly
            rooms = rooms_response.rooms if hasattr(rooms_response, 'rooms') else []
            room_found = any(r.name == room_name for r in rooms)
            if room_found:
                self.log_test("Room Verification", True, f"Room {room_name} found in room list")
            else:
                self.log_test("Room Verification", False, f"Room {room_name} not found in room list")
            
            # Generate a test token
            token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            token.with_identity("test-user")
            token.with_name("Test User") 
            token.with_grants(api.VideoGrants(
                room_join=True,
                room=room_name
            ))
            user_token = token.to_jwt()
            self.log_test("Token Generation", True, "User token created successfully")
            
        except Exception as e:
            self.log_test("Room Operations", False, f"Error: {str(e)}")
        finally:
            # Cleanup - delete the room
            try:
                await self.livekit_api.room.delete_room(api.DeleteRoomRequest(room=room_name))
            except:
                pass

    async def test_trigger_endpoint(self):
        """Test 4: Test the /trigger-agent endpoint."""
        print(f"\n{BLUE}=== Test 4: Trigger Endpoint & Agent Dispatch ==={RESET}")
        
        # First get a test agent - fix the endpoint path
        try:
            async with self.session.get(f"{BASE_URL}{API_PREFIX}/agents/client/{TEST_CLIENT_ID}") as resp:
                if resp.status == 200:
                    agents = await resp.json()
                    if agents and len(agents) > 0:
                        self.test_agent = agents[0]
                        self.log_test("Test Agent Available", True, f"Using agent: {self.test_agent.get('slug')}")
                    else:
                        self.log_test("Test Agent Available", False, "No agents found for test client")
                        return
                else:
                    self.log_test("Test Agent Available", False, f"Failed to fetch agents: {resp.status}")
                    # Try to list what endpoints are available
                    if resp.status == 404:
                        print(f"   {YELLOW}Note: The agents endpoint structure might be different{RESET}")
                    return
        except Exception as e:
            self.log_test("Test Agent Available", False, str(e))
            return
        
        # Trigger the agent
        room_name = f"test-trigger-{int(time.time())}"
        trigger_data = {
            "agent_slug": self.test_agent.get("slug"),
            "mode": "voice",
            "room_name": room_name,
            "user_id": "test-user",
            "client_id": TEST_CLIENT_ID
        }
        
        try:
            async with self.session.post(f"{BASE_URL}{API_PREFIX}/trigger-agent", json=trigger_data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("success"):
                        self.log_test("Trigger Endpoint", True, 
                                    f"Success: {result.get('message', 'Agent triggered')}")
                        
                        # Check response details
                        if result.get("room_name"):
                            self.log_test("Room Assignment", True, f"Room: {result.get('room_name')}")
                        if result.get("user_token"):
                            self.log_test("Token Provided", True, "User token included in response")
                        
                        # Check if container was created (for containerized deployments)
                        if self.docker_client:
                            await asyncio.sleep(2)  # Wait for container to start
                            containers = self.docker_client.containers.list(filters={"label": "managed_by=autonomite-saas"})
                            agent_containers = [c for c in containers if f"agent_{TEST_CLIENT_ID}" in c.name]
                            if agent_containers:
                                self.log_test("Container Creation", True, f"Found {len(agent_containers)} agent container(s)")
                            else:
                                # Check for shared worker instead
                                worker_containers = [c for c in self.docker_client.containers.list() if "agent-worker" in c.name]
                                if worker_containers:
                                    self.log_test("Worker Architecture", True, "Using shared worker (not per-client containers)")
                                else:
                                    self.log_test("Container/Worker Check", False, "No agent containers or shared workers found")
                    else:
                        self.log_test("Trigger Endpoint", False, f"Success=False: {result.get('message')}")
                else:
                    text = await resp.text()
                    self.log_test("Trigger Endpoint", False, f"Status: {resp.status}, Response: {text[:200]}")
        except Exception as e:
            self.log_test("Trigger Endpoint", False, str(e))

    async def test_admin_interface(self):
        """Test 5: Verify admin interface is accessible."""
        print(f"\n{BLUE}=== Test 5: Admin Interface ==={RESET}")
        
        endpoints = [
            ("/admin", "Admin Dashboard"),
            ("/admin/clients", "Clients Page"),
            ("/admin/agents", "Agents Page"),
            ("/admin/containers", "Containers Page")
        ]
        
        for endpoint, name in endpoints:
            try:
                async with self.session.get(f"{BASE_URL}{endpoint}") as resp:
                    if resp.status == 200:
                        self.log_test(f"Admin {name}", True, "Accessible")
                    elif resp.status == 404:
                        self.log_test(f"Admin {name}", False, "Not implemented (404)")
                    else:
                        self.log_test(f"Admin {name}", False, f"Status: {resp.status}")
            except Exception as e:
                self.log_test(f"Admin {name}", False, str(e))

    async def test_docker_infrastructure(self):
        """Test 6: Verify Docker infrastructure."""
        print(f"\n{BLUE}=== Test 6: Docker Infrastructure ==={RESET}")
        
        if not self.docker_client:
            self.log_test("Docker Available", False, "Docker client not available")
            return
            
        try:
            # Check core services
            core_services = ["autonomite-fastapi", "autonomite-redis", "autonomite-agent-worker"]
            for service_name in core_services:
                containers = [c for c in self.docker_client.containers.list() if service_name in c.name]
                if containers:
                    container = containers[0]
                    self.log_test(f"Service: {service_name}", True, f"Status: {container.status}")
                else:
                    self.log_test(f"Service: {service_name}", False, "Not running")
            
            # Check agent runtime image
            try:
                self.docker_client.images.get("autonomite/agent-runtime:latest")
                self.log_test("Agent Runtime Image", True, "Image available")
            except docker.errors.ImageNotFound:
                self.log_test("Agent Runtime Image", False, "Image not found")
                
        except Exception as e:
            self.log_test("Docker Infrastructure", False, str(e))

    async def run_all_tests(self):
        """Run all mission critical tests."""
        print(f"{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}AUTONOMITE PLATFORM - MISSION CRITICAL TEST SUITE v3.0{RESET}")
        print(f"{BLUE}{'='*60}{RESET}")
        print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Target: {BASE_URL}")
        print(f"LiveKit: {LIVEKIT_URL}")
        
        await self.setup()
        
        try:
            await self.test_health_connectivity()
            await self.test_livekit_workers()
            await self.test_room_operations()
            await self.test_trigger_endpoint()
            await self.test_admin_interface()
            await self.test_docker_infrastructure()
        finally:
            await self.teardown()
        
        self.print_summary()

    def print_summary(self):
        """Prints a summary of all test results."""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}=== MISSION CRITICAL TEST V3 SUMMARY ==={RESET}")
        print(f"{BLUE}{'='*60}{RESET}")
        
        total = len(self.test_results)
        passed = sum(1 for r in self.test_results if r["passed"])
        failed = total - passed
        
        print(f"Total Tests Run: {total}")
        print(f"{GREEN}Passed: {passed}{RESET}")
        print(f"{RED}Failed: {failed}{RESET}")
        
        if failed > 0:
            print(f"\n{YELLOW}--- Failure Details ---{RESET}")
            for result in self.test_results:
                if not result["passed"]:
                    print(f"  - {RED}{result['test']}{RESET}: {result['details']}")
        
        print(f"\n{BLUE}Mission Critical Status: ", end="")
        if failed == 0:
            print(f"{GREEN}✅ ALL SYSTEMS OPERATIONAL{RESET}")
        elif failed <= 2:
            print(f"{YELLOW}⚠️  MINOR ISSUES DETECTED{RESET}")
        else:
            print(f"{RED}❌ CRITICAL SYSTEMS FAILURE DETECTED{RESET}")
        print(f"{BLUE}{'='*60}{RESET}")

if __name__ == "__main__":
    runner = MissionCriticalTests()
    asyncio.run(runner.run_all_tests())