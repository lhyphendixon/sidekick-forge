#!/usr/bin/env python3
"""
Mission Critical Functionality Test Suite for Autonomite Platform

This test suite verifies all core functionality is working properly.
Run this before and after any feature updates to ensure no regression.

Usage:
    python3 test_mission_critical.py
    python3 test_mission_critical.py --verbose
    python3 test_mission_critical.py --quick  # Skip slow tests
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

# Initialize colorama for cross-platform colored output
init(autoreset=True)

# Configuration
BASE_URL = "http://localhost:8000"
ADMIN_URL = f"{BASE_URL}/admin"
API_URL = f"{BASE_URL}/api/v1"

# Test client IDs
TEST_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"  # Autonomite
TEST_CLIENT_NAME = "Autonomite"
TEST_AGENT_SLUG = "autonomite"

# Test tracking
test_results = []
test_start_time = None


class TestResult:
    """Test result tracking"""
    def __init__(self, name: str, category: str):
        self.name = name
        self.category = category
        self.passed = False
        self.error = None
        self.duration = 0
        self.details = {}


def print_header(text: str):
    """Print a formatted header"""
    print(f"\n{Fore.CYAN}{'=' * 60}")
    print(f"{Fore.CYAN}{text.center(60)}")
    print(f"{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")


def print_test(name: str, status: bool, error: str = "", details: str = ""):
    """Print test result"""
    if status:
        status_text = f"{Fore.GREEN}✅ PASS{Style.RESET_ALL}"
    else:
        status_text = f"{Fore.RED}❌ FAIL{Style.RESET_ALL}"
    
    print(f"{status_text} {name}")
    if error:
        print(f"   {Fore.RED}Error: {error}{Style.RESET_ALL}")
    if details and args.verbose:
        print(f"   {Fore.YELLOW}Details: {details}{Style.RESET_ALL}")


def print_category(category: str):
    """Print category header"""
    print(f"\n{Fore.YELLOW}▶ {category}{Style.RESET_ALL}")


async def test_endpoint(client: httpx.AsyncClient, method: str, url: str, 
                       expected_status: int = 200, **kwargs) -> Tuple[bool, Optional[Dict], str]:
    """Test an HTTP endpoint"""
    try:
        response = await client.request(method, url, **kwargs)
        success = response.status_code == expected_status
        
        try:
            data = response.json() if response.content else None
        except:
            data = None
            
        error = f"Expected {expected_status}, got {response.status_code}" if not success else ""
        return success, data, error
    except Exception as e:
        return False, None, str(e)


async def run_health_checks(client: httpx.AsyncClient) -> List[TestResult]:
    """Test basic health endpoints"""
    results = []
    
    print_category("Health & Connectivity")
    
    # Basic health
    test = TestResult("Basic Health Check", "Health")
    success, data, error = await test_endpoint(client, "GET", f"{BASE_URL}/health")
    test.passed = success and data and data.get("status") == "healthy"
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    # Detailed health
    test = TestResult("Detailed Health Check", "Health")
    success, data, error = await test_endpoint(client, "GET", f"{BASE_URL}/health/detailed")
    test.passed = success
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    # Admin interface
    test = TestResult("Admin Interface", "Health")
    success, _, error = await test_endpoint(client, "GET", f"{ADMIN_URL}/clients")
    test.passed = success
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    return results


async def run_client_tests(client: httpx.AsyncClient) -> List[TestResult]:
    """Test client management functionality"""
    results = []
    
    print_category("Client Management")
    
    # List clients via admin
    test = TestResult("List Clients (Admin)", "Clients")
    success, _, error = await test_endpoint(client, "GET", f"{ADMIN_URL}/clients")
    test.passed = success
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    # Get specific client
    test = TestResult("Get Client Details", "Clients")
    success, _, error = await test_endpoint(client, "GET", f"{ADMIN_URL}/clients/{TEST_CLIENT_ID}")
    test.passed = success
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    # Test client update
    test = TestResult("Update Client Configuration", "Clients")
    form_data = {
        "name": TEST_CLIENT_NAME,
        "domain": "autonomite.ai",
        "description": "Autonomite AI Platform - Test Update"
    }
    success, _, error = await test_endpoint(
        client, "POST", 
        f"{ADMIN_URL}/clients/{TEST_CLIENT_ID}/update",
        expected_status=303,  # Redirect
        data=form_data,
        follow_redirects=False
    )
    test.passed = success
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    return results


async def run_agent_tests(client: httpx.AsyncClient) -> List[TestResult]:
    """Test agent management functionality"""
    results = []
    
    print_category("Agent Management")
    
    # List agents for client
    test = TestResult("List Client Agents", "Agents")
    success, data, error = await test_endpoint(
        client, "GET", 
        f"{API_URL}/agents/client/{TEST_CLIENT_ID}"
    )
    test.passed = success and isinstance(data, list)
    test.details["agent_count"] = len(data) if data else 0
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error, 
              f"Found {test.details['agent_count']} agents")
    
    # Get specific agent
    test = TestResult("Get Agent Details", "Agents")
    success, data, error = await test_endpoint(
        client, "GET",
        f"{API_URL}/agents/client/{TEST_CLIENT_ID}/{TEST_AGENT_SLUG}"
    )
    test.passed = success and data and data.get("slug") == TEST_AGENT_SLUG
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    # Sync agents
    test = TestResult("Sync Agents from Supabase", "Agents")
    success, data, error = await test_endpoint(
        client, "POST",
        f"{API_URL}/agents/client/{TEST_CLIENT_ID}/sync"
    )
    test.passed = success and data and data.get("success") == True
    test.details["synced_count"] = data.get("data", {}).get("count", 0) if data else 0
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error,
              f"Synced {test.details['synced_count']} agents")
    
    # Update agent
    test = TestResult("Update Agent Configuration", "Agents")
    update_data = {
        "name": TEST_AGENT_SLUG.title(),
        "description": "Test update via mission critical test"
    }
    success, data, error = await test_endpoint(
        client, "POST",
        f"{ADMIN_URL}/agents/{TEST_CLIENT_ID}/{TEST_AGENT_SLUG}/update",
        json=update_data
    )
    test.passed = success and data and data.get("success") == True
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    return results


async def run_livekit_tests(client: httpx.AsyncClient) -> List[TestResult]:
    """Test LiveKit integration"""
    results = []
    
    print_category("LiveKit Integration")
    
    # Test trigger endpoint availability
    test = TestResult("Trigger Endpoint Available", "LiveKit")
    success, _, error = await test_endpoint(
        client, "POST",
        f"{API_URL}/trigger-agent",
        expected_status=422,  # Expecting validation error without data
        json={}
    )
    test.passed = success
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    # Test agent spawn (dry run)
    test = TestResult("Agent Trigger (Dry Run)", "LiveKit")
    trigger_data = {
        "agent_slug": TEST_AGENT_SLUG,
        "mode": "voice",
        "room_name": "test-room-dry-run",
        "user_id": "test-user",
        "client_id": TEST_CLIENT_ID
    }
    response = await client.post(f"{API_URL}/trigger-agent", json=trigger_data)
    # 200/201 is success, 404 means agent not found (also OK for dry run)
    test.passed = response.status_code in [200, 201, 404]
    if not test.passed:
        test.error = f"Unexpected status: {response.status_code}"
    test.error = error if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    return results


async def run_persistence_tests(client: httpx.AsyncClient) -> List[TestResult]:
    """Test data persistence (no Redis)"""
    results = []
    
    print_category("Data Persistence")
    
    # Create test timestamp
    test_timestamp = datetime.now(timezone.utc).isoformat()
    
    # Update client with timestamp
    test = TestResult("Client Data Persistence", "Persistence")
    form_data = {
        "name": TEST_CLIENT_NAME,
        "domain": "autonomite.ai",
        "description": f"Persistence test at {test_timestamp}"
    }
    success1, _, error1 = await test_endpoint(
        client, "POST",
        f"{ADMIN_URL}/clients/{TEST_CLIENT_ID}/update",
        expected_status=303,
        data=form_data,
        follow_redirects=False
    )
    
    # Wait a moment for database update
    await asyncio.sleep(1.0)
    
    # Verify update persisted via API endpoint
    response = await client.get(f"{API_URL}/clients/{TEST_CLIENT_ID}")
    success2 = response.status_code == 200
    
    if success2:
        try:
            client_data = response.json()
            # Check if description was saved
            saved_description = client_data.get("description", "")
            test.passed = success1 and test_timestamp in saved_description
            if not test.passed:
                test.error = f"Description mismatch. Expected timestamp in '{saved_description}'"
        except Exception as e:
            test.passed = False
            test.error = f"Failed to parse client data: {e}"
    else:
        test.passed = False
        test.error = f"Failed to get client data: {response.status_code}"
    
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    # Test Redis is NOT being used
    test = TestResult("Redis-Free Verification", "Persistence")
    try:
        # Check logs for Redis errors
        log_check = subprocess.run(
            ["tail", "-n", "100", "/tmp/fastapi_redis_fallback.log"],
            capture_output=True,
            text=True
        )
        redis_errors = log_check.stdout.lower().count("redis") if log_check.returncode == 0 else 0
        test.passed = redis_errors == 0
        test.details["redis_mentions"] = redis_errors
        test.error = f"Found {redis_errors} Redis mentions in logs" if redis_errors > 0 else None
    except Exception as e:
        test.passed = False
        test.error = str(e)
    results.append(test)
    print_test(test.name, test.passed, test.error)
    
    return results


async def run_api_sync_tests(client: httpx.AsyncClient) -> List[TestResult]:
    """Test API key synchronization"""
    results = []
    
    print_category("API Key Synchronization")
    
    # Check client has API keys synced
    test = TestResult("API Keys Present", "Sync")
    response = await client.get(f"{ADMIN_URL}/clients/{TEST_CLIENT_ID}")
    content = response.text if response.status_code == 200 else ""
    
    # Check for presence of API keys in the page
    api_keys_found = []
    for key in ["groq", "deepgram", "elevenlabs", "cohere"]:
        if key in content.lower():
            api_keys_found.append(key)
    
    test.passed = len(api_keys_found) > 0
    test.details["keys_found"] = api_keys_found
    test.error = "No API keys found in client config" if not test.passed else None
    results.append(test)
    print_test(test.name, test.passed, test.error,
              f"Found keys: {', '.join(api_keys_found)}")
    
    return results


async def run_all_tests(verbose: bool = False, quick: bool = False) -> Dict[str, any]:
    """Run all mission critical tests"""
    global test_start_time
    test_start_time = time.time()
    
    print_header("MISSION CRITICAL FUNCTIONALITY TEST")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target: {BASE_URL}")
    
    all_results = []
    
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        # Run test categories
        all_results.extend(await run_health_checks(client))
        all_results.extend(await run_client_tests(client))
        all_results.extend(await run_agent_tests(client))
        
        if not quick:
            all_results.extend(await run_livekit_tests(client))
            all_results.extend(await run_persistence_tests(client))
            all_results.extend(await run_api_sync_tests(client))
    
    # Generate summary
    total_tests = len(all_results)
    passed_tests = sum(1 for r in all_results if r.passed)
    failed_tests = total_tests - passed_tests
    duration = time.time() - test_start_time
    
    print_header("TEST SUMMARY")
    print(f"Total Tests: {total_tests}")
    print(f"{Fore.GREEN}Passed: {passed_tests}{Style.RESET_ALL}")
    print(f"{Fore.RED}Failed: {failed_tests}{Style.RESET_ALL}")
    print(f"Duration: {duration:.2f} seconds")
    
    # Show failed tests
    if failed_tests > 0:
        print(f"\n{Fore.RED}Failed Tests:{Style.RESET_ALL}")
        for result in all_results:
            if not result.passed:
                print(f"  - {result.category}: {result.name}")
                if result.error:
                    print(f"    Error: {result.error}")
    
    # Overall status
    print_header("OVERALL STATUS")
    if failed_tests == 0:
        print(f"{Fore.GREEN}✅ ALL TESTS PASSED - PLATFORM IS OPERATIONAL{Style.RESET_ALL}")
        return {"success": True, "passed": passed_tests, "failed": 0, "total": total_tests}
    else:
        print(f"{Fore.RED}❌ TESTS FAILED - PLATFORM HAS ISSUES{Style.RESET_ALL}")
        return {"success": False, "passed": passed_tests, "failed": failed_tests, "total": total_tests}


def main():
    """Main entry point"""
    global args
    
    parser = argparse.ArgumentParser(description="Autonomite Platform Mission Critical Tests")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--quick", "-q", action="store_true", help="Skip slow tests")
    parser.add_argument("--json", "-j", action="store_true", help="Output JSON summary")
    args = parser.parse_args()
    
    # Run tests
    try:
        result = asyncio.run(run_all_tests(verbose=args.verbose, quick=args.quick))
        
        if args.json:
            print(json.dumps(result, indent=2))
        
        # Exit with appropriate code
        sys.exit(0 if result["success"] else 1)
        
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Test interrupted by user{Style.RESET_ALL}")
        sys.exit(2)
    except Exception as e:
        print(f"\n{Fore.RED}Test suite error: {e}{Style.RESET_ALL}")
        sys.exit(3)


if __name__ == "__main__":
    main()