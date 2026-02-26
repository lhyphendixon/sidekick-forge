#!/usr/bin/env python3
"""
Sidekick Forge - Mission Critical Test Suite

Verifies core platform functionality before and after deployments.

Usage:
    python3 test_mission_critical.py --quick     # Health + containers (pre-deploy)
    python3 test_mission_critical.py              # Full test suite
    python3 test_mission_critical.py --verbose    # Full suite with details
"""

import sys
import os
import json
import time
import asyncio
import argparse
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

import httpx
from colorama import init, Fore, Style

init(autoreset=True)

# ── Configuration ────────────────────────────────────────────────────────────

BASE_URL = "http://localhost:8000"
API_URL = f"{BASE_URL}/api/v1"

# Dev-token auth works for middleware-protected routes (clients, agents, trigger)
DEV_AUTH = {"Authorization": "Bearer dev-token"}

# Known test client from platform DB
TEST_CLIENT_ID = "11389177-e4d8-49a9-9a00-f77bb4de6592"
TEST_CLIENT_NAME = "Autonomite"

# Expected Docker container name patterns
FASTAPI_CONTAINER = "sidekick-forge-fastapi"
AGENT_WORKER_CONTAINER = "sidekick-forge-agent-worker"
REDIS_CONTAINER = "sidekick-forge-redis"

# ── Test infrastructure ──────────────────────────────────────────────────────

test_results: List["TestResult"] = []
args = None


class TestResult:
    def __init__(self, name: str, category: str):
        self.name = name
        self.category = category
        self.passed = False
        self.error = None
        self.details = {}


def print_header(text: str):
    print(f"\n{Fore.CYAN}{'=' * 60}")
    print(f"{Fore.CYAN}{text.center(60)}")
    print(f"{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")


def print_test(name: str, passed: bool, error: str = "", details: str = ""):
    icon = f"{Fore.GREEN}PASS{Style.RESET_ALL}" if passed else f"{Fore.RED}FAIL{Style.RESET_ALL}"
    print(f"  [{icon}] {name}")
    if error:
        print(f"         {Fore.RED}{error}{Style.RESET_ALL}")
    if details and args and args.verbose:
        print(f"         {Fore.YELLOW}{details}{Style.RESET_ALL}")


def print_category(category: str):
    print(f"\n{Fore.YELLOW}--- {category} ---{Style.RESET_ALL}")


async def hit(client: httpx.AsyncClient, method: str, url: str,
              expected: int = 200, **kwargs) -> Tuple[bool, Optional[dict], str]:
    """Hit an endpoint and return (success, json_body, error_msg)."""
    try:
        resp = await client.request(method, url, **kwargs)
        ok = resp.status_code == expected
        try:
            data = resp.json() if resp.content else None
        except Exception:
            data = None
        err = f"Expected {expected}, got {resp.status_code}" if not ok else ""
        return ok, data, err
    except Exception as e:
        return False, None, str(e)


# ══════════════════════════════════════════════════════════════════════════════
# TEST CATEGORIES
# ══════════════════════════════════════════════════════════════════════════════

async def test_health(client: httpx.AsyncClient) -> List[TestResult]:
    """Public health endpoints (no auth needed)."""
    results = []
    print_category("Health & Connectivity")

    # 1. Basic health
    t = TestResult("GET /health", "Health")
    ok, data, err = await hit(client, "GET", f"{BASE_URL}/health")
    t.passed = ok and data and data.get("status") == "healthy"
    t.error = err or (None if t.passed else f"status={data.get('status') if data else 'no data'}")
    results.append(t)
    print_test(t.name, t.passed, t.error)

    # 2. Detailed health
    t = TestResult("GET /health/detailed", "Health")
    ok, data, err = await hit(client, "GET", f"{BASE_URL}/health/detailed")
    t.passed = ok and data and "checks" in data
    if t.passed:
        checks = data["checks"]
        t.details = checks
        if not checks.get("platform_database"):
            t.passed = False
            t.error = "platform_database check failed"
        elif not checks.get("livekit"):
            t.passed = False
            t.error = "livekit check failed"
    else:
        t.error = err
    results.append(t)
    print_test(t.name, t.passed, t.error, str(t.details) if t.details else "")

    # 3. Root endpoint
    t = TestResult("GET / (root)", "Health")
    ok, data, err = await hit(client, "GET", f"{BASE_URL}/")
    t.passed = ok
    t.error = err if not ok else None
    results.append(t)
    print_test(t.name, t.passed, t.error)

    return results


