#!/usr/bin/env python3
"""Simple test for document queries"""

print("Starting document test...")

try:
    from supabase import create_client
    print("✅ Imported supabase client")
except Exception as e:
    print(f"❌ Import error: {e}")
    exit(1)

# Platform database
platform_url = "https://eukudpgfpihxsypulopm.supabase.co"
platform_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV1a3VkcGdmcGloeHN5cHVsb3BtIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1MzUxMjkyMiwiZXhwIjoyMDY5MDg4OTIyfQ.wOSF5bSdd763_PVyCmSEBGjtbhP67WMfms1aGydO_44"

print("Creating platform client...")
try:
    platform_client = create_client(platform_url, platform_key)
    print("✅ Platform client created")
except Exception as e:
    print(f"❌ Platform client error: {e}")
    exit(1)

# Get Autonomite client
print("Getting Autonomite client...")
client_id = "11389177-e4d8-49a9-9a00-f77bb4de6592"  # Correct client ID
client_result = platform_client.table('clients').select('*').eq('id', client_id).execute()
print(f"Query result: {len(client_result.data) if client_result.data else 0} clients found")

if client_result.data:
    client = client_result.data[0]
    
    print(f"Client name: {client.get('name')}")
    print(f"Client fields: {list(client.keys())}")
    
    # Check different possible locations for credentials
    config = client.get('configuration', {})
    print(f"Configuration: {config}")
    
    # Maybe credentials are at top level?
    url_fields = [k for k in client.keys() if 'supabase' in k.lower() and 'url' in k.lower()]
    key_fields = [k for k in client.keys() if 'supabase' in k.lower() and 'key' in k.lower()]
    print(f"URL fields: {url_fields}")
    print(f"Key fields: {key_fields}")
    
    # Get credentials - they're at top level!
    url = client.get('supabase_url')
    key = client.get('supabase_service_role_key')
    
    print(f"Client Supabase URL: {url}")
    
    if url and key:
        # Connect to Autonomite's database
        autonomite_client = create_client(url, key)
        
        # Test 1: Get agent
        agent_slug = "clarence-coherence"
        print(f"\n1. Getting agent '{agent_slug}'...")
        agent_result = autonomite_client.table("agents").select("*").eq("slug", agent_slug).execute()
        
        if agent_result.data:
            agent = agent_result.data[0]
            agent_id = agent['id']
            print(f"   ✅ Found agent: {agent['name']} (ID: {agent_id})")
            
            # Test 2: Direct query to agent_documents
            print(f"\n2. Querying agent_documents table...")
            try:
                # Simple count query
                count_result = autonomite_client.table("agent_documents").select("*").eq("agent_id", agent_id).execute()
                print(f"   ✅ Found {len(count_result.data)} document assignments")
                
                if count_result.data:
                    # Show first few
                    print("   Document IDs assigned:")
                    for rel in count_result.data[:5]:
                        print(f"     - Document ID: {rel['document_id']}")
                        
                    # Test 3: Get one document's details
                    doc_id = count_result.data[0]['document_id']
                    doc_result = autonomite_client.table("documents").select("*").eq("id", doc_id).execute()
                    if doc_result.data:
                        doc = doc_result.data[0]
                        print(f"\n3. Sample document:")
                        print(f"   Title: {doc.get('title', 'Untitled')}")
                        print(f"   Content preview: {doc.get('content', '')[:200]}...")
                        
            except Exception as e:
                print(f"   ❌ Error: {e}")
                
            # Test 4: Try the exact query from context.py
            print(f"\n4. Testing exact query from context.py...")
            try:
                docs_result = autonomite_client.table("agent_documents").select(
                    "document_id, documents(id, title, content, metadata)"
                ).eq("agent_id", agent_id).execute()
                
                print(f"   Query returned {len(docs_result.data)} results")
                if docs_result.data:
                    for doc_rel in docs_result.data[:3]:
                        doc = doc_rel.get("documents")
                        if doc:
                            print(f"   - {doc.get('title', 'Untitled')}")
                        else:
                            print(f"   - Document ID {doc_rel['document_id']} (no document data)")
                            
            except Exception as e:
                print(f"   ❌ Join query error: {e}")
                
        else:
            print(f"   ❌ No agent found with slug '{agent_slug}'")
            
            # List agents
            all_agents = autonomite_client.table("agents").select("slug, name").execute()
            if all_agents.data:
                print("\n   Available agents:")
                for a in all_agents.data:
                    print(f"     - {a['name']} (slug: {a['slug']})")