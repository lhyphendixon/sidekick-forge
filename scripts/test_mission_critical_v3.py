#!/usr/bin/env python3
"""
Mission Critical Test Suite v3.0
Enhanced with direct LiveKit worker dispatch and room joining verification.
This script replaces log-scraping with direct API calls for more reliable diagnostics.
"""

import asyncio
import aiohttp
import json
import sys
import time
from datetime import datetime
import docker
import os
from livekit import api

# --- Configuration ---
BASE_URL = "http://localhost:8000"
API_PREFIX = "/api/v1"
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"  # Default autonomite client

# LiveKit credentials must be set as environment variables
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

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

        try:
            self.docker_client = docker.from_env()
        except Exception as e:
            print(f"{YELLOW}Warning: Docker client not available. Docker-related tests will be skipped. Error: {e}{RESET}")
            self.docker_client = None

        if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
            print(f"{RED}Error: LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET must be set in your environment.{RESET}")
            sys.exit(1)
            
        self.livekit_api = None  # Will be initialized in async setup

    async def setup(self):
        """Initialize test session."""
        self.session = aiohttp.ClientSession()
        self.livekit_api = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    async def teardown(self):
        """Cleanup test session."""
        if self.session:
            await self.session.close()
        if self.livekit_api:
            await self.livekit_api.aclose()

    def log_test(self, test_name: str, passed: bool, details: str = ""):
        """Log the result of a test."""
        status = f"{GREEN}✅ PASSED{RESET}" if passed else f"{RED}❌ FAILED{RESET}"
        print(f"\n{status} {test_name}")
        if details:
            print(f"   {details}")
        self.test_results.append({"test": test_name, "passed": passed, "details": details})
        if not passed:
            # Optionally, exit on first failure for quick debugging
            # sys.exit(1)
            pass

    async def run_all_tests(self):
        """Execute all test steps in sequence."""
        await self.setup()
        
        await self.test_step_1_api_health()
        await self.test_step_2_get_test_agent()
        await self.test_step_3_docker_and_worker_health()
        await self.test_step_4_agent_dispatch_and_join()
        
        await self.teardown()
        self.print_summary()

    async def test_step_1_api_health(self):
        """Test 1: Check basic API health endpoints."""
        print(f"\n{BLUE}=== Test 1: API Health & Connectivity ==={RESET}")
        try:
            async with self.session.get(f"{BASE_URL}/health") as resp:
                if resp.status == 200:
                    self.log_test("FastAPI Health Check", True, "API is responsive.")
                else:
                    self.log_test("FastAPI Health Check", False, f"API returned status {resp.status}.")
        except aiohttp.ClientConnectorError as e:
            self.log_test("FastAPI Health Check", False, f"Connection failed. Is the FastAPI container running? Error: {e}")

    async def test_step_2_get_test_agent(self):
        """Test 2: Fetch an agent to use for dispatch tests."""
        print(f"\n{BLUE}=== Test 2: Fetching Agent Configuration ==={RESET}")
        try:
            async with self.session.get(f"{BASE_URL}{API_PREFIX}/agents/client/{TEST_CLIENT_ID}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    agents = data if isinstance(data, list) else data.get("data", [])
                    if agents:
                        self.test_agent = agents[0]  # Use the first available agent
                        self.log_test("Fetch Agent", True, f"Successfully fetched agent: {self.test_agent['slug']}")
                    else:
                        self.log_test("Fetch Agent", False, f"No agents found for client ID {TEST_CLIENT_ID}.")
                else:
                    self.log_test("Fetch Agent", False, f"Failed to get agents. Status: {resp.status}")
        except Exception as e:
            self.log_test("Fetch Agent", False, f"An error occurred: {e}")

    async def test_step_3_docker_and_worker_health(self):
        """Test 3: Verify Docker state and LiveKit worker registration."""
        print(f"\n{BLUE}=== Test 3: Docker & LiveKit Worker Verification ==={RESET}")
        
        # Docker Daemon Check
        if not self.docker_client:
            self.log_test("Docker Daemon", False, "Skipping due to Docker client init failure.")
            return

        try:
            self.docker_client.ping()
            self.log_test("Docker Daemon", True, "Docker is running.")
        except Exception as e:
            self.log_test("Docker Daemon", False, f"Docker daemon not responding: {e}")
            return # Stop if docker isn't running

        # Agent Worker Container Check
        worker_found = False
        try:
            # Check for various possible worker container names
            containers = self.docker_client.containers.list()
            for container in containers:
                if "agent-worker" in container.name or "agent_worker" in container.name:
                    self.log_test("Agent Worker Container", True, f"Worker container '{container.name}' is running.")
                    worker_found = True
                    break
            if not worker_found:
                self.log_test("Agent Worker Container", False, "No agent worker container found. Check deployment.")
        except Exception as e:
            self.log_test("Agent Worker Container", False, f"Error checking containers: {e}")

        # LiveKit Worker Registration Check
        try:
            workers_resp = await self.livekit_api.agent.list_workers(api.ListWorkersRequest())
            active_workers = workers_resp.workers
            if active_workers:
                details = f"Found {len(active_workers)} registered worker(s)."
                for worker in active_workers:
                    details += f"\n   - ID: {worker.id}, Name: '{worker.agent_name}', State: {worker.state}"
                    if not worker.agent_name:
                        details += f"\n   {YELLOW}Warning: Worker {worker.id} has no agent_name. Explicit dispatch will fail.{RESET}"
                self.log_test("LiveKit Worker Registration", True, details)
            else:
                self.log_test("LiveKit Worker Registration", False, "No workers are registered with LiveKit. Check agent_worker logs.")
        except Exception as e:
            self.log_test("LiveKit Worker Registration", False, f"API error when listing workers: {e}")

    async def test_step_4_agent_dispatch_and_join(self):
        """Test 4: Full-flow test of dispatching an agent and verifying it joins the room."""
        print(f"\n{BLUE}=== Test 4: Agent Dispatch and Room Join Verification ==={RESET}")

        if not self.test_agent:
            self.log_test("Agent Dispatch & Join", False, "Skipping test because no agent was fetched in Step 2.")
            return

        test_room_name = f"mctest-v3-{int(time.time())}"
        
        # Pre-flight check: Ensure there's a worker that can handle this agent
        agent_name_to_dispatch = "session-agent-rag" # This MUST match the name your worker registers.
        
        try:
            # Create a room for the test
            await self.livekit_api.room.create_room(api.CreateRoomRequest(name=test_room_name, empty_timeout=60))
            self.log_test("Room Creation", True, f"LiveKit room '{test_room_name}' created for test.")

            # New: Verify room exists with polling to fix the "delay" issue
            await self.verify_room_exists(test_room_name)

            # Dispatch the agent
            dispatch_req = api.CreateAgentDispatchRequest(
                room=api.RoomDefinition(name=test_room_name),
                agent=api.AgentDefinition(name=agent_name_to_dispatch)
            )
            dispatch = await self.livekit_api.agent.create_dispatch(dispatch_req)
            self.log_test("Agent Dispatch", True, f"Successfully dispatched agent '{agent_name_to_dispatch}' to room. Dispatch ID: {dispatch.dispatch_id}")

            # Verify the agent joins the room
            await self.verify_participant_join(test_room_name, "agent_")
            
        except Exception as e:
            self.log_test("Agent Dispatch & Join", False, f"An unexpected error occurred: {e}")
        finally:
            # Cleanup
            try:
                await self.livekit_api.room.delete_room(api.DeleteRoomRequest(room=test_room_name))
                print(f"\n   Cleanup: Room '{test_room_name}' deleted.")
            except Exception:
                pass # Room might not exist if creation failed

    async def verify_room_exists(self, room_name: str):
        """Polls LiveKit to see if a room is available in the list."""
        timeout = 10 # seconds
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                room_list = await self.livekit_api.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
                if hasattr(room_list, 'rooms') and room_list.rooms:
                    self.log_test("Room Verification", True, "Room found in list successfully.")
                    return
            except Exception as e:
                self.log_test("Room Verification", False, f"API error while verifying room: {e}")
                return
            await asyncio.sleep(1)
        self.log_test("Room Verification", False, f"Timeout after {timeout}s. Room was created but not found in list.")

    async def verify_participant_join(self, room_name: str, identity_prefix: str):
        """Polls LiveKit to see if a participant with a given prefix joins."""
        timeout = 30  # seconds
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                participants_resp = await self.livekit_api.room.list_participants(api.ListParticipantsRequest(room=room_name))
                joined_identities = [p.identity for p in participants_resp.participants if p.identity.startswith(identity_prefix)]

                if joined_identities:
                    self.log_test("Agent Room Join", True, f"Agent joined successfully with identity '{joined_identities[0]}'.")
                    return
            except Exception as e:
                self.log_test("Agent Room Join", False, f"API error while verifying join: {e}")
                return

            await asyncio.sleep(2)

        self.log_test("Agent Room Join", False, f"Timeout after {timeout}s. Agent with prefix '{identity_prefix}' never joined. Check 'agent_worker' logs for errors.")

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
        else:
            print(f"{RED}❌ CRITICAL SYSTEMS FAILURE DETECTED{RESET}")
        print(f"{BLUE}{'='*60}{RESET}")

if __name__ == "__main__":
    runner = MissionCriticalTests()
    asyncio.run(runner.run_all_tests()) 