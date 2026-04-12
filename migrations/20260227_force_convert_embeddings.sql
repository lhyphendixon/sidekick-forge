-- Force convert embeddings column from TEXT to vector(1024)
-- Run this in Supabase SQL Editor for project: yuowazxcxwhczywurmmw

-- Step 1: Add temp vector column
ALTER TABLE documents ADD COLUMN IF NOT EXISTS embeddings_new vector(1024);

-- Step 2: Convert TEXT embeddings to vector
UPDATE documents
SET embeddings_new = embeddings::vector(1024)
WHERE embeddings IS NOT NULL
  AND embeddings_new IS NULL;

-- Step 3: Count how many were converted
SELECT COUNT(*) as converted FROM documents WHERE embeddings_new IS NOT NULL;

-- Step 4: Drop old column and rename new one
ALTER TABLE documents DROP COLUMN embeddings;
ALTER TABLE documents RENAME COLUMN embeddings_new TO embeddings;

-- Step 5: Recreate the index
DROP INDEX IF EXISTS idx_documents_embeddings;
CREATE INDEX idx_documents_embeddings
ON documents USING ivfflat (embeddings vector_cosine_ops)
WITH (lists = 100);

-- Step 6: Verify
SELECT
  COUNT(*) as total_docs,
  COUNT(embeddings) as docs_with_embeddings
FROM documents;
