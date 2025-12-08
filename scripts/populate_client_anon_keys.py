#!/usr/bin/env python3
"""
Populate missing supabase_anon_key for clients
This fixes the transcript display issue where clients were falling back to platform credentials
"""
import os
import sys
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

# Platform database credentials
PLATFORM_URL = os.getenv('SUPABASE_URL')
PLATFORM_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

print("🔑 Populate Client Supabase Anon Keys\n")
print("=" * 60)

try:
    # Create platform Supabase client
    platform_supabase = create_client(PLATFORM_URL, PLATFORM_KEY)
    
    # Check if we're updating a specific client or listing all
    if len(sys.argv) < 2:
        print("\n📋 Clients with missing supabase_anon_key:\n")
        
        # List all clients that have supabase_url but no anon key
        result = platform_supabase.table('clients').select(
            'id, name, supabase_url, supabase_anon_key'
        ).execute()
        
        missing_keys = []
        for client in result.data:
            if client.get('supabase_url') and not client.get('supabase_anon_key'):
                missing_keys.append(client)
                print(f"  ❌ {client['name']}")
                print(f"     ID: {client['id']}")
                print(f"     URL: {client['supabase_url']}")
                print(f"     Anon Key: NOT SET\n")
        
        if not missing_keys:
            print("  ✅ All clients with supabase_url have anon keys set!")
        else:
            print("\n" + "=" * 60)
            print("\n📝 To update a client's anon key, run:")
            print("   python scripts/populate_client_anon_keys.py <client_id> <anon_key>")
            print("\nOr update ALL at once:")
            print("   python scripts/populate_client_anon_keys.py --batch")
            print("   (You'll be prompted for each client's anon key)")
    
    elif sys.argv[1] == "--batch":
        # Batch update mode - prompt for each client
        result = platform_supabase.table('clients').select(
            'id, name, supabase_url, supabase_anon_key'
        ).execute()
        
        clients_to_update = [
            c for c in result.data 
            if c.get('supabase_url') and not c.get('supabase_anon_key')
        ]
        
        if not clients_to_update:
            print("✅ All clients already have anon keys set!")
            sys.exit(0)
        
        print(f"\n🔄 Found {len(clients_to_update)} clients to update\n")
        
        for client in clients_to_update:
            print("─" * 60)
            print(f"\n📝 Client: {client['name']}")
            print(f"   ID: {client['id']}")
            print(f"   URL: {client['supabase_url']}")
            
            anon_key = input(f"\n   Enter anon key for {client['name']} (or 'skip'): ").strip()
            
            if anon_key.lower() == 'skip':
                print("   ⏭️  Skipped")
                continue
            
            if not anon_key or len(anon_key) < 20:
                print("   ❌ Invalid key, skipping...")
                continue
            
            # Update the client
            update_result = platform_supabase.table('clients').update({
                'supabase_anon_key': anon_key
            }).eq('id', client['id']).execute()
            
            if update_result.data:
                print(f"   ✅ Successfully updated!")
            else:
                print(f"   ❌ Failed to update")
        
        print("\n" + "=" * 60)
        print("\n🎉 Batch update complete!")
        print("\n🔄 You may need to restart services or refresh browser tabs")
    
    else:
        # Update specific client
        if len(sys.argv) < 3:
            print("❌ Usage: python populate_client_anon_keys.py <client_id> <anon_key>")
            sys.exit(1)
        
        client_id = sys.argv[1]
        anon_key = sys.argv[2]
        
        # Validate the anon key (basic check)
        if len(anon_key) < 20:
            print("❌ Invalid anon key - too short")
            sys.exit(1)
        
        # Get client info
        client_result = platform_supabase.table('clients').select(
            'id, name, supabase_url'
        ).eq('id', client_id).single().execute()
        
        if not client_result.data:
            print(f"❌ Client {client_id} not found")
            sys.exit(1)
        
        client = client_result.data
        print(f"\n📝 Updating client: {client['name']}")
        print(f"   ID: {client['id']}")
        print(f"   URL: {client.get('supabase_url', 'NOT SET')}")
        print(f"   Anon Key: {anon_key[:20]}...{anon_key[-4:]}\n")
        
        # Update the anon key
        update_result = platform_supabase.table('clients').update({
            'supabase_anon_key': anon_key
        }).eq('id', client_id).execute()
        
        if update_result.data:
            print("✅ Successfully updated supabase_anon_key!")
            print("\n🔄 Next steps:")
            print("   1. Refresh any open browser tabs with preview/embed")
            print("   2. Transcripts should now stream correctly\n")
        else:
            print("❌ Failed to update anon key")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    if "401" in str(e):
        print("   Authentication error - check SUPABASE_SERVICE_ROLE_KEY in .env")
    elif "403" in str(e):
        print("   Permission denied - check RLS policies on clients table")
    elif "42703" in str(e):
        print("   Column 'supabase_anon_key' doesn't exist - you need to add it via migration")


