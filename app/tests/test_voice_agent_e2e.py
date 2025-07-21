"""
End-to-end tests for voice agent functionality
"""
import asyncio
import json
import pytest
from typing import Dict, Any
import logging
from datetime import datetime

from app.utils.diagnostics import agent_diagnostics, diagnostic_context

logger = logging.getLogger(__name__)


class VoiceAgentE2ETest:
    """Comprehensive end-to-end tests for voice agents"""
    
    def __init__(self):
        self.test_results = []
        self.client_id = "df91fd06-816f-4273-a903-5a4861277040"
        self.agent_slug = "clarence-coherence"
        
    async def test_1_backend_health(self) -> Dict[str, Any]:
        """Test 1: Backend health and connectivity"""
        async with diagnostic_context("backend_health_test") as diag:
            try:
                import httpx
                
                # Test health endpoint
                async with httpx.AsyncClient() as client:
                    response = await client.get("http://localhost:8000/health")
                    diag.add_event("health_check", f"Health check status: {response.status_code}")
                    
                    if response.status_code == 200:
                        health_data = response.json()
                        diag.add_event("health_data", "Health check passed", health_data)
                        return {"success": True, "health": health_data}
                    else:
                        diag.add_error(Exception(f"Health check failed: {response.status_code}"), "health_check")
                        return {"success": False, "error": "Health check failed"}
                        
            except Exception as e:
                diag.add_error(e, "backend_connectivity")
                return {"success": False, "error": str(e)}
                
    async def test_2_livekit_connectivity(self) -> Dict[str, Any]:
        """Test 2: LiveKit connectivity"""
        return await agent_diagnostics.test_livekit_connection(
            server_url="wss://litebridge-hw6srhvi.livekit.cloud",
            api_key="APIUtuiQ47BQBsk",
            api_secret="rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
        )
        
    async def test_3_agent_configuration(self) -> Dict[str, Any]:
        """Test 3: Agent configuration and database"""
        async with diagnostic_context("agent_config_test", agent_slug=self.agent_slug) as diag:
            try:
                from app.core.dependencies import get_agent_service
                
                agent_service = get_agent_service()
                agent = await agent_service.get_agent(self.client_id, self.agent_slug)
                
                if agent:
                    diag.add_event("agent_found", "Agent configuration loaded", {
                        "name": agent.name,
                        "voice_provider": agent.voice_settings.get("provider"),
                        "llm_provider": agent.voice_settings.get("llm_provider")
                    })
                    
                    # Check API keys
                    missing_keys = []
                    if not agent.api_keys.get("groq_api_key"):
                        missing_keys.append("groq_api_key")
                    if not agent.api_keys.get("deepgram_api_key"):
                        missing_keys.append("deepgram_api_key")
                    if not agent.api_keys.get("cartesia_api_key"):
                        missing_keys.append("cartesia_api_key")
                        
                    if missing_keys:
                        diag.add_event("missing_keys", f"Missing API keys: {missing_keys}", {"missing": missing_keys})
                        
                    return {
                        "success": len(missing_keys) == 0,
                        "agent": agent.name,
                        "missing_keys": missing_keys
                    }
                else:
                    diag.add_error(Exception("Agent not found"), "agent_lookup")
                    return {"success": False, "error": "Agent not found"}
                    
            except Exception as e:
                diag.add_error(e, "agent_config")
                return {"success": False, "error": str(e)}
                
    async def test_4_trigger_agent(self) -> Dict[str, Any]:
        """Test 4: Trigger agent and room creation"""
        return await agent_diagnostics.test_agent_trigger_flow(self.agent_slug, self.client_id)
        
    async def test_5_container_health(self) -> Dict[str, Any]:
        """Test 5: Container health check"""
        container_name = f"agent_{self.client_id.replace('-', '')[:8]}_{self.agent_slug.replace('-', '_')}"
        return await agent_diagnostics.test_agent_container(container_name)
        
    async def test_6_voice_connection_flow(self) -> Dict[str, Any]:
        """Test 6: Complete voice connection flow"""
        async with diagnostic_context("voice_connection_test") as diag:
            try:
                import httpx
                from livekit import api, rtc
                
                # Trigger agent
                diag.add_event("trigger", "Triggering agent for voice test")
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "http://localhost:8000/api/v1/trigger-agent",
                        json={
                            "agent_slug": self.agent_slug,
                            "mode": "voice",
                            "room_name": f"e2e_test_{int(datetime.now().timestamp())}",
                            "user_id": "e2e_test_user",
                            "client_id": self.client_id
                        }
                    )
                    
                    if response.status_code != 200:
                        diag.add_error(Exception(f"Trigger failed: {response.text}"), "trigger")
                        return {"success": False, "error": "Trigger failed"}
                        
                    trigger_data = response.json()
                    room_name = trigger_data["data"]["room_name"]
                    token = trigger_data["data"]["livekit_config"]["user_token"]
                    server_url = trigger_data["data"]["livekit_config"]["server_url"]
                    
                diag.checkpoint("agent_triggered")
                
                # Wait for agent to start
                await asyncio.sleep(3)
                
                # Check room participants
                lk_api = api.LiveKitAPI(
                    "wss://litebridge-hw6srhvi.livekit.cloud",
                    "APIUtuiQ47BQBsk",
                    "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
                )
                
                participants = await lk_api.room.list_participants(
                    api.ListParticipantsRequest(room=room_name)
                )
                
                agent_found = any(p.identity.startswith("agent") for p in participants.participants)
                diag.add_event("participants", f"Found {len(participants.participants)} participants", {
                    "count": len(participants.participants),
                    "agent_found": agent_found
                })
                
                # Clean up
                await lk_api.room.delete_room(api.DeleteRoomRequest(room=room_name))
                
                return {
                    "success": agent_found,
                    "participants": len(participants.participants),
                    "agent_connected": agent_found
                }
                
            except Exception as e:
                diag.add_error(e, "voice_connection")
                return {"success": False, "error": str(e)}
                
    async def run_all_tests(self) -> Dict[str, Any]:
        """Run all tests and generate report"""
        tests = [
            ("Backend Health", self.test_1_backend_health),
            ("LiveKit Connectivity", self.test_2_livekit_connectivity),
            ("Agent Configuration", self.test_3_agent_configuration),
            ("Trigger Agent", self.test_4_trigger_agent),
            ("Container Health", self.test_5_container_health),
            ("Voice Connection Flow", self.test_6_voice_connection_flow)
        ]
        
        results = []
        total_passed = 0
        
        for test_name, test_func in tests:
            logger.info(f"Running test: {test_name}")
            try:
                result = await test_func()
                passed = result.get("success", False)
                if passed:
                    total_passed += 1
                    
                results.append({
                    "name": test_name,
                    "passed": passed,
                    "result": result,
                    "timestamp": datetime.utcnow().isoformat()
                })
                
                logger.info(f"Test {test_name}: {'PASSED' if passed else 'FAILED'}")
                
            except Exception as e:
                logger.error(f"Test {test_name} crashed: {e}")
                results.append({
                    "name": test_name,
                    "passed": False,
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                })
                
        return {
            "total_tests": len(tests),
            "passed": total_passed,
            "failed": len(tests) - total_passed,
            "success_rate": f"{(total_passed / len(tests) * 100):.1f}%",
            "results": results,
            "timestamp": datetime.utcnow().isoformat()
        }


async def main():
    """Run the test suite"""
    tester = VoiceAgentE2ETest()
    results = await tester.run_all_tests()
    
    print("\n" + "="*60)
    print("VOICE AGENT E2E TEST RESULTS")
    print("="*60)
    print(f"Total Tests: {results['total_tests']}")
    print(f"Passed: {results['passed']}")
    print(f"Failed: {results['failed']}")
    print(f"Success Rate: {results['success_rate']}")
    print("\nDetailed Results:")
    print("-"*60)
    
    for test in results['results']:
        status = "✅ PASS" if test['passed'] else "❌ FAIL"
        print(f"{status} {test['name']}")
        if not test['passed']:
            error = test.get('error') or test.get('result', {}).get('error', 'Unknown error')
            print(f"     Error: {error}")
            
    print("="*60)
    
    # Save results to file
    with open('/tmp/voice_agent_e2e_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to: /tmp/voice_agent_e2e_results.json")
    
    return results


if __name__ == "__main__":
    asyncio.run(main())