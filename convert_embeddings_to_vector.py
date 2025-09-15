#!/usr/bin/env python3
"""
Convert JSON string embeddings to PostgreSQL vector type for KCG's database
Following Oversight's implementation notes to avoid errors
"""
import psycopg
import json
import os

# Direct PostgreSQL connection (need to get password from Supabase)
DATABASE_URL = os.environ.get("DATABASE_URL")

def convert_embeddings():
    """Convert JSON embeddings to vector type, skipping already-converted rows"""
    
    if not DATABASE_URL:
        print("DATABASE_URL environment variable not set")
        print("Please set it to: postgresql://postgres:[password]@db.qbeftummyzfiyihfsyup.supabase.co:5432/postgres")
        return
    
    print("=== Converting Embeddings to Vector Type ===\n")
    
    with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
        with conn.cursor() as cur:
            
            # 1. First, check current state
            print("1. Checking current embedding storage types...")
            cur.execute("""
                SELECT 
                    pg_typeof(embedding)::text as type,
                    COUNT(*) as count
                FROM public.documents
                GROUP BY pg_typeof(embedding)::text
            """)
            
            print("Current types in documents table:")
            for row in cur.fetchall():
                print(f"  {row[0]}: {row[1]} rows")
            
            # 2. Add vector column if it doesn't exist or is wrong type
            print("\n2. Ensuring vector column exists...")
            try:
                # Check if column exists and is vector type
                cur.execute("""
                    SELECT data_type, udt_name 
                    FROM information_schema.columns 
                    WHERE table_schema = 'public' 
                    AND table_name = 'documents' 
                    AND column_name = 'embedding'
                """)
                result = cur.fetchone()
                
                if not result or 'vector' not in str(result[1]).lower():
                    print("  Adding/altering embedding column to vector(1024)...")
                    
                    # First rename old column if it exists
                    try:
                        cur.execute("ALTER TABLE public.documents RENAME COLUMN embedding TO embedding_old")
                        conn.commit()
                        print("  Renamed existing column to embedding_old")
                    except:
                        conn.rollback()
                    
                    # Add new vector column
                    cur.execute("ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS embedding vector(1024)")
                    conn.commit()
                    print("  Added vector(1024) column")
                else:
                    print("  Vector column already exists")
                    
            except Exception as e:
                print(f"  Column check error: {e}")
                conn.rollback()
            
            # 3. Convert JSON strings to vectors
            print("\n3. Converting JSON embeddings to vectors...")
            
            # Get rows with JSON/text embeddings
            cur.execute("""
                SELECT id, embedding_old
                FROM public.documents
                WHERE embedding_old IS NOT NULL
                AND embedding IS NULL
                LIMIT 100
            """)
            
            rows_to_convert = cur.fetchall()
            
            if not rows_to_convert:
                # Try with original column name if no _old column
                cur.execute("""
                    SELECT id, embedding
                    FROM public.documents
                    WHERE embedding IS NOT NULL
                    AND pg_typeof(embedding)::text IN ('text', 'jsonb', 'json', 'character varying')
                    LIMIT 100
                """)
                rows_to_convert = cur.fetchall()
            
            print(f"  Found {len(rows_to_convert)} rows to convert")
            
            converted = 0
            for doc_id, embedding_json in rows_to_convert:
                try:
                    # Parse JSON string
                    if isinstance(embedding_json, str):
                        embedding_array = json.loads(embedding_json)
                    else:
                        embedding_array = embedding_json
                    
                    if len(embedding_array) != 1024:
                        print(f"  Skipping {doc_id}: wrong dimension ({len(embedding_array)})")
                        continue
                    
                    # Convert to PostgreSQL vector format
                    vector_str = '[' + ','.join(str(float(x)) for x in embedding_array) + ']'
                    
                    # Update with vector
                    cur.execute(
                        "UPDATE public.documents SET embedding = %s::vector WHERE id = %s",
                        (vector_str, doc_id)
                    )
                    
                    converted += 1
                    if converted % 10 == 0:
                        print(f"  Converted {converted} documents...")
                        conn.commit()
                    
                except Exception as e:
                    print(f"  Error converting {doc_id}: {str(e)[:100]}")
                    conn.rollback()
                    continue
            
            conn.commit()
            print(f"  Successfully converted {converted} embeddings")
            
            # 4. Create index for similarity search
            print("\n4. Creating vector index...")
            try:
                # Drop existing index if any
                cur.execute("DROP INDEX IF EXISTS documents_embedding_idx")
                
                # Create IVFFlat index with cosine distance
                cur.execute("""
                    CREATE INDEX documents_embedding_idx 
                    ON public.documents 
                    USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = 100)
                """)
                conn.commit()
                print("  Created IVFFlat index with cosine distance")
            except Exception as e:
                print(f"  Index creation error (may already exist): {str(e)[:100]}")
                conn.rollback()
            
            # 5. Verify conversion
            print("\n5. Verification...")
            
            # Check vector dimensions
            cur.execute("""
                SELECT 
                    vector_dims(embedding) as dims,
                    COUNT(*) as count
                FROM public.documents
                WHERE embedding IS NOT NULL
                AND pg_typeof(embedding)::text = 'vector'
                GROUP BY vector_dims(embedding)
            """)
            
            print("  Vector dimensions:")
            for row in cur.fetchall():
                print(f"    {row[0]} dimensions: {row[1]} documents")
            
            # Final type check
            cur.execute("""
                SELECT 
                    pg_typeof(embedding)::text as type,
                    COUNT(*) as count
                FROM public.documents
                WHERE embedding IS NOT NULL
                GROUP BY pg_typeof(embedding)::text
            """)
            
            print("\n  Final storage types:")
            for row in cur.fetchall():
                print(f"    {row[0]}: {row[1]} rows")
            
            print("\n=== Conversion Complete ===")
            
            # 6. Clean up old column if exists
            try:
                cur.execute("ALTER TABLE public.documents DROP COLUMN IF EXISTS embedding_old")
                conn.commit()
                print("Cleaned up old embedding column")
            except:
                pass

if __name__ == "__main__":
    convert_embeddings()