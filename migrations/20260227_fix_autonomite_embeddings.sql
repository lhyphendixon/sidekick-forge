-- Fix Autonomite embeddings schema
-- Problem: The embeddings column in documents table is TEXT (JSON string)
--          instead of vector(1024) type like other clients (e.g., Mitra)
--
-- Run this in Supabase SQL Editor for project: yuowazxcxwhczywurmmw
-- This migration is idempotent - safe to run multiple times

-- Step 1: Enable vector extension if not already enabled
CREATE EXTENSION IF NOT EXISTS vector;

-- Step 2: Check current column type and convert if needed
DO $$
DECLARE
  col_type TEXT;
  converted_count INTEGER;
BEGIN
  -- Get current column type
  SELECT data_type INTO col_type
  FROM information_schema.columns
  WHERE table_name = 'documents' AND column_name = 'embeddings';

  RAISE NOTICE 'Current embeddings column type: %', col_type;

  IF col_type = 'text' OR col_type = 'character varying' THEN
    -- Need to convert: add temp column, copy data, drop old, rename new
    RAISE NOTICE 'Converting TEXT to vector...';

    -- Add temp vector column
    ALTER TABLE documents ADD COLUMN IF NOT EXISTS embeddings_temp vector(1024);

    -- Convert TEXT to vector
    UPDATE documents
    SET embeddings_temp = embeddings::vector(1024)
    WHERE embeddings IS NOT NULL
      AND embeddings::text != ''
      AND embeddings_temp IS NULL;

    SELECT COUNT(*) INTO converted_count FROM documents WHERE embeddings_temp IS NOT NULL;
    RAISE NOTICE 'Converted % documents', converted_count;

    -- Drop old column and rename
    ALTER TABLE documents DROP COLUMN embeddings;
    ALTER TABLE documents RENAME COLUMN embeddings_temp TO embeddings;

    RAISE NOTICE 'Column conversion complete';
  ELSIF col_type = 'USER-DEFINED' THEN
    RAISE NOTICE 'embeddings column is already vector type - skipping conversion';
  ELSE
    RAISE NOTICE 'Unknown column type: % - manual intervention may be needed', col_type;
  END IF;
END $$;

-- Step 3: Create index on embeddings for faster searches (IVFFlat)
DROP INDEX IF EXISTS idx_documents_embeddings;
CREATE INDEX idx_documents_embeddings
ON documents USING ivfflat (embeddings vector_cosine_ops)
WITH (lists = 100);

-- Step 4: Create GIN index on agent_permissions for faster filtering
CREATE INDEX IF NOT EXISTS idx_documents_agent_permissions
ON documents USING gin (agent_permissions);

-- Step 5: Drop and recreate match_documents function
DROP FUNCTION IF EXISTS match_documents(vector, integer);
DROP FUNCTION IF EXISTS match_documents(vector, integer, float8);
DROP FUNCTION IF EXISTS match_documents(vector, text, float8, integer);
DROP FUNCTION IF EXISTS match_documents(p_query_embedding vector, p_agent_slug text, p_match_threshold float8, p_match_count integer);

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
    RETURN QUERY
    SELECT
      d.id,
      d.id as document_id,
      COALESCE(d.title, 'Untitled')::text AS title,
      d.content,
      COALESCE(d.file_url, '')::text AS source_url,
      COALESCE(d.document_type, 'document')::text AS source_type,
      COALESCE(d.chunk_index, 0)::int AS chunk_index,
      NULL::int AS page_number,
      NULL::int AS char_start,
      NULL::int AS char_end,
      1 - (d.embeddings <=> p_query_embedding) AS similarity
    FROM documents d
    WHERE
      d.embeddings IS NOT NULL
      AND 1 - (d.embeddings <=> p_query_embedding) > p_match_threshold
    ORDER BY d.embeddings <=> p_query_embedding
    LIMIT p_match_count;
  ELSE
    RETURN QUERY
    SELECT
      d.id,
      d.id as document_id,
      COALESCE(d.title, 'Untitled')::text AS title,
      d.content,
      COALESCE(d.file_url, '')::text AS source_url,
      COALESCE(d.document_type, 'document')::text AS source_type,
      COALESCE(d.chunk_index, 0)::int AS chunk_index,
      NULL::int AS page_number,
      NULL::int AS char_start,
      NULL::int AS char_end,
      1 - (d.embeddings <=> p_query_embedding) AS similarity
    FROM documents d
    WHERE
      d.agent_permissions @> to_jsonb(ARRAY[p_agent_slug])
      AND d.embeddings IS NOT NULL
      AND 1 - (d.embeddings <=> p_query_embedding) > p_match_threshold
    ORDER BY d.embeddings <=> p_query_embedding
    LIMIT p_match_count;
  END IF;
END;
$$;

GRANT EXECUTE ON FUNCTION match_documents(vector, text, float8, integer) TO anon, authenticated, service_role;

-- Step 6: Clean up confusing unused columns
ALTER TABLE documents DROP COLUMN IF EXISTS embedding;
ALTER TABLE documents DROP COLUMN IF EXISTS embedding_vec;
ALTER TABLE document_chunks DROP COLUMN IF EXISTS embeddings_vec;

-- Step 7: Verify the fix
DO $$
DECLARE
  doc_count INTEGER;
  vec_count INTEGER;
  litebridge_count INTEGER;
BEGIN
  SELECT COUNT(*) INTO doc_count FROM documents;
  SELECT COUNT(*) INTO vec_count FROM documents WHERE embeddings IS NOT NULL;
  SELECT COUNT(*) INTO litebridge_count FROM documents WHERE agent_permissions @> '["litebridge"]'::jsonb AND embeddings IS NOT NULL;

  RAISE NOTICE '=== Migration Results ===';
  RAISE NOTICE 'Total documents: %', doc_count;
  RAISE NOTICE 'Documents with embeddings: %', vec_count;
  RAISE NOTICE 'Litebridge documents with embeddings: %', litebridge_count;
END $$;

-- Step 8: Test the function
DO $$
DECLARE
  result_count INTEGER;
BEGIN
  SELECT COUNT(*) INTO result_count
  FROM match_documents(
    (SELECT embeddings FROM documents WHERE embeddings IS NOT NULL LIMIT 1),
    'litebridge',
    0.0,
    5
  );
  RAISE NOTICE 'Test query returned % results', result_count;
END $$;
