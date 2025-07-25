#!/usr/bin/env python3
"""
Mission Critical Test Suite - Enhanced Edition
Includes all runtime proof and deployment verification features
"""

import asyncio
import aiohttp
import json
import sys
import time
import docker
import subprocess
import os
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
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
CYAN = '\033[96m'
RESET = '\033[0m'

class EnhancedMissionCriticalTests:
    def __init__(self):
        self.session = None
        self.test_results = []
        self.test_room_name = f"test-room-{int(time.time())}"
        self.test_container_id = None
        self.runtime_proof = {}
        self.deployment_evidence = {}
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
            
    def log_test(self, test_name: str, passed: bool, details: str = "", evidence: Dict = None):
        """Log test result with optional evidence"""
        status = f"{GREEN}✅ PASSED{RESET}" if passed else f"{RED}❌ FAILED{RESET}"
        print(f"\n{status} {test_name}")
        if details:
            print(f"   {details}")
        if evidence:
            print(f"   Evidence: {json.dumps(evidence, indent=2)}")
        self.test_results.append({
            "test": test_name,
            "passed": passed,
            "details": details,
            "evidence": evidence or {}
        })
        
    def collect_runtime_proof(self, category: str, key: str, value: Any):
        """Collect runtime proof for later analysis"""
        if category not in self.runtime_proof:
            self.runtime_proof[category] = {}
        self.runtime_proof[category][key] = value
        
    async def test_comprehensive_deployment_verification(self):
        """Enhanced deployment verification with full runtime proof"""
        print(f"\n{BLUE}=== Comprehensive Deployment Verification ==={RESET}")
        
        if not self.docker_client:
            self.log_test("Docker access", False, "Docker client not available")
            return
            
        # Check for deployment scripts
        deploy_scripts = [
            "/root/autonomite-agent-platform/scripts/deploy_agent.sh",
            "/root/autonomite-agent-platform/scripts/verify_deployment.sh",
            "/root/autonomite-agent-platform/scripts/quick_restart.sh"
        ]
        
        for script in deploy_scripts:
            exists = os.path.exists(script)
            self.log_test(
                f"Deployment script {os.path.basename(script)}", 
                exists,
                "Available" if exists else "Missing"
            )
            
        # Find and verify agent containers
        containers = self.docker_client.containers.list(filters={"label": "autonomite.managed=true"})
        
        if not containers:
            self.log_test("Agent containers", False, "No managed containers found")
            return
            
        self.log_test("Agent containers", True, f"Found {len(containers)} managed containers")
        
        for container in containers:
            container_name = container.name
            print(f"\n{CYAN}  Verifying container: {container_name}{RESET}")
            
            evidence = {
                "container_id": container.id[:12],
                "status": container.status,
                "created": container.attrs['Created'],
                "image": container.image.tags[0] if container.image.tags else "untagged"
            }
            
            # Get deployment metadata
            deploy_tag = container.labels.get("autonomite.deploy_tag", "unknown")
            deployed_at = container.labels.get("autonomite.deployed_at", "unknown")
            client_id = container.labels.get("autonomite.client_id", "unknown")
            
            evidence.update({
                "deploy_tag": deploy_tag,
                "deployed_at": deployed_at,
                "client_id": client_id
            })
            
            # Verify deployed code
            try:
                # Check for session_agent.py
                exit_code, output = container.exec_run("test -f /app/session_agent.py")
                has_session_agent = exit_code == 0
                
                # Check for greeting code
                if has_session_agent:
                    exit_code, output = container.exec_run(
                        "grep -q 'Greeting sent successfully' /app/session_agent.py"
                    )
                    has_greeting_code = exit_code == 0
                    
                    exit_code, output = container.exec_run(
                        "grep -q 'user_speech_committed' /app/session_agent.py"
                    )
                    has_event_handlers = exit_code == 0
                else:
                    has_greeting_code = False
                    has_event_handlers = False
                    
                evidence.update({
                    "session_agent_present": has_session_agent,
                    "greeting_code_present": has_greeting_code,
                    "event_handlers_present": has_event_handlers
                })
                
                # Check runtime logs
                logs = container.logs(tail=500).decode('utf-8')
                
                runtime_markers = {
                    "worker_registered": "registered worker" in logs,
                    "session_agent_started": "Starting session agent" in logs,
                    "handlers_registered": "Event handlers registered" in logs or "Registering event handlers" in logs,
                    "greeting_attempts": logs.count("Attempting to send greeting"),
                    "greeting_success": logs.count("Greeting sent successfully"),
                    "stuck_processes": logs.count("process did not exit in time"),
                    "error_count": logs.count("ERROR")
                }
                
                evidence.update(runtime_markers)
                
                # Determine deployment health
                is_healthy = (
                    has_session_agent and
                    has_event_handlers and
                    runtime_markers["worker_registered"] and
                    runtime_markers["stuck_processes"] == 0
                )
                
                self.log_test(
                    f"Deployment verification: {container_name}",
                    is_healthy,
                    f"Code: {'✓' if has_session_agent else '✗'}, "
                    f"Handlers: {'✓' if has_event_handlers else '✗'}, "
                    f"Worker: {'✓' if runtime_markers['worker_registered'] else '✗'}, "
                    f"Errors: {runtime_markers['error_count']}",
                    evidence
                )
                
                self.collect_runtime_proof("deployment", container_name, evidence)
                
            except Exception as e:
                self.log_test(f"Deployment verification: {container_name}", False, str(e))
                
    async def test_greeting_runtime_proof(self):
        """Test greeting functionality with full runtime proof"""
        print(f"\n{BLUE}=== Greeting Runtime Proof ==={RESET}")
        
        if not self.docker_client:
            self.log_test("Greeting runtime proof", False, "Docker not accessible")
            return
            
        containers = self.docker_client.containers.list(filters={"label": "autonomite.managed=true"})
        
        for container in containers:
            print(f"\n{CYAN}  Testing greetings for: {container.name}{RESET}")
            
            # Generate test room
            test_room = f"greeting_test_{int(time.time())}"
            
            # Get client ID from container
            client_id = container.labels.get("autonomite.client_id", TEST_CLIENT_ID)
            
            # Find agent slug from container name
            # Format: agent_<client_id_prefix>_<agent_slug>
            parts = container.name.split('_')
            agent_slug = '_'.join(parts[2:]).replace('_', '-') if len(parts) > 2 else "test-agent"
            
            # Trigger agent
            payload = {
                "agent_slug": agent_slug,
                "mode": "voice",
                "room_name": test_room,
                "user_id": "greeting-test",
                "client_id": client_id
            }
            
            try:
                async with self.session.post(
                    f"{BASE_URL}{API_PREFIX}/trigger-agent",
                    json=payload
                ) as resp:
                    if resp.status == 200:
                        # Wait for processing
                        await asyncio.sleep(5)
                        
                        # Collect evidence
                        logs = container.logs(tail=200).decode('utf-8')
                        
                        evidence = {
                            "test_room": test_room,
                            "room_found": test_room in logs,
                            "job_accepted": f"Job accepted for room '{test_room}'" in logs,
                            "connected": f"Connected to room: {test_room}" in logs,
                            "greeting_attempted": f"Attempting to send greeting" in logs and test_room in logs,
                            "greeting_sent": "Greeting sent successfully" in logs and test_room in logs,
                            "audio_published": "Published audio track" in logs or "track published" in logs
                        }
                        
                        # Extract greeting message if found
                        greeting_match = None
                        for line in logs.split('\n'):
                            if "Attempting to send greeting:" in line and test_room in logs[:logs.find(line)]:
                                greeting_match = line.split("Attempting to send greeting:")[-1].strip()
                                evidence["greeting_message"] = greeting_match
                                break
                                
                        is_successful = (
                            evidence["room_found"] and
                            evidence["job_accepted"] and
                            evidence["greeting_sent"]
                        )
                        
                        self.log_test(
                            f"Greeting test: {container.name}",
                            is_successful,
                            f"Room: {'✓' if evidence['room_found'] else '✗'}, "
                            f"Accepted: {'✓' if evidence['job_accepted'] else '✗'}, "
                            f"Greeting: {'✓' if evidence['greeting_sent'] else '✗'}",
                            evidence
                        )
                        
                        self.collect_runtime_proof("greetings", container.name, evidence)
                        
                    else:
                        self.log_test(f"Greeting test: {container.name}", False, f"Trigger failed: {resp.status}")
                        
            except Exception as e:
                self.log_test(f"Greeting test: {container.name}", False, str(e))
                
    async def test_build_and_deployment_pipeline(self):
        """Test the automated build and deployment pipeline"""
        print(f"\n{BLUE}=== Build & Deployment Pipeline ==={RESET}")
        
        # Check for buildx
        try:
            result = subprocess.run(
                ["docker", "buildx", "version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            has_buildx = result.returncode == 0
            self.log_test(
                "Docker buildx",
                has_buildx,
                f"Version: {result.stdout.strip()}" if has_buildx else "Not available"
            )
        except:
            self.log_test("Docker buildx", False, "Check failed")
            
        # Check build cache
        cache_path = Path("/tmp/buildx-cache")
        if cache_path.exists():
            cache_size = sum(f.stat().st_size for f in cache_path.rglob('*') if f.is_file())
            cache_mb = cache_size / (1024 * 1024)
            self.log_test(
                "Build cache",
                True,
                f"Cache size: {cache_mb:.1f} MB"
            )
        else:
            self.log_test("Build cache", False, "No cache found")
            
        # Check last deployment info
        last_deploy_path = Path("/tmp/last_deployment.json")
        if last_deploy_path.exists():
            try:
                with open(last_deploy_path) as f:
                    last_deploy = json.load(f)
                    
                deploy_time = datetime.fromisoformat(last_deploy["timestamp"].replace('Z', '+00:00'))
                age_minutes = (datetime.now(deploy_time.tzinfo) - deploy_time).total_seconds() / 60
                
                self.log_test(
                    "Last deployment",
                    last_deploy.get("status") == "success",
                    f"Age: {age_minutes:.1f} minutes, "
                    f"Markers: {last_deploy.get('markers_found', 0)}/5",
                    last_deploy
                )
                
                self.collect_runtime_proof("deployment", "last_deployment", last_deploy)
                
            except Exception as e:
                self.log_test("Last deployment", False, f"Parse error: {e}")
        else:
            self.log_test("Last deployment", False, "No deployment record found")
            
    async def test_container_performance_metrics(self):
        """Detailed container performance and health metrics"""
        print(f"\n{BLUE}=== Container Performance Metrics ==={RESET}")
        
        if not self.docker_client:
            self.log_test("Performance metrics", False, "Docker not accessible")
            return
            
        containers = self.docker_client.containers.list(filters={"label": "autonomite.managed=true"})
        
        for container in containers:
            try:
                # Get detailed stats
                stats = container.stats(stream=False)
                
                # CPU metrics
                cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                           stats['precpu_stats']['cpu_usage']['total_usage']
                system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                              stats['precpu_stats']['system_cpu_usage']
                cpu_count = len(stats['cpu_stats']['cpu_usage'].get('percpu_usage', [1]))
                cpu_percent = (cpu_delta / system_delta) * cpu_count * 100 if system_delta > 0 else 0
                
                # Memory metrics
                mem_usage = stats['memory_stats']['usage']
                mem_limit = stats['memory_stats']['limit']
                mem_percent = (mem_usage / mem_limit) * 100 if mem_limit > 0 else 0
                mem_mb = mem_usage / (1024 * 1024)
                
                # Network metrics
                networks = stats.get('networks', {})
                net_rx_bytes = sum(net.get('rx_bytes', 0) for net in networks.values())
                net_tx_bytes = sum(net.get('tx_bytes', 0) for net in networks.values())
                
                # Container info
                info = container.attrs
                restart_count = info['RestartCount']
                uptime_seconds = (datetime.now() - datetime.fromisoformat(
                    info['State']['StartedAt'].replace('Z', '+00:00')
                )).total_seconds()
                
                metrics = {
                    "cpu_percent": round(cpu_percent, 2),
                    "memory_mb": round(mem_mb, 2),
                    "memory_percent": round(mem_percent, 2),
                    "network_rx_kb": round(net_rx_bytes / 1024, 2),
                    "network_tx_kb": round(net_tx_bytes / 1024, 2),
                    "restart_count": restart_count,
                    "uptime_minutes": round(uptime_seconds / 60, 2),
                    "health_status": info['State'].get('Health', {}).get('Status', 'none')
                }
                
                # Determine health
                is_healthy = (
                    cpu_percent < 80 and
                    mem_percent < 80 and
                    restart_count == 0 and
                    metrics["health_status"] in ["healthy", "none"]
                )
                
                self.log_test(
                    f"Performance: {container.name}",
                    is_healthy,
                    f"CPU: {cpu_percent:.1f}%, Mem: {mem_mb:.1f}MB ({mem_percent:.1f}%), "
                    f"Uptime: {metrics['uptime_minutes']:.1f}m",
                    metrics
                )
                
                self.collect_runtime_proof("performance", container.name, metrics)
                
            except Exception as e:
                self.log_test(f"Performance: {container.name}", False, str(e))
                
    def generate_runtime_proof_report(self):
        """Generate comprehensive runtime proof report"""
        print(f"\n{BLUE}=== RUNTIME PROOF REPORT ==={RESET}")
        print(f"Generated at: {datetime.now().isoformat()}")
        
        # Save to file
        report_path = Path("/tmp/mission_critical_runtime_proof.json")
        report = {
            "timestamp": datetime.now().isoformat(),
            "test_results": self.test_results,
            "runtime_proof": self.runtime_proof,
            "summary": {
                "total_tests": len(self.test_results),
                "passed": sum(1 for r in self.test_results if r["passed"]),
                "failed": sum(1 for r in self.test_results if not r["passed"])
            }
        }
        
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
            
        print(f"\nRuntime proof saved to: {report_path}")
        
        # Print summary
        for category, data in self.runtime_proof.items():
            print(f"\n{CYAN}{category.upper()}:{RESET}")
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, dict):
                        print(f"  {key}:")
                        for k, v in value.items():
                            print(f"    {k}: {v}")
                    else:
                        print(f"  {key}: {value}")
                        
    def print_summary(self):
        """Print enhanced test summary"""
        print(f"\n{BLUE}{'='*60}{RESET}")
        print(f"{BLUE}=== ENHANCED TEST SUMMARY ==={RESET}")
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
                    
        # Critical system status
        critical_tests = [
            "Agent containers",
            "Deployment verification",
            "Greeting test",
            "Performance"
        ]
        
        critical_passed = all(
            any(r["passed"] for r in self.test_results if result["test"].startswith(test))
            for test in critical_tests
        )
        
        print(f"\n{BLUE}Mission Critical Status: ", end="")
        if critical_passed:
            print(f"{GREEN}✅ ALL CRITICAL SYSTEMS OPERATIONAL{RESET}")
        else:
            print(f"{RED}❌ CRITICAL SYSTEMS FAILURE{RESET}")
            
        # Generate runtime proof report
        self.generate_runtime_proof_report()
        
        return failed == 0

async def main():
    """Run enhanced mission critical tests"""
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}AUTONOMITE PLATFORM - ENHANCED MISSION CRITICAL TEST SUITE{RESET}")
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    tests = EnhancedMissionCriticalTests()
    
    try:
        await tests.setup()
        
        # Run enhanced test categories
        await tests.test_comprehensive_deployment_verification()
        await tests.test_greeting_runtime_proof()
        await tests.test_build_and_deployment_pipeline()
        await tests.test_container_performance_metrics()
        
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
    # Check if running in quick mode
    if "--quick" in sys.argv:
        print("Quick mode not supported in enhanced suite")
        sys.exit(1)
    
    asyncio.run(main())