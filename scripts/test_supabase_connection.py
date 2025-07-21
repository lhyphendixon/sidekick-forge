#!/usr/bin/env python3
"""
Test Supabase connection and credentials
"""
import httpx
import json

# Credentials from the log
supabase_url = "https://yuowazxcxwhczywurmmw.supabase.co"
service_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"

# Test agent_configurations table
print("Testing agent_configurations table access...")
response = httpx.get(
    f"{supabase_url}/rest/v1/agent_configurations",
    headers={
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json"
    },
    params={"select": "*", "limit": "5"}
)

print(f"Status: {response.status_code}")
print(f"Headers: {response.headers}")
if response.status_code == 200:
    data = response.json()
    print(f"Found {len(data)} agent configurations")
    if data:
        print("\nFirst agent configuration:")
        print(json.dumps(data[0], indent=2))
else:
    print(f"Error: {response.text}")

# Test agents table
print("\n" + "="*50)
print("Testing agents table access...")
response = httpx.get(
    f"{supabase_url}/rest/v1/agents",
    headers={
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json"
    },
    params={"select": "*", "limit": "5"}
)

print(f"Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print(f"Found {len(data)} agents")
    if data:
        print("\nAgent names:")
        for agent in data:
            print(f"  - {agent.get('name', 'Unknown')} ({agent.get('slug', 'unknown')})")
else:
    print(f"Error: {response.text}")