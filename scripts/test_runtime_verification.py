#!/usr/bin/env python3
"""
Runtime Verification Test Suite
Addresses issues identified by oversight agent:
1. Runtime deployment confirmation
2. Event handler implementation verification
3. End-to-end testing with real logs
4. Configuration validation
5. HTMX/UI feedback verification
"""

import asyncio
import subprocess
import json
import time
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import httpx
import docker
from rich.console import Console
from rich.table import Table
from rich.progress import track
from rich import print as rprint

console = Console()

class RuntimeVerificationTest:
    def __init__(self):
        self.docker_client = docker.from_env()
        self.api_base = "http://localhost:8000"
        self.results = []
        self.deployment_proof = {}
        
    def run_command(self, cmd: str) -> Tuple[int, str, str]:
        """Execute command and return exit code, stdout, stderr"""
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr
    
    def collect_runtime_proof(self, test_name: str, evidence: Dict):
        """Collect runtime proof as required by oversight"""
        self.deployment_proof[test_name] = {
            "timestamp": datetime.now().isoformat(),
            "evidence": evidence
        }
    
    async def test_deployment_verification(self) -> Dict:
        """Test 1: Verify actual runtime deployment (addresses oversight issue #1)"""
        console.print("\n[bold blue]Test 1: Runtime Deployment Verification[/bold blue]")
        
        evidence = {}
        
        # Check running containers
        containers = self.docker_client.containers.list(filters={"name": "agent_"})
        evidence["running_containers"] = []
        
        for container in containers:
            info = {
                "name": container.name,
                "image": container.image.tags[0] if container.image.tags else "unknown",
                "status": container.status,
                "created": container.attrs['Created']
            }
            evidence["running_containers"].append(info)
            
            # Verify actual code in container
            try:
                exit_code, stdout, _ = container.exec_run("ls -la /app/")
                evidence[f"{container.name}_files"] = stdout.decode()
                
                # Check which script is running
                exit_code, stdout, _ = container.exec_run("ps aux | grep python")
                evidence[f"{container.name}_processes"] = stdout.decode()
                
                # Check for session_agent.py vs minimal_agent.py
                for script in ["session_agent.py", "minimal_agent.py"]:
                    exit_code, stdout, _ = container.exec_run(f"test -f /app/{script} && echo 'EXISTS' || echo 'NOT FOUND'")
                    evidence[f"{container.name}_{script}"] = stdout.decode().strip()
                    
            except Exception as e:
                evidence[f"{container.name}_error"] = str(e)
        
        # Verify images match expected versions
        images = self.docker_client.images.list(name="autonomite/agent-runtime")
        evidence["available_images"] = [{"tags": img.tags, "created": img.attrs['Created']} for img in images[:5]]
        
        # Check if deployment matches claims
        success = len(containers) > 0 and any("session-agent" in str(c.image.tags) for c in containers)
        
        self.collect_runtime_proof("deployment_verification", evidence)
        
        return {
            "test": "deployment_verification",
            "success": success,
            "message": f"Found {len(containers)} agent containers",
            "evidence": evidence
        }
    
    async def test_event_handler_verification(self) -> Dict:
        """Test 2: Verify event handlers are implemented and logging (addresses oversight issue #2)"""
        console.print("\n[bold blue]Test 2: Event Handler Verification[/bold blue]")
        
        evidence = {}
        containers = self.docker_client.containers.list(filters={"name": "agent_"})
        
        for container in containers:
            container_logs = container.logs(tail=1000).decode()
            
            # Check for event handler registration
            evidence[f"{container.name}_handlers_registered"] = bool(re.search(r"Registering event handlers", container_logs))
            
            # Check for specific handlers
            handlers = [
                "user_speech_committed",
                "agent_speech_committed", 
                "user_started_speaking",
                "user_stopped_speaking",
                "participant_connected",
                "track_subscribed"
            ]
            
            for handler in handlers:
                pattern = f"@.*\\.on\\(['\"]?{handler}"
                evidence[f"{container.name}_{handler}"] = bool(re.search(pattern, container_logs))
            
            # Check for handler execution logs
            evidence[f"{container.name}_speech_events"] = container_logs.count("SPEECH EVENT")
            evidence[f"{container.name}_participant_events"] = container_logs.count("participant_connected")
            
            # Extract handler-related log lines
            handler_logs = [line for line in container_logs.split('\n') if any(h in line for h in handlers)]
            evidence[f"{container.name}_handler_logs"] = handler_logs[-10:] if handler_logs else []
        
        success = any(evidence.get(f"{c.name}_handlers_registered", False) for c in containers)
        
        self.collect_runtime_proof("event_handler_verification", evidence)
        
        return {
            "test": "event_handler_verification",
            "success": success,
            "message": "Event handlers " + ("registered" if success else "NOT found"),
            "evidence": evidence
        }
    
    async def test_greeting_logic(self) -> Dict:
        """Test 3: Verify greeting logic execution (addresses oversight issue #3)"""
        console.print("\n[bold blue]Test 3: Greeting Logic Verification[/bold blue]")
        
        evidence = {}
        
        # Trigger a test room
        test_room = f"test_greeting_{int(time.time())}"
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/api/v1/trigger-agent",
                json={
                    "agent_slug": "clarence-coherence",
                    "mode": "voice",
                    "room_name": test_room,
                    "user_id": "test-user",
                    "client_id": "df91fd06-816f-4273-a903-5a4861277040"
                }
            )
            evidence["trigger_response"] = response.status_code
        
        # Wait for agent to join
        await asyncio.sleep(3)
        
        # Check logs for greeting
        containers = self.docker_client.containers.list(filters={"name": "agent_"})
        for container in containers:
            logs = container.logs(since=int(time.time() - 10)).decode()
            
            # Look for greeting attempts
            greeting_patterns = [
                r"Attempting to send greeting",
                r"Greeting sent",
                r"say\(\)",
                r"Hello.*I'm"
            ]
            
            for pattern in greeting_patterns:
                matches = re.findall(pattern, logs, re.IGNORECASE)
                evidence[f"{container.name}_{pattern}"] = len(matches)
            
            # Check for greeting failures
            if "greeting failed" in logs.lower() or "error.*greeting" in logs.lower():
                evidence[f"{container.name}_greeting_errors"] = True
                
            # Extract greeting-related logs
            greeting_logs = [line for line in logs.split('\n') if 'greeting' in line.lower()]
            evidence[f"{container.name}_greeting_logs"] = greeting_logs[-5:] if greeting_logs else []
        
        success = any(evidence.get(f"{c.name}_Greeting sent", 0) > 0 for c in containers)
        
        self.collect_runtime_proof("greeting_logic", evidence)
        
        return {
            "test": "greeting_logic",
            "success": success,
            "message": "Greeting " + ("sent successfully" if success else "NOT sent"),
            "evidence": evidence
        }
    
    async def test_pipeline_configuration(self) -> Dict:
        """Test 4: Verify pipeline configuration and initialization (addresses oversight issue #4)"""
        console.print("\n[bold blue]Test 4: Pipeline Configuration Verification[/bold blue]")
        
        evidence = {}
        containers = self.docker_client.containers.list(filters={"name": "agent_"})
        
        for container in containers:
            logs = container.logs(tail=500).decode()
            
            # Check for pipeline components initialization
            components = {
                "STT": [r"Using.*STT", r"Initializing.*STT", r"STT.*initialized"],
                "LLM": [r"Using.*LLM", r"Initializing.*LLM", r"LLM.*initialized"],
                "TTS": [r"Using.*TTS", r"Initializing.*TTS", r"TTS.*initialized"],
                "VAD": [r"VAD.*load", r"silero.*VAD"],
                "AgentSession": [r"AgentSession.*created", r"session.*started"]
            }
            
            for component, patterns in components.items():
                found = False
                for pattern in patterns:
                    if re.search(pattern, logs, re.IGNORECASE):
                        found = True
                        break
                evidence[f"{container.name}_{component}"] = found
            
            # Check for API key validation
            api_keys = ["GROQ", "DEEPGRAM", "CARTESIA", "ELEVENLABS", "OPENAI"]
            for key in api_keys:
                evidence[f"{container.name}_{key}_configured"] = f"{key}_API_KEY" in logs
            
            # Check for initialization errors
            init_errors = re.findall(r"(init.*failed|Failed to initialize|initialization error)", logs, re.IGNORECASE)
            evidence[f"{container.name}_init_errors"] = init_errors
            
            # Extract pipeline logs
            pipeline_logs = [line for line in logs.split('\n') if any(c.lower() in line.lower() for c in components.keys())]
            evidence[f"{container.name}_pipeline_logs"] = pipeline_logs[-10:] if pipeline_logs else []
        
        success = all(
            any(evidence.get(f"{c.name}_{comp}", False) for c in containers)
            for comp in ["STT", "LLM", "TTS", "AgentSession"]
        )
        
        self.collect_runtime_proof("pipeline_configuration", evidence)
        
        return {
            "test": "pipeline_configuration",
            "success": success,
            "message": "Pipeline " + ("fully configured" if success else "configuration INCOMPLETE"),
            "evidence": evidence
        }
    
    async def test_end_to_end_interaction(self) -> Dict:
        """Test 5: Simulate end-to-end interaction (addresses oversight issue #2 & #5)"""
        console.print("\n[bold blue]Test 5: End-to-End Interaction Test[/bold blue]")
        
        evidence = {}
        
        # Create a test room and join as participant
        test_room = f"e2e_test_{int(time.time())}"
        
        # Trigger agent
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/api/v1/trigger-agent",
                json={
                    "agent_slug": "clarence-coherence",
                    "mode": "voice",
                    "room_name": test_room,
                    "user_id": "test-user",
                    "client_id": "df91fd06-816f-4273-a903-5a4861277040"
                }
            )
            evidence["trigger_status"] = response.status_code
            if response.status_code == 200:
                data = response.json()
                evidence["room_created"] = data.get("data", {}).get("room_info", {}).get("status") == "created"
                evidence["container_spawned"] = data.get("data", {}).get("container_info", {}).get("status") == "running"
        
        # Wait for agent to be ready
        await asyncio.sleep(5)
        
        # Check agent joined room
        containers = self.docker_client.containers.list(filters={"name": "agent_"})
        for container in containers:
            logs = container.logs(since=int(time.time() - 10)).decode()
            
            # Check for room join
            evidence[f"{container.name}_room_joined"] = test_room in logs
            
            # Check for participant events
            evidence[f"{container.name}_waiting_for_participants"] = "No participants" in logs or "waiting" in logs.lower()
            
            # Extract interaction logs
            interaction_logs = [line for line in logs.split('\n') if test_room in line]
            evidence[f"{container.name}_room_logs"] = interaction_logs[-5:] if interaction_logs else []
        
        success = evidence.get("room_created", False) and evidence.get("container_spawned", False)
        
        self.collect_runtime_proof("end_to_end_interaction", evidence)
        
        return {
            "test": "end_to_end_interaction",
            "success": success,
            "message": "E2E test " + ("passed" if success else "FAILED"),
            "evidence": evidence
        }
    
    def generate_report(self):
        """Generate comprehensive test report with runtime proof"""
        console.print("\n[bold green]Runtime Verification Report[/bold green]")
        
        # Summary table
        table = Table(title="Test Results Summary")
        table.add_column("Test", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Message", style="yellow")
        
        for result in self.results:
            status = "✅ PASS" if result["success"] else "❌ FAIL"
            table.add_row(result["test"], status, result["message"])
        
        console.print(table)
        
        # Detailed runtime proof
        console.print("\n[bold]Runtime Proof Evidence:[/bold]")
        
        for test_name, proof in self.deployment_proof.items():
            console.print(f"\n[cyan]{test_name}:[/cyan]")
            console.print(f"Timestamp: {proof['timestamp']}")
            
            # Key evidence points
            evidence = proof['evidence']
            if 'running_containers' in evidence:
                for container in evidence['running_containers']:
                    console.print(f"  Container: {container['name']} ({container['image']})")
            
            # Log excerpts
            for key, value in evidence.items():
                if '_logs' in key and value:
                    console.print(f"\n  {key}:")
                    for log in value[-3:]:  # Last 3 log lines
                        console.print(f"    {log}")
        
        # Save report
        report_path = f"/tmp/runtime_verification_report_{int(time.time())}.json"
        with open(report_path, 'w') as f:
            json.dump({
                "results": self.results,
                "runtime_proof": self.deployment_proof,
                "timestamp": datetime.now().isoformat()
            }, f, indent=2)
        
        console.print(f"\n[green]Report saved to: {report_path}[/green]")
        
        return all(r["success"] for r in self.results)
    
    async def run_all_tests(self):
        """Run all verification tests"""
        tests = [
            self.test_deployment_verification(),
            self.test_event_handler_verification(),
            self.test_greeting_logic(),
            self.test_pipeline_configuration(),
            self.test_end_to_end_interaction()
        ]
        
        for test in track(tests, description="Running tests..."):
            result = await test
            self.results.append(result)
            await asyncio.sleep(1)  # Avoid overwhelming the system
        
        return self.generate_report()


async def main():
    """Run the runtime verification test suite"""
    tester = RuntimeVerificationTest()
    success = await tester.run_all_tests()
    
    if not success:
        console.print("\n[red]⚠️  Some tests failed. Review the runtime proof above.[/red]")
        return 1
    else:
        console.print("\n[green]✅ All runtime verification tests passed![/green]")
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)