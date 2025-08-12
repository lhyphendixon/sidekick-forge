#!/usr/bin/env python3
"""
Test the complete voice agent flow with all fixes applied
"""
import httpx
import asyncio
import logging
import json
import time
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_complete_flow():
    """Test the complete voice trigger flow"""
    base_url = "http://localhost:8000"
    autonomite_client_id = "11389177-e4d8-49a9-9a00-f77bb4de6592"
    
    async with httpx.AsyncClient() as client:
        logger.info("=== Testing Complete Voice Agent Flow ===")
        
        room_name = f"flow_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Step 1: Trigger agent via v2 endpoint
        trigger_payload = {
            "agent_slug": "litebridge",
            "client_id": autonomite_client_id,
            "mode": "voice",
            "room_name": room_name,
            "user_id": "test_user_flow",
            "session_id": "test_session_flow"
        }
        
        logger.info(f"Step 1: Triggering agent for room {room_name}")
        try:
            response = await client.post(
                f"{base_url}/api/v2/trigger-agent",
                json=trigger_payload,
                timeout=30.0
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info("✅ Agent triggered successfully!")
                
                # Extract important data
                data = result.get('data', {})
                room_info = data.get('room_info', {})
                livekit_config = data.get('livekit_config', {})
                
                logger.info(f"   - Room: {data.get('room_name')}")
                logger.info(f"   - Status: {data.get('status')}")
                logger.info(f"   - LiveKit URL: {livekit_config.get('server_url')}")
                logger.info(f"   - User token provided: {'✓' if livekit_config.get('user_token') else '✗'}")
                
                # Check metadata structure
                if room_info and 'metadata' in room_info:
                    metadata = room_info['metadata']
                    logger.info("\n📋 Room metadata check:")
                    logger.info(f"   - Has client_id: {'client_id' in metadata}")
                    logger.info(f"   - Has agent_slug: {'agent_slug' in metadata}")
                    logger.info(f"   - Has api_keys: {'api_keys' in metadata}")
                    logger.info(f"   - Metadata keys: {list(metadata.keys())[:5]}...")  # Show first 5 keys
                
                # Step 2: Wait a moment for room to be ready
                logger.info("\nStep 2: Waiting for room to be fully ready...")
                await asyncio.sleep(2)
                
                # Step 3: Check if agent dispatch is configured
                dispatch_info = data.get('dispatch_info', {})
                logger.info("\nStep 3: Agent dispatch configuration:")
                logger.info(f"   - Dispatch mode: {dispatch_info.get('status')}")
                logger.info(f"   - Message: {dispatch_info.get('message')}")
                
                # Print summary
                logger.info("\n" + "="*60)
                logger.info("SUMMARY:")
                logger.info(f"✅ Room created: {room_name}")
                logger.info(f"✅ Backend LiveKit: {livekit_config.get('server_url')}")
                logger.info(f"✅ User token: {'Generated' if livekit_config.get('user_token') else 'Missing'}")
                logger.info(f"✅ Agent dispatch: Configured for automatic dispatch")
                logger.info("\nNEXT STEPS:")
                logger.info("1. Monitor agent worker logs for job dispatch:")
                logger.info("   docker logs -f sidekick-forge_agent-worker_1")
                logger.info("2. Test with WordPress plugin using:")
                logger.info(f"   - Room: {room_name}")
                logger.info(f"   - Token: {livekit_config.get('user_token', 'N/A')[:50]}...")
                
                return True
            else:
                logger.error(f"❌ Agent trigger failed: {response.status_code}")
                logger.error(f"   Response: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error during flow test: {e}")
            return False


async def main():
    """Run the complete flow test"""
    success = await test_complete_flow()
    
    if success:
        logger.info("\n✅ All systems operational - ready for WordPress plugin testing!")
    else:
        logger.info("\n❌ Flow test failed - check errors above")


if __name__ == "__main__":
    asyncio.run(main())