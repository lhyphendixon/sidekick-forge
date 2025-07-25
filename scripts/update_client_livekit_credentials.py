#!/usr/bin/env python3
"""
Update LiveKit credentials for a client in Supabase
"""
import sys
import json
from supabase import create_client

def update_livekit_credentials(client_id: str, url: str, api_key: str, api_secret: str):
    """Update LiveKit credentials for a client"""
    
    # Supabase credentials
    supabase_url = 'https://yuowazxcxwhczywurmmw.supabase.co'
    supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY'
    
    supabase = create_client(supabase_url, supabase_key)
    
    try:
        # Get current client settings
        result = supabase.table('clients').select('settings').eq('id', client_id).single().execute()
        
        if not result.data:
            print(f"❌ Client {client_id} not found!")
            return False
        
        settings = result.data.get('settings', {})
        
        # Update LiveKit configuration
        if 'livekit' not in settings:
            settings['livekit'] = {}
        
        settings['livekit'] = {
            'server_url': url,
            'api_key': api_key,
            'api_secret': api_secret
        }
        
        # Update in database
        update_result = supabase.table('clients').update({
            'settings': settings
        }).eq('id', client_id).execute()
        
        print(f"✅ LiveKit credentials updated successfully!")
        print(f"   URL: {url}")
        print(f"   API Key: {api_key[:8]}...{api_key[-4:]}")
        print(f"   API Secret: {'*' * 20}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error updating credentials: {e}")
        return False

def main():
    if len(sys.argv) != 5:
        print("Usage: python update_client_livekit_credentials.py <client_id> <url> <api_key> <api_secret>")
        print("Example: python update_client_livekit_credentials.py df91fd06-816f-4273-a903-5a4861277040 wss://example.livekit.cloud API123 SECRET456")
        sys.exit(1)
    
    client_id = sys.argv[1]
    url = sys.argv[2]
    api_key = sys.argv[3]
    api_secret = sys.argv[4]
    
    # Validate that we're not setting test credentials
    if api_key == "APIUtuiQ47BQBsk":
        print("❌ ERROR: Cannot set expired test credentials!")
        print("   Please provide valid LiveKit credentials.")
        sys.exit(1)
    
    update_livekit_credentials(client_id, url, api_key, api_secret)

if __name__ == "__main__":
    main()