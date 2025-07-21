#!/usr/bin/env python3
"""
Run comprehensive diagnostics on the voice agent system
"""
import asyncio
import sys
import os
import json
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tests.test_voice_agent_e2e import VoiceAgentE2ETest
from app.utils.diagnostics import agent_diagnostics


async def run_diagnostics():
    """Run all diagnostic tests"""
    print("="*60)
    print("AUTONOMITE VOICE AGENT DIAGNOSTICS")
    print("="*60)
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 1. Test LiveKit connectivity
    print("1. Testing LiveKit connectivity...")
    livekit_result = await agent_diagnostics.test_livekit_connection(
        server_url="wss://litebridge-hw6srhvi.livekit.cloud",
        api_key="APIUtuiQ47BQBsk",
        api_secret="rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
    )
    
    if livekit_result["success"]:
        print(f"   ✅ LiveKit connected - {livekit_result['rooms_count']} rooms found")
    else:
        print(f"   ❌ LiveKit connection failed: {livekit_result['error']}")
    
    # 2. Check containers
    print("\n2. Checking agent containers...")
    try:
        import docker
        client = docker.from_env()
        containers = [c for c in client.containers.list(all=True) if "agent_" in c.name]
        
        print(f"   Found {len(containers)} agent containers:")
        for container in containers:
            status_icon = "🟢" if container.status == "running" else "🔴"
            print(f"   {status_icon} {container.name}: {container.status}")
            
            # Test container health
            if container.status == "running":
                result = await agent_diagnostics.test_agent_container(container.name)
                if result["health"] == "healthy":
                    print(f"      ✅ Container healthy")
                else:
                    print(f"      ⚠️ Container health: {result['health']}")
                    
                # Show any issues
                if result.get("issues"):
                    print(f"      Issues found: {len(result['issues'])}")
                    for issue in result["issues"][:3]:  # Show first 3 issues
                        print(f"        - {issue['type']}: {issue['line'][:80]}...")
                        
    except Exception as e:
        print(f"   ❌ Container check failed: {e}")
    
    # 3. Run E2E tests
    print("\n3. Running end-to-end tests...")
    tester = VoiceAgentE2ETest()
    test_results = await tester.run_all_tests()
    
    print(f"\n   Test Summary:")
    print(f"   Total: {test_results['total_tests']}")
    print(f"   Passed: {test_results['passed']} ✅")
    print(f"   Failed: {test_results['failed']} ❌")
    print(f"   Success Rate: {test_results['success_rate']}")
    
    # 4. Show failing tests
    if test_results['failed'] > 0:
        print("\n   Failed Tests:")
        for test in test_results['results']:
            if not test['passed']:
                error = test.get('error') or test.get('result', {}).get('error', 'Unknown')
                print(f"   ❌ {test['name']}: {error}")
    
    # 5. Generate recommendations
    print("\n" + "="*60)
    print("RECOMMENDATIONS:")
    print("="*60)
    
    recommendations = []
    
    if not livekit_result["success"]:
        recommendations.append("- Fix LiveKit connectivity issues")
        recommendations.append("  Check API credentials and server URL")
        
    if test_results['failed'] > 0:
        for test in test_results['results']:
            if not test['passed'] and 'container' in test['name'].lower():
                recommendations.append("- Container issues detected")
                recommendations.append("  Run: docker logs <container_name>")
                break
                
            if not test['passed'] and 'config' in test['name'].lower():
                recommendations.append("- Agent configuration issues")
                recommendations.append("  Check API keys in Supabase")
                break
                
    if not recommendations:
        recommendations.append("✅ All systems operational!")
        
    for rec in recommendations:
        print(rec)
    
    # Save full report
    report = {
        "timestamp": datetime.now().isoformat(),
        "livekit": livekit_result,
        "tests": test_results,
        "recommendations": recommendations
    }
    
    report_file = f"/tmp/voice_agent_diagnostic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
        
    print(f"\nFull report saved to: {report_file}")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(run_diagnostics())