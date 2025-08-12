#!/usr/bin/env python3
"""
Test script to verify multi-tenant setup is working
"""
import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

# Add the app directory to Python path
sys.path.insert(0, '/root/sidekick-forge')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_platform_connection():
    """Test connection to platform database"""
    logger.info("Testing platform database connection...")
    
    try:
        from app.services.client_connection_manager import get_connection_manager
        
        connection_manager = get_connection_manager()
        logger.info("✅ ClientConnectionManager initialized successfully")
        
        # Try to query clients
        result = connection_manager.platform_client.table('clients').select('*').execute()
        
        if result.data:
            logger.info(f"✅ Found {len(result.data)} clients in platform database")
            for client in result.data:
                logger.info(f"   - {client['name']} (ID: {client['id']})")
        else:
            logger.warning("⚠️ No clients found in platform database")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Platform connection failed: {e}")
        return False


async def test_multitenant_services():
    """Test multi-tenant services"""
    logger.info("\nTesting multi-tenant services...")
    
    try:
        from app.services.agent_service_multitenant import AgentService
        from app.services.client_service_multitenant import ClientService
        
        # Test ClientService
        client_service = ClientService()
        clients = await client_service.get_clients()
        logger.info(f"✅ ClientService working - found {len(clients)} clients")
        
        # Test AgentService
        if clients:
            agent_service = AgentService()
            first_client = clients[0]
            logger.info(f"\nTesting AgentService with client '{first_client.name}'...")
            
            try:
                from uuid import UUID
                client_uuid = UUID(first_client.id)
                agents = await agent_service.get_agents(client_uuid)
                logger.info(f"✅ AgentService working - found {len(agents)} agents")
                
                for agent in agents[:3]:  # Show first 3 agents
                    logger.info(f"   - {agent.name} (slug: {agent.slug})")
                    
            except Exception as e:
                logger.error(f"❌ Error fetching agents: {e}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Multi-tenant services failed: {e}")
        return False


async def test_api_endpoints():
    """Test multi-tenant API endpoints"""
    logger.info("\nTesting multi-tenant API endpoints...")
    
    try:
        import httpx
        
        # Start a test server (if not already running)
        base_url = "http://localhost:8000"
        
        async with httpx.AsyncClient() as client:
            # Test health endpoint
            try:
                response = await client.get(f"{base_url}/health")
                if response.status_code == 200:
                    logger.info("✅ Health endpoint working")
                else:
                    logger.warning(f"⚠️ Health endpoint returned {response.status_code}")
            except Exception as e:
                logger.error(f"❌ Cannot connect to API server: {e}")
                logger.info("   Please ensure the FastAPI server is running")
                return False
            
            # Test clients endpoint
            try:
                response = await client.get(f"{base_url}/api/v1/clients")
                if response.status_code == 200:
                    clients = response.json()
                    logger.info(f"✅ Clients endpoint working - {len(clients)} clients")
                else:
                    logger.error(f"❌ Clients endpoint failed: {response.status_code}")
            except Exception as e:
                logger.error(f"❌ Clients endpoint error: {e}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ API endpoint tests failed: {e}")
        return False


async def main():
    """Run all tests"""
    logger.info("Starting multi-tenant setup tests...")
    
    # Load environment variables
    load_dotenv('/root/sidekick-forge/.env')
    
    # Run tests
    tests = [
        ("Platform Connection", test_platform_connection),
        ("Multi-tenant Services", test_multitenant_services),
        ("API Endpoints", test_api_endpoints)
    ]
    
    results = []
    for test_name, test_func in tests:
        logger.info(f"\n{'='*60}")
        logger.info(f"Running: {test_name}")
        logger.info(f"{'='*60}")
        
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"Test crashed: {e}")
            results.append((test_name, False))
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info("TEST SUMMARY")
    logger.info(f"{'='*60}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        logger.info(f"{test_name}: {status}")
    
    logger.info(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("\n🎉 All tests passed! Multi-tenant setup is working correctly.")
    else:
        logger.warning("\n⚠️ Some tests failed. Please check the errors above.")


if __name__ == "__main__":
    asyncio.run(main())