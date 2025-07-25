#!/usr/bin/env python3
"""
Update agents via direct SQL using Supabase REST API
"""
import requests
import json
import base64

# Supabase configuration
SUPABASE_URL = "https://yuowazxcxwhczywurmmw.supabase.co"
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.tN4FaKbNTCPU7ooCh9kH-qZcxeHCDo46Y0LfOjzKO0o"
AUTONOMITE_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"

def main():
    """Update agents to be associated with Autonomite client"""
    
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    
    # First, let's see what agents we have
    print("Fetching all agents...")
    
    get_url = f"{SUPABASE_URL}/rest/v1/agents"
    
    try:
        response = requests.get(get_url, headers=headers)
        
        if response.status_code == 200:
            agents = response.json()
            print(f"Found {len(agents)} agents")
            
            for agent in agents:
                print(f"- {agent.get('slug')}: client_id = {agent.get('client_id', 'null')}")
                
            # Now update each agent
            print("\nUpdating agents to be associated with Autonomite client...")
            
            for agent in agents:
                agent_id = agent.get('id')
                agent_slug = agent.get('slug')
                
                update_url = f"{SUPABASE_URL}/rest/v1/agents?id=eq.{agent_id}"
                
                update_data = {
                    "client_id": AUTONOMITE_CLIENT_ID
                }
                
                update_response = requests.patch(update_url, headers=headers, json=update_data)
                
                if update_response.status_code in [200, 204]:
                    print(f"✅ Updated {agent_slug}")
                else:
                    print(f"❌ Failed to update {agent_slug}: {update_response.status_code}")
                    print(f"   Response: {update_response.text}")
                    
        else:
            print(f"Failed to fetch agents: {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"Error: {str(e)}")
        
    # Verify the update
    print("\nVerifying updates...")
    verify_response = requests.get(get_url, headers=headers)
    
    if verify_response.status_code == 200:
        agents = verify_response.json()
        print(f"\nAgent associations after update:")
        for agent in agents:
            print(f"- {agent.get('slug')}: client_id = {agent.get('client_id', 'null')}")
    
if __name__ == "__main__":
    main()