-- Fix match_documents for platform DB schema
-- Uses document_chunks.embeddings_vec for vector search
-- Uses agent_documents for agent-level permissions
DROP FUNCTION IF EXISTS match_documents(vector, text, float8, integer);
CREATE OR REPLACE FUNCTION match_documents(
    p_query_embedding vector,
    p_agent_slug text,
    p_match_threshold float8,
    p_match_count integer
)
RETURNS TABLE(
    id uuid,
    document_id uuid,
    title text,
    content text,
    source_url text,
    source_type text,
    chunk_index int,
    page_number int,
    char_start int,
    char_end int,
    similarity float8
)
LANGUAGE plpgsql
AS $$
BEGIN
  IF p_agent_slug IS NULL OR p_agent_slug = '' THEN
    -- No agent filter: search all chunks with embeddings
    RETURN QUERY
    SELECT
      dc.id,
      dc.document_id,
      COALESCE(d.title, 'Untitled')::text AS title,
      dc.content,
      COALESCE(d.file_name, '')::text AS source_url,
      COALESCE(d.document_type, 'document')::text AS source_type,
      COALESCE(dc.chunk_index, 0)::int AS chunk_index,
      NULL::int AS page_number,
      NULL::int AS char_start,
      NULL::int AS char_end,
      1 - (dc.embeddings_vec <=> p_query_embedding) AS similarity
    FROM document_chunks dc
    JOIN documents d ON d.id = dc.document_id
    WHERE
      dc.embeddings_vec IS NOT NULL
      AND 1 - (dc.embeddings_vec <=> p_query_embedding) > p_match_threshold
    ORDER BY dc.embeddings_vec <=> p_query_embedding
    LIMIT p_match_count;
  ELSE
    -- Agent-filtered: only search chunks belonging to documents assigned to this agent
    RETURN QUERY
    SELECT
      dc.id,
      dc.document_id,
      COALESCE(d.title, 'Untitled')::text AS title,
      dc.content,
      COALESCE(d.file_name, '')::text AS source_url,
      COALESCE(d.document_type, 'document')::text AS source_type,
      COALESCE(dc.chunk_index, 0)::int AS chunk_index,
      NULL::int AS page_number,
      NULL::int AS char_start,
      NULL::int AS char_end,
      1 - (dc.embeddings_vec <=> p_query_embedding) AS similarity
    FROM document_chunks dc
    JOIN documents d ON d.id = dc.document_id
    JOIN agent_documents ad ON ad.document_id = dc.document_id
    JOIN agents a ON a.id = ad.agent_id AND a.slug = p_agent_slug
    WHERE
      ad.enabled = true
      AND dc.embeddings_vec IS NOT NULL
      AND 1 - (dc.embeddings_vec <=> p_query_embedding) > p_match_threshold
    ORDER BY dc.embeddings_vec <=> p_query_embedding
    LIMIT p_match_count;
  END IF;
END;
$$;

GRANT EXECUTE ON FUNCTION match_documents(vector, text, float8, integer) TO anon, authenticated, service_role;
