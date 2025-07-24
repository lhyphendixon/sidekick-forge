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
    
    client_id = "df91fd06-816f-4273-a903-5a4861277040"
    
    print(f"Looking for junction tables...")
    
    # Common junction table names
    junction_tables = [
        'client_agents',
        'wordpress_site_agents', 
        'site_agents',
        'agent_sites',
        'wordpress_sites'
    ]
    
    for table in junction_tables:
        try:
            result = admin.table(table).select('*').limit(1).execute()
            print(f"✓ Found table '{table}' with columns:", list(result.data[0].keys()) if result.data else "empty")
            
            # If it's wordpress_sites, check the schema
            if table == 'wordpress_sites' and result.data:
                print(f"\nChecking wordpress_sites for client {client_id}...")
                # Try both id and client_id
                sites = admin.table('wordpress_sites').select('*').eq('client_id', client_id).execute()
                if not sites.data:
                    sites = admin.table('wordpress_sites').select('*').eq('id', client_id).execute()
                
                if sites.data:
                    site = sites.data[0]
                    print(f"✓ Found site: {site.get('domain', 'N/A')}")
                    
                    # Check for agents field
                    if 'agents' in site:
                        print(f"  Agents field: {site['agents']}")
                    
                    # Check for API keys
                    for key in site.keys():
                        if 'api' in key.lower() or 'key' in key.lower():
                            print(f"  {key}: {'***' if site[key] else 'Not set'}")
        except Exception as e:
            if 'does not exist' not in str(e):
                print(f"✗ Error with table '{table}': {e}")
    
    # Also check if agents have a slug we can use
    print("\n\nChecking if 'gpt' agent exists...")
    gpt_agent = admin.table('agents').select('*').eq('slug', 'gpt').execute()
    if gpt_agent.data:
        print(f"✓ Found 'gpt' agent: {gpt_agent.data[0]['name']}")
    else:
        print("✗ No agent with slug 'gpt' found")
        
        # List available slugs
        all_agents = admin.table('agents').select('slug, name').execute()
        if all_agents.data:
            print("\nAvailable agent slugs:")
            for agent in all_agents.data:
                print(f"  - {agent['slug']}: {agent['name']}")

if __name__ == "__main__":
    asyncio.run(main())