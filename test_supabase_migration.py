#!/usr/bin/env python
"""
Test script to verify Supabase-only services work correctly
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.dependencies_supabase import get_client_service, get_agent_service
from app.models.client import ClientCreate, ClientSettings, SupabaseConfig, LiveKitConfig


async def test_supabase_services():
    """Test the Supabase-only services"""
    print("Testing Supabase-only services...")
    
    # Test client service
    client_service = get_client_service()
    
    # List all clients
    print("\n1. Listing all clients:")
    try:
        clients = await client_service.get_all_clients()
        print(f"   Found {len(clients)} clients")
        for client in clients:
            print(f"   - {client.id}: {client.name}")
    except Exception as e:
        print(f"   Error listing clients: {e}")
    
    # Get specific client
    print("\n2. Getting specific client (autonomite):")
    try:
        client = await client_service.get_client("autonomite", auto_sync=False)
        if client:
            print(f"   Found client: {client.name}")
            print(f"   Domain: {client.domain}")
            print(f"   Active: {client.active}")
        else:
            print("   Client not found")
    except Exception as e:
        print(f"   Error getting client: {e}")
    
    # Test agent service
    print("\n3. Testing agent service:")
    agent_service = get_agent_service()
    
    try:
        # Get agents for autonomite client
        agents = await agent_service.get_client_agents("autonomite")
        print(f"   Found {len(agents)} agents for autonomite client")
        for agent in agents:
            print(f"   - {agent.slug}: {agent.name}")
    except Exception as e:
        print(f"   Error getting agents: {e}")
    
    # Get cache stats (should return empty stats)
    print("\n4. Testing cache stats:")
    stats = client_service.get_cache_stats()
    print(f"   Cache stats: {stats}")
    
    print("\n✅ Supabase-only services test completed!")


if __name__ == "__main__":
    asyncio.run(test_supabase_services())