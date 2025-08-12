#!/usr/bin/env python3
"""
Configure Mitra Politi client with proper Supabase settings
"""

import sys
from supabase import create_client

def configure_mitra_client(mitra_service_key):
    """Set up Mitra Politi client with Supabase configuration"""
    
    # Platform database
    platform_url = 'https://eukudpgfpihxsypulopm.supabase.co'
    platform_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV1a3VkcGdmcGloeHN5cHVsb3BtIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1MzUxMjkyMiwiZXhwIjoyMDY5MDg4OTIyfQ.wOSF5bSdd763_PVyCmSEBGjtbhP67WMfms1aGydO_44'
    
    platform_client = create_client(platform_url, platform_key)
    
    print("Configuring Mitra Politi client...")
    
    # Update Mitra Politi client with Supabase settings
    update_data = {
        'settings': {
            'supabase': {
                'url': 'https://uyswpsluhkebudoqdnhk.supabase.co',
                'service_role_key': mitra_service_key
            }
        }
    }
    
    result = platform_client.table('clients').update(update_data).eq('id', 'c43bc44e-a185-404b-b7b4-aa26a6964c9c').execute()
    
    if result.data:
        print("✅ Successfully configured Mitra Politi client with Supabase settings")
        
        # Test the connection
        print("\nTesting connection to Mitra's database...")
        try:
            mitra_client = create_client('https://uyswpsluhkebudoqdnhk.supabase.co', mitra_service_key)
            test_result = mitra_client.table('agents').select('count').execute()
            print("✅ Connection successful!")
            
            # Check if RAG functions exist
            print("\nChecking RAG functions...")
            try:
                # Try to call match_documents with dummy data
                dummy_vector = [0.1] * 1024
                rag_result = mitra_client.rpc('match_documents', {
                    'p_query_embedding': dummy_vector,
                    'p_agent_slug': 'test',
                    'p_match_threshold': 0.5,
                    'p_match_count': 5
                }).execute()
                print("✅ match_documents function exists and is callable")
            except Exception as e:
                print(f"⚠️  match_documents function issue: {e}")
                print("\nYou need to apply the RAG function fix SQL to Mitra's database")
                
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            print("Please check the service role key")
            
        return True
    else:
        print("❌ Failed to update client settings")
        return False


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 configure_mitra_client.py <MITRA_SERVICE_ROLE_KEY>")
        print("\nExample:")
        print("  python3 configure_mitra_client.py 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...'")
        print("\nGet the service role key from Mitra's Supabase dashboard:")
        print("  https://uyswpsluhkebudoqdnhk.supabase.co → Settings → API → Service Role Key")
        sys.exit(1)
    
    mitra_key = sys.argv[1]
    success = configure_mitra_client(mitra_key)
    
    if success:
        print("\n✅ Configuration complete!")
        print("\nNext steps:")
        print("1. Restart FastAPI: docker-compose restart fastapi")
        print("2. Test the Aya agent text chat again")
    else:
        sys.exit(1)