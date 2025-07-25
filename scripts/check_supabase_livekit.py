#!/usr/bin/env python3
"""Check LiveKit credentials in Supabase"""
import json
from supabase import create_client

# Supabase credentials
SUPABASE_URL = "https://yuowazxcxwhczywurmmw.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzU3ODQ1NzMsImV4cCI6MjA1MTM2MDU3M30.SmqTIWrScKQWkJ2_PICWVJYpRSKfvqkRcjMMt0ApH1U"

# Create client
supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Get Autonomite client
client_id = "df91fd06-816f-4273-a903-5a4861277040"

result = supabase.table("clients").select("*").eq("id", client_id).execute()

if result.data:
    client = result.data[0]
    print(f"✅ Found client: {client['name']}")
    
    settings = client.get('settings', {})
    if isinstance(settings, str):
        settings = json.loads(settings)
    
    livekit = settings.get('livekit', {})
    print(f"\n📡 LiveKit Configuration in Supabase:")
    print(f"   URL: {livekit.get('server_url', 'Not set')}")
    print(f"   API Key: {livekit.get('api_key', 'Not set')}")
    print(f"   API Secret: {livekit.get('api_secret', 'Not set')[:20]}...{livekit.get('api_secret', 'Not set')[-20:]}")