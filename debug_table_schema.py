#!/usr/bin/env python3
"""
Debug script to check the actual schema of the clients table
"""
import sys
import os
import asyncio
import json

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from supabase import create_client
from app.config import settings

async def main():
    print(f"Connecting to platform database: {settings.supabase_url}")
    
    # Connect to the main Sidekick Forge platform database
    platform_supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
    
    try:
        # First, let's see what tables exist
        print("\n=== CHECKING AVAILABLE TABLES ===")
        tables_result = platform_supabase.rpc('get_table_names').execute()
        if tables_result.data:
            print("Available tables:")
            for table in tables_result.data:
                print(f"  - {table}")
        else:
            print("Could not get table names via RPC, trying direct query...")
            
        # Try a different approach - get schema info
        print("\n=== CHECKING CLIENTS TABLE SCHEMA ===")
        schema_query = """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns 
        WHERE table_name = 'clients' 
        AND table_schema = 'public'
        ORDER BY ordinal_position;
        """
        
        try:
            schema_result = platform_supabase.rpc('exec_sql', {'query': schema_query}).execute()
            if schema_result.data:
                print("Clients table columns:")
                for col in schema_result.data:
                    print(f"  - {col['column_name']} ({col['data_type']}) - nullable: {col['is_nullable']}")
            else:
                print("Could not get schema via RPC")
        except Exception as e:
            print(f"Schema query failed: {e}")
        
        # Let's try selecting with different column names
        print("\n=== TRYING DIFFERENT COLUMN SELECTIONS ===")
        
        # Try selecting all columns explicitly
        try:
            result = platform_supabase.table("clients").select("id, name, description, domain, active, settings, created_at, updated_at").limit(1).execute()
            if result.data:
                print("✅ Standard column selection worked")
                client = result.data[0]
                print(f"Sample client keys: {list(client.keys())}")
            else:
                print("❌ No data returned")
        except Exception as e:
            print(f"❌ Standard selection failed: {e}")
        
        # Try selecting just settings
        try:
            result = platform_supabase.table("clients").select("id, name, settings").limit(1).execute()
            if result.data:
                print("✅ Settings column selection worked")
                client = result.data[0]
                print(f"Settings value: {client.get('settings')}")
                print(f"Settings type: {type(client.get('settings'))}")
            else:
                print("❌ No data returned for settings selection")
        except Exception as e:
            print(f"❌ Settings selection failed: {e}")
        
        # Try with different casing
        try:
            result = platform_supabase.table("clients").select("id, name, Settings").limit(1).execute()
            if result.data:
                print("✅ Capital Settings column worked")
        except Exception as e:
            print(f"❌ Capital Settings failed: {e}")
            
        # Try rawsupabase SQL
        print("\n=== TRYING RAW SQL ===")
        try:
            sql_result = platform_supabase.rpc('exec_sql', {
                'query': 'SELECT id, name, settings FROM clients LIMIT 1'
            }).execute()
            if sql_result.data:
                print("✅ Raw SQL worked")
                print(f"Raw result: {sql_result.data}")
            else:
                print("❌ Raw SQL returned no data")
        except Exception as e:
            print(f"❌ Raw SQL failed: {e}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = asyncio.run(main())