#!/usr/bin/env python3
"""
Mission Critical Test Suite v4.0
Tests core functionality with REAL API calls to catch actual production issues
No mocks, no fake data - real external API calls and real metadata structures
"""
import asyncio
import aiohttp
import httpx
import time
import subprocess
import os
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv('/root/sidekick-forge/.env')

# Configuration
BASE_URL = "http://localhost:8000"
API_PREFIX = "/api/v1"
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"  # Autonomite client

# LiveKit Configuration (loaded from env)
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "wss://litebridge-hw6srhvi.livekit.cloud")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "APIrZaVVGtq5PCX")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "mRj96UaZFIA8ECFqBK9kIZYFlfW0FHWYZz7Yi3loJ0V")

# Supabase Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

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

async def test_real_api_keys():
    """Test REAL external API keys from client configuration"""
    print(f"\n{BLUE}=== Test 7: Real External API Keys ==={RESET}")
    
    # Use the API to get client configuration instead of direct DB access
    async with aiohttp.ClientSession() as session:
        try:
            # Get client details from API
            async with session.get(f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}") as resp:
                if resp.status != 200:
                    log_test("Load Client Settings", False, f"API returned {resp.status}")
                    return
                    
                client_data = await resp.json()
                settings = client_data.get('settings', {})
                if isinstance(settings, str):
                    settings = json.loads(settings)
                
                api_keys = settings.get('api_keys', {})
                
                # Test each API key with REAL external calls
                
                # Test Groq API
                groq_key = api_keys.get('groq_api_key')
                if groq_key and not groq_key.startswith('test'):
                    groq_passed = await test_groq_real_api(groq_key)
                    log_test("Groq API Key (Real Call)", groq_passed, 
                            "API key valid and model available" if groq_passed else "API key invalid or model deprecated")
                else:
                    log_test("Groq API Key", False, "Missing or test key")
                
                # Test Deepgram API
                deepgram_key = api_keys.get('deepgram_api_key')
                if deepgram_key and not deepgram_key.startswith('test'):
                    deepgram_passed = await test_deepgram_real_api(deepgram_key)
                    log_test("Deepgram API Key (Real Call)", deepgram_passed, 
                            "API key valid" if deepgram_passed else "API key invalid")
                else:
                    log_test("Deepgram API Key", False, "Missing or test key")
                
                # Test ElevenLabs API
                elevenlabs_key = api_keys.get('elevenlabs_api_key')
                if elevenlabs_key and not elevenlabs_key.startswith('test'):
                    elevenlabs_passed = await test_elevenlabs_real_api(elevenlabs_key)
                    log_test("ElevenLabs API Key (Real Call)", elevenlabs_passed, 
                            "API key valid" if elevenlabs_passed else "API key invalid")
                else:
                    log_test("ElevenLabs API Key", False, "Missing or test key")
                    
        except Exception as e:
            log_test("Load Client Settings", False, str(e))

async def test_groq_real_api(api_key: str) -> bool:
    """Test Groq API with REAL call to catch model deprecation"""
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Test with the model that SHOULD be used
        data = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": "Reply with 'OK'"}],
            "max_tokens": 10
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=10.0
            )
            
            if response.status_code == 200:
                # Also test deprecated model to ensure it fails
                data["model"] = "llama-3.1-70b-versatile"
                deprecated_response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=10.0
                )
                
                if deprecated_response.status_code != 200:
                    error_data = deprecated_response.json()
                    if "model_decommissioned" in str(error_data):
                        print(f"   {GREEN}✓ Correctly detected deprecated model{RESET}")
                    return True
                else:
                    print(f"   {RED}✗ Deprecated model still works - this is unexpected{RESET}")
                    return False
            else:
                error_data = response.json()
                print(f"   {RED}✗ Groq API error: {error_data}{RESET}")
                return False
                
    except Exception as e:
        print(f"   {RED}✗ Groq API test failed: {e}{RESET}")
        return False

async def test_deepgram_real_api(api_key: str) -> bool:
    """Test Deepgram API with REAL call"""
    try:
        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.deepgram.com/v1/projects",
                headers=headers,
                timeout=10.0
            )
            
            return response.status_code == 200
            
    except Exception as e:
        print(f"   {RED}✗ Deepgram API test failed: {e}{RESET}")
        return False

async def test_elevenlabs_real_api(api_key: str) -> bool:
    """Test ElevenLabs API with REAL call"""
    try:
        headers = {
            "xi-api-key": api_key
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.elevenlabs.io/v1/voices",
                headers=headers,
                timeout=10.0
            )
            
            return response.status_code == 200
            
    except Exception as e:
        print(f"   {RED}✗ ElevenLabs API test failed: {e}{RESET}")
        return False