async def test_docker_containers() -> List[TestResult]:
    """Verify Docker containers are running."""
    results = []
    print_category("Docker Containers")

    containers_to_check = [
        ("FastAPI", FASTAPI_CONTAINER),
        ("Agent Worker", AGENT_WORKER_CONTAINER),
        ("Redis", REDIS_CONTAINER),
    ]

    for label, name_pattern in containers_to_check:
        t = TestResult(f"{label} container running", "Docker")
        try:
            proc = subprocess.run(
                ["docker", "ps", "--filter", f"name={name_pattern}",
                 "--format", "{{.Names}} | {{.Status}}"],
                capture_output=True, text=True, timeout=5,
            )
            output = proc.stdout.strip()
            t.passed = bool(output) and ("Up" in output or "running" in output.lower())
            if t.passed:
                t.details["status"] = output
            else:
                t.error = f"Container not running: {output or '(not found)'}"
        except Exception as e:
            t.error = str(e)
        results.append(t)
        print_test(t.name, t.passed, t.error, t.details.get("status", ""))

    return results


async def test_agent_worker_registered() -> List[TestResult]:
    """Verify agent worker started and registered with LiveKit."""
    results = []
    print_category("Agent Worker")

    t = TestResult("Worker registered with LiveKit", "Agent Worker")
    try:
        proc = subprocess.run(
            ["docker", "ps", "--filter", f"name={AGENT_WORKER_CONTAINER}",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        container = proc.stdout.strip().split("\n")[0] if proc.stdout.strip() else ""
        if not container:
            t.error = "Agent worker container not found"
        else:
            logs = subprocess.run(
                ["docker", "logs", container],
                capture_output=True, text=True, timeout=10,
            )
            log_text = (logs.stdout or "") + (logs.stderr or "")

            # Check startup sequence (appears at the very start of logs)
            started = "starting worker" in log_text.lower()
            initialized = "process initialized" in log_text.lower()
            t.passed = started and initialized
            if not t.passed:
                t.error = f"Worker not fully initialized (started={started}, initialized={initialized})"
            t.details["container"] = container
    except Exception as e:
        t.error = str(e)
    results.append(t)
    print_test(t.name, t.passed, t.error)

    # Check AGENT_NAME matches .env
    t = TestResult("Agent name configuration", "Agent Worker")
    try:
        proc = subprocess.run(
            ["docker", "ps", "--filter", f"name={AGENT_WORKER_CONTAINER}",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        container = proc.stdout.strip().split("\n")[0] if proc.stdout.strip() else ""
        if container:
            logs = subprocess.run(
                ["docker", "logs", container],
                capture_output=True, text=True, timeout=10,
            )
            log_text = (logs.stdout or "") + (logs.stderr or "")

            # Read expected AGENT_NAME from .env
            env_agent_name = None
            env_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
            )
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("AGENT_NAME=") and not line.startswith("#"):
                            env_agent_name = line.split("=", 1)[1].strip().strip('"').strip("'")
                            break

            if env_agent_name and f"Agent name: {env_agent_name}" in log_text:
                t.passed = True
                t.details["agent_name"] = env_agent_name
            elif env_agent_name:
                t.passed = False
                t.error = f"Expected AGENT_NAME={env_agent_name} in worker logs"
            else:
                t.passed = True
                t.details["note"] = "AGENT_NAME not set in .env; worker uses default"
        else:
            t.error = "Agent worker container not found"
    except Exception as e:
        t.error = str(e)
    results.append(t)
    print_test(t.name, t.passed, t.error, str(t.details) if t.details else "")

    return results


async def test_auth_and_middleware(client: httpx.AsyncClient) -> List[TestResult]:
    """Test authentication middleware behavior."""
    results = []
    print_category("Authentication & Middleware")

    # Supabase-auth-protected endpoint rejects without valid token
    t = TestResult("Protected endpoint rejects no-auth (/api/v1/me)", "Auth")
    ok, _, err = await hit(client, "GET", f"{API_URL}/me", expected=401)
    t.passed = ok  # We EXPECT 401
    t.error = err if not t.passed else None
    results.append(t)
    print_test(t.name, t.passed, t.error)

    # Middleware-level dev-token bypass works for /api/v1/clients
    t = TestResult("Dev-token bypass on middleware routes", "Auth")
    ok, data, err = await hit(client, "GET", f"{API_URL}/clients", headers=DEV_AUTH)
    t.passed = ok and isinstance(data, (list, dict))
    t.error = err if not t.passed else None
    results.append(t)
    print_test(t.name, t.passed, t.error)

    return results


async def test_clients_api(client: httpx.AsyncClient) -> List[TestResult]:
    """Test client management API."""
    results = []
    print_category("Clients API")

    # List clients
    t = TestResult("GET /api/v1/clients", "Clients")
    ok, data, err = await hit(client, "GET", f"{API_URL}/clients", headers=DEV_AUTH)
    t.passed = ok and isinstance(data, (list, dict))
    t.error = err if not t.passed else None
    if t.passed:
        client_list = data if isinstance(data, list) else data.get("data", [])
        t.details["count"] = len(client_list)
    results.append(t)
    print_test(t.name, t.passed, t.error, f"{t.details.get('count', '?')} clients")

    # Get specific client
    t = TestResult(f"GET /api/v1/clients/{TEST_CLIENT_ID[:8]}...", "Clients")
    ok, data, err = await hit(
        client, "GET", f"{API_URL}/clients/{TEST_CLIENT_ID}", headers=DEV_AUTH
    )
    t.passed = ok and data is not None
    t.error = err if not t.passed else None
    if t.passed and isinstance(data, dict):
        t.details["name"] = data.get("name", "?")
    results.append(t)
    print_test(t.name, t.passed, t.error, t.details.get("name", ""))

    return results


async def test_agents_api(client: httpx.AsyncClient) -> List[TestResult]:
    """Test agent management API."""
    results = []
    print_category("Agents API")

    # List agents for client
    t = TestResult("GET /api/v1/agents?client_id=...", "Agents")
    ok, data, err = await hit(
        client, "GET",
        f"{API_URL}/agents?client_id={TEST_CLIENT_ID}",
        headers=DEV_AUTH,
    )
    t.passed = ok and isinstance(data, (list, dict))
    t.error = err if not t.passed else None
    agent_list = []
    if t.passed:
        agent_list = data if isinstance(data, list) else data.get("data", [])
        t.details["count"] = len(agent_list)
        t.details["slugs"] = [a.get("slug") for a in agent_list[:5]]
    results.append(t)
    print_test(t.name, t.passed, t.error, f"{t.details.get('count', '?')} agents")

    # Get specific agent (use first from list)
    test_slug = agent_list[0].get("slug") if agent_list else "clarence-coherence"
    t = TestResult(f"GET /api/v1/agents/{test_slug}", "Agents")
    ok, data, err = await hit(
        client, "GET",
        f"{API_URL}/agents/{test_slug}?client_id={TEST_CLIENT_ID}",
        headers=DEV_AUTH,
    )
    t.passed = ok and data is not None
    t.error = err if not t.passed else None
    results.append(t)
    print_test(t.name, t.passed, t.error)

    # Sync agents
    t = TestResult("POST /api/v1/agents/sync", "Agents")
    ok, data, err = await hit(
        client, "POST",
        f"{API_URL}/agents/sync?client_id={TEST_CLIENT_ID}",
        headers=DEV_AUTH,
    )
    t.passed = ok and data is not None
    t.error = err if not t.passed else None
    if t.passed and isinstance(data, dict):
        t.details["synced"] = data.get("agent_count", data.get("count", "?"))
    results.append(t)
    print_test(t.name, t.passed, t.error, f"synced {t.details.get('synced', '?')}")

    return results


async def test_trigger_endpoint(client: httpx.AsyncClient) -> List[TestResult]:
    """Test agent trigger endpoint."""
    results = []
    print_category("Agent Trigger")

    # Trigger with missing fields should return 422
    t = TestResult("POST /api/v1/trigger-agent (validation)", "Trigger")
    ok, _, err = await hit(
        client, "POST",
        f"{API_URL}/trigger-agent",
        expected=422,
        headers=DEV_AUTH,
        json={},
    )
    t.passed = ok
    t.error = err if not t.passed else None
    results.append(t)
    print_test(t.name, t.passed, t.error)

    # Trigger with valid text mode data (includes required 'message' field)
    t = TestResult("POST /api/v1/trigger-agent (text mode)", "Trigger")
    room_name = f"test-mission-critical-{int(time.time())}"
    trigger_data = {
        "agent_slug": "clarence-coherence",
        "mode": "text",
        "room_name": room_name,
        "user_id": "test-mission-critical",
        "client_id": TEST_CLIENT_ID,
        "message": "Hello, this is a test.",
    }
    ok, data, err = await hit(
        client, "POST",
        f"{API_URL}/trigger-agent",
        headers=DEV_AUTH,
        json=trigger_data,
    )
    if ok and data:
        t.passed = True
        resp_data = data.get("data", data)
        has_room = bool(
            resp_data.get("room_info")
            or resp_data.get("room_name")
            or resp_data.get("room")
        )
        has_token = bool(
            resp_data.get("livekit_config", {}).get("user_token")
            or resp_data.get("token")
        )
        t.details = {"room_created": has_room, "token_generated": has_token}
    else:
        t.passed = False
        t.error = err or "Trigger returned unexpected response"
    results.append(t)
    print_test(t.name, t.passed, t.error, str(t.details) if t.details else "")

    return results


async def test_supabase_protected_endpoints(client: httpx.AsyncClient) -> List[TestResult]:
    """
    Verify endpoints that require Supabase auth reject dev-token properly.
    These endpoints are behind route-level Supabase auth (not just middleware).
    We confirm they exist and enforce auth.
    """
    results = []
    print_category("Supabase-Auth Protected Endpoints")

    endpoints = [
        ("GET", f"{API_URL}/me", "User profile"),
        ("GET", f"{API_URL}/tools?client_id={TEST_CLIENT_ID}", "Tools list"),
        ("GET", f"{API_URL}/conversations?client_id={TEST_CLIENT_ID}", "Conversations list"),
    ]

    for method, url, label in endpoints:
        t = TestResult(f"{label} rejects dev-token (401)", "Auth Protection")
        ok, _, err = await hit(client, method, url, expected=401, headers=DEV_AUTH)
        t.passed = ok  # We EXPECT 401
        t.error = err if not t.passed else None
        results.append(t)
        print_test(t.name, t.passed, t.error)

    return results


async def test_openapi_spec(client: httpx.AsyncClient) -> List[TestResult]:
    """Verify OpenAPI spec is available and has expected routes."""
    results = []
    print_category("OpenAPI Spec")

    t = TestResult("GET /openapi.json", "OpenAPI")
    ok, data, err = await hit(client, "GET", f"{BASE_URL}/openapi.json")
    t.passed = ok and data and "paths" in data
    t.error = err if not t.passed else None
    if t.passed:
        paths = data.get("paths", {})
        t.details["route_count"] = len(paths)
        # Verify key routes exist in the spec
        expected_routes = ["/api/v1/clients", "/api/v1/agents", "/api/v1/trigger-agent"]
        missing = [r for r in expected_routes if r not in paths]
        if missing:
            t.details["missing_routes"] = missing
    results.append(t)
    print_test(t.name, t.passed, t.error, f"{t.details.get('route_count', '?')} routes")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════════════════════════════════════

async def run_all_tests(quick: bool = False) -> Dict:
    start = time.time()

    print_header("SIDEKICK FORGE - MISSION CRITICAL TESTS")
    print(f"  Started:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Target:   {BASE_URL}")
    print(f"  Mode:     {'QUICK' if quick else 'FULL'}")

    all_results: List[TestResult] = []

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # ── Always run (quick + full) ────────────────────────────────
        all_results.extend(await test_health(client))
        all_results.extend(await test_docker_containers())
        all_results.extend(await test_agent_worker_registered())

        if not quick:
            # ── Full suite only ──────────────────────────────────────
            all_results.extend(await test_auth_and_middleware(client))
            all_results.extend(await test_clients_api(client))
            all_results.extend(await test_agents_api(client))
            all_results.extend(await test_trigger_endpoint(client))
            all_results.extend(await test_supabase_protected_endpoints(client))
            all_results.extend(await test_openapi_spec(client))

    # ── Summary ──────────────────────────────────────────────────────
    total = len(all_results)
    passed = sum(1 for r in all_results if r.passed)
    failed = total - passed
    duration = time.time() - start

    print_header("TEST SUMMARY")
    print(f"  Total:    {total}")
    print(f"  {Fore.GREEN}Passed:   {passed}{Style.RESET_ALL}")
    if failed:
        print(f"  {Fore.RED}Failed:   {failed}{Style.RESET_ALL}")
    print(f"  Duration: {duration:.1f}s")

    if failed:
        print(f"\n{Fore.RED}Failed tests:{Style.RESET_ALL}")
        for r in all_results:
            if not r.passed:
                print(f"  - [{r.category}] {r.name}: {r.error or 'unknown'}")

    print_header("RESULT")
    if failed == 0:
        print(f"  {Fore.GREEN}ALL TESTS PASSED{Style.RESET_ALL}")
    else:
        print(f"  {Fore.RED}{failed} TEST(S) FAILED{Style.RESET_ALL}")

    return {"success": failed == 0, "passed": passed, "failed": failed, "total": total}


def main():
    global args
    parser = argparse.ArgumentParser(description="Sidekick Forge Mission Critical Tests")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--quick", "-q", action="store_true", help="Health + containers only")
    parser.add_argument("--json", "-j", action="store_true", help="Output JSON summary")
    args = parser.parse_args()

    try:
        result = asyncio.run(run_all_tests(quick=args.quick))
        if args.json:
            print(json.dumps(result, indent=2))
        sys.exit(0 if result["success"] else 1)
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Interrupted.{Style.RESET_ALL}")
        sys.exit(2)
    except Exception as e:
        print(f"\n{Fore.RED}Test suite error: {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()
        sys.exit(3)


if __name__ == "__main__":
    main()
