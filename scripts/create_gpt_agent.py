#!/usr/bin/env python3
import asyncio
import os
import sys
sys.path.insert(0, '/opt/autonomite-saas')

from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.integrations.supabase_client import supabase_manager
    
    # Initialize
    await supabase_manager.initialize()
    
    # Get admin client
    admin = supabase_manager.admin_client
    
    # Create a GPT agent
    agent_data = {
        "slug": "gpt",
        "name": "GPT Assistant",
        "description": "A helpful AI assistant powered by GPT",
        "system_prompt": "You are a helpful AI assistant. Be friendly, professional, and concise in your responses.",
        "enabled": True,
        "livekit_enabled": True,
        "voice_settings": {
            "voice": "alloy",
            "speed": 1.0
        },
        "provider_config": {
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
            "stt_provider": "deepgram",
            "tts_provider": "cartesia",
            "tts_model": "sonic-english"
        }
    }
    
    try:
        # Check if agent already exists
        existing = admin.table('agents').select('*').eq('slug', 'gpt').execute()
        if existing.data:
            print("Agent 'gpt' already exists!")
            return
        
        # Create the agent
        result = admin.table('agents').insert(agent_data).execute()
        
        if result.data:
            print(f"✓ Successfully created GPT agent with ID: {result.data[0]['id']}")
            print(f"  Slug: gpt")
            print(f"  Name: GPT Assistant")
            print(f"  LiveKit Enabled: True")
            print(f"  Provider Config: {result.data[0].get('provider_config', {})}")
        else:
            print("✗ Failed to create agent")
            
    except Exception as e:
        print(f"Error creating agent: {e}")

if __name__ == "__main__":
    asyncio.run(main())