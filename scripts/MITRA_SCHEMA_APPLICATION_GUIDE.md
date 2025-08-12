# Mitra Politi Schema Application Guide

## Quick Start

We're using the **same schema that worked successfully for KCG yesterday**. This schema has been proven to work without any ivfflat dimension errors.

## Files

- **Schema File**: `/root/sidekick-forge/scripts/mitra_politi_kcg_based_schema.sql`
- **Application Script**: `/root/sidekick-forge/scripts/apply_mitra_kcg_schema.sh`
- **Verification Script**: `/root/sidekick-forge/scripts/verify_mitra_schema.py`

## Step-by-Step Instructions

### Step 1: Open Mitra's SQL Editor

Navigate to:
```
https://uyswpsluhkebudoqdnhk.supabase.co/project/uyswpsluhkebudoqdnhk/sql/new
```

### Step 2: Copy the Schema

Open the schema file and copy ALL contents:
```bash
cat /root/sidekick-forge/scripts/mitra_politi_kcg_based_schema.sql
```

Or use the viewer script:
```bash
./scripts/apply_mitra_kcg_schema.sh
```

### Step 3: Paste and Run

1. Paste the entire schema into the SQL editor
2. Click the "Run" button
3. Wait for completion (should take a few seconds)

### Step 4: Verify the Schema

Run the verification script with Mitra's service role key:
```bash
MITRA_SERVICE_KEY='your-service-role-key' python3 /root/sidekick-forge/scripts/verify_mitra_schema.py
```

### Step 5: Update Platform Configuration

After successful schema application, update the service role key in the platform:
```bash
python3 /root/sidekick-forge/scripts/update_mitra_service_key.py 'your-service-role-key'
```

## What This Schema Includes

### Tables Created:
- ✅ `agents` - Agent configurations
- ✅ `agent_configurations` - Additional agent settings
- ✅ `conversations` - Conversation records
- ✅ `messages` - Individual messages
- ✅ `conversation_transcripts` - Transcripts with embeddings
- ✅ `documents` - Document storage with embeddings
- ✅ `document_chunks` - Chunked documents for RAG
- ✅ `global_settings` - Configuration settings

### Vector Configuration:
- All embedding columns use **1024 dimensions**
- No 4096-dimensional columns (avoids ivfflat errors)
- Proper ivfflat indexes with `vector_cosine_ops`

### Key Features:
- pgvector extension enabled
- Row-level security ready
- Proper foreign key relationships
- Optimized indexes for performance

## Troubleshooting

### If you get "table already exists" errors:
This is fine - the schema uses `CREATE TABLE IF NOT EXISTS`. It won't overwrite existing data.

### If you get dimension errors:
This shouldn't happen with this schema, but if it does:
1. Check if there are any existing tables with different dimensions
2. You may need to drop and recreate the problematic tables

### To completely reset (if needed):
```sql
-- WARNING: This will delete all data!
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
-- Then run the schema file again
```

## Testing After Application

Test document embedding creation:
```sql
-- Insert a test document
INSERT INTO documents (title, content, embeddings)
VALUES (
  'Test Document',
  'This is a test document content',
  ARRAY_FILL(0.1, ARRAY[1024])::vector
);

-- Test vector search
SELECT id, title 
FROM documents 
WHERE embeddings IS NOT NULL
ORDER BY embeddings <=> ARRAY_FILL(0.1, ARRAY[1024])::vector
LIMIT 5;
```

## Success Indicators

You know the schema is applied correctly when:
1. ✅ All tables are created without errors
2. ✅ Vector columns show as `vector(1024)` type
3. ✅ Indexes are created successfully
4. ✅ Verification script shows all green checks
5. ✅ Document upload works in the platform
6. ✅ Agent creation works in the platform

## Next Steps

After successful schema application:
1. Configure agents in the platform
2. Upload documents to the knowledge base
3. Test chat functionality
4. Configure API keys in global_settings if needed

## Support

If you encounter issues:
1. Check the Supabase logs for detailed error messages
2. Ensure you're using the service role key (not anon key)
3. Verify pgvector extension is enabled in Supabase
4. Make sure you're in the correct project

---

**Note**: This schema is identical to what was successfully used for KCG, just adapted for Mitra Politi's database.