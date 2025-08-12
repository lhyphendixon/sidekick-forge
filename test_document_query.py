#!/usr/bin/env python3
"""Test document queries to debug RAG issue"""

import os
import sys
sys.path.insert(0, '/root/sidekick-forge')

from app.integrations.supabase_client import supabase_manager

# Fix environment
os.environ['DOMAIN_NAME'] = 'localhost'
os.environ['DATABASE_URL'] = 'postgresql://postgres:postgres@localhost:5432/postgres'

# Get Autonomite client config
client_result = supabase_manager.admin_client.table('clients').select('*').eq('id', 'df91fd06-816f-4273-a903-5a4861277040').execute()

if client_result.data:
    client = client_result.data[0]
    config = client.get('configuration', {})
    
    # Get credentials
    url = config.get('supabase_url')
    key = config.get('supabase_service_key')
    
    if url and key:
        from supabase import create_client
        autonomite_client = create_client(url, key)
        
        # Test 1: Get agent by slug
        agent_slug = "clarence-coherence"
        print(f"Test 1: Getting agent with slug '{agent_slug}'")
        agent_result = autonomite_client.table("agents").select("id, name, slug").eq("slug", agent_slug).execute()
        
        if agent_result.data:
            agent = agent_result.data[0]
            agent_id = agent['id']
            print(f"✅ Found agent: {agent['name']} (ID: {agent_id})")
            
            # Test 2: Count documents for this agent via agent_documents
            print(f"\nTest 2: Counting documents via agent_documents table")
            try:
                # First, check if agent_documents table exists
                docs_count_result = autonomite_client.table("agent_documents").select("count").eq("agent_id", agent_id).execute()
                count = len(docs_count_result.data) if docs_count_result.data else 0
                print(f"✅ Found {count} document assignments")
                
                # Test 3: Get actual documents
                if count > 0:
                    print(f"\nTest 3: Getting document details")
                    docs_result = autonomite_client.table("agent_documents").select(
                        "document_id, documents(id, title, content)"
                    ).eq("agent_id", agent_id).limit(5).execute()
                    
                    if docs_result.data:
                        print(f"✅ Retrieved {len(docs_result.data)} documents:")
                        for doc_rel in docs_result.data:
                            doc = doc_rel.get("documents", {})
                            if doc:
                                title = doc.get("title", "Untitled")
                                content_preview = (doc.get("content", "")[:100] + "...") if doc.get("content") else "No content"
                                print(f"   - {title}: {content_preview}")
                    else:
                        print("❌ No document data returned")
                        
            except Exception as e:
                print(f"❌ agent_documents query failed: {e}")
                
            # Test 4: Alternative - check documents table directly
            print(f"\nTest 4: Checking documents table directly")
            try:
                # First check all documents
                all_docs = autonomite_client.table("documents").select("id, title").execute()
                print(f"Total documents in database: {len(all_docs.data) if all_docs.data else 0}")
                
                # Check if documents have agent_slug field
                docs_with_slug = autonomite_client.table("documents").select("id, title, agent_slug").eq("agent_slug", agent_slug).execute()
                if docs_with_slug.data:
                    print(f"✅ Found {len(docs_with_slug.data)} documents with agent_slug='{agent_slug}'")
                else:
                    print(f"❌ No documents found with agent_slug='{agent_slug}'")
                    
            except Exception as e:
                print(f"❌ documents table query failed: {e}")
                
        else:
            print(f"❌ No agent found with slug '{agent_slug}'")
            
            # List all agents
            all_agents = autonomite_client.table("agents").select("id, name, slug").execute()
            if all_agents.data:
                print(f"\nAvailable agents:")
                for agent in all_agents.data:
                    print(f"   - {agent['name']} (slug: {agent['slug']})")
    else:
        print("❌ Missing Supabase credentials")
else:
    print("❌ Client not found")