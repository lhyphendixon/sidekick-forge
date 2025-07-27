#!/usr/bin/env python3
"""
Test the final fixes for LiveKit integration
"""
import httpx
import asyncio
import logging
import json
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_voice_trigger():
    """Test voice trigger with all fixes applied"""
    base_url = "http://localhost:8000"
    
    # Use the Autonomite client from multi-tenant setup
    autonomite_client_id = "11389177-e4d8-49a9-9a00-f77bb4de6592"
    
    async with httpx.AsyncClient() as client:
        logger.info("Testing voice trigger with final fixes...")
        
        room_name = f"test_final_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Test v2 endpoint with multi-tenant support
        trigger_payload = {
            "agent_slug": "litebridge",
            "client_id": autonomite_client_id,
            "mode": "voice",
            "room_name": room_name,
            "user_id": "test_user_final",
            "session_id": "test_session_final"
        }
        
        try:
            response = await client.post(
                f"{base_url}/api/v2/trigger-agent",
                json=trigger_payload,
                timeout=30.0
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✅ Voice trigger successful!")
                
                # Check room creation
                room_data = result.get('data', {})
                logger.info(f"   - Room: {room_data.get('room_name')}")
                logger.info(f"   - Status: {room_data.get('status')}")
                
                # Check if LiveKit config is properly set
                livekit_config = room_data.get('livekit_config', {})
                if livekit_config:
                    logger.info(f"   - LiveKit URL: {livekit_config.get('server_url')}")
                    logger.info(f"   - User token: {'✓' if livekit_config.get('user_token') else '✗'}")
                
                # Check metadata structure
                room_info = room_data.get('room_info', {})
                if room_info and 'metadata' in room_info:
                    metadata = room_info['metadata']
                    logger.info("\n📋 Metadata structure check:")
                    logger.info(f"   - Has client_id: {'client_id' in metadata}")
                    logger.info(f"   - Has api_keys: {'api_keys' in metadata}")
                    logger.info(f"   - Has system_prompt: {'system_prompt' in metadata}")
                    logger.info(f"   - Has voice_settings: {'voice_settings' in metadata}")
                    
                    # This is what the worker expects
                    if 'api_keys' in metadata:
                        api_keys = metadata['api_keys']
                        available_keys = [k for k, v in api_keys.items() if v and not str(v).startswith('test')]
                        logger.info(f"   - Available API keys: {len(available_keys)}")
                
                return True
            else:
                logger.error(f"❌ Voice trigger failed: {response.status_code}")
                logger.error(f"   Response: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Voice trigger error: {e}")
            return False


async def check_room_details(room_name: str):
    """Check if room was created with correct agent dispatch settings"""
    base_url = "http://localhost:8000"
    
    async with httpx.AsyncClient() as client:
        logger.info(f"\nChecking room details for {room_name}...")
        
        # We would need an endpoint to check room details
        # For now, we can only verify through the trigger response


async def main():
    """Run all tests"""
    logger.info("Testing Final LiveKit Fixes")
    logger.info("=" * 60)
    
    success = await test_voice_trigger()
    
    logger.info("\n" + "=" * 60)
    if success:
        logger.info("✅ All fixes appear to be working correctly!")
        logger.info("\nNext steps:")
        logger.info("1. Test with actual WordPress plugin")
        logger.info("2. Monitor agent worker logs: docker logs -f sidekick-forge_agent-worker_1")
        logger.info("3. Check LiveKit dashboard for room creation")
    else:
        logger.info("❌ Tests failed - check the errors above")


if __name__ == "__main__":
    asyncio.run(main())