#!/usr/bin/env python
"""
Test script to compare Redis-hybrid vs Supabase-only modes
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.service_factory import get_client_service, get_agent_service, use_supabase_only


async def test_service_mode():
    """Test the current service mode"""
    mode = "Supabase-only" if use_supabase_only() else "Redis-hybrid"
    print(f"Testing in {mode} mode...")
    print(f"USE_SUPABASE_ONLY = {os.getenv('USE_SUPABASE_ONLY', 'false')}")
    
    # Test client service
    client_service = get_client_service()
    print(f"\nClient service type: {type(client_service).__name__}")
    
    # List all clients
    print("\n1. Listing all clients:")
    try:
        clients = await client_service.get_all_clients()
        print(f"   Found {len(clients)} clients")
        for client in clients:
            print(f"   - {client.id}: {client.name}")
    except Exception as e:
        print(f"   Error listing clients: {e}")
    
    # Get cache stats
    print("\n2. Testing cache stats:")
    if hasattr(client_service, 'get_cache_stats'):
        stats = client_service.get_cache_stats()
        print(f"   Cache stats: {stats}")
    else:
        print("   Cache stats not available in this mode")
    
    # Test agent service
    print("\n3. Testing agent service:")
    agent_service = get_agent_service(client_service)
    print(f"   Agent service type: {type(agent_service).__name__}")
    
    print(f"\n✅ {mode} mode test completed!")


async def test_both_modes():
    """Test both Redis-hybrid and Supabase-only modes"""
    print("=" * 60)
    print("TESTING REDIS-HYBRID MODE")
    print("=" * 60)
    os.environ["USE_SUPABASE_ONLY"] = "false"
    await test_service_mode()
    
    print("\n" + "=" * 60)
    print("TESTING SUPABASE-ONLY MODE")
    print("=" * 60)
    os.environ["USE_SUPABASE_ONLY"] = "true"
    await test_service_mode()


if __name__ == "__main__":
    # Run tests for both modes
    asyncio.run(test_both_modes())