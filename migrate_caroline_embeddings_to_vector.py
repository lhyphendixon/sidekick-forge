#!/usr/bin/env python3
"""
Migrate Caroline Cory's embeddings from JSON strings to PostgreSQL vector type
This will enable all 1,280 documents to appear in RAG search results
"""
import os
import sys
import json
import time
from supabase import create_client
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

def get_postgres_connection(client):
    """Get direct PostgreSQL connection for faster bulk operations"""
    # Extract connection details from Supabase URL
    supabase_url = client.get('supabase_url')
    supabase_key = client.get('supabase_service_role_key')

    # Parse the project reference from URL
    # Format: https://PROJECT_REF.supabase.co
    project_ref = supabase_url.split('//')[1].split('.')[0]

    # Supabase connection string format
    # Note: You'll need the database password - this is different from the service role key
    # For now, we'll use the Supabase client API
    return None

def main():
    print("="*100)
    print("CAROLINE CORY EMBEDDINGS MIGRATION")
    print("Converting JSON string embeddings to PostgreSQL vector type")
    print("="*100)

    # Connect to platform
    platform_url = os.getenv('SUPABASE_URL')
    platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    platform_sb = create_client(platform_url, platform_key)

    # Find Caroline Cory client
    clients = platform_sb.table('clients').select('*').execute()
    cc_client = next((c for c in clients.data if 'caroline' in (c.get('name') or '').lower()), None)

    if not cc_client:
        print("❌ Caroline Cory client not found")
        return 1

    client_sb = create_client(cc_client.get('supabase_url'), cc_client.get('supabase_service_role_key'))

    print(f"\nClient: {cc_client.get('name')}")
    print(f"Supabase URL: {cc_client.get('supabase_url')}")

    # Get document count
    total_docs = client_sb.table('documents').select('id', count='exact').execute()
    print(f"\nTotal documents: {total_docs.count}")

    # Step 1: Check if embeddings_vec column exists
    print("\n" + "-"*100)
    print("STEP 1: Checking database schema")
    print("-"*100)

    sample_chunk = client_sb.table('document_chunks').select('id, embeddings, embeddings_vec').limit(1).execute()
    if not sample_chunk.data:
        print("❌ No document chunks found")
        return 1

    has_embeddings_vec = 'embeddings_vec' in sample_chunk.data[0]
    print(f"embeddings_vec column exists: {has_embeddings_vec}")

    if not has_embeddings_vec:
        print("\n⚠️  embeddings_vec column does not exist!")
        print("Creating column via SQL...")
        print("\nPlease run this SQL in Caroline Cory's Supabase SQL Editor:")
        print("-"*100)
        print("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embeddings_vec vector(1024);")
        print("CREATE INDEX IF NOT EXISTS document_chunks_embeddings_vec_idx")
        print("  ON document_chunks USING ivfflat (embeddings_vec vector_cosine_ops)")
        print("  WITH (lists = 100);")
        print("-"*100)

        response = input("\nHave you created the column? (yes/no): ")
        if response.lower() != 'yes':
            print("Migration cancelled. Please create the column first.")
            return 1

    # Step 2: Count chunks needing migration
    print("\n" + "-"*100)
    print("STEP 2: Analyzing chunks")
    print("-"*100)

    # Get total chunk count
    total_chunks = client_sb.table('document_chunks').select('id', count='exact').execute()
    print(f"Total chunks: {total_chunks.count}")

    # We need to check how many have embeddings_vec vs embeddings
    # Since we can't easily query for NULL embeddings_vec with count, we'll sample
    print(f"\nSampling chunks to determine migration scope...")

    # Get a sample of chunks
    sample_size = min(100, total_chunks.count)
    sample_chunks = client_sb.table('document_chunks').select('id, embeddings, embeddings_vec').limit(sample_size).execute()

    needs_migration = 0
    has_vector = 0
    for chunk in sample_chunks.data:
        if chunk.get('embeddings_vec') is None and chunk.get('embeddings') is not None:
            needs_migration += 1
        elif chunk.get('embeddings_vec') is not None:
            has_vector += 1

    estimated_needs_migration = int((needs_migration / sample_size) * total_chunks.count)
    estimated_has_vector = int((has_vector / sample_size) * total_chunks.count)

    print(f"\nSample analysis (n={sample_size}):")
    print(f"  Chunks needing migration: {needs_migration} ({needs_migration/sample_size*100:.1f}%)")
    print(f"  Chunks with vector: {has_vector} ({has_vector/sample_size*100:.1f}%)")
    print(f"\nEstimated totals:")
    print(f"  Chunks to migrate: ~{estimated_needs_migration}")
    print(f"  Chunks already migrated: ~{estimated_has_vector}")

    if estimated_needs_migration == 0:
        print("\n✅ All chunks already have vector embeddings!")
        return 0

    # Step 3: Perform migration
    print("\n" + "-"*100)
    print("STEP 3: Migration Options")
    print("-"*100)

    print("\nWe have two migration strategies:")
    print("  1. SQL-based (Fast, runs in database) - RECOMMENDED")
    print("  2. Python-based (Slower, uses API)")

    print("\n⚠️  IMPORTANT: This migration will update all document_chunks.")
    print("    Estimated time: 1-5 minutes")

    response = input("\nProceed with SQL-based migration? (yes/no): ")
    if response.lower() != 'yes':
        print("Migration cancelled.")
        return 1

    # Generate SQL migration script
    print("\n" + "-"*100)
    print("STEP 4: Generating SQL Migration Script")
    print("-"*100)

    sql_script = """
-- Embeddings Migration Script for Caroline Cory
-- Converts JSON string embeddings to PostgreSQL vector type

-- Create embeddings_vec column if it doesn't exist
ALTER TABLE document_chunks
ADD COLUMN IF NOT EXISTS embeddings_vec vector(1024);

-- Migrate embeddings from JSON strings to vector type
-- This processes chunks in batches to avoid memory issues
DO $$
DECLARE
    chunk_record RECORD;
    embedding_array float[];
    converted_count INTEGER := 0;
    error_count INTEGER := 0;
    start_time TIMESTAMP := clock_timestamp();
BEGIN
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

            -- Progress update every 100 chunks
            IF converted_count % 100 = 0 THEN
                RAISE NOTICE 'Converted % chunks... (%.1f seconds)',
                    converted_count,
                    EXTRACT(EPOCH FROM (clock_timestamp() - start_time));
            END IF;

        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Error converting chunk %: %', chunk_record.id, SQLERRM;
            error_count := error_count + 1;
        END;
    END LOOP;

    RAISE NOTICE 'Migration complete!';
    RAISE NOTICE 'Successfully converted: % chunks', converted_count;
    RAISE NOTICE 'Errors: % chunks', error_count;
    RAISE NOTICE 'Total time: %.1f seconds',
        EXTRACT(EPOCH FROM (clock_timestamp() - start_time));
END $$;

-- Create index for vector similarity search
CREATE INDEX IF NOT EXISTS document_chunks_embeddings_vec_idx
ON document_chunks
USING ivfflat (embeddings_vec vector_cosine_ops)
WITH (lists = 100);

-- Verify the migration
SELECT
    COUNT(*) as total_chunks,
    COUNT(embeddings) as chunks_with_json_embeddings,
    COUNT(embeddings_vec) as chunks_with_vector_embeddings,
    COUNT(CASE WHEN embeddings_vec IS NULL AND embeddings IS NOT NULL THEN 1 END) as chunks_still_needing_migration
FROM document_chunks;
"""

    # Save SQL script
    script_path = '/root/sidekick-forge/caroline_embeddings_migration.sql'
    with open(script_path, 'w') as f:
        f.write(sql_script)

    print(f"\n✅ SQL migration script saved to: {script_path}")
    print("\n" + "="*100)
    print("NEXT STEPS:")
    print("="*100)
    print("\n1. Copy the SQL script content")
    print("2. Go to Caroline Cory's Supabase Dashboard")
    print("3. Navigate to: SQL Editor")
    print("4. Paste and run the SQL script")
    print("5. Monitor the NOTICE messages for progress")
    print("\nThe migration will:")
    print(f"  • Convert ~{estimated_needs_migration} chunks from JSON strings to vector type")
    print("  • Create an index for fast vector similarity search")
    print("  • Validate all embeddings are 1024 dimensions")
    print("  • Report any errors encountered")

    print("\n" + "="*100)
    print("ALTERNATIVE: Run SQL via Supabase SQL Editor (Manual)")
    print("="*100)
    print("\nSQL Script:")
    print("-"*100)
    print(sql_script)
    print("-"*100)

    print("\n✅ Migration preparation complete!")
    print(f"\nAfter running the SQL script, the Divine Plan document and all other documents")
    print(f"will be searchable via RAG with proper vector similarity matching.")

    return 0

if __name__ == '__main__':
    sys.exit(main())
