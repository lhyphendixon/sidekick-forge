#!/usr/bin/env python3
"""
Test the new v2 multi-tenant endpoints
"""
import httpx
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_v2_endpoints():
    """Test the v2 multi-tenant endpoints"""
    base_url = "http://localhost:8000"
    
    # The Autonomite client ID from the platform database
    autonomite_client_id = "11389177-e4d8-49a9-9a00-f77bb4de6592"
    
    async with httpx.AsyncClient() as client:
        # Test v2 clients endpoint
        logger.info("Testing /api/v2/clients endpoint...")
        try:
            response = await client.get(f"{base_url}/api/v2/clients")
            if response.status_code == 200:
                clients = response.json()
                logger.info(f"✅ V2 Clients endpoint working - {len(clients)} clients")
                for c in clients:
                    logger.info(f"   - {c['name']} (ID: {c['id']})")
            elif response.status_code == 401:
                logger.info("⚠️ V2 Clients endpoint requires authentication")
            else:
                logger.error(f"❌ V2 Clients endpoint failed: {response.status_code}")
        except Exception as e:
            logger.error(f"❌ V2 Clients endpoint error: {e}")
        
        # Test v2 agents endpoint
        logger.info(f"\nTesting /api/v2/agents endpoint for Autonomite client...")
        try:
            response = await client.get(
                f"{base_url}/api/v2/agents",
                params={"client_id": autonomite_client_id}
            )
            if response.status_code == 200:
                agents = response.json()
                logger.info(f"✅ V2 Agents endpoint working - {len(agents)} agents")
                for agent in agents[:3]:
                    logger.info(f"   - {agent['name']} (slug: {agent['slug']})")
            elif response.status_code == 401:
                logger.info("⚠️ V2 Agents endpoint requires authentication")
            else:
                logger.error(f"❌ V2 Agents endpoint failed: {response.status_code}")
        except Exception as e:
            logger.error(f"❌ V2 Agents endpoint error: {e}")
        
        # Test v2 trigger endpoint
        logger.info(f"\nTesting /api/v2/trigger-agent endpoint...")
        try:
            trigger_payload = {
                "agent_slug": "litebridge",
                "client_id": autonomite_client_id,
                "mode": "text",
                "message": "Hello from multi-tenant test",
                "user_id": "test_user_123"
            }
            
            response = await client.post(
                f"{base_url}/api/v2/trigger-agent",
                json=trigger_payload
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✅ V2 Trigger endpoint working")
                logger.info(f"   - Success: {result.get('success')}")
                logger.info(f"   - Architecture: {result.get('agent_info', {}).get('architecture', 'unknown')}")
            elif response.status_code == 401:
                logger.info("⚠️ V2 Trigger endpoint requires authentication")
            else:
                logger.error(f"❌ V2 Trigger endpoint failed: {response.status_code}")
                logger.error(f"   Response: {response.text}")
        except Exception as e:
            logger.error(f"❌ V2 Trigger endpoint error: {e}")


async def main():
    """Run the tests"""
    logger.info("Testing V2 Multi-tenant Endpoints")
    logger.info("=" * 60)
    
    await test_v2_endpoints()
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ V2 endpoint tests completed")
    logger.info("\nThe v2 endpoints are available at:")
    logger.info("  - GET  /api/v2/clients")
    logger.info("  - GET  /api/v2/agents?client_id=<uuid>")
    logger.info("  - POST /api/v2/trigger-agent")
    logger.info("\nThese work alongside the existing v1 endpoints for gradual migration.")


if __name__ == "__main__":
    asyncio.run(main())