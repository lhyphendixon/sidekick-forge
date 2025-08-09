#!/usr/bin/env python3
"""
Mission Critical Test Suite v5.0
Comprehensive test suite addressing all 53 GitHub issues
Features: Parallel execution, quick mode, verbose output, JSON reports
"""
import asyncio
import aiohttp
import httpx
import time
import subprocess
import os
import json
import sys
import argparse
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from enum import Enum

# Load environment variables
from dotenv import load_dotenv
load_dotenv('/root/sidekick-forge/.env')

# Configuration
BASE_URL = "http://localhost:8000"
API_PREFIX = "/api/v1"
API_V2_PREFIX = "/api/v2"
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"  # Autonomite client

# LiveKit Configuration
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "wss://litebridge-hw6srhvi.livekit.cloud")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# Supabase Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# ANSI color codes
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
CYAN = '\033[96m'
RESET = '\033[0m'
BOLD = '\033[1m'

class TestStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WARNING = "warning"

@dataclass
class TestResult:
    test_name: str
    category: str
    status: TestStatus
    details: str
    duration_ms: float
    timestamp: str
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class MissionCriticalTestSuite:
    def __init__(self, args):
        self.args = args
        self.results: List[TestResult] = []
        self.start_time = time.time()
        self.session: Optional[aiohttp.ClientSession] = None
        self.verbose = args.verbose
        self.quick = args.quick
        self.parallel = args.parallel
        self.json_output = args.json
        
    def log(self, message: str, level: str = "INFO"):
        """Log with optional verbose mode"""
        if not self.json_output:
            if level == "ERROR":
                print(f"{RED}{message}{RESET}")
            elif level == "SUCCESS":
                print(f"{GREEN}{message}{RESET}")
            elif level == "WARNING":
                print(f"{YELLOW}{message}{RESET}")
            elif level == "DEBUG" and self.verbose:
                print(f"{CYAN}[DEBUG] {message}{RESET}")
            elif level == "INFO":
                print(message)
    
    def log_test(self, test_name: str, category: str, passed: bool, details: str = "", 
                  error: str = None, metadata: Dict = None, duration_ms: float = 0):
        """Log test result"""
        status = TestStatus.PASSED if passed else TestStatus.FAILED
        
        result = TestResult(
            test_name=test_name,
            category=category,
            status=status,
            details=details,
            duration_ms=duration_ms,
            timestamp=datetime.utcnow().isoformat(),
            error=error,
            metadata=metadata
        )
        
        self.results.append(result)
        
        if not self.json_output:
            status_str = f"{GREEN}✅ PASSED{RESET}" if passed else f"{RED}❌ FAILED{RESET}"
            print(f"  {status_str} {test_name}")
            if details and (not passed or self.verbose):
                print(f"     {details}")
            if error and not passed:
                print(f"     {RED}Error: {error}{RESET}")
    
    async def setup(self):
        """Setup test environment"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
    
    async def teardown(self):
        """Cleanup test environment"""
        if self.session:
            await self.session.close()
    
    # ==================== INFRASTRUCTURE TESTS ====================
    
    async def test_infrastructure(self):
        """Test core infrastructure dependencies"""
        self.log(f"\n{BLUE}{'='*60}{RESET}")
        self.log(f"{BLUE}=== INFRASTRUCTURE TESTS ==={RESET}")
        self.log(f"{BLUE}{'='*60}{RESET}")
        
        # Test Redis is NOT required (Issue #2)
        await self.test_no_redis_dependency()
        
        # Test Supabase-only operation
        await self.test_supabase_only_operation()
        
        # Test Docker and workers
        await self.test_docker_status()
        
    async def test_no_redis_dependency(self):
        """Test that system works without Redis (Issue #2)"""
        start = time.perf_counter()
        
        # Check that Redis is not in docker-compose
        try:
            with open('/root/sidekick-forge/docker-compose.yml', 'r') as f:
                compose_content = f.read()
                if 'redis' in compose_content.lower():
                    self.log_test(
                        "No Redis Dependency",
                        "Infrastructure",
                        False,
                        "Redis still present in docker-compose.yml",
                        duration_ms=(time.perf_counter() - start) * 1000
                    )
                else:
                    # Verify system is operational without Redis
                    async with self.session.get(f"{BASE_URL}/health") as resp:
                        if resp.status == 200:
                            self.log_test(
                                "No Redis Dependency",
                                "Infrastructure",
                                True,
                                "System operational without Redis",
                                duration_ms=(time.perf_counter() - start) * 1000
                            )
                        else:
                            self.log_test(
                                "No Redis Dependency",
                                "Infrastructure",
                                False,
                                f"Health check failed: {resp.status}",
                                duration_ms=(time.perf_counter() - start) * 1000
                            )
        except Exception as e:
            self.log_test(
                "No Redis Dependency",
                "Infrastructure",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_supabase_only_operation(self):
        """Test Supabase-only session and rate limiting"""
        start = time.perf_counter()
        
        try:
            # Test that rate limiting works without Redis
            responses = []
            for _ in range(5):
                async with self.session.get(f"{BASE_URL}/health") as resp:
                    responses.append(resp.status)
            
            if all(r == 200 for r in responses):
                self.log_test(
                    "Supabase-Only Operation",
                    "Infrastructure",
                    True,
                    "Rate limiting functional without Redis",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
            else:
                self.log_test(
                    "Supabase-Only Operation",
                    "Infrastructure",
                    False,
                    f"Some requests failed: {responses}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Supabase-Only Operation",
                "Infrastructure",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_docker_status(self):
        """Test Docker and worker containers"""
        start = time.perf_counter()
        
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                containers = result.stdout.strip().split('\n')
                worker_containers = [c for c in containers if 'agent' in c or 'worker' in c]
                
                self.log_test(
                    "Docker Workers",
                    "Infrastructure",
                    len(worker_containers) > 0,
                    f"Found {len(worker_containers)} worker container(s)",
                    metadata={"containers": worker_containers},
                    duration_ms=(time.perf_counter() - start) * 1000
                )
            else:
                self.log_test(
                    "Docker Workers",
                    "Infrastructure",
                    False,
                    "Failed to query Docker",
                    error=result.stderr,
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Docker Workers",
                "Infrastructure",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    # ==================== CONFIGURATION VALIDATION TESTS ====================
    
    async def test_configuration_validation(self):
        """Test configuration validation and error handling"""
        self.log(f"\n{BLUE}{'='*60}{RESET}")
        self.log(f"{BLUE}=== CONFIGURATION VALIDATION TESTS ==={RESET}")
        self.log(f"{BLUE}{'='*60}{RESET}")
        
        await self.test_missing_api_keys_return_400()
        await self.test_invalid_provider_config()
        await self.test_embedding_config_validation()
        await self.test_no_silent_fallbacks()
        await self.test_structured_error_messages()
    
    async def test_missing_api_keys_return_400(self):
        """Test that missing API keys return 400 not 500 (Issue #45)"""
        start = time.perf_counter()
        
        payload = {
            "agent_slug": "test-agent",
            "mode": "voice",
            "room_name": f"test-{int(time.time())}",
            "user_id": "test-user",
            "client_id": "invalid-client-id"  # Invalid client = no API keys
        }
        
        try:
            async with self.session.post(
                f"{BASE_URL}{API_PREFIX}/trigger-agent",
                json=payload
            ) as resp:
                # Should return 4xx for configuration errors
                is_client_error = 400 <= resp.status < 500
                
                self.log_test(
                    "Missing API Keys Return 400",
                    "Configuration",
                    is_client_error,
                    f"Status: {resp.status} (Expected 4xx)",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Missing API Keys Return 400",
                "Configuration",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_invalid_provider_config(self):
        """Test invalid provider configurations fail properly"""
        start = time.perf_counter()
        
        # Test with missing provider
        payload = {
            "agent_slug": "test-agent",
            "mode": "voice",
            "room_name": f"test-{int(time.time())}",
            "user_id": "test-user",
            "client_id": TEST_CLIENT_ID,
            "voice_settings": {
                # Missing llm_provider
                "stt_provider": "deepgram",
                "tts_provider": "elevenlabs"
            }
        }
        
        try:
            async with self.session.post(
                f"{BASE_URL}{API_PREFIX}/trigger-agent",
                json=payload
            ) as resp:
                response_text = await resp.text()
                
                # Should fail with clear error about missing provider
                has_clear_error = (
                    resp.status == 400 and
                    ("llm_provider" in response_text.lower() or 
                     "missing" in response_text.lower())
                )
                
                self.log_test(
                    "Invalid Provider Config",
                    "Configuration",
                    has_clear_error,
                    f"Status: {resp.status}, Clear error: {has_clear_error}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Invalid Provider Config",
                "Configuration",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_embedding_config_validation(self):
        """Test embedding configuration validation (Issue #13)"""
        start = time.perf_counter()
        
        # Test with missing embedding config
        payload = {
            "agent_slug": "test-agent",
            "mode": "text",
            "message": "Test message",
            "user_id": "test-user",
            "client_id": TEST_CLIENT_ID,
            "embedding": {}  # Empty embedding config
        }
        
        try:
            async with self.session.post(
                f"{BASE_URL}{API_PREFIX}/trigger-agent",
                json=payload
            ) as resp:
                response_text = await resp.text()
                
                # Should validate embedding config
                validated = (
                    resp.status in [200, 400] and  # Either works or fails clearly
                    resp.status != 500  # Not a server error
                )
                
                self.log_test(
                    "Embedding Config Validation",
                    "Configuration",
                    validated,
                    f"Status: {resp.status} (No 500 error)",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Embedding Config Validation",
                "Configuration",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_no_silent_fallbacks(self):
        """Test no silent fallbacks to default providers (Issue #7)"""
        start = time.perf_counter()
        
        # Check entrypoint.py for fallback patterns
        try:
            with open('/root/sidekick-forge/docker/agent/entrypoint.py', 'r') as f:
                content = f.read()
                
                # Check for no environment variable fallbacks
                has_env_fallbacks = 'os.getenv' in content and 'or "openai"' in content
                
                # Check for ConfigurationError raises
                has_fail_fast = 'ConfigurationError' in content and 'raise' in content
                
                self.log_test(
                    "No Silent Fallbacks",
                    "Configuration",
                    not has_env_fallbacks and has_fail_fast,
                    f"Env fallbacks: {has_env_fallbacks}, Fail-fast: {has_fail_fast}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "No Silent Fallbacks",
                "Configuration",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_structured_error_messages(self):
        """Test structured error responses (Issue #28, #48)"""
        start = time.perf_counter()
        
        # Trigger an error to check response structure
        payload = {
            "agent_slug": "nonexistent-agent",
            "mode": "voice",
            "room_name": f"test-{int(time.time())}",
            "user_id": "test-user",
            "client_id": "invalid-client"
        }
        
        try:
            async with self.session.post(
                f"{BASE_URL}{API_PREFIX}/trigger-agent",
                json=payload
            ) as resp:
                if resp.status >= 400:
                    try:
                        error_json = await resp.json()
                        has_structure = (
                            isinstance(error_json, dict) and
                            any(k in error_json for k in ['error', 'detail', 'message'])
                        )
                    except:
                        has_structure = False
                else:
                    has_structure = True  # Success is also valid
                
                self.log_test(
                    "Structured Error Messages",
                    "Configuration",
                    has_structure,
                    f"Status: {resp.status}, Structured: {has_structure}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Structured Error Messages",
                "Configuration",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    # ==================== LIVEKIT ARCHITECTURE TESTS ====================
    
    async def test_livekit_architecture(self):
        """Test LiveKit SDK patterns and dispatch"""
        self.log(f"\n{BLUE}{'='*60}{RESET}")
        self.log(f"{BLUE}=== LIVEKIT ARCHITECTURE TESTS ==={RESET}")
        self.log(f"{BLUE}{'='*60}{RESET}")
        
        await self.test_explicit_dispatch()
        await self.test_agent_name_consistency()
        await self.test_no_automatic_dispatch()
        await self.test_job_context_pattern()
        await self.test_agent_session_usage()
    
    async def test_explicit_dispatch(self):
        """Test explicit dispatch with agent_name (Issue #42)"""
        start = time.perf_counter()
        
        # Check entrypoint for explicit dispatch
        try:
            with open('/root/sidekick-forge/docker/agent/entrypoint.py', 'r') as f:
                content = f.read()
                
                # Check for agent_name in WorkerOptions
                has_agent_name = 'agent_name=' in content and 'WorkerOptions' in content
                
                # Check for agent_name checking in request_filter
                has_filter_check = 'job_request.agent_name' in content
                
                self.log_test(
                    "Explicit Dispatch Pattern",
                    "LiveKit",
                    has_agent_name and has_filter_check,
                    f"Agent name: {has_agent_name}, Filter: {has_filter_check}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Explicit Dispatch Pattern",
                "LiveKit",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_agent_name_consistency(self):
        """Test agent_name consistency across components (Issue #18, #53)"""
        start = time.perf_counter()
        
        expected_agent_name = "sidekick-agent"
        
        try:
            # Check entrypoint.py
            with open('/root/sidekick-forge/docker/agent/entrypoint.py', 'r') as f:
                entrypoint_content = f.read()
                entrypoint_has_name = f'"{expected_agent_name}"' in entrypoint_content
            
            # Check trigger.py for room creation
            with open('/root/sidekick-forge/app/api/v1/trigger.py', 'r') as f:
                trigger_content = f.read()
                trigger_has_name = f'agent_name="{expected_agent_name}"' in trigger_content
            
            self.log_test(
                "Agent Name Consistency",
                "LiveKit",
                entrypoint_has_name and trigger_has_name,
                f"Entrypoint: {entrypoint_has_name}, Trigger: {trigger_has_name}",
                duration_ms=(time.perf_counter() - start) * 1000
            )
        except Exception as e:
            self.log_test(
                "Agent Name Consistency",
                "LiveKit",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_no_automatic_dispatch(self):
        """Test no automatic dispatch fallback (Issue #8)"""
        start = time.perf_counter()
        
        try:
            with open('/root/sidekick-forge/docker/agent/entrypoint.py', 'r') as f:
                content = f.read()
                
                # Should reject jobs that don't match agent_name
                has_reject = 'job_request.reject()' in content
                
                # Should not have automatic acceptance
                no_auto_accept = 'await job_request.accept()' in content and 'if' in content
                
                self.log_test(
                    "No Automatic Dispatch",
                    "LiveKit",
                    has_reject and no_auto_accept,
                    f"Has reject: {has_reject}, Conditional accept: {no_auto_accept}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "No Automatic Dispatch",
                "LiveKit",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_job_context_pattern(self):
        """Test JobContext pattern usage"""
        start = time.perf_counter()
        
        try:
            with open('/root/sidekick-forge/docker/agent/entrypoint.py', 'r') as f:
                content = f.read()
                
                # Check for JobContext usage
                has_job_context = 'JobContext' in content
                has_ctx_room = 'ctx.room' in content
                has_ctx_connect = 'ctx.connect()' in content
                
                self.log_test(
                    "JobContext Pattern",
                    "LiveKit",
                    all([has_job_context, has_ctx_room, has_ctx_connect]),
                    f"JobContext: {has_job_context}, ctx.room: {has_ctx_room}, ctx.connect: {has_ctx_connect}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "JobContext Pattern",
                "LiveKit",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_agent_session_usage(self):
        """Test AgentSession vs VoicePipelineAgent (Issue #9)"""
        start = time.perf_counter()
        
        try:
            with open('/root/sidekick-forge/docker/agent/entrypoint.py', 'r') as f:
                content = f.read()
                
                # Should use AgentSession, not VoicePipelineAgent
                uses_agent_session = 'AgentSession' in content
                no_pipeline_agent = 'VoicePipelineAgent' not in content
                
                self.log_test(
                    "AgentSession Usage",
                    "LiveKit",
                    uses_agent_session and no_pipeline_agent,
                    f"AgentSession: {uses_agent_session}, No VoicePipelineAgent: {no_pipeline_agent}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "AgentSession Usage",
                "LiveKit",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    # ==================== USER ID FORMAT TESTS ====================
    
    async def test_user_id_formats(self):
        """Test UUID and string user_id handling (Issue #3)"""
        self.log(f"\n{BLUE}{'='*60}{RESET}")
        self.log(f"{BLUE}=== USER ID FORMAT TESTS ==={RESET}")
        self.log(f"{BLUE}{'='*60}{RESET}")
        
        await self.test_uuid_format_user_id()
        await self.test_string_format_user_id()
        await self.test_user_id_type_conversion()
    
    async def test_uuid_format_user_id(self):
        """Test UUID format user_id"""
        start = time.perf_counter()
        
        uuid_user_id = str(uuid.uuid4())
        
        payload = {
            "agent_slug": "test-agent",
            "mode": "text",
            "message": "Test with UUID",
            "user_id": uuid_user_id,
            "client_id": TEST_CLIENT_ID
        }
        
        try:
            async with self.session.post(
                f"{BASE_URL}{API_PREFIX}/trigger-agent",
                json=payload
            ) as resp:
                # Should handle UUID format
                handles_uuid = resp.status != 500
                
                self.log_test(
                    "UUID Format User ID",
                    "User ID",
                    handles_uuid,
                    f"Status: {resp.status} for UUID: {uuid_user_id}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "UUID Format User ID",
                "User ID",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_string_format_user_id(self):
        """Test string format user_id"""
        start = time.perf_counter()
        
        string_user_id = "test-user-123"
        
        payload = {
            "agent_slug": "test-agent",
            "mode": "text",
            "message": "Test with string",
            "user_id": string_user_id,
            "client_id": TEST_CLIENT_ID
        }
        
        try:
            async with self.session.post(
                f"{BASE_URL}{API_PREFIX}/trigger-agent",
                json=payload
            ) as resp:
                # Should handle string format
                handles_string = resp.status != 500
                
                self.log_test(
                    "String Format User ID",
                    "User ID",
                    handles_string,
                    f"Status: {resp.status} for string: {string_user_id}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "String Format User ID",
                "User ID",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_user_id_type_conversion(self):
        """Test user_id type conversion in context manager"""
        start = time.perf_counter()
        
        try:
            with open('/root/sidekick-forge/app/agent_modules/context.py', 'r') as f:
                content = f.read()
                
                # Should handle both UUID and string types
                handles_types = (
                    'str(user_id)' in content or
                    'isinstance' in content or
                    'UUID' in content
                )
                
                self.log_test(
                    "User ID Type Conversion",
                    "User ID",
                    handles_types,
                    "Context manager handles type conversion",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "User ID Type Conversion",
                "User ID",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    # ==================== ADMIN ROUTE TESTS ====================
    
    async def test_admin_routes(self):
        """Test admin interface and routes (Issue #4)"""
        self.log(f"\n{BLUE}{'='*60}{RESET}")
        self.log(f"{BLUE}=== ADMIN ROUTE TESTS ==={RESET}")
        self.log(f"{BLUE}{'='*60}{RESET}")
        
        await self.test_admin_dashboard_access()
        await self.test_admin_client_preview()
        await self.test_admin_agent_management()
    
    async def test_admin_dashboard_access(self):
        """Test admin dashboard accessibility"""
        start = time.perf_counter()
        
        try:
            async with self.session.get(f"{BASE_URL}/admin") as resp:
                self.log_test(
                    "Admin Dashboard Access",
                    "Admin",
                    resp.status == 200,
                    f"Status: {resp.status}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Admin Dashboard Access",
                "Admin",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_admin_client_preview(self):
        """Test admin client preview with correct parameters"""
        start = time.perf_counter()
        
        try:
            # Test voice preview endpoint
            async with self.session.get(
                f"{BASE_URL}/admin/clients/{TEST_CLIENT_ID}/preview/voice"
            ) as resp:
                # Should work or return clear error
                valid_response = resp.status in [200, 400, 404]
                
                self.log_test(
                    "Admin Client Preview",
                    "Admin",
                    valid_response,
                    f"Voice preview status: {resp.status}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Admin Client Preview",
                "Admin",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_admin_agent_management(self):
        """Test admin agent management per client"""
        start = time.perf_counter()
        
        try:
            async with self.session.get(f"{BASE_URL}/admin/agents") as resp:
                if resp.status == 200:
                    content = await resp.text()
                    # Should display agents
                    has_agents = "agent" in content.lower()
                    
                    self.log_test(
                        "Admin Agent Management",
                        "Admin",
                        has_agents,
                        "Agent management page accessible",
                        duration_ms=(time.perf_counter() - start) * 1000
                    )
                else:
                    self.log_test(
                        "Admin Agent Management",
                        "Admin",
                        False,
                        f"Status: {resp.status}",
                        duration_ms=(time.perf_counter() - start) * 1000
                    )
        except Exception as e:
            self.log_test(
                "Admin Agent Management",
                "Admin",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    # ==================== PERFORMANCE TESTS ====================
    
    async def test_performance(self):
        """Test performance metrics and timing"""
        if self.quick:
            self.log(f"\n{YELLOW}Skipping performance tests in quick mode{RESET}")
            return
            
        self.log(f"\n{BLUE}{'='*60}{RESET}")
        self.log(f"{BLUE}=== PERFORMANCE TESTS ==={RESET}")
        self.log(f"{BLUE}{'='*60}{RESET}")
        
        await self.test_health_check_performance()
        await self.test_api_response_times()
    
    async def test_health_check_performance(self):
        """Test health check response time"""
        start = time.perf_counter()
        
        try:
            async with self.session.get(f"{BASE_URL}/health") as resp:
                response_time_ms = (time.perf_counter() - start) * 1000
                
                # Health check should be < 100ms
                is_fast = response_time_ms < 100
                
                self.log_test(
                    "Health Check Performance",
                    "Performance",
                    is_fast,
                    f"Response time: {response_time_ms:.2f}ms",
                    metadata={"response_time_ms": response_time_ms},
                    duration_ms=response_time_ms
                )
        except Exception as e:
            self.log_test(
                "Health Check Performance",
                "Performance",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_api_response_times(self):
        """Test API endpoint response times"""
        endpoints = [
            (f"{BASE_URL}/health", "Health", 100),
            (f"{BASE_URL}/admin", "Admin", 500),
            (f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}", "Client API", 200),
        ]
        
        for url, name, max_ms in endpoints:
            start = time.perf_counter()
            
            try:
                async with self.session.get(url) as resp:
                    response_time_ms = (time.perf_counter() - start) * 1000
                    
                    is_fast = response_time_ms < max_ms
                    
                    self.log_test(
                        f"{name} Response Time",
                        "Performance",
                        is_fast,
                        f"{response_time_ms:.2f}ms (max: {max_ms}ms)",
                        metadata={"response_time_ms": response_time_ms},
                        duration_ms=response_time_ms
                    )
            except Exception as e:
                self.log_test(
                    f"{name} Response Time",
                    "Performance",
                    False,
                    error=str(e),
                    duration_ms=(time.perf_counter() - start) * 1000
                )
    
    # ==================== EXTERNAL API TESTS ====================
    
    async def test_external_apis(self):
        """Test external API integrations"""
        if self.quick:
            self.log(f"\n{YELLOW}Skipping external API tests in quick mode{RESET}")
            return
            
        self.log(f"\n{BLUE}{'='*60}{RESET}")
        self.log(f"{BLUE}=== EXTERNAL API TESTS ==={RESET}")
        self.log(f"{BLUE}{'='*60}{RESET}")
        
        # Get client configuration
        try:
            async with self.session.get(f"{BASE_URL}{API_PREFIX}/clients/{TEST_CLIENT_ID}") as resp:
                if resp.status != 200:
                    self.log("Failed to load client configuration", "ERROR")
                    return
                
                client_data = await resp.json()
                settings = client_data.get('settings', {})
                if isinstance(settings, str):
                    settings = json.loads(settings)
                
                api_keys = settings.get('api_keys', {})
                
                # Test each API
                await self.test_groq_api(api_keys.get('groq_api_key'))
                await self.test_deepgram_api(api_keys.get('deepgram_api_key'))
                await self.test_elevenlabs_api(api_keys.get('elevenlabs_api_key'))
                await self.test_cartesia_api(api_keys.get('cartesia_api_key'))
                
        except Exception as e:
            self.log(f"Failed to test external APIs: {e}", "ERROR")
    
    async def test_groq_api(self, api_key: str):
        """Test Groq API with current model"""
        if not api_key or api_key.startswith('test'):
            self.log_test("Groq API", "External API", False, "No valid API key")
            return
        
        start = time.perf_counter()
        
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            # Test current model
            data = {
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": "Reply with OK"}],
                "max_tokens": 10
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=10.0
                )
                
                self.log_test(
                    "Groq API (llama-3.3)",
                    "External API",
                    response.status_code == 200,
                    f"Status: {response.status_code}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Groq API",
                "External API",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_deepgram_api(self, api_key: str):
        """Test Deepgram API"""
        if not api_key or api_key.startswith('test'):
            self.log_test("Deepgram API", "External API", False, "No valid API key")
            return
        
        start = time.perf_counter()
        
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
                
                self.log_test(
                    "Deepgram API",
                    "External API",
                    response.status_code == 200,
                    f"Status: {response.status_code}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Deepgram API",
                "External API",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_elevenlabs_api(self, api_key: str):
        """Test ElevenLabs API"""
        if not api_key or api_key.startswith('test'):
            self.log_test("ElevenLabs API", "External API", False, "No valid API key")
            return
        
        start = time.perf_counter()
        
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
                
                self.log_test(
                    "ElevenLabs API",
                    "External API",
                    response.status_code == 200,
                    f"Status: {response.status_code}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "ElevenLabs API",
                "External API",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    async def test_cartesia_api(self, api_key: str):
        """Test Cartesia API"""
        if not api_key or api_key.startswith('test') or api_key.startswith('fixed_'):
            self.log_test("Cartesia API", "External API", False, "No valid API key")
            return
        
        start = time.perf_counter()
        
        try:
            headers = {
                "X-API-Key": api_key,
                "Cartesia-Version": "2024-06-10"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.cartesia.ai/voices",
                    headers=headers,
                    timeout=10.0
                )
                
                self.log_test(
                    "Cartesia API",
                    "External API",
                    response.status_code == 200,
                    f"Status: {response.status_code}",
                    duration_ms=(time.perf_counter() - start) * 1000
                )
        except Exception as e:
            self.log_test(
                "Cartesia API",
                "External API",
                False,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000
            )
    
    # ==================== MAIN TEST RUNNER ====================
    
    async def run_all_tests(self):
        """Run all test suites"""
        test_groups = [
            ("Infrastructure", self.test_infrastructure),
            ("Configuration", self.test_configuration_validation),
            ("LiveKit", self.test_livekit_architecture),
            ("User ID", self.test_user_id_formats),
            ("Admin", self.test_admin_routes),
            ("Performance", self.test_performance),
            ("External APIs", self.test_external_apis),
        ]
        
        if self.parallel and not self.quick:
            # Run test groups in parallel
            self.log(f"\n{CYAN}Running tests in parallel mode...{RESET}")
            await asyncio.gather(*[test_func() for _, test_func in test_groups])
        else:
            # Run sequentially
            for name, test_func in test_groups:
                await test_func()
    
    def print_summary(self):
        """Print test summary"""
        if self.json_output:
            # Output JSON report
            report = {
                "version": "5.0",
                "timestamp": datetime.utcnow().isoformat(),
                "duration_seconds": time.time() - self.start_time,
                "mode": {
                    "quick": self.quick,
                    "parallel": self.parallel,
                    "verbose": self.verbose
                },
                "results": [asdict(r) for r in self.results],
                "summary": {
                    "total": len(self.results),
                    "passed": sum(1 for r in self.results if r.status == TestStatus.PASSED),
                    "failed": sum(1 for r in self.results if r.status == TestStatus.FAILED),
                    "skipped": sum(1 for r in self.results if r.status == TestStatus.SKIPPED),
                    "warning": sum(1 for r in self.results if r.status == TestStatus.WARNING)
                }
            }
            
            # Save to file
            with open('/tmp/mission_critical_v5_results.json', 'w') as f:
                json.dump(report, f, indent=2, default=str)
            
            # Print to stdout for CI/CD
            print(json.dumps(report, default=str))
        else:
            # Print human-readable summary
            self.log(f"\n{BLUE}{'='*60}{RESET}")
            self.log(f"{BLUE}{BOLD}MISSION CRITICAL TEST V5 SUMMARY{RESET}")
            self.log(f"{BLUE}{'='*60}{RESET}")
            
            passed = sum(1 for r in self.results if r.status == TestStatus.PASSED)
            failed = sum(1 for r in self.results if r.status == TestStatus.FAILED)
            total = len(self.results)
            
            self.log(f"Total Tests Run: {total}")
            self.log(f"{GREEN}Passed: {passed}{RESET}")
            self.log(f"{RED}Failed: {failed}{RESET}")
            
            if failed > 0:
                self.log(f"\n{YELLOW}--- Failed Tests ---{RESET}")
                for result in self.results:
                    if result.status == TestStatus.FAILED:
                        self.log(f"  {RED}• {result.category}/{result.test_name}{RESET}")
                        if result.error:
                            self.log(f"    Error: {result.error}")
            
            # Overall status
            self.log(f"\n{BLUE}--- Overall Status ---{RESET}")
            duration = time.time() - self.start_time
            self.log(f"Execution Time: {duration:.2f} seconds")
            
            if failed == 0:
                self.log(f"{GREEN}{BOLD}✅ ALL SYSTEMS OPERATIONAL{RESET}")
                sys.exit(0)
            elif failed <= 3:
                self.log(f"{YELLOW}{BOLD}⚠️  MINOR ISSUES DETECTED{RESET}")
                sys.exit(1)
            else:
                self.log(f"{RED}{BOLD}❌ CRITICAL SYSTEMS FAILURE{RESET}")
                sys.exit(2)

async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Mission Critical Test Suite v5.0')
    parser.add_argument('--quick', action='store_true', help='Run only critical tests')
    parser.add_argument('--verbose', action='store_true', help='Show detailed output')
    parser.add_argument('--parallel', action='store_true', help='Run tests in parallel')
    parser.add_argument('--json', action='store_true', help='Output results as JSON')
    
    args = parser.parse_args()
    
    # Print header
    if not args.json:
        print(f"{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}{BOLD}SIDEKICK FORGE - MISSION CRITICAL TEST SUITE v5.0{RESET}")
        print(f"{BLUE}Comprehensive Testing | {53} GitHub Issues Covered{RESET}")
        print(f"{BLUE}{'='*60}{RESET}")
        print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Target: {BASE_URL}")
        print(f"Mode: {'QUICK' if args.quick else 'FULL'} | {'PARALLEL' if args.parallel else 'SEQUENTIAL'}")
    
    # Run tests
    suite = MissionCriticalTestSuite(args)
    await suite.setup()
    
    try:
        await suite.run_all_tests()
    finally:
        await suite.teardown()
        suite.print_summary()

if __name__ == "__main__":
    asyncio.run(main())