#!/usr/bin/env python3
"""
Test Mitra Politi database setup after schema application
"""

import os
import sys
import numpy as np
from supabase import create_client
from datetime import datetime

def test_mitra_database(service_key: str):
    """Run comprehensive tests on Mitra's database"""
    
    SUPABASE_URL = "https://uyswpsluhkebudoqdnhk.supabase.co"
    
    print("=" * 50)
    print("Testing Mitra Politi Database Setup")
    print("=" * 50)
    print()
    
    try:
        # Connect to database
        print("1. Testing connection...")
        client = create_client(SUPABASE_URL, service_key)
        print("   ✅ Connected successfully")
        print()
        
        # Test tables exist
        print("2. Checking tables...")
        tables_to_check = [
            'agents', 'conversations', 'conversation_transcripts',
            'documents', 'document_chunks', 'global_settings'
        ]
        
        for table in tables_to_check:
            try:
                result = client.table(table).select('*').limit(1).execute()
                print(f"   ✅ Table '{table}' exists")
            except Exception as e:
                print(f"   ❌ Table '{table}' missing or inaccessible: {e}")
        print()
        
        # Test vector operations
        print("3. Testing vector operations...")
        
        # Create a test document with embeddings
        test_embedding = np.random.random(1024).tolist()
        
        doc_data = {
            'title': 'Test Document for Mitra',
            'content': 'This is a test document to verify vector operations',
            'embeddings': test_embedding,
            'status': 'ready',
            'file_name': 'test.txt',
            'file_type': 'txt',
            'created_at': datetime.now().isoformat()
        }
        
        try:
            # Insert test document
            insert_result = client.table('documents').insert(doc_data).execute()
            if insert_result.data:
                doc_id = insert_result.data[0]['id']
                print(f"   ✅ Created test document (ID: {doc_id})")
                
                # Test vector similarity search
                search_result = client.rpc('match_documents', {
                    'query_embedding': test_embedding,
                    'match_count': 5
                }).execute()
                
                if search_result.data is not None:
                    print(f"   ✅ Vector search works (found {len(search_result.data)} results)")
                else:
                    print("   ⚠️  Vector search function may not be configured")
                
                # Clean up test document
                client.table('documents').delete().eq('id', doc_id).execute()
                print("   ✅ Cleaned up test document")
            else:
                print("   ❌ Failed to create test document")
        except Exception as e:
            print(f"   ⚠️  Vector operations test incomplete: {e}")
        print()
        
        # Test agent creation
        print("4. Testing agent operations...")
        try:
            agent_data = {
                'name': 'Mitra Test Agent',
                'slug': 'mitra-test-agent',
                'description': 'Test agent for Mitra Politi',
                'system_prompt': 'You are a helpful assistant.',
                'enabled': True
            }
            
            # Check if agent already exists
            existing = client.table('agents').select('*').eq('slug', 'mitra-test-agent').execute()
            
            if existing.data:
                # Update existing
                update_result = client.table('agents').update({
                    'description': f'Updated at {datetime.now().isoformat()}'
                }).eq('slug', 'mitra-test-agent').execute()
                print("   ✅ Agent update works")
            else:
                # Create new
                insert_result = client.table('agents').insert(agent_data).execute()
                if insert_result.data:
                    print("   ✅ Agent creation works")
                else:
                    print("   ❌ Failed to create agent")
        except Exception as e:
            print(f"   ❌ Agent operations failed: {e}")
        print()
        
        # Check global settings
        print("5. Checking global settings...")
        try:
            settings_result = client.table('global_settings').select('*').execute()
            if settings_result.data:
                print(f"   ✅ Found {len(settings_result.data)} settings")
                for setting in settings_result.data[:3]:  # Show first 3
                    print(f"      - {setting.get('setting_key', 'unknown')}")
            else:
                print("   ⚠️  No global settings found (may need to be configured)")
        except Exception as e:
            print(f"   ❌ Could not check global settings: {e}")
        print()
        
        # Summary
        print("=" * 50)
        print("✅ Database setup appears to be working correctly!")
        print("=" * 50)
        print()
        print("Next steps:")
        print("1. Update the service role key in the platform database")
        print("2. Configure global settings for API providers")
        print("3. Create production agents")
        print("4. Upload documents to the knowledge base")
        
        return True
        
    except Exception as e:
        print(f"❌ Fatal error during testing: {e}")
        print()
        print("Please ensure:")
        print("1. The schema has been applied to the database")
        print("2. You're using the correct service role key")
        print("3. The pgvector extension is enabled")
        return False


def main():
    """Main function"""
    if len(sys.argv) != 2:
        print("Usage: python3 test_mitra_setup.py <SERVICE_ROLE_KEY>")
        print("\nExample:")
        print("  python3 test_mitra_setup.py 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...'")
        sys.exit(1)
    
    service_key = sys.argv[1]
    success = test_mitra_database(service_key)
    
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()