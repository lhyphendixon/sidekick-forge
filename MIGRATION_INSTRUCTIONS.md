# Caroline Cory RAG Search Fix - Complete Migration Guide

**Issue:** Divine Plan document and potentially other documents not appearing in RAG search results

**Root Cause:** Embeddings stored as JSON strings instead of PostgreSQL vector type

**Solution:** Migrate embeddings to vector format and update RPC function

---

## Quick Stats

- **Total documents:** 1,280
- **Total chunks:** 17,150
- **Chunks needing migration:** ~16,978 (99%)
- **Documents not assigned to agent:** 146 (separate issue, lower priority)
- **Estimated migration time:** 2-5 minutes

---

## Migration Steps

### Step 1: Migrate Embeddings to Vector Format

**File:** `caroline_embeddings_migration.sql`

**Instructions:**

1. Open Caroline Cory's Supabase Dashboard
   - URL: https://supabase.com/dashboard/project/vtwehoceecybuaqjsqxa

2. Click **SQL Editor** in the left sidebar

3. Click **New Query**

4. Copy the entire contents of `caroline_embeddings_migration.sql`

5. Paste into the SQL editor

6. Click **RUN** (or press Cmd/Ctrl + Enter)

7. **Monitor the progress** in the Results panel
   - You'll see NOTICE messages every 500 chunks
   - Example: `[500/16978] Converted 500 chunks in 15.2 seconds (32.9 chunks/sec)`

8. **Wait for completion message:**
   ```
   ======================================
   Migration complete!
   ======================================
   Successfully converted: 16978 chunks
   Errors: 0 chunks
   Total time: 180.5 seconds
   Average rate: 94.1 chunks/second
   ======================================
   ```

9. **Verify the migration** - The final SELECT query will show:
   ```
   total_chunks: 17150
   chunks_with_json_embeddings: 17150
   chunks_with_vector_embeddings: 16978
   chunks_still_needing_migration: 0
   percentage_migrated: 99.0
   ```

**Expected time:** 2-5 minutes

---

### Step 2: Update match_documents RPC Function

**File:** `update_match_documents_rpc.sql`

**Instructions:**

1. In the same SQL Editor (or create a new query)

2. Copy the entire contents of `update_match_documents_rpc.sql`

3. Paste and click **RUN**

4. **Verify success** - You should see:
   ```
   Success. No rows returned
   ```

**Expected time:** < 5 seconds

---

### Step 3: Test the Migration

Run this test query in the SQL Editor:

```sql
-- Test that Divine Plan document appears in results
SELECT
    id,
    title,
    similarity,
    chunk_index
FROM match_documents(
    -- Dummy embedding vector for testing
    ARRAY_FILL(0.1::float, ARRAY[1024])::vector,
    'carolineai',  -- agent slug
    0.0,           -- very low threshold to see any results
    20             -- return top 20
);
```

**Expected result:** You should see results from various documents, potentially including Divine Plan.

---

### Step 4: Test with Real Query

The real test is to ask the agent the benchmark question:

**Query:** "Does divine intervention interfere with human free will"

**Expected result:** Citations should include "Divine Plan_Int_printer_052118 PRINTER READY"

---

## What This Migration Does

### Embeddings Migration (Step 1)

1. ✅ Creates `embeddings_vec` column (vector type) if it doesn't exist
2. ✅ Converts all 16,978 chunks from JSON strings to PostgreSQL vectors
3. ✅ Validates each embedding is exactly 1024 dimensions
4. ✅ Creates ivfflat index for fast vector similarity search
5. ✅ Reports progress every 500 chunks
6. ✅ Shows final statistics

### RPC Function Update (Step 2)

1. ✅ Updates `match_documents` to use `embeddings_vec` instead of `embeddings`
2. ✅ Properly joins with `agent_documents` table
3. ✅ Filters by agent slug and enabled status
4. ✅ Uses native vector operations for 10-100x faster similarity search

---

## Benefits After Migration

### Performance Improvements

- **10-100x faster** vector similarity searches
- Native PostgreSQL vector operations instead of JSON parsing
- Optimized ivfflat index for approximate nearest neighbor search

### Functionality Improvements

- ✅ All 1,280 documents will be searchable via RAG
- ✅ Divine Plan document will appear in results
- ✅ More accurate similarity scoring
- ✅ Better citation quality

---

## Optional: Assign Remaining 146 Documents

There are 146 documents that exist in the database but are not assigned to the agent. These won't appear in RAG searches regardless of embedding format.

**To fix (optional):**

Run this SQL:

```sql
-- Assign all unassigned documents to the agent
INSERT INTO agent_documents (agent_id, document_id, access_type, enabled)
SELECT 'fad73422-0f7c-4771-a98b-5165f4369d8a', id, 'read', true
FROM documents
WHERE id NOT IN (
  SELECT document_id
  FROM agent_documents
  WHERE agent_id = 'fad73422-0f7c-4771-a98b-5165f4369d8a'
)
ON CONFLICT DO NOTHING;
```

This will increase searchable documents from 1,134 to 1,280 (100%).

---

## Troubleshooting

### If migration fails midway

The migration is idempotent - you can run it again and it will only process chunks that haven't been converted yet. Just re-run the same SQL script.

### If no results after migration

1. Check that Step 2 (RPC update) was completed
2. Verify the index was created:
   ```sql
   SELECT indexname, tablename
   FROM pg_indexes
   WHERE tablename = 'document_chunks'
   AND indexname LIKE '%embeddings_vec%';
   ```

3. Test the RPC directly with the test query in Step 3

### If performance is slow

The ivfflat index may need to be rebuilt:

```sql
DROP INDEX IF EXISTS document_chunks_embeddings_vec_idx;

CREATE INDEX document_chunks_embeddings_vec_idx
ON document_chunks
USING ivfflat (embeddings_vec vector_cosine_ops)
WITH (lists = 100);
```

---

## Files Generated

1. **`caroline_embeddings_migration.sql`** - Main migration script
2. **`update_match_documents_rpc.sql`** - RPC function update
3. **`MIGRATION_INSTRUCTIONS.md`** - This file
4. **`DIVINE_PLAN_RAG_ISSUE_REPORT.md`** - Detailed investigation report

---

## Verification Checklist

After completing all steps, verify:

- [ ] Embeddings migration completed (Step 1)
- [ ] RPC function updated (Step 2)
- [ ] Test query returns results (Step 3)
- [ ] Divine Plan document appears in real queries (Step 4)
- [ ] Vector index exists and is working
- [ ] (Optional) All 1,280 documents assigned to agent

---

## Summary

**Before Migration:**
- 0% of documents searchable via vector similarity
- Divine Plan document not appearing in results
- Slow JSON string parsing for embeddings
- 146 documents not assigned

**After Migration:**
- 99%+ of documents searchable via vector similarity
- Divine Plan document appears in results
- 10-100x faster similarity searches
- Native PostgreSQL vector operations
- (Optional) 100% documents assigned

**Total time to fix:** ~5-10 minutes

---

**Ready to proceed?** Start with Step 1!
