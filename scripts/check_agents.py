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
    
    # Check for the specific client
    client_id = "df91fd06-816f-4273-a903-5a4861277040"
    
    print(f"Checking client {client_id}...")
    
    # Get client info
    client_result = admin.table('clients').select('*').eq('id', client_id).execute()
    if client_result.data:
        client = client_result.data[0]
        print(f"✓ Found client: {client['name']}")
        print(f"  Domain: {client.get('domain', 'N/A')}")
        print(f"  Tier: {client.get('tier', 'N/A')}")
    else:
        print("✗ Client not found!")
        return
    
    # Get agents for this client
    print(f"\nChecking agents table schema...")
    # First, let's see what columns exist
    agents_sample = admin.table('agents').select('*').limit(1).execute()
    if agents_sample.data:
        print("Agent table columns:", list(agents_sample.data[0].keys()))
    
    # Try different column names
    for col in ['client_id', 'client', 'site_id', 'wordpress_site_id']:
        try:
            agents_result = admin.table('agents').select('*').eq(col, client_id).execute()
            print(f"✓ Found agents using column '{col}'")
            break
        except Exception as e:
            if 'does not exist' in str(e):
                continue
            raise
    else:
        # If no column matches, just get all agents
        print("Getting all agents...")
        agents_result = admin.table('agents').select('*').limit(10).execute()
    
    if agents_result.data:
        print(f"✓ Found {len(agents_result.data)} agents:")
        for agent in agents_result.data:
            print(f"  - Slug: {agent['slug']}, Name: {agent['name']}")
            print(f"    LLM: {agent.get('llm_provider', 'N/A')} / {agent.get('llm_model', 'N/A')}")
            print(f"    STT: {agent.get('stt_provider', 'N/A')} / TTS: {agent.get('tts_provider', 'N/A')}")
    else:
        print("✗ No agents found for this client!")
        
        # Check if there are any agents at all
        all_agents = admin.table('agents').select('slug, name, client_id').limit(5).execute()
        if all_agents.data:
            print("\nSample agents in system:")
            for agent in all_agents.data:
                print(f"  - Client: {agent['client_id'][:8]}..., Slug: {agent['slug']}")

if __name__ == "__main__":
    asyncio.run(main())