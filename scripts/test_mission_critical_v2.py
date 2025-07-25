#!/usr/bin/env python3
"""
Mission Critical Test Suite v2.0
Tests all critical functionality including containerized agent architecture
"""

import asyncio
import aiohttp
import json
import sys
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
import subprocess
import docker
import os
import re
from pathlib import Path

# Test configuration
BASE_URL = "http://localhost:8000"
API_PREFIX = "/api/v1"
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"  # autonomite client

# Color codes for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

class MissionCriticalTests:
    def __init__(self):
        self.session = None
        self.test_results = []
        self.test_room_name = f"test-room-{int(time.time())}"
        self.test_container_id = None
        self.runtime_proof = {}
        try:
            self.docker_client = docker.from_env()
        except:
            self.docker_client = None
        
    async def setup(self):
        """Initialize test session"""
        self.session = aiohttp.ClientSession()
        
    async def teardown(self):
        """Cleanup test session"""
        if self.session:
            await self.session.close()
            
    def log_test(self, test_name: str, passed: bool, details: str = ""):
        """Log test result"""
        status = f"{GREEN}✅ PASSED{RESET}" if passed else f"{RED}❌ FAILED{RESET}"
        print(f"\n{status} {test_name}")
        if details:
            print(f"   {details}")
        self.test_results.append({
            "test": test_name,
            "passed": passed,
            "details": details
        })
        
    async def test_health_endpoints(self):
        """Test 1: Health and connectivity endpoints"""
        print(f"\n{BLUE}=== Testing Health & Connectivity ==={RESET}")
        
        # Test basic health
        try:
            async with self.session.get(f"{BASE_URL}/health") as resp:
                if resp.status == 200:
                    self.log_test("Basic health check", True, "API is responsive")
                else:
                    self.log_test("Basic health check", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("Basic health check", False, str(e))
            
        # Test detailed health
        try:
            async with self.session.get(f"{BASE_URL}/health/detailed") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    services_up = all(
                        data.get(svc, {}).get("status") == "connected" 
                        for svc in ["supabase", "livekit"]
                    )
                    self.log_test(
                        "Detailed health check", 
                        services_up,
                        f"Supabase: {data.get('supabase', {}).get('status')}, "
                        f"LiveKit: {data.get('livekit', {}).get('status')}"
                    )
                else:
                    self.log_test("Detailed health check", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("Detailed health check", False, str(e))
            
    async def test_client_management(self):
        """Test 2: Client management functionality"""
        print(f"\n{BLUE}=== Testing Client Management ==={RESET}")
        
        # List clients
        try:
            async with self.session.get(f"{BASE_URL}{API_PREFIX}/clients") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    has_clients = len(data.get("data", [])) > 0
                    self.log_test(
                        "List clients", 
                        has_clients,
                        f"Found {len(data.get('data', []))} clients"
                    )
                else:
                    self.log_test("List clients", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("List clients", False, str(e))
            
        # Get specific client
        try:
            async with self.session.get(f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    client = data.get("data", {})
                    has_livekit = bool(
                        client.get("settings", {}).get("livekit", {}).get("server_url")
                    )
                    self.log_test(
                        "Get client details", 
                        has_livekit,
                        f"Client: {client.get('name')}, LiveKit configured: {has_livekit}"
                    )
                else:
                    self.log_test("Get client details", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("Get client details", False, str(e))
            
    async def test_agent_management(self):
        """Test 3: Agent management functionality"""
        print(f"\n{BLUE}=== Testing Agent Management ==={RESET}")
        
        # List agents for client
        try:
            async with self.session.get(
                f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}/agents"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    agents = data.get("data", [])
                    has_agents = len(agents) > 0
                    self.log_test(
                        "List client agents", 
                        has_agents,
                        f"Found {len(agents)} agents"
                    )
                    
                    # Store first agent for later tests
                    if agents:
                        self.test_agent = agents[0]
                else:
                    self.log_test("List client agents", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("List client agents", False, str(e))
            
        # Sync agents from Supabase
        try:
            async with self.session.post(
                f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}/sync-agents"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.log_test(
                        "Sync agents from Supabase", 
                        True,
                        f"Synced {data.get('data', {}).get('synced', 0)} agents"
                    )
                else:
                    self.log_test("Sync agents from Supabase", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("Sync agents from Supabase", False, str(e))
            
    async def test_container_management(self):
        """Test 4: Container management functionality"""
        print(f"\n{BLUE}=== Testing Container Management ==={RESET}")
        
        # List containers
        try:
            async with self.session.get(f"{BASE_URL}{API_PREFIX}/containers") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.log_test(
                        "List containers", 
                        True,
                        f"Found {data.get('total', 0)} containers"
                    )
                else:
                    self.log_test("List containers", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("List containers", False, str(e))
            
        # Container health check
        try:
            async with self.session.get(f"{BASE_URL}{API_PREFIX}/containers/health") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.log_test(
                        "Container health check", 
                        True,
                        f"Total: {data.get('total', 0)}, "
                        f"Healthy: {data.get('healthy', 0)}, "
                        f"Unhealthy: {data.get('unhealthy', 0)}"
                    )
                else:
                    self.log_test("Container health check", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("Container health check", False, str(e))
            
    async def test_deployment_verification(self):
        """Test 5: Deployment and runtime verification"""
        print(f"\n{BLUE}=== Testing Deployment Verification ==={RESET}")
        
        if not self.docker_client:
            self.log_test("Docker client", False, "Docker not accessible")
            return
            
        # Find agent containers
        containers = self.docker_client.containers.list(filters={"label": "autonomite.managed=true"})
        
        if not containers:
            self.log_test("Agent containers deployed", False, "No managed containers found")
            return
            
        self.log_test("Agent containers deployed", True, f"Found {len(containers)} managed containers")
        
        for container in containers:
            container_name = container.name
            print(f"\n  Checking container: {container_name}")
            
            # Check deployment labels
            deploy_tag = container.labels.get("autonomite.deploy_tag", "unknown")
            deployed_at = container.labels.get("autonomite.deployed_at", "unknown")
            
            # Verify session_agent.py is running
            try:
                logs = container.logs(tail=200).decode('utf-8')
                
                # Check for session agent markers
                has_session_agent = "Starting session agent" in logs
                has_handlers = "Event handlers registered" in logs or "Registering event handlers" in logs
                has_worker = "registered worker" in logs
                
                self.runtime_proof[container_name] = {
                    "deploy_tag": deploy_tag,
                    "deployed_at": deployed_at,
                    "session_agent_running": has_session_agent,
                    "handlers_registered": has_handlers,
                    "worker_registered": has_worker
                }
                
                self.log_test(
                    f"Container {container_name} deployment", 
                    has_session_agent and has_handlers,
                    f"Session agent: {has_session_agent}, Handlers: {has_handlers}, Worker: {has_worker}"
                )
                
                # Check for stuck processes
                stuck_processes = logs.count("process did not exit in time")
                if stuck_processes > 0:
                    self.log_test(
                        f"Container {container_name} health", 
                        False,
                        f"Found {stuck_processes} stuck process errors"
                    )
                    
                # Verify critical files
                try:
                    exit_code, output = container.exec_run("ls -la /app/session_agent.py")
                    file_exists = exit_code == 0
                    self.log_test(
                        f"Container {container_name} session_agent.py", 
                        file_exists,
                        "File present" if file_exists else "File missing"
                    )
                except Exception as e:
                    self.log_test(f"Container {container_name} file check", False, str(e))
                    
            except Exception as e:
                self.log_test(f"Container {container_name} verification", False, str(e))
                
    async def test_greeting_verification(self):
        """Test 6: Greeting functionality verification"""
        print(f"\n{BLUE}=== Testing Greeting Verification ==={RESET}")
        
        if not self.docker_client:
            self.log_test("Greeting verification", False, "Docker not accessible")
            return
            
        # Find agent containers
        containers = self.docker_client.containers.list(filters={"label": "autonomite.managed=true"})
        
        for container in containers:
            try:
                logs = container.logs(tail=500).decode('utf-8')
                
                # Check for greeting attempts
                greeting_attempts = logs.count("Attempting to send greeting")
                greeting_success = logs.count("Greeting sent successfully")
                
                self.runtime_proof[container.name]["greeting_attempts"] = greeting_attempts
                self.runtime_proof[container.name]["greeting_success"] = greeting_success
                
                if greeting_attempts > 0:
                    success_rate = (greeting_success / greeting_attempts) * 100 if greeting_attempts > 0 else 0
                    self.log_test(
                        f"Container {container.name} greetings", 
                        success_rate > 50,
                        f"Attempts: {greeting_attempts}, Success: {greeting_success} ({success_rate:.1f}%)"
                    )
                else:
                    self.log_test(
                        f"Container {container.name} greetings", 
                        True,
                        "No greeting attempts yet (normal if no preview)"
                    )
                    
                # Check for audio publishing
                audio_published = "Published audio track" in logs or "track published" in logs
                if audio_published:
                    self.log_test(
                        f"Container {container.name} audio", 
                        True,
                        "Audio track published successfully"
                    )
                    
            except Exception as e:
                self.log_test(f"Container {container.name} greeting check", False, str(e))
                
    async def test_container_health_monitoring(self):
        """Test 7: Container health and performance monitoring"""
        print(f"\n{BLUE}=== Testing Container Health Monitoring ==={RESET}")
        
        if not self.docker_client:
            self.log_test("Health monitoring", False, "Docker not accessible")
            return
            
        containers = self.docker_client.containers.list(filters={"label": "autonomite.managed=true"})
        
        for container in containers:
            try:
                # Get container stats
                stats = container.stats(stream=False)
                
                # Check CPU usage
                cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
                system_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
                cpu_percent = (cpu_delta / system_delta) * 100 if system_delta > 0 else 0
                
                # Check memory usage
                mem_usage = stats['memory_stats']['usage']
                mem_limit = stats['memory_stats']['limit']
                mem_percent = (mem_usage / mem_limit) * 100 if mem_limit > 0 else 0
                
                # Check container health status
                health_status = container.attrs['State'].get('Health', {}).get('Status', 'none')
                
                self.runtime_proof[container.name]["cpu_percent"] = round(cpu_percent, 2)
                self.runtime_proof[container.name]["memory_percent"] = round(mem_percent, 2)
                self.runtime_proof[container.name]["health_status"] = health_status
                
                # Determine if healthy
                is_healthy = (
                    health_status in ['healthy', 'none'] and 
                    cpu_percent < 80 and 
                    mem_percent < 80
                )
                
                self.log_test(
                    f"Container {container.name} health", 
                    is_healthy,
                    f"Health: {health_status}, CPU: {cpu_percent:.1f}%, Memory: {mem_percent:.1f}%"
                )
                
                # Check for restart count
                restart_count = container.attrs['RestartCount']
                if restart_count > 0:
                    self.log_test(
                        f"Container {container.name} stability", 
                        False,
                        f"Container restarted {restart_count} times"
                    )
                    
            except Exception as e:
                self.log_test(f"Container {container.name} health", False, str(e))
                
    async def test_trigger_endpoint(self):
        """Test 8: Agent trigger endpoint with container spawning"""
        print(f"\n{BLUE}=== Testing Agent Trigger & Container Spawning ==={RESET}")
        
        if not hasattr(self, 'test_agent'):
            self.log_test("Trigger agent", False, "No test agent available")
            return
            
        # Test voice mode trigger
        payload = {
            "agent_slug": self.test_agent["slug"],
            "mode": "voice",
            "room_name": self.test_room_name,
            "user_id": "test-user-123",
            "client_id": TEST_CLIENT_ID
        }
        
        try:
            async with self.session.post(
                f"{BASE_URL}{API_PREFIX}/trigger-agent",
                json=payload
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    container_info = data.get("data", {}).get("container_info", {})
                    
                    # Check if container was spawned
                    if container_info.get("status") in ["running", "main_agent_ready"]:
                        self.test_container_id = container_info.get("container_id")
                        self.log_test(
                            "Trigger agent (voice mode)", 
                            True,
                            f"Container: {container_info.get('container_name', 'N/A')}, "
                            f"Method: {container_info.get('method', 'N/A')}"
                        )
                        
                        # Additional checks for containerized deployment
                        is_containerized = container_info.get("method") == "containerized_agent"
                        has_livekit_cloud = bool(container_info.get("livekit_cloud"))
                        
                        self.log_test(
                            "Container isolation", 
                            is_containerized,
                            f"Using containerized agents: {is_containerized}"
                        )
                        
                        self.log_test(
                            "Client LiveKit configuration", 
                            has_livekit_cloud,
                            f"LiveKit Cloud: {container_info.get('livekit_cloud', 'Not configured')}"
                        )
                    else:
                        self.log_test(
                            "Trigger agent (voice mode)", 
                            False,
                            f"Container status: {container_info.get('status')}"
                        )
                else:
                    self.log_test("Trigger agent (voice mode)", False, f"Status: {resp.status}")
                    
        except Exception as e:
            self.log_test("Trigger agent (voice mode)", False, str(e))
            
        # Test text mode trigger
        text_payload = {
            "agent_slug": self.test_agent["slug"],
            "mode": "text",
            "message": "Hello, test message",
            "user_id": "test-user-123",
            "client_id": TEST_CLIENT_ID
        }
        
        try:
            async with self.session.post(
                f"{BASE_URL}{API_PREFIX}/trigger-agent",
                json=text_payload
            ) as resp:
                if resp.status == 200:
                    self.log_test("Trigger agent (text mode)", True, "Text mode endpoint responsive")
                else:
                    self.log_test("Trigger agent (text mode)", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("Trigger agent (text mode)", False, str(e))
            
    async def test_room_specific_processing(self):
        """Test 9: Verify agent processes specific room requests"""
        print(f"\n{BLUE}=== Testing Room-Specific Processing ==={RESET}")
        
        if not hasattr(self, 'test_agent') or not self.test_container_id:
            self.log_test("Room-specific processing", False, "No test agent or container available")
            return
            
        # Generate unique room name
        specific_room = f"test_specific_{int(time.time())}"
        
        # Trigger agent for specific room
        payload = {
            "agent_slug": self.test_agent["slug"],
            "mode": "voice",
            "room_name": specific_room,
            "user_id": "test-user-room-specific",
            "client_id": TEST_CLIENT_ID
        }
        
        try:
            async with self.session.post(
                f"{BASE_URL}{API_PREFIX}/trigger-agent",
                json=payload
            ) as resp:
                if resp.status == 200:
                    # Wait for agent to process
                    await asyncio.sleep(5)
                    
                    # Check if agent received this specific room
                    if self.docker_client:
                        containers = self.docker_client.containers.list(
                            filters={"label": f"autonomite.client_id={TEST_CLIENT_ID}"}
                        )
                        
                        room_found = False
                        for container in containers:
                            logs = container.logs(tail=100).decode('utf-8')
                            if specific_room in logs:
                                room_found = True
                                # Check if job was accepted
                                if f"Job accepted for room '{specific_room}'" in logs:
                                    self.log_test(
                                        "Room-specific job acceptance", 
                                        True,
                                        f"Agent accepted job for room {specific_room}"
                                    )
                                else:
                                    self.log_test(
                                        "Room-specific job acceptance", 
                                        False,
                                        f"Agent saw room {specific_room} but didn't accept job"
                                    )
                                break
                                
                        if not room_found:
                            self.log_test(
                                "Room-specific processing", 
                                False,
                                f"Agent did not receive room {specific_room}"
                            )
                    else:
                        self.log_test("Room-specific processing", False, "Docker not accessible")
                else:
                    self.log_test("Room-specific processing", False, f"Trigger failed: {resp.status}")
                    
        except Exception as e:
            self.log_test("Room-specific processing", False, str(e))
            
    async def test_docker_infrastructure(self):
        """Test 6: Docker infrastructure"""
        print(f"\n{BLUE}=== Testing Docker Infrastructure ==={RESET}")
        
        # Check if Docker is running
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=5
            )
            docker_running = result.returncode == 0
            self.log_test(
                "Docker daemon", 
                docker_running,
                "Docker is running" if docker_running else "Docker not available"
            )
        except Exception as e:
            self.log_test("Docker daemon", False, str(e))
            
        # Check if agent image exists
        try:
            result = subprocess.run(
                ["docker", "images", "-q", "autonomite/agent-runtime:latest"],
                capture_output=True,
                text=True,
                timeout=5
            )
            image_exists = bool(result.stdout.strip())
            self.log_test(
                "Agent runtime image", 
                image_exists,
                "Image exists" if image_exists else "Image not found - need to build"
            )
        except Exception as e:
            self.log_test("Agent runtime image", False, str(e))
            
        # Check running containers
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}", "--filter", "label=autonomite.type=agent"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                container_count = len([l for l in lines[1:] if l.strip()]) if len(lines) > 1 else 0
                self.log_test(
                    "Agent containers", 
                    True,
                    f"Found {container_count} agent containers running"
                )
            else:
                self.log_test("Agent containers", False, "Failed to list containers")
        except Exception as e:
            self.log_test("Agent containers", False, str(e))
            
    async def test_data_persistence(self):
        """Test 7: Data persistence (Supabase-only)"""
        print(f"\n{BLUE}=== Testing Data Persistence ==={RESET}")
        
        # Create test data
        test_key = f"test_key_{int(time.time())}"
        test_value = {"test": "data", "timestamp": datetime.now().isoformat()}
        
        # Since we don't have a direct key-value endpoint, test via client update
        try:
            # Get current client data
            async with self.session.get(f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}") as resp:
                if resp.status == 200:
                    current_data = await resp.json()
                    
                    # Update with test metadata
                    update_payload = {
                        "metadata": {
                            **current_data.get("data", {}).get("metadata", {}),
                            test_key: test_value
                        }
                    }
                    
                    # Update client
                    async with self.session.put(
                        f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}",
                        json=update_payload
                    ) as update_resp:
                        if update_resp.status == 200:
                            # Verify persistence
                            async with self.session.get(
                                f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}"
                            ) as verify_resp:
                                if verify_resp.status == 200:
                                    verify_data = await verify_resp.json()
                                    stored_value = verify_data.get("data", {}).get("metadata", {}).get(test_key)
                                    
                                    self.log_test(
                                        "Supabase data persistence", 
                                        stored_value == test_value,
                                        "Data persisted and retrieved successfully"
                                    )
                                else:
                                    self.log_test("Supabase data persistence", False, "Failed to verify")
                        else:
                            self.log_test("Supabase data persistence", False, "Failed to update")
                else:
                    self.log_test("Supabase data persistence", False, "Failed to get client")
        except Exception as e:
            self.log_test("Supabase data persistence", False, str(e))
            
    async def test_api_key_sync(self):
        """Test 8: API Key synchronization"""
        print(f"\n{BLUE}=== Testing API Key Synchronization ==={RESET}")
        
        try:
            # Get client with API keys
            async with self.session.get(f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    api_keys = data.get("data", {}).get("settings", {}).get("api_keys", {})
                    
                    # Check for various API keys
                    expected_keys = ["openai", "groq", "deepgram", "elevenlabs"]
                    found_keys = [k for k in expected_keys if api_keys.get(k)]
                    
                    self.log_test(
                        "API key availability", 
                        len(found_keys) > 0,
                        f"Found API keys: {', '.join(found_keys) if found_keys else 'None'}"
                    )
                    
                    # Test sync endpoint
                    async with self.session.post(
                        f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}/sync"
                    ) as sync_resp:
                        if sync_resp.status == 200:
                            self.log_test("API key sync endpoint", True, "Sync completed successfully")
                        else:
                            self.log_test("API key sync endpoint", False, f"Status: {sync_resp.status}")
                else:
                    self.log_test("API key availability", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("API key synchronization", False, str(e))
            
    async def test_admin_interface(self):
        """Test 9: Admin interface availability"""
        print(f"\n{BLUE}=== Testing Admin Interface ==={RESET}")
        
        # Test admin homepage
        try:
            async with self.session.get(f"{BASE_URL}/admin") as resp:
                if resp.status == 200:
                    content = await resp.text()
                    has_htmx = "htmx" in content.lower()
                    has_tailwind = "tailwind" in content.lower()
                    
                    self.log_test(
                        "Admin interface", 
                        resp.status == 200,
                        f"HTMX: {has_htmx}, Tailwind: {has_tailwind}"
                    )
                else:
                    self.log_test("Admin interface", False, f"Status: {resp.status}")
        except Exception as e:
            self.log_test("Admin interface", False, str(e))
            
        # Test key admin pages
        admin_pages = [
            ("/admin/clients", "Clients page"),
            ("/admin/agents", "Agents page"),
            ("/admin/containers", "Containers page")
        ]
        
        for path, name in admin_pages:
            try:
                async with self.session.get(f"{BASE_URL}{path}") as resp:
                    self.log_test(
                        f"Admin {name}", 
                        resp.status == 200,
                        f"Status: {resp.status}"
                    )
            except Exception as e:
                self.log_test(f"Admin {name}", False, str(e))
                
    def print_summary(self):
        """Print test summary"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}=== TEST SUMMARY ==={RESET}")
        print(f"{BLUE}{'='*60}{RESET}")
        
        total = len(self.test_results)
        passed = sum(1 for r in self.test_results if r["passed"])
        failed = total - passed
        
        print(f"\nTotal Tests: {total}")
        print(f"{GREEN}Passed: {passed}{RESET}")
        print(f"{RED}Failed: {failed}{RESET}")
        
        if failed > 0:
            print(f"\n{RED}Failed Tests:{RESET}")
            for result in self.test_results:
                if not result["passed"]:
                    print(f"  - {result['test']}: {result['details']}")
                    
        # Print runtime proof if collected
        if self.runtime_proof:
            print(f"\n{BLUE}=== RUNTIME PROOF ==={RESET}")
            for container, evidence in self.runtime_proof.items():
                print(f"\n{container}:")
                for key, value in evidence.items():
                    print(f"  {key}: {value}")
                    
        # Mission critical status
        critical_tests = [
            "Basic health check",
            "List clients",
            "Trigger agent (voice mode)",
            "Container isolation",
            "Docker daemon",
            "Agent containers deployed",
            "Container health monitoring"
        ]
        
        critical_passed = all(
            r["passed"] for r in self.test_results 
            if r["test"] in critical_tests
        )
        
        print(f"\n{BLUE}Mission Critical Status: ", end="")
        if critical_passed:
            print(f"{GREEN}✅ ALL CRITICAL SYSTEMS OPERATIONAL{RESET}")
        else:
            print(f"{RED}❌ CRITICAL SYSTEMS FAILURE{RESET}")
            
        return failed == 0

async def main():
    """Run all mission critical tests"""
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}AUTONOMITE PLATFORM - MISSION CRITICAL TEST SUITE v2.0{RESET}")
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    tests = MissionCriticalTests()
    
    try:
        await tests.setup()
        
        # Run all test categories
        await tests.test_health_endpoints()
        await tests.test_client_management()
        await tests.test_agent_management()
        await tests.test_container_management()
        await tests.test_deployment_verification()
        await tests.test_greeting_verification()
        await tests.test_container_health_monitoring()
        await tests.test_trigger_endpoint()
        await tests.test_docker_infrastructure()
        await tests.test_data_persistence()
        await tests.test_api_key_sync()
        await tests.test_admin_interface()
        await tests.test_room_specific_processing()
        
        # Print summary
        all_passed = tests.print_summary()
        
        await tests.teardown()
        
        # Exit with appropriate code
        sys.exit(0 if all_passed else 1)
        
    except Exception as e:
        print(f"\n{RED}FATAL ERROR: {str(e)}{RESET}")
        await tests.teardown()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())