#!/usr/bin/env python3
import asyncio
import os
import sys
sys.path.insert(0, '/opt/autonomite-saas')

from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.services.container_manager import container_manager
    
    # Initialize if needed
    await container_manager.initialize()
    
    # Test direct container spawn
    client_id = "df91fd06-816f-4273-a903-5a4861277040"
    agent_slug = "gpt"
    room_name = "test-direct-spawn"
    
    print(f"Testing direct container spawn...")
    print(f"Client ID: {client_id}")
    print(f"Agent Slug: {agent_slug}")
    print(f"Room Name: {room_name}")
    
    # Environment variables for the container
    env_vars = {
        "LIVEKIT_URL": "wss://litebridge-hw6srhvi.livekit.cloud",
        "LIVEKIT_API_KEY": "APIUtuiQ47BQBsk",
        "LIVEKIT_API_SECRET": "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM",
        "AGENT_SLUG": agent_slug,
        "ROOM_NAME": room_name,
        "CLIENT_ID": client_id,
        "PYTHONUNBUFFERED": "1",
        "LOG_LEVEL": "INFO"
    }
    
    # Deploy container
    try:
        result = await container_manager.deploy_agent_container(
            site_id=client_id,
            agent_slug=agent_slug,
            session_id=f"session_{room_name}",
            agent_config={
                "slug": agent_slug,
                "name": "GPT Assistant",
                "system_prompt": "You are a helpful AI assistant.",
                "room_name": room_name,
                "livekit_url": env_vars["LIVEKIT_URL"],
                "livekit_api_key": env_vars["LIVEKIT_API_KEY"],
                "livekit_api_secret": env_vars["LIVEKIT_API_SECRET"],
                "voice_id": "alloy",
                "stt_provider": "deepgram",
                "tts_provider": "cartesia",
                "deepgram_api_key": "69b8941c1598569b5f607cea260fe4d64b8bfa37",
                "cartesia_api_key": "sk_car_onb4wQxd93N9k4c6wtb5kF"
            },
            site_config={
                "domain": "test-client",
                "tier": "pro"
            }
        )
        
        print(f"\nContainer spawn result:")
        print(f"Result type: {type(result)}")
        print(f"Result keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}")
        print(f"Full result: {result}")
        
        if result.get('status') == 'running':
            print(f"\n✅ Container spawned successfully!")
            print(f"Container Name: {result.get('container_name', 'N/A')}")
            print(f"Check logs with: docker logs -f {result.get('container_name', 'N/A')}")
    except Exception as e:
        print(f"❌ Error spawning container: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())