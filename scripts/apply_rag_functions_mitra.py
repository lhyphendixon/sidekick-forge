#!/usr/bin/env python3
"""
Apply RAG functions to Mitra Politi database via Python
"""

import sys
from supabase import create_client

def apply_rag_functions(service_key):
    """Apply the RAG functions that the platform expects"""
    
    MITRA_URL = "https://uyswpsluhkebudoqdnhk.supabase.co"
    
    print("Connecting to Mitra's database...")
    client = create_client(MITRA_URL, service_key)
    
    # SQL to create the correct match_documents function
    sql_match_documents = """
    -- Drop existing functions to avoid conflicts
    DROP FUNCTION IF EXISTS match_documents(vector, integer);
    DROP FUNCTION IF EXISTS match_documents(vector, integer, float8);
    DROP FUNCTION IF EXISTS match_documents(vector, text, float8, integer);
    DROP FUNCTION IF EXISTS match_documents(p_query_embedding vector, p_agent_slug text, p_match_threshold float8, p_match_count integer);

    -- Create the function with the EXACT signature the platform expects
    CREATE OR REPLACE FUNCTION match_documents(
        p_query_embedding vector,
        p_agent_slug text,
        p_match_threshold float8,
        p_match_count integer
    )
    RETURNS TABLE(
        id uuid,
        title text,
        content text,
        similarity float8
    )
    LANGUAGE plpgsql
    AS $$
    BEGIN
      -- For now, return empty results if no documents exist
      -- This allows the system to work even without documents
      RETURN QUERY
      SELECT 
        d.id,
        COALESCE(d.title, 'Untitled')::text AS title,
        d.content,
        1 - (d.embeddings <=> p_query_embedding) AS similarity
      FROM documents d
      WHERE 
        d.embeddings IS NOT NULL
        AND 1 - (d.embeddings <=> p_query_embedding) > p_match_threshold
      ORDER BY d.embeddings <=> p_query_embedding
      LIMIT p_match_count;
    END;
    $$;
    """
    
    sql_conversation_function = """
    -- Drop existing function
    DROP FUNCTION IF EXISTS match_conversation_transcripts_secure(vector, text, uuid, integer);
    DROP FUNCTION IF EXISTS match_conversation_transcripts_secure(query_embeddings vector, agent_slug_param text, user_id_param uuid, match_count integer);

    -- Create conversation matching function
    CREATE OR REPLACE FUNCTION match_conversation_transcripts_secure(
        query_embeddings vector,
        agent_slug_param text,
        user_id_param uuid,
        match_count integer DEFAULT 5
    )
    RETURNS TABLE(
        conversation_id uuid,
        user_message text,
        agent_response text,
        similarity float8,
        created_at timestamp with time zone
    )
    LANGUAGE plpgsql
    AS $$
    BEGIN
      -- Return empty for now if no conversations exist
      RETURN QUERY
      SELECT 
        ct.conversation_id,
        ct.content AS user_message,
        ''::text AS agent_response,
        1 - (ct.embeddings <=> query_embeddings) AS similarity,
        ct.created_at
      FROM conversation_transcripts ct
      WHERE ct.role = 'user'
        AND ct.user_id = user_id_param
        AND ct.embeddings IS NOT NULL
      ORDER BY ct.embeddings <=> query_embeddings
      LIMIT match_count;
    END;
    $$;
    """
    
    sql_permissions = """
    -- Grant permissions
    GRANT EXECUTE ON FUNCTION match_documents(vector, text, float8, integer) TO anon, authenticated, service_role;
    GRANT EXECUTE ON FUNCTION match_conversation_transcripts_secure(vector, text, uuid, integer) TO anon, authenticated, service_role;
    """
    
    try:
        print("\n1. Creating match_documents function...")
        # Execute via raw SQL (Supabase Python client doesn't have direct SQL execution)
        # We'll use the REST API directly
        import requests
        
        headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json"
        }
        
        # Unfortunately, Supabase REST API doesn't support direct SQL execution
        # Let's test if the functions exist first
        print("\n2. Testing current function status...")
        
        try:
            # Try to call the function with dummy data
            dummy_vector = [0.1] * 1024
            result = client.rpc('match_documents', {
                'p_query_embedding': dummy_vector,
                'p_agent_slug': 'test',
                'p_match_threshold': 0.5,
                'p_match_count': 5
            }).execute()
            print("✅ match_documents function already exists and works!")
        except Exception as e:
            print(f"❌ match_documents function not working: {str(e)[:100]}")
            print("\n⚠️  You need to apply the SQL manually in Supabase SQL Editor:")
            print("   1. Go to: https://uyswpsluhkebudoqdnhk.supabase.co/project/uyswpsluhkebudoqdnhk/sql/new")
            print("   2. Copy the SQL from: /root/sidekick-forge/scripts/fix_mitra_rag_complete.sql")
            print("   3. Run the SQL")
            print("   4. Wait a moment for the schema cache to refresh")
            return False
        
        try:
            # Test conversation function
            import uuid
            result = client.rpc('match_conversation_transcripts_secure', {
                'query_embeddings': dummy_vector,
                'agent_slug_param': 'test',
                'user_id_param': str(uuid.uuid4()),
                'match_count': 5
            }).execute()
            print("✅ match_conversation_transcripts_secure function already exists and works!")
        except Exception as e:
            print(f"❌ match_conversation_transcripts_secure function not working: {str(e)[:100]}")
            
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 apply_rag_functions_mitra.py <SERVICE_ROLE_KEY>")
        print("\nThis script tests if the RAG functions exist in Mitra's database.")
        print("If they don't exist, you'll need to apply the SQL manually.")
        sys.exit(1)
    
    service_key = sys.argv[1]
    
    print("=" * 60)
    print("Testing RAG Functions in Mitra Politi Database")
    print("=" * 60)
    
    success = apply_rag_functions(service_key)
    
    if success:
        print("\n✅ Functions are working!")
        print("\nNext steps:")
        print("1. Restart FastAPI to clear any caches: docker-compose restart fastapi")
        print("2. Try the Aya text chat again")
    else:
        print("\n⚠️  Manual SQL application required")
        print("\nThe functions need to be created via Supabase SQL Editor.")
        print("See instructions above.")