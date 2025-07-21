#!/usr/bin/env python3
"""
Create a global 'clarence-coherence' agent in the platform's Supabase
This is a workaround for the 401 error when containers try to query for this agent
"""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.integrations.supabase_client import supabase_manager


async def create_global_agent():
    """Create the global clarence-coherence agent"""
    try:
        # Initialize Supabase
        await supabase_manager.initialize()
        
        # Check if agent already exists
        existing = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("agents")
            .select("*")
            .eq("slug", "clarence-coherence")
        )
        
        if existing:
            print("✅ Agent 'clarence-coherence' already exists")
            return
        
        # Create the agent
        agent_data = {
            "slug": "clarence-coherence",
            "name": "Clarence Coherence",
            "description": "Default AI assistant for Autonomite platform",
            "system_prompt": "You are clarence-coherence, a helpful AI assistant with access to user context and knowledge. You provide personalized support based on user profiles and conversation history.",
            "enabled": True,
            "voice_settings": {
                "provider": "livekit",
                "voice_id": "alloy",
                "temperature": 0.7,
                "provider_config": {}
            },
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        result = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("agents").insert(agent_data)
        )
        
        if result:
            print("✅ Successfully created global agent 'clarence-coherence'")
            print(f"   Agent ID: {result[0].get('id', 'Unknown')}")
        else:
            print("❌ Failed to create agent")
            
    except Exception as e:
        print(f"❌ Error creating global agent: {e}")
        sys.exit(1)
    finally:
        await supabase_manager.close()


if __name__ == "__main__":
    asyncio.run(create_global_agent())