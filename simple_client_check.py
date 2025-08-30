#!/usr/bin/env python3
"""
Simple script to check client data access
"""
import sys
import os

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from supabase import create_client
from app.config import settings

def main():
    print(f"Database URL: {settings.supabase_url}")
    print(f"Service key (last 20 chars): ...{settings.supabase_service_role_key[-20:]}")
    
    # Connect to the platform database
    supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
    
    print("\n=== Testing basic connection ===")
    try:
        # Try a simple count query
        result = supabase.table("clients").select("id", count="exact").execute()
        print(f"✅ Connection works - found {result.count} clients")
    except Exception as e:
        print(f"❌ Basic connection failed: {e}")
        return
    
    print("\n=== Testing individual client queries ===")
    client_ids = [
        "11389177-e4d8-49a9-9a00-f77bb4de6592",  # Autonomite
        "72aefd69-c233-42c4-9e5e-c36891c26543",  # Kimberly
        "c43bc44e-a185-404b-b7b4-aa26a6964c9c"   # Mitra
    ]
    
    for client_id in client_ids:
        try:
            result = supabase.table("clients").select("*").eq("id", client_id).execute()
            if result.data:
                client = result.data[0]
                print(f"\n--- Client {client.get('name', 'Unknown')} ---")
                print(f"ID: {client_id}")
                
                # Print all available keys
                print(f"Available keys: {list(client.keys())}")
                
                # Check each key for settings-like data
                for key, value in client.items():
                    if 'setting' in key.lower() or 'config' in key.lower() or 'supabase' in key.lower():
                        print(f"  {key}: {value}")
                    elif value and isinstance(value, (dict, str)) and len(str(value)) > 50:
                        print(f"  {key} (potential config): {str(value)[:100]}...")
                
            else:
                print(f"❌ No data found for client {client_id}")
        except Exception as e:
            print(f"❌ Query failed for client {client_id}: {e}")
    
    print("\n=== Testing wildcard select ===")
    try:
        result = supabase.table("clients").select("*").limit(1).execute()
        if result.data:
            client = result.data[0]
            print("Sample client structure:")
            for key, value in client.items():
                value_type = type(value).__name__
                value_preview = str(value)[:50] + "..." if len(str(value)) > 50 else str(value)
                print(f"  {key} ({value_type}): {value_preview}")
        else:
            print("❌ No clients returned")
    except Exception as e:
        print(f"❌ Wildcard select failed: {e}")

if __name__ == "__main__":
    main()