#!/usr/bin/env python3
"""
Associate global agents with the Autonomite client
"""
import os
import sys
from supabase import create_client, Client

# Add app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.config import settings

def main():
    """Associate global agents with Autonomite client"""
    
    # Initialize Supabase client
    supabase: Client = create_client(
        settings.supabase_url,
        settings.supabase_service_role_key
    )
    
    # The Autonomite client ID
    autonomite_client_id = "df91fd06-816f-4273-a903-5a4861277040"
    
    print("Fetching global agents from main agents table...")
    
    # Get all agents from the main agents table
    result = supabase.table("agents").select("*").execute()
    
    if not result.data:
        print("No agents found in main agents table")
        return
    
    print(f"Found {len(result.data)} agents in main table")
    
    # Update each agent to be associated with the Autonomite client
    for agent in result.data:
        agent_slug = agent.get("slug", "unknown")
        print(f"\nProcessing agent: {agent_slug}")
        
        try:
            # Update the agent with the client_id
            update_result = supabase.table("agents").update({
                "client_id": autonomite_client_id
            }).eq("id", agent["id"]).execute()
            
            if update_result.data:
                print(f"✅ Successfully associated {agent_slug} with Autonomite client")
            else:
                print(f"❌ Failed to update {agent_slug}")
                
        except Exception as e:
            print(f"❌ Error updating {agent_slug}: {str(e)}")
    
    print("\n✅ Agent association complete!")
    
    # Verify the update
    print("\nVerifying agents are now associated with Autonomite client...")
    verify_result = supabase.table("agents").select("slug, client_id").execute()
    
    for agent in verify_result.data:
        print(f"- {agent['slug']}: client_id = {agent.get('client_id', 'None')}")

if __name__ == "__main__":
    main()