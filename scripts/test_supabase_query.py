#!/usr/bin/env python3
"""
Test Supabase queries to debug 401 error
"""
import asyncio
import os
import sys
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from supabase import create_client


async def test_queries():
    """Test various Supabase queries"""
    try:
        # Create client with service role key
        supabase_url = settings.supabase_url
        service_key = settings.supabase_service_role_key
        
        print(f"Supabase URL: {supabase_url}")
        print(f"Service key prefix: {service_key[:20]}...")
        
        client = create_client(supabase_url, service_key)
        
        # Test 1: List all agents
        print("\n1. Testing list all agents...")
        try:
            result = client.table("agents").select("*").execute()
            print(f"   ✅ Success: Found {len(result.data)} agents")
            for agent in result.data:
                print(f"      - {agent['slug']}: {agent['name']}")
        except Exception as e:
            print(f"   ❌ Error: {e}")
        
        # Test 2: Query specific agent
        print("\n2. Testing query specific agent (clarence-coherence)...")
        try:
            result = client.table("agents").select("*").eq("slug", "clarence-coherence").execute()
            if result.data:
                print(f"   ✅ Success: Found agent {result.data[0]['name']}")
            else:
                print("   ⚠️ No agent found with slug 'clarence-coherence'")
        except Exception as e:
            print(f"   ❌ Error: {e}")
        
        # Test 3: Query agent_configurations
        print("\n3. Testing query agent_configurations...")
        try:
            result = client.table("agent_configurations").select("*").limit(5).execute()
            print(f"   ✅ Success: Found {len(result.data)} configurations")
        except Exception as e:
            print(f"   ❌ Error: {e}")
        
        # Test 4: Query with specific filter format
        print("\n4. Testing query with URL-encoded filter...")
        try:
            # This mimics the failing query format
            result = client.table("agents").select("*").eq("slug", "clarence-coherence").execute()
            print(f"   ✅ Success: Query worked")
        except Exception as e:
            print(f"   ❌ Error: {e}")
            
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(test_queries())