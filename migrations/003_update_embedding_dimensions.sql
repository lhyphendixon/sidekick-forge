-- Migration to update embedding dimensions for Live Free Academy client
-- This migration changes the embedding column from vector(4096) to vector(1024)
-- for the documents_with_embeddings table in the Live Free Academy Supabase project

-- Note: This migration should be run on the Live Free Academy Supabase project
-- It assumes the pgvector extension is already installed

-- Step 1: Create a temporary column with the new dimension
ALTER TABLE documents_with_embeddings 
ADD COLUMN IF NOT EXISTS embedding_1024 vector(1024);

-- Step 2: Copy and truncate existing embeddings to 1024 dimensions
-- This assumes embeddings were generated with more dimensions than needed
UPDATE documents_with_embeddings 
SET embedding_1024 = 
    CASE 
        WHEN embedding IS NOT NULL THEN 
            CAST(
                ARRAY(
                    SELECT unnest(embedding::float[])
                    LIMIT 1024
                ) AS vector
            )
        ELSE NULL
    END
WHERE embedding IS NOT NULL;

-- Step 3: Drop the old embedding column
ALTER TABLE documents_with_embeddings 
DROP COLUMN IF EXISTS embedding;

-- Step 4: Rename the new column to embedding
ALTER TABLE documents_with_embeddings 
RENAME COLUMN embedding_1024 TO embedding;

-- Step 5: Create/recreate any indexes on the embedding column
-- Assuming you have an ivfflat index for similarity search
CREATE INDEX IF NOT EXISTS documents_embedding_idx 
ON documents_with_embeddings 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- Step 6: Update document_chunks table if it exists and has embeddings
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.columns 
        WHERE table_name = 'document_chunks' 
        AND column_name = 'embeddings'
    ) THEN
        -- Add temporary column
        ALTER TABLE document_chunks 
        ADD COLUMN IF NOT EXISTS embeddings_1024 vector(1024);
        
        -- Copy and truncate existing embeddings
        UPDATE document_chunks 
        SET embeddings_1024 = 
            CASE 
                WHEN embeddings IS NOT NULL THEN 
                    CAST(
                        ARRAY(
                            SELECT unnest(embeddings::float[])
                            LIMIT 1024
                        ) AS vector
                    )
                ELSE NULL
            END
        WHERE embeddings IS NOT NULL;
        
        -- Drop old column
        ALTER TABLE document_chunks 
        DROP COLUMN IF EXISTS embeddings;
        
        -- Rename new column
        ALTER TABLE document_chunks 
        RENAME COLUMN embeddings_1024 TO embeddings;
        
        -- Create index
        CREATE INDEX IF NOT EXISTS chunks_embeddings_idx 
        ON document_chunks 
        USING ivfflat (embeddings vector_cosine_ops)
        WITH (lists = 100);
    END IF;
END $$;

-- Add a comment to track this migration
COMMENT ON TABLE documents_with_embeddings IS 'Documents table with 1024-dimensional embeddings (migrated from 4096)';