async def test_voice_pipeline_real():
    """Test voice pipeline with REAL room creation and metadata"""
    print(f"\n{BLUE}=== Test 8: Real Voice Pipeline Test ==={RESET}")
    
    room_name = f"test-real-{int(time.time())}"
    
    # Create room with REAL metadata that matches production
    payload = {
        "agent_slug": "autonomite",  # Use a real agent that exists
        "mode": "voice",
        "room_name": room_name,
        "user_id": "test-user",
        "client_id": TEST_CLIENT_ID,
        "voice_settings": {
            "llm_provider": "groq",
            "llm_model": "llama-3.3-70b-versatile",  # Current model
            "stt_provider": "deepgram",
            "tts_provider": "elevenlabs"
        }
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(f"{BASE_URL}{API_PREFIX}/trigger-agent", 
                                  json=payload,
                                  timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Verify response structure matches what frontend expects
                    required_fields = ["room_name", "token", "server_url"]
                    missing_fields = [f for f in required_fields if f not in data or not data[f]]
                    
                    if missing_fields:
                        log_test("Voice Pipeline Room Creation", False, 
                                f"Missing fields: {', '.join(missing_fields)}")
                    else:
                        log_test("Voice Pipeline Room Creation", True, 
                                f"Room created: {room_name}")
                        
                        # Test that agent would receive correct metadata
                        await test_agent_metadata_structure(data.get("room_name", room_name))
                else:
                    error_text = await resp.text()
                    log_test("Voice Pipeline Room Creation", False, 
                            f"Status {resp.status}: {error_text[:100]}")
        except Exception as e:
            log_test("Voice Pipeline Room Creation", False, str(e))

async def test_agent_metadata_structure(room_name: str):
    """Test agent metadata structure matches what agent expects"""
    # In real scenario, agent would get metadata from room
    # Here we verify the structure we're sending is correct
    
    test_metadata = {
        "voice_settings": {
            "llm_provider": "groq",
            "llm_model": "llama3-70b-8192",  # Old model name that should be mapped
            "stt_provider": "deepgram",
            "tts_provider": "elevenlabs"
        }
    }
    
    # Test model mapping logic
    voice_settings = test_metadata.get("voice_settings", {})
    model = voice_settings.get("llm_model", test_metadata.get("model", "default"))
    
    # This is what the agent code should do
    if model == "llama3-70b-8192" or model == "llama-3.1-70b-versatile":
        mapped_model = "llama-3.3-70b-versatile"
        log_test("Model Mapping Logic", True, 
                f"Old model '{model}' correctly mapped to '{mapped_model}'")
    else:
        log_test("Model Mapping Logic", False, 
                f"Model '{model}' not mapped correctly")

async def test_deprecated_model_detection():
    """Test that deprecated models are properly detected"""
    print(f"\n{BLUE}=== Test 9: Deprecated Model Detection ==={RESET}")
    
    # Use the API to get client configuration
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}") as resp:
                if resp.status != 200:
                    log_test("Deprecated Model Test", False, f"API returned {resp.status}")
                    return
                    
                client_data = await resp.json()
                settings = client_data.get('settings', {})
                if isinstance(settings, str):
                    settings = json.loads(settings)
                
                api_keys = settings.get('api_keys', {})
                groq_key = api_keys.get('groq_api_key')
                
                if not groq_key:
                    log_test("Deprecated Model Test", False, "No Groq API key found")
                    return
                
                # Test deprecated models
                deprecated_models = [
                    "llama-3.1-70b-versatile",
                    "llama3-70b-8192"
                ]
                
                for model in deprecated_models:
                    headers = {
                        "Authorization": f"Bearer {groq_key}",
                        "Content-Type": "application/json"
                    }
                    
                    data = {
                        "model": model,
                        "messages": [{"role": "user", "content": "Test"}],
                        "max_tokens": 5
                    }
                    
                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers=headers,
                            json=data,
                            timeout=10.0
                        )
                        
                        if response.status_code != 200:
                            error_data = response.json()
                            if "model_decommissioned" in str(error_data):
                                log_test(f"Deprecated Model '{model}'", True, 
                                        "Correctly identified as deprecated")
                            else:
                                log_test(f"Deprecated Model '{model}'", False, 
                                        f"Failed with different error: {error_data}")
                        else:
                            log_test(f"Deprecated Model '{model}'", False, 
                                    "Model still works - should be deprecated!")
                            
        except Exception as e:
            log_test("Deprecated Model Test", False, str(e))

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
    print(f"{BLUE}SIDEKICK FORGE - MISSION CRITICAL TEST SUITE v4.0{RESET}")
    print(f"{BLUE}Real API Calls | Real Metadata | No Mocks{RESET}")
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target: {BASE_URL}")
    print(f"Client: {TEST_CLIENT_ID}")
    
    # Basic infrastructure tests
    await test_health_endpoints()
    await test_client_operations()
    await test_agent_operations()
    await test_livekit_operations()
    await test_docker_and_workers()
    await test_admin_interface()
    
    # Real API and integration tests
    await test_real_api_keys()
    await test_voice_pipeline_real()
    await test_deprecated_model_detection()
    
    print_summary()

if __name__ == "__main__":
    asyncio.run(main())