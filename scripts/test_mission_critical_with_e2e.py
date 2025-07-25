#!/usr/bin/env python3
"""
Mission Critical Test Suite with E2E Browser Testing
Combines backend API tests with frontend UI verification
"""

import asyncio
import sys
import subprocess
import json
from pathlib import Path
from datetime import datetime

# Import the updated mission critical test
sys.path.append(str(Path(__file__).parent))
from test_mission_critical_v2 import MissionCriticalTests

# Color codes
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
CYAN = '\033[96m'
RESET = '\033[0m'

class MissionCriticalWithE2E:
    """Extended test suite including browser E2E tests"""
    
    def __init__(self):
        self.results = {
            "backend_tests": {},
            "e2e_tests": {},
            "overall": {}
        }
        
    async def run_backend_tests(self):
        """Run the standard mission critical backend tests"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}RUNNING BACKEND MISSION CRITICAL TESTS{RESET}")
        print(f"{BLUE}{'='*60}{RESET}")
        
        tests = MissionCriticalTests()
        
        try:
            await tests.setup()
            
            # Run all backend test categories
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
            
            # Get results
            total = len(tests.test_results)
            passed = sum(1 for r in tests.test_results if r["passed"])
            failed = total - passed
            
            self.results["backend_tests"] = {
                "total": total,
                "passed": passed,
                "failed": failed,
                "details": tests.test_results,
                "runtime_proof": tests.runtime_proof
            }
            
            await tests.teardown()
            
            return failed == 0
            
        except Exception as e:
            print(f"\n{RED}Backend test error: {e}{RESET}")
            self.results["backend_tests"]["error"] = str(e)
            return False
            
    def run_e2e_tests(self):
        """Run browser-based E2E tests"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}RUNNING E2E BROWSER TESTS{RESET}")
        print(f"{BLUE}{'='*60}{RESET}")
        
        try:
            # Check if Playwright is properly installed
            check_playwright = subprocess.run(
                ["playwright", "--version"],
                capture_output=True,
                text=True
            )
            
            if check_playwright.returncode != 0:
                print(f"{YELLOW}Warning: Playwright not properly installed{RESET}")
                print("Run: playwright install chromium")
                self.results["e2e_tests"]["error"] = "Playwright not installed"
                return False
            
            # Run E2E tests
            e2e_path = Path(__file__).parent.parent / "tests" / "test_preview_e2e.py"
            
            result = subprocess.run(
                [sys.executable, str(e2e_path)],
                capture_output=True,
                text=True,
                env={**subprocess.os.environ, "HEADLESS": "true"}
            )
            
            print(result.stdout)
            if result.stderr:
                print(f"{RED}E2E Errors:{RESET}")
                print(result.stderr)
            
            # Try to load the E2E test report
            report_files = sorted(
                Path("/tmp").glob("e2e_test_report_*.json"),
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )
            
            if report_files:
                with open(report_files[0]) as f:
                    e2e_report = json.load(f)
                    
                self.results["e2e_tests"] = e2e_report
            else:
                self.results["e2e_tests"]["error"] = "No report generated"
                
            return result.returncode == 0
            
        except Exception as e:
            print(f"\n{RED}E2E test error: {e}{RESET}")
            self.results["e2e_tests"]["error"] = str(e)
            return False
            
    def check_critical_ui_flow(self):
        """Verify the critical UI flow is working"""
        if "results" not in self.results["e2e_tests"]:
            return False
            
        critical_steps = [
            "Admin Login",
            "Navigate to Agent", 
            "Voice Preview UI",
            "Start Voice Chat"
        ]
        
        results = self.results["e2e_tests"]["results"]
        
        for step in critical_steps:
            if not any(r["test"] == step and r["passed"] for r in results):
                return False
                
        return True
        
    def generate_comprehensive_report(self):
        """Generate a comprehensive test report"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}COMPREHENSIVE TEST REPORT{RESET}")
        print(f"{BLUE}{'='*60}{RESET}")
        
        # Backend summary
        backend = self.results["backend_tests"]
        if "total" in backend:
            print(f"\n{CYAN}Backend Tests:{RESET}")
            print(f"  Total: {backend['total']}")
            print(f"  {GREEN}Passed: {backend['passed']}{RESET}")
            print(f"  {RED}Failed: {backend['failed']}{RESET}")
            
            # Show failed backend tests
            if backend["failed"] > 0:
                print(f"\n  {RED}Failed Backend Tests:{RESET}")
                for test in backend["details"]:
                    if not test["passed"]:
                        print(f"    - {test['test']}: {test['details']}")
                        
        # E2E summary
        e2e = self.results["e2e_tests"]
        if "summary" in e2e:
            print(f"\n{CYAN}E2E Browser Tests:{RESET}")
            print(f"  Total: {e2e['summary']['total']}")
            print(f"  {GREEN}Passed: {e2e['summary']['passed']}{RESET}")
            print(f"  {RED}Failed: {e2e['summary']['failed']}{RESET}")
            
            # Show E2E test results
            if "results" in e2e:
                for test in e2e["results"]:
                    status = f"{GREEN}✓{RESET}" if test["passed"] else f"{RED}✗{RESET}"
                    print(f"  {status} {test['test']}")
                    
        # Critical flow status
        print(f"\n{CYAN}Critical UI Flow:{RESET}")
        ui_flow_ok = self.check_critical_ui_flow()
        if ui_flow_ok:
            print(f"  {GREEN}✅ UI flow working end-to-end{RESET}")
        else:
            print(f"  {RED}❌ UI flow has failures{RESET}")
            
        # Save comprehensive report
        report_path = Path(f"/tmp/comprehensive_test_report_{int(datetime.now().timestamp())}.json")
        with open(report_path, 'w') as f:
            json.dump(self.results, f, indent=2)
            
        print(f"\n{CYAN}Full report saved to: {report_path}{RESET}")
        
        # Overall status
        backend_ok = backend.get("failed", 1) == 0
        e2e_ok = e2e.get("summary", {}).get("failed", 1) == 0
        
        print(f"\n{BLUE}{'='*60}{RESET}")
        if backend_ok and e2e_ok and ui_flow_ok:
            print(f"{GREEN}✅ ALL SYSTEMS OPERATIONAL - PLATFORM VERIFIED{RESET}")
            return True
        else:
            print(f"{RED}❌ PLATFORM HAS ISSUES - SEE REPORT{RESET}")
            if not backend_ok:
                print(f"  - Backend API issues detected")
            if not e2e_ok:
                print(f"  - Frontend UI issues detected")
            if not ui_flow_ok:
                print(f"  - Critical UI flow broken")
            return False

async def main():
    """Run comprehensive mission critical tests with E2E"""
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}AUTONOMITE PLATFORM - COMPREHENSIVE TEST SUITE{RESET}")
    print(f"{BLUE}Includes Backend API + Frontend E2E Testing{RESET}")
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    tester = MissionCriticalWithE2E()
    
    # Run backend tests
    backend_ok = await tester.run_backend_tests()
    
    # Run E2E tests (even if backend has some failures)
    e2e_ok = tester.run_e2e_tests()
    
    # Generate comprehensive report
    all_ok = tester.generate_comprehensive_report()
    
    # Exit with appropriate code
    sys.exit(0 if all_ok else 1)

if __name__ == "__main__":
    # Check for quick mode
    if "--quick" in sys.argv:
        print("Quick mode not supported for E2E tests")
        sys.exit(1)
        
    asyncio.run(main())