#!/usr/bin/env python3
"""Test user profile query to see what data is available"""

import asyncio
from supabase import create_client
import json

async def test_user_profile():
    # Your user ID from the console
    user_id = "351bb07b-03fc-4fb4-b09b-748ef8a72084"
    
    # Autonomite's Supabase credentials
    supabase_url = "https://yuowazxcxwhczywurmmw.supabase.co"
    supabase_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"
    
    # Create Supabase client
    supabase = create_client(supabase_url, supabase_key)
    
    print(f"🔍 Looking up profile for user: {user_id}")
    
    # Query the profiles table
    result = supabase.table("profiles").select("*").eq("user_id", user_id).single().execute()
    
    if result.data:
        print("\n✅ Profile found!")
        print(json.dumps(result.data, indent=2))
        
        # Check specific fields
        profile = result.data
        print(f"\n📋 Profile Summary:")
        print(f"  - Name field: {profile.get('name', 'NOT FOUND')}")
        print(f"  - Full name field: {profile.get('full_name', 'NOT FOUND')}")
        print(f"  - Display name field: {profile.get('display_name', 'NOT FOUND')}")
        print(f"  - Username field: {profile.get('username', 'NOT FOUND')}")
        print(f"  - First name field: {profile.get('first_name', 'NOT FOUND')}")
        print(f"  - Last name field: {profile.get('last_name', 'NOT FOUND')}")
        print(f"  - Email: {profile.get('email', 'NOT FOUND')}")
        
        # List all fields
        print(f"\n🔑 All available fields:")
        for key in profile.keys():
            print(f"  - {key}: {type(profile[key]).__name__}")
    else:
        print("❌ No profile found")

if __name__ == "__main__":
    asyncio.run(test_user_profile())