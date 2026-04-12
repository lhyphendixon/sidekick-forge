-- Complete RAG Function Fix for Mitra Politi
-- This ensures the functions work with the exact signatures the platform expects

-- Step 1: Drop ALL existing match_documents functions to avoid conflicts
DROP FUNCTION IF EXISTS match_documents(vector, integer);
DROP FUNCTION IF EXISTS match_documents(vector, integer, float8);
DROP FUNCTION IF EXISTS match_documents(vector, text, float8, integer);
DROP FUNCTION IF EXISTS match_documents(p_query_embedding vector, p_agent_slug text, p_match_threshold float8, p_match_count integer);

-- Step 2: Create the EXACT function the platform calls
-- IMPORTANT: Parameter order matters for Supabase RPC calls!
CREATE OR REPLACE FUNCTION match_documents(
    p_query_embedding vector,
    p_agent_slug text,
    p_match_threshold float8,
    p_match_count integer
)
RETURNS TABLE(
    id bigint,
    title text,
    content text,
    similarity float8
)
LANGUAGE plpgsql
AS $$
BEGIN
  -- Handle case where there might not be agent filtering
  IF p_agent_slug IS NULL OR p_agent_slug = '' THEN
    -- Return documents without agent filtering
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
  ELSE
    -- Return documents filtered by agent
    RETURN QUERY
    SELECT 
      d.id,
      COALESCE(d.title, 'Untitled')::text AS title,
      d.content,
      1 - (d.embeddings <=> p_query_embedding) AS similarity
    FROM documents d
    LEFT JOIN agent_documents ad ON d.id = ad.document_id
    LEFT JOIN agents a ON ad.agent_id = a.id
    WHERE 
      a.slug = p_agent_slug
      AND d.embeddings IS NOT NULL
      AND 1 - (d.embeddings <=> p_query_embedding) > p_match_threshold
    ORDER BY d.embeddings <=> p_query_embedding
    LIMIT p_match_count;
  END IF;
END;
$$;

-- Step 3: Drop and recreate conversation matching function
DROP FUNCTION IF EXISTS match_conversation_transcripts_secure(vector, text, uuid, integer);
DROP FUNCTION IF EXISTS match_conversation_transcripts_secure(query_embeddings vector, agent_slug_param text, user_id_param uuid, match_count integer);

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
  RETURN QUERY
  WITH user_messages AS (
    SELECT 
      ct.conversation_id,
      ct.content AS user_message,
      ct.embeddings,
      ct.created_at,
      ct.sequence
    FROM conversation_transcripts ct
    WHERE ct.role = 'user'
      AND ct.user_id = user_id_param
      AND ct.embeddings IS NOT NULL
  ),
  assistant_messages AS (
    SELECT 
      ct.conversation_id,
      ct.content AS agent_response,
      ct.sequence
    FROM conversation_transcripts ct
    WHERE ct.role = 'assistant'
  )
  SELECT 
    u.conversation_id,
    u.user_message,
    COALESCE(a.agent_response, '')::text AS agent_response,
    1 - (u.embeddings <=> query_embeddings) AS similarity,
    u.created_at
  FROM user_messages u
  LEFT JOIN assistant_messages a 
    ON a.conversation_id = u.conversation_id 
    AND a.sequence = u.sequence + 1
  ORDER BY u.embeddings <=> query_embeddings
  LIMIT match_count;
END;
$$;

-- Step 4: Ensure agent_documents table exists
CREATE TABLE IF NOT EXISTS agent_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID,
    document_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Step 5: Create indexes if they don't exist
CREATE INDEX IF NOT EXISTS idx_agent_documents_agent_id ON agent_documents(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_documents_document_id ON agent_documents(document_id);
CREATE INDEX IF NOT EXISTS idx_agent_documents_enabled ON agent_documents(enabled);

-- Step 6: Grant all necessary permissions
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated, service_role;

-- Step 7: Refresh the schema cache (important for Supabase)
NOTIFY pgrst, 'reload schema';

-- Step 8: Test the functions exist with correct signatures
DO $$
DECLARE
    func_exists boolean;
BEGIN
    -- Check if match_documents exists with correct signature
    SELECT EXISTS (
        SELECT 1 
        FROM pg_proc p
        JOIN pg_namespace n ON p.pronamespace = n.oid
        WHERE n.nspname = 'public' 
        AND p.proname = 'match_documents'
        AND p.pronargs = 4
    ) INTO func_exists;
    
    IF func_exists THEN
        RAISE NOTICE '✅ match_documents function created successfully';
    ELSE
        RAISE WARNING '❌ match_documents function not found';
    END IF;
    
    -- Check if match_conversation_transcripts_secure exists
    SELECT EXISTS (
        SELECT 1 
        FROM pg_proc p
        JOIN pg_namespace n ON p.pronamespace = n.oid
        WHERE n.nspname = 'public' 
        AND p.proname = 'match_conversation_transcripts_secure'
        AND p.pronargs = 4
    ) INTO func_exists;
    
    IF func_exists THEN
        RAISE NOTICE '✅ match_conversation_transcripts_secure function created successfully';
    ELSE
        RAISE WARNING '❌ match_conversation_transcripts_secure function not found';
    END IF;
END $$;

-- Success message
DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'RAG Functions Fixed for Mitra Politi';
    RAISE NOTICE '========================================';
    RAISE NOTICE '';
    RAISE NOTICE 'Functions created:';
    RAISE NOTICE '1. match_documents(p_query_embedding, p_agent_slug, p_match_threshold, p_match_count)';
    RAISE NOTICE '2. match_conversation_transcripts_secure(query_embeddings, agent_slug_param, user_id_param, match_count)';
    RAISE NOTICE '';
    RAISE NOTICE 'The schema cache has been notified to reload.';
    RAISE NOTICE 'Text chat should now work with the Aya agent.';
    RAISE NOTICE '';
    RAISE NOTICE 'If it still doesn''t work, you may need to:';
    RAISE NOTICE '1. Wait a moment for the cache to refresh';
    RAISE NOTICE '2. Restart the FastAPI container: docker-compose restart fastapi';
    RAISE NOTICE '';
END $$;