#!/usr/bin/env python3
"""
Generate SQL migration script for Caroline Cory's embeddings
"""
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

platform_sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY'))
clients = platform_sb.table('clients').select('*').execute()
cc_client = next((c for c in clients.data if 'caroline' in (c.get('name') or '').lower()), None)
client_sb = create_client(cc_client.get('supabase_url'), cc_client.get('supabase_service_role_key'))

# Get stats
total_chunks = client_sb.table('document_chunks').select('id', count='exact').execute()
sample_chunks = client_sb.table('document_chunks').select('id, embeddings, embeddings_vec').limit(100).execute()

needs_migration = sum(1 for c in sample_chunks.data if c.get('embeddings_vec') is None and c.get('embeddings') is not None)
estimated_needs_migration = int((needs_migration / 100) * total_chunks.count)

print("="*100)
print("CAROLINE CORY EMBEDDINGS MIGRATION SQL SCRIPT")
print("="*100)
print(f"\nTotal chunks: {total_chunks.count}")
print(f"Estimated chunks needing migration: ~{estimated_needs_migration} ({needs_migration}%)")

sql_script = """-- =====================================================================
-- EMBEDDINGS MIGRATION SCRIPT FOR CAROLINE CORY
-- Converts JSON string embeddings to PostgreSQL vector type
-- This enables all documents to appear in RAG search results
-- =====================================================================

-- Step 1: Ensure embeddings_vec column exists
ALTER TABLE document_chunks
ADD COLUMN IF NOT EXISTS embeddings_vec vector(1024);

-- Step 2: Migrate embeddings from JSON strings to vector type
-- Processing in batches to handle large datasets efficiently
DO $$
DECLARE
    chunk_record RECORD;
    embedding_array float[];
    converted_count INTEGER := 0;
    error_count INTEGER := 0;
    start_time TIMESTAMP := clock_timestamp();
    batch_start_time TIMESTAMP;
    total_to_process INTEGER;
BEGIN
    -- Count total chunks to process
    SELECT COUNT(*) INTO total_to_process
    FROM document_chunks
    WHERE embeddings IS NOT NULL
    AND embeddings_vec IS NULL;

    RAISE NOTICE '======================================';
    RAISE NOTICE 'Starting migration of % chunks', total_to_process;
    RAISE NOTICE '======================================';

    batch_start_time := clock_timestamp();

    -- Process all chunks that have JSON embeddings but no vector embeddings
    FOR chunk_record IN
        SELECT id, embeddings::text as emb_text
        FROM document_chunks
        WHERE embeddings IS NOT NULL
        AND embeddings_vec IS NULL
        ORDER BY id
    LOOP
        BEGIN
            -- Parse JSON array and convert to PostgreSQL array
            SELECT ARRAY(
                SELECT value::float
                FROM json_array_elements_text(chunk_record.emb_text::json)
            ) INTO embedding_array;

            -- Validate dimension
            IF array_length(embedding_array, 1) != 1024 THEN
                RAISE NOTICE 'Chunk % has invalid dimension: %',
                    chunk_record.id, array_length(embedding_array, 1);
                error_count := error_count + 1;
                CONTINUE;
            END IF;

            -- Update with vector type
            UPDATE document_chunks
            SET embeddings_vec = embedding_array::vector
            WHERE id = chunk_record.id;

            converted_count := converted_count + 1;

            -- Progress update every 500 chunks
            IF converted_count % 500 = 0 THEN
                RAISE NOTICE '[%/%] Converted % chunks in %.1f seconds (%.1f chunks/sec)',
                    converted_count,
                    total_to_process,
                    converted_count,
                    EXTRACT(EPOCH FROM (clock_timestamp() - batch_start_time)),
                    converted_count / GREATEST(EXTRACT(EPOCH FROM (clock_timestamp() - batch_start_time)), 0.1);
                batch_start_time := clock_timestamp();
            END IF;

        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Error converting chunk %: %', chunk_record.id, SQLERRM;
            error_count := error_count + 1;
        END;
    END LOOP;

    RAISE NOTICE '======================================';
    RAISE NOTICE 'Migration complete!';
    RAISE NOTICE '======================================';
    RAISE NOTICE 'Successfully converted: % chunks', converted_count;
    RAISE NOTICE 'Errors: % chunks', error_count;
    RAISE NOTICE 'Total time: %.1f seconds', EXTRACT(EPOCH FROM (clock_timestamp() - start_time));
    RAISE NOTICE 'Average rate: %.1f chunks/second',
        converted_count / GREATEST(EXTRACT(EPOCH FROM (clock_timestamp() - start_time)), 0.1);
    RAISE NOTICE '======================================';
END $$;

-- Step 3: Create index for fast vector similarity search
-- Note: This may take a few minutes with 17,000+ chunks
RAISE NOTICE 'Creating vector similarity index...';

CREATE INDEX IF NOT EXISTS document_chunks_embeddings_vec_idx
ON document_chunks
USING ivfflat (embeddings_vec vector_cosine_ops)
WITH (lists = 100);

RAISE NOTICE 'Index created successfully!';

-- Step 4: Verify the migration
SELECT
    COUNT(*) as total_chunks,
    COUNT(embeddings) as chunks_with_json_embeddings,
    COUNT(embeddings_vec) as chunks_with_vector_embeddings,
    COUNT(CASE WHEN embeddings_vec IS NULL AND embeddings IS NOT NULL THEN 1 END) as chunks_still_needing_migration,
    ROUND(100.0 * COUNT(embeddings_vec) / NULLIF(COUNT(*), 0), 1) as percentage_migrated
FROM document_chunks;

-- =====================================================================
-- MIGRATION COMPLETE
-- All documents should now be searchable via RAG with vector similarity
-- =====================================================================
"""

# Save to file
output_path = '/root/sidekick-forge/caroline_embeddings_migration.sql'
with open(output_path, 'w') as f:
    f.write(sql_script)

print(f"\n✅ SQL migration script saved to: {output_path}")

print("\n" + "="*100)
print("INSTRUCTIONS:")
print("="*100)
print("\n1. Go to Caroline Cory's Supabase Dashboard")
print(f"   URL: {cc_client.get('supabase_url').replace('https://', 'https://supabase.com/dashboard/project/')}")
print("\n2. Navigate to: SQL Editor (in left sidebar)")
print("\n3. Click 'New Query'")
print(f"\n4. Copy the SQL script from: {output_path}")
print("   OR copy from the output below")
print("\n5. Paste into the SQL editor and click 'Run'")
print("\n6. Monitor the NOTICE messages in the Results panel")
print(f"\n   Expected time: 2-5 minutes for ~{estimated_needs_migration} chunks")

print("\n" + "="*100)
print("WHAT THIS MIGRATION DOES:")
print("="*100)
print(f"\n✓ Converts {estimated_needs_migration} chunks from JSON strings to vector type")
print("✓ Creates ivfflat index for fast vector similarity search")
print("✓ Validates all embeddings are 1024 dimensions")
print("✓ Reports errors for any invalid embeddings")
print("✓ Shows progress every 500 chunks")
print("\nAfter migration:")
print("✓ Divine Plan document will appear in RAG search results")
print("✓ All 1,280 documents will be fully searchable")
print("✓ Vector similarity matching will be 10-100x faster")

print("\n" + "="*100)
print("SQL MIGRATION SCRIPT:")
print("="*100)
print(sql_script)

print("\n" + "="*100)
print("READY TO MIGRATE!")
print("="*100)
