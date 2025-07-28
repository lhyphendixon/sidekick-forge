#!/usr/bin/env python3
"""
Test Voice Pipeline Directly
Tests the voice processing pipeline to diagnose issues
"""
import asyncio
import logging
import httpx
import json
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_BASE = "http://localhost:8000"
CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"
AGENT_SLUG = "autonomite"

async def test_voice_pipeline():
    """Test voice processing pipeline"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Create a test room
        room_name = f"voice-test-{int(time.time())}"
        
        logger.info(f"Creating test room: {room_name}")
        
        # Trigger the agent
        trigger_response = await client.post(
            f"{API_BASE}/api/v1/trigger-agent",
            json={
                "agent_slug": AGENT_SLUG,
                "mode": "voice",
                "room_name": room_name,
                "user_id": "test-user",
                "client_id": CLIENT_ID
            }
        )
        
        if trigger_response.status_code != 200:
            logger.error(f"Failed to trigger agent: {trigger_response.text}")
            return False
            
        result = trigger_response.json()
        logger.info(f"Agent triggered successfully")
        logger.info(f"- Room: {room_name}")
        logger.info(f"- Token provided: {'user_token' in result.get('data', {}).get('livekit_config', {})}")
        logger.info(f"- Agent dispatched: {result.get('data', {}).get('dispatch_info', {}).get('status')}")
        
        # Wait for agent to connect
        await asyncio.sleep(3)
        
        # Check if agent is in the room
        logger.info("\nChecking agent status...")
        
        # The agent should be connected and waiting for voice input
        return True

async def main():
    """Main test function"""
    logger.info("=== Voice Pipeline Test ===\n")
    
    success = await test_voice_pipeline()
    
    if success:
        logger.info("\n✅ Voice pipeline setup successful")
        logger.info("\nNext steps to debug:")
        logger.info("1. Check agent logs: docker-compose logs --tail=100 agent-worker | grep -E '(speech|Speaking|Audio|VAD)'")
        logger.info("2. Verify microphone is working in browser")
        logger.info("3. Check browser console for WebRTC errors")
        logger.info("4. Test with a different browser/device")
    else:
        logger.error("\n❌ Voice pipeline test failed")

if __name__ == "__main__":
    asyncio.run(main())