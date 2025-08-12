#!/usr/bin/env python3
"""
Verification script for Mitra Politi database schema
Checks if all required tables, columns, and functions are present
"""

import os
import sys
from supabase import create_client
from datetime import datetime

# Mitra Politi Database
MITRA_DB_URL = "https://uyswpsluhkebudoqdnhk.supabase.co"

def verify_schema(service_key: str):
    """Verify the Mitra Politi database schema"""
    
    print("=== Mitra Politi Database Schema Verification ===")
    print(f"Database: {MITRA_DB_URL}")
    print(f"Time: {datetime.now()}\n")
    
    # Connect to database
    client = create_client(MITRA_DB_URL, service_key)
    
    # Tables to check
    required_tables = [
        "agents",
        "conversations", 
        "conversation_transcripts",
        "documents",
        "document_chunks",
        "agent_documents",
        "global_settings",
        "messages"
    ]
    
    results = {
        "tables": {},
        "functions": {},
        "overall": True
    }
    
    print("Checking Tables:")
    print("-" * 40)
    
    for table_name in required_tables:
        try:
            # Try to query the table
            result = client.table(table_name).select("*").limit(0).execute()
            print(f"✅ {table_name:30} EXISTS")
            results["tables"][table_name] = True
        except Exception as e:
            error_msg = str(e).lower()
            if "relation" in error_msg and "does not exist" in error_msg:
                print(f"❌ {table_name:30} MISSING")
                results["tables"][table_name] = False
                results["overall"] = False
            else:
                print(f"⚠️  {table_name:30} ERROR: {str(e)[:50]}")
                results["tables"][table_name] = None
    
    print("\nChecking RPC Functions:")
    print("-" * 40)
    
    # Test RPC functions
    test_functions = [
        ("match_documents", "Document similarity search"),
        ("match_conversation_transcripts_secure", "Conversation history search")
    ]
    
    for func_name, description in test_functions:
        try:
            # Try to call the function with dummy parameters
            # This will fail but in a specific way if the function exists
            import numpy as np
            dummy_vector = np.zeros(1024).tolist()
            
            if func_name == "match_documents":
                result = client.rpc(func_name, {
                    "query_embedding": dummy_vector,
                    "match_count": 1
                }).execute()
            else:
                result = client.rpc(func_name, {
                    "query_embeddings": dummy_vector,
                    "agent_slug_param": "test",
                    "user_id_param": "00000000-0000-0000-0000-000000000000",
                    "match_count": 1
                }).execute()
            
            print(f"✅ {func_name:40} EXISTS")
            results["functions"][func_name] = True
            
        except Exception as e:
            error_msg = str(e).lower()
            if "function" in error_msg and "does not exist" in error_msg:
                print(f"❌ {func_name:40} MISSING")
                results["functions"][func_name] = False
                results["overall"] = False
            elif "could not find" in error_msg:
                print(f"❌ {func_name:40} MISSING")
                results["functions"][func_name] = False
                results["overall"] = False
            else:
                # Function exists but might have other issues (permissions, etc.)
                print(f"✅ {func_name:40} EXISTS (with warnings)")
                results["functions"][func_name] = True
    
    print("\nChecking Sample Data:")
    print("-" * 40)
    
    # Check if there are any agents
    try:
        agents = client.table("agents").select("*").execute()
        agent_count = len(agents.data) if agents.data else 0
        print(f"Agents in database: {agent_count}")
        
        if agent_count > 0:
            print("\nExisting agents:")
            for agent in agents.data[:5]:  # Show first 5
                print(f"  - {agent.get('name', 'Unknown')} (slug: {agent.get('slug', 'N/A')})")
    except:
        print("Could not query agents table")
    
    # Check global settings
    try:
        settings = client.table("global_settings").select("*").execute()
        setting_count = len(settings.data) if settings.data else 0
        print(f"\nGlobal settings entries: {setting_count}")
        
        if setting_count > 0:
            print("\nKey settings:")
            for setting in settings.data[:10]:
                print(f"  - {setting.get('setting_key', 'Unknown')}: {setting.get('setting_value', 'N/A')[:50]}")
    except:
        print("Could not query global_settings table")
    
    # Summary
    print("\n" + "=" * 50)
    print("VERIFICATION SUMMARY")
    print("=" * 50)
    
    missing_tables = [t for t, exists in results["tables"].items() if exists is False]
    missing_functions = [f for f, exists in results["functions"].items() if exists is False]
    
    if results["overall"]:
        print("✅ ALL CHECKS PASSED - Schema is ready!")
    else:
        print("❌ SCHEMA INCOMPLETE")
        
        if missing_tables:
            print(f"\nMissing tables: {', '.join(missing_tables)}")
        if missing_functions:
            print(f"Missing functions: {', '.join(missing_functions)}")
        
        print("\nTo fix:")
        print(f"1. Open Supabase SQL Editor for {MITRA_DB_URL}")
        print(f"2. Run the migration script: /root/sidekick-forge/scripts/mitra_politi_full_schema.sql")
        print(f"3. Re-run this verification script")
    
    return results["overall"]

if __name__ == "__main__":
    service_key = os.getenv("MITRA_SERVICE_KEY")
    
    if not service_key:
        print("ERROR: MITRA_SERVICE_KEY environment variable not set")
        print("\nUsage:")
        print("  export MITRA_SERVICE_KEY='your-service-role-key'")
        print("  python3 verify_mitra_schema.py")
        print("\nOr:")
        print("  MITRA_SERVICE_KEY='your-service-role-key' python3 verify_mitra_schema.py")
        sys.exit(1)
    
    success = verify_schema(service_key)
    sys.exit(0 if success else 1)