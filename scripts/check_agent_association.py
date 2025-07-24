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
    
    print("Checking how agents are linked to clients/sites...")
    
    # Check wordpress_sites settings field
    sites = admin.table('wordpress_sites').select('*').eq('client_id', client_id).execute()
    if sites.data:
        site = sites.data[0]
        print(f"\nWordPress site for client {client_id}:")
        print(f"  Site URL: {site.get('site_url', 'N/A')}")
        print(f"  Site Name: {site.get('site_name', 'N/A')}")
        
        # Check settings field
        if 'settings' in site and site['settings']:
            print(f"  Settings: {site['settings']}")
            
            # If settings contains agents info
            if isinstance(site['settings'], dict):
                if 'agents' in site['settings']:
                    print(f"  Configured agents: {site['settings']['agents']}")
                if 'default_agent' in site['settings']:
                    print(f"  Default agent: {site['settings']['default_agent']}")
        
        # Check metadata field
        if 'metadata' in site and site['metadata']:
            print(f"  Metadata: {site['metadata']}")
    
    # Check if there's a client_agents or site_agents table
    print("\n\nChecking for agent configuration in settings...")
    
    # Let's also check the clients table for agent configuration
    client_result = admin.table('clients').select('*').eq('id', client_id).execute()
    if client_result.data:
        client = client_result.data[0]
        print(f"\nClient settings:")
        for key in ['settings', 'metadata', 'agents', 'default_agent']:
            if key in client and client[key]:
                print(f"  {key}: {client[key]}")
    
    # Check if we should just use one of the existing agents
    print("\n\nRecommendation:")
    print("The frontend is trying to use agent slug 'gpt' which doesn't exist.")
    print("You should either:")
    print("1. Create an agent with slug 'gpt' in the agents table")
    print("2. Update the frontend to use one of these existing slugs:")
    print("   - autonomite (seems like the main one)")
    print("   - litebridge")
    print("   - roi")

if __name__ == "__main__":
    asyncio.run(main())