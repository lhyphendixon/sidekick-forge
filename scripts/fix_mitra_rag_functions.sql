-- Fix RAG functions for Mitra Politi database
-- This adds the correct function signatures that the platform expects

-- Drop existing functions if they exist with wrong signatures
DROP FUNCTION IF EXISTS match_documents(vector, integer);
DROP FUNCTION IF EXISTS match_documents(vector, text, float8, integer);

-- Create the correct match_documents function that the platform expects
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
  RETURN QUERY
  SELECT 
    d.id,
    d.title::text AS title,
    d.content,
    1 - (d.embeddings <=> p_query_embedding) AS similarity
  FROM documents d
  LEFT JOIN agent_documents ad ON d.id = ad.document_id
  LEFT JOIN agents a ON ad.agent_id = a.id
  WHERE 
    (a.slug = p_agent_slug OR p_agent_slug IS NULL)
    AND d.embeddings IS NOT NULL
    AND 1 - (d.embeddings <=> p_query_embedding) > p_match_threshold
  ORDER BY d.embeddings <=> p_query_embedding
  LIMIT p_match_count;
END;
$$;

-- Create simple version for backward compatibility
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding vector,
    match_count integer DEFAULT 5,
    match_threshold float8 DEFAULT 0.5
)
RETURNS TABLE(
    id uuid,
    content text,
    metadata jsonb,
    similarity float8
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        dc.id,
        dc.content,
        dc.chunk_metadata as metadata,
        1 - (dc.embeddings <=> query_embedding) as similarity
    FROM document_chunks dc
    WHERE dc.embeddings IS NOT NULL
      AND 1 - (dc.embeddings <=> query_embedding) > match_threshold
    ORDER BY dc.embeddings <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Fix match_conversation_transcripts_secure function
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
  SELECT 
    u.conversation_id,
    u.content AS user_message,
    a.content AS agent_response,
    1 - (u.embeddings <=> query_embeddings) AS similarity,
    u.created_at
  FROM conversation_transcripts u
  LEFT JOIN conversation_transcripts a ON a.conversation_id = u.conversation_id AND a.role = 'assistant'
  LEFT JOIN agents ag ON u.agent_id = ag.id
  WHERE u.role = 'user'
    AND u.embeddings IS NOT NULL
    AND u.user_id = user_id_param
    AND (ag.slug = agent_slug_param OR agent_slug_param IS NULL)
  ORDER BY u.embeddings <=> query_embeddings
  LIMIT match_count;
END;
$$;

-- Add agent_documents table if missing (needed for the RAG functions)
CREATE TABLE IF NOT EXISTS agent_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID,
    document_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create index if not exists
CREATE INDEX IF NOT EXISTS idx_agent_documents_agent_id ON agent_documents(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_documents_document_id ON agent_documents(document_id);

-- Grant permissions
GRANT EXECUTE ON FUNCTION match_documents(vector, text, float8, integer) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION match_documents(vector, integer, float8) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION match_conversation_transcripts_secure(vector, text, uuid, integer) TO anon, authenticated;

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'RAG functions fixed successfully for Mitra Politi database!';
    RAISE NOTICE 'Text chat should now work properly with the Aya agent.';
END $$;