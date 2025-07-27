#!/usr/bin/env python3
"""
Test the v2 trigger endpoint with voice mode
"""
import httpx
import asyncio
import logging
import json
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_voice_trigger():
    """Test voice trigger with v2 endpoint"""
    base_url = "http://localhost:8000"
    autonomite_client_id = "11389177-e4d8-49a9-9a00-f77bb4de6592"
    
    async with httpx.AsyncClient() as client:
        # Test voice trigger
        logger.info("Testing voice trigger with v2 endpoint...")
        
        room_name = f"test_room_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        trigger_payload = {
            "agent_slug": "litebridge",
            "client_id": autonomite_client_id,
            "mode": "voice",
            "room_name": room_name,
            "user_id": "test_user_123",
            "session_id": "test_session_456"
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
                logger.info(f"   - Room: {result.get('data', {}).get('room_name')}")
                logger.info(f"   - Status: {result.get('data', {}).get('status')}")
                logger.info(f"   - Architecture: {result.get('data', {}).get('architecture', 'unknown')}")
                
                # Check if we got LiveKit config
                livekit_config = result.get('data', {}).get('livekit_config')
                if livekit_config:
                    logger.info(f"   - LiveKit URL: {livekit_config.get('server_url')}")
                    logger.info(f"   - Token provided: {'user_token' in livekit_config}")
            else:
                logger.error(f"❌ Voice trigger failed: {response.status_code}")
                logger.error(f"   Response: {response.text}")
                
        except Exception as e:
            logger.error(f"❌ Voice trigger error: {e}")


async def test_agent_auto_detection():
    """Test agent trigger without client_id (auto-detection)"""
    base_url = "http://localhost:8000"
    
    async with httpx.AsyncClient() as client:
        logger.info("\nTesting agent auto-detection (no client_id)...")
        
        trigger_payload = {
            "agent_slug": "litebridge",
            # No client_id provided - should auto-detect
            "mode": "text",
            "message": "Hello from auto-detection test",
            "user_id": "test_user_789"
        }
        
        try:
            response = await client.post(
                f"{base_url}/api/v2/trigger-agent",
                json=trigger_payload,
                timeout=30.0
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✅ Auto-detection successful!")
                agent_info = result.get('agent_info', {})
                logger.info(f"   - Agent: {agent_info.get('name')}")
                logger.info(f"   - Client: {agent_info.get('client_name')}")
                logger.info(f"   - Client ID: {agent_info.get('client_id')}")
            else:
                logger.error(f"❌ Auto-detection failed: {response.status_code}")
                logger.error(f"   Response: {response.text}")
                
        except Exception as e:
            logger.error(f"❌ Auto-detection error: {e}")


async def main():
    """Run all tests"""
    logger.info("Testing V2 Voice Trigger and Auto-Detection")
    logger.info("=" * 60)
    
    await test_voice_trigger()
    await test_agent_auto_detection()
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ V2 advanced tests completed")


if __name__ == "__main__":
    asyncio.run(main())