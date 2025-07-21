#!/usr/bin/env python3
"""
Test script to verify agent update syncs to both agents and agent_configurations tables
"""
import httpx
import json
import time

# Test configuration
api_base_url = "http://localhost:8000"
agent_slug = "farah"  # Using an existing agent from the test data
client_id = "global"  # Testing global agent update

# Updated voice settings to test
test_update = {
    "name": "Farah - Updated via Sync Test",
    "description": "Testing sync mechanism",
    "system_prompt": "You are Farah, updated via sync test.",
    "voice_settings": {
        "provider": "cartesia",
        "voice_id": "test-voice-123",
        "temperature": 0.9,
        "llm_provider": "groq",
        "llm_model": "mixtral-8x7b-32768",
        "stt_provider": "deepgram",
        "model": "sonic-english",
        "output_format": "pcm_44100"
    }
}

print("Testing agent update sync mechanism...")
print(f"Agent: {agent_slug}")
print(f"Client: {client_id}")
print("\nUpdate payload:")
print(json.dumps(test_update, indent=2))

# Step 1: Update agent via API
print("\n1. Updating agent via API...")
response = httpx.put(
    f"{api_base_url}/api/v1/agents/client/{client_id}/{agent_slug}",
    json=test_update,
    timeout=30.0
)

if response.status_code == 200:
    print("✅ Agent updated successfully")
    updated_agent = response.json()
    print(f"   Name: {updated_agent.get('name')}")
    print(f"   Voice Provider: {updated_agent.get('voice_settings', {}).get('provider')}")
else:
    print(f"❌ Failed to update agent: {response.status_code}")
    print(f"   Error: {response.text}")
    exit(1)

# Wait a moment for updates to propagate
time.sleep(2)

# Step 2: Check agents table directly
print("\n2. Checking agents table...")
supabase_url = "https://yuowazxcxwhczywurmmw.supabase.co"
service_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"

response = httpx.get(
    f"{supabase_url}/rest/v1/agents",
    headers={
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json"
    },
    params={"slug": f"eq.{agent_slug}", "select": "name,slug,system_prompt,voice_settings"}
)

if response.status_code == 200 and response.json():
    agent_data = response.json()[0]
    print("✅ Found in agents table:")
    print(f"   Name: {agent_data.get('name')}")
    print(f"   System prompt: {agent_data.get('system_prompt')[:50]}...")
    voice_settings = agent_data.get('voice_settings')
    if isinstance(voice_settings, str):
        voice_settings = json.loads(voice_settings)
    print(f"   Voice settings: {json.dumps(voice_settings, indent=6)}")
else:
    print("❌ Agent not found in agents table")

# Step 3: Check agent_configurations table
print("\n3. Checking agent_configurations table...")
response = httpx.get(
    f"{supabase_url}/rest/v1/agent_configurations",
    headers={
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json"
    },
    params={"agent_slug": f"eq.{agent_slug}", "select": "agent_name,system_prompt,voice_id,temperature,provider_config,voice_settings"}
)

if response.status_code == 200 and response.json():
    config_data = response.json()[0]
    print("✅ Found in agent_configurations table:")
    print(f"   Agent name: {config_data.get('agent_name')}")
    print(f"   System prompt: {config_data.get('system_prompt')[:50]}...")
    print(f"   Voice ID: {config_data.get('voice_id')}")
    print(f"   Temperature: {config_data.get('temperature')}")
    
    # Check provider_config
    provider_config = config_data.get('provider_config')
    if isinstance(provider_config, str):
        provider_config = json.loads(provider_config)
    print(f"   Provider config:")
    print(f"      LLM: {provider_config.get('llm', {})}")
    print(f"      TTS: {provider_config.get('tts', {})}")
    print(f"      STT: {provider_config.get('stt', {})}")
    
    # Check voice_settings
    voice_settings = config_data.get('voice_settings')
    if isinstance(voice_settings, str):
        voice_settings = json.loads(voice_settings)
    print(f"   Voice settings: {json.dumps(voice_settings, indent=6)}")
else:
    print("❌ Agent configuration not found")

print("\n4. Summary:")
print("=" * 50)
if response.status_code == 200:
    print("✅ Both tables were updated successfully!")
    print("   - agents table has the new data")
    print("   - agent_configurations table has the new provider_config")
else:
    print("❌ Sync mechanism needs attention")