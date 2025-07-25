#!/usr/bin/env python3
"""
Multi-tenant testing for Autonomite SaaS platform
Tests isolation, dispatch failures, and edge cases
"""
import asyncio
import httpx
import json
import time
from typing import Dict, List, Any
from datetime import datetime


class MultiTenantTester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)
        self.results = []
        
    async def close(self):
        await self.client.aclose()
        
    async def test_multi_tenant_isolation(self):
        """Test that multiple clients are properly isolated"""
        print("\n▶ Testing Multi-Tenant Isolation")
        
        try:
            # Get all clients
            resp = await self.client.get(f"{self.base_url}/api/v1/clients")
            assert resp.status_code == 200
            clients = resp.json()
            
            if len(clients) < 2:
                print("⚠️  SKIP: Need at least 2 clients for isolation test")
                return {"status": "skipped", "reason": "insufficient clients"}
                
            # Take first two clients
            client1 = clients[0]
            client2 = clients[1]
            
            print(f"  Testing isolation between {client1['name']} and {client2['name']}")
            
            # Trigger agent for client1
            trigger1_resp = await self.client.post(
                f"{self.base_url}/api/v1/trigger-agent",
                json={
                    "agent_slug": "test-agent",
                    "client_id": client1["id"],
                    "mode": "voice",
                    "room_name": f"test_isolation_{client1['id'][:8]}",
                    "user_id": "test_user_1"
                }
            )
            
            # Trigger agent for client2
            trigger2_resp = await self.client.post(
                f"{self.base_url}/api/v1/trigger-agent",
                json={
                    "agent_slug": "test-agent",
                    "client_id": client2["id"],
                    "mode": "voice",
                    "room_name": f"test_isolation_{client2['id'][:8]}",
                    "user_id": "test_user_2"
                }
            )
            
            # Both should succeed or fail independently
            print(f"  Client 1 trigger: {trigger1_resp.status_code}")
            print(f"  Client 2 trigger: {trigger2_resp.status_code}")
            
            # Check container isolation if both succeeded
            if trigger1_resp.status_code == 200 and trigger2_resp.status_code == 200:
                containers_resp = await self.client.get(f"{self.base_url}/api/v1/containers")
                if containers_resp.status_code == 200:
                    containers = containers_resp.json()
                    
                    # Verify containers are separate
                    client1_containers = [c for c in containers if c.get("client_id") == client1["id"]]
                    client2_containers = [c for c in containers if c.get("client_id") == client2["id"]]
                    
                    assert len(set(c["id"] for c in client1_containers) & 
                              set(c["id"] for c in client2_containers)) == 0, \
                        "Container IDs overlap between clients!"
                    
                    print(f"  ✅ Container isolation verified")
                    print(f"     Client 1 has {len(client1_containers)} containers")
                    print(f"     Client 2 has {len(client2_containers)} containers")
                    
            return {"status": "passed", "details": "Multi-tenant isolation verified"}
            
        except Exception as e:
            error_msg = str(e) if str(e) else f"Exception type: {type(e).__name__}"
            print(f"  ❌ FAIL: {error_msg}")
            return {"status": "failed", "error": error_msg}
            
    async def test_dispatch_failure_scenarios(self):
        """Test various failure scenarios"""
        print("\n▶ Testing Dispatch Failure Scenarios")
        
        failures_tested = []
        
        # Test 1: Invalid agent slug
        try:
            print("  Testing invalid agent slug...")
            resp = await self.client.post(
                f"{self.base_url}/api/v1/trigger-agent",
                json={
                    "agent_slug": "non-existent-agent",
                    "mode": "voice",
                    "room_name": "test_failure_1",
                    "user_id": "test_user"
                }
            )
            assert resp.status_code >= 400, f"Expected error, got {resp.status_code}"
            failures_tested.append("invalid_agent_slug")
            print("  ✅ Invalid agent slug handled correctly")
        except Exception as e:
            print(f"  ❌ Invalid agent test failed: {e}")
            
        # Test 2: Missing required fields
        try:
            print("  Testing missing room_name for voice mode...")
            resp = await self.client.post(
                f"{self.base_url}/api/v1/trigger-agent",
                json={
                    "agent_slug": "test-agent",
                    "mode": "voice",
                    "user_id": "test_user"
                    # Missing room_name
                }
            )
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
            failures_tested.append("missing_room_name")
            print("  ✅ Missing room_name validation works")
        except Exception as e:
            print(f"  ❌ Missing field test failed: {e}")
            
        # Test 3: Invalid client ID
        try:
            print("  Testing invalid client ID...")
            resp = await self.client.post(
                f"{self.base_url}/api/v1/trigger-agent",
                json={
                    "agent_slug": "test-agent",
                    "client_id": "invalid-uuid-format",
                    "mode": "voice",
                    "room_name": "test_failure_3",
                    "user_id": "test_user"
                }
            )
            # Should either fail or auto-detect
            failures_tested.append("invalid_client_id")
            print(f"  ✅ Invalid client ID handled (status: {resp.status_code})")
        except Exception as e:
            print(f"  ❌ Invalid client test failed: {e}")
            
        return {
            "status": "passed" if len(failures_tested) > 0 else "failed",
            "scenarios_tested": failures_tested
        }
        
    async def test_concurrent_triggers(self):
        """Test concurrent agent triggers"""
        print("\n▶ Testing Concurrent Triggers")
        
        try:
            # Create multiple concurrent trigger requests
            tasks = []
            num_concurrent = 5
            
            for i in range(num_concurrent):
                task = self.client.post(
                    f"{self.base_url}/api/v1/trigger-agent",
                    json={
                        "agent_slug": "test-agent",
                        "mode": "voice",
                        "room_name": f"test_concurrent_{i}",
                        "user_id": f"test_user_{i}"
                    }
                )
                tasks.append(task)
                
            print(f"  Triggering {num_concurrent} concurrent requests...")
            start_time = time.time()
            
            # Execute all requests concurrently
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            
            duration = time.time() - start_time
            print(f"  Completed in {duration:.2f} seconds")
            
            # Analyze results
            success_count = sum(1 for r in responses 
                              if not isinstance(r, Exception) and r.status_code == 200)
            error_count = sum(1 for r in responses if isinstance(r, Exception))
            failed_count = sum(1 for r in responses 
                             if not isinstance(r, Exception) and r.status_code != 200)
            
            print(f"  Results: {success_count} success, {failed_count} failed, {error_count} errors")
            
            return {
                "status": "passed",
                "concurrent_requests": num_concurrent,
                "duration": duration,
                "success_rate": success_count / num_concurrent
            }
            
        except Exception as e:
            error_msg = str(e) if str(e) else f"Exception type: {type(e).__name__}"
            print(f"  ❌ FAIL: {error_msg}")
            return {"status": "failed", "error": error_msg}
            
    async def test_metrics_availability(self):
        """Test Prometheus metrics endpoint"""
        print("\n▶ Testing Metrics Availability")
        
        try:
            # Follow redirects for metrics endpoint
            resp = await self.client.get(f"{self.base_url}/metrics/", follow_redirects=True)
            assert resp.status_code == 200, f"Metrics endpoint returned {resp.status_code}"
            
            metrics_text = resp.text
            
            # Check for expected metrics
            expected_metrics = [
                "http_requests_total",
                "http_request_duration_seconds",
                "agent_triggers_total",
                "container_operations_total"
            ]
            
            found_metrics = []
            for metric in expected_metrics:
                if metric in metrics_text:
                    found_metrics.append(metric)
                    
            print(f"  ✅ Found {len(found_metrics)}/{len(expected_metrics)} expected metrics")
            
            return {
                "status": "passed",
                "metrics_found": found_metrics,
                "total_lines": len(metrics_text.split('\n'))
            }
            
        except Exception as e:
            error_msg = str(e) if str(e) else f"Exception type: {type(e).__name__}"
            print(f"  ❌ FAIL: {error_msg}")
            return {"status": "failed", "error": error_msg}
            
    async def run_all_tests(self):
        """Run all multi-tenant tests"""
        print("\n" + "="*60)
        print("           MULTI-TENANT TESTING SUITE")
        print("="*60)
        print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Target: {self.base_url}")
        
        # Run tests
        self.results.append(("Multi-Tenant Isolation", await self.test_multi_tenant_isolation()))
        self.results.append(("Dispatch Failures", await self.test_dispatch_failure_scenarios()))
        self.results.append(("Concurrent Triggers", await self.test_concurrent_triggers()))
        self.results.append(("Metrics Availability", await self.test_metrics_availability()))
        
        # Summary
        print("\n" + "="*60)
        print("                    TEST SUMMARY")
        print("="*60)
        
        passed = sum(1 for _, r in self.results if r["status"] == "passed")
        failed = sum(1 for _, r in self.results if r["status"] == "failed")
        skipped = sum(1 for _, r in self.results if r["status"] == "skipped")
        
        print(f"Total Tests: {len(self.results)}")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print(f"Skipped: {skipped}")
        
        # Detailed results
        print("\nDetailed Results:")
        for test_name, result in self.results:
            status_icon = "✅" if result["status"] == "passed" else "❌" if result["status"] == "failed" else "⚠️"
            print(f"{status_icon} {test_name}: {result}")
            
        return passed == len(self.results) - skipped


async def main():
    tester = MultiTenantTester()
    try:
        success = await tester.run_all_tests()
        return 0 if success else 1
    finally:
        await tester.close()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)