#!/usr/bin/env python3
"""
Update agent associations via the API
"""
import requests
import json

BASE_URL = "http://localhost:8000"

def main():
    """Update agents to be associated with Autonomite client"""
    
    autonomite_client_id = "df91fd06-816f-4273-a903-5a4861277040"
    
    # List of known agents from the admin page
    agent_slugs = ["litebridge", "roi", "autonomite", "farah", "clarence-coherence", "gpt"]
    
    print("Updating agent associations via API...")
    
    for slug in agent_slugs:
        print(f"\nUpdating agent: {slug}")
        
        # Update the agent to set client_id
        update_url = f"{BASE_URL}/api/v1/agents/client/global/{slug}"
        
        payload = {
            "client_id": autonomite_client_id
        }
        
        try:
            response = requests.put(update_url, json=payload)
            
            if response.status_code == 200:
                print(f"✅ Successfully updated {slug}")
            else:
                print(f"❌ Failed to update {slug}: {response.status_code}")
                print(f"   Response: {response.text}")
                
        except Exception as e:
            print(f"❌ Error updating {slug}: {str(e)}")
    
    print("\n✅ Update complete!")
    
    # Test fetching agents for the client
    print("\nTesting agent fetch for Autonomite client...")
    test_url = f"{BASE_URL}/api/v1/agents/client/{autonomite_client_id}"
    
    try:
        response = requests.get(test_url)
        if response.status_code == 200:
            agents = response.json()
            print(f"Found {len(agents)} agents for Autonomite client:")
            for agent in agents:
                print(f"- {agent.get('slug', 'unknown')}")
        else:
            print(f"Failed to fetch agents: {response.status_code}")
    except Exception as e:
        print(f"Error fetching agents: {str(e)}")

if __name__ == "__main__":
    main()