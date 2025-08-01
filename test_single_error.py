#!/usr/bin/env python3
"""Test what error is thrown when single() doesn't find results"""

import asyncio
from supabase import create_client
from postgrest import APIError as PostgrestAPIError

async def test_single_error():
    # Use Autonomite's Supabase credentials for testing
    supabase_url = "https://yuowazxcxwhczywurmmw.supabase.co"
    supabase_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"
    
    # Create Supabase client
    supabase = create_client(supabase_url, supabase_key)
    
    print("🔍 Testing single() with non-existent user...")
    
    try:
        # Query with a user ID that definitely doesn't exist (but valid UUID format)
        result = supabase.table("profiles").select("*").eq("user_id", "00000000-0000-0000-0000-000000000000").single().execute()
        print(f"Result: {result}")
        if result.data:
            print("✅ Data found (unexpected)")
        else:
            print("❌ No data found")
    except PostgrestAPIError as e:
        print(f"🚨 PostgrestAPIError caught: {e}")
        print(f"   Code: {e.code}")
        print(f"   Message: {e.message}")
        print(f"   Details: {e.details}")
    except Exception as e:
        print(f"🚨 Other exception: {type(e).__name__}: {e}")
    
    print("\n🔍 Testing regular select() with non-existent user (for comparison)...")
    
    try:
        # Same query but without single() (valid UUID format)
        result = supabase.table("profiles").select("*").eq("user_id", "00000000-0000-0000-0000-000000000000").execute()
        print(f"Result: {result}")
        if result.data:
            print(f"✅ Data found: {len(result.data)} rows")
        else:
            print("❌ No data found (empty list)")
    except Exception as e:
        print(f"🚨 Exception: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(test_single_error())