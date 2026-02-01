# Migration Plan: Caroline Cory → Qwen3-Embedding-4B

## Current State

**Client**: Caroline Cory (Superhuman AI)
**Client ID**: `4abb05ac-08dc-4928-ae30-249e2e7d9cc1`
**Tenant Database**: `vtwehoceecybuaqjsqxa.supabase.co`

### Current Embedding Configuration
| Setting | Current Value |
|---------|---------------|
| Provider | `siliconflow` |
| Document Model | `Qwen/Qwen3-Embedding-0.6B` |
| Conversation Model | `Qwen/Qwen3-Embedding-0.6B` |
| Dimension | `null` (defaults to 1024) |

### Current Data Counts
| Table | Total | With Embeddings |
|-------|-------|-----------------|
| document_chunks | 17,150 | 16,897 |
| conversation_transcripts | 635 | 146 |

### Target Configuration
| Setting | New Value |
|---------|-----------|
| Provider | `siliconflow` (unchanged) |
| Document Model | `Qwen/Qwen3-Embedding-4B` |
| Conversation Model | `Qwen/Qwen3-Embedding-4B` |
| Dimension | `1024` (explicit) |

---

## Why Regeneration is Required

Both `Qwen3-Embedding-0.6B` and `Qwen3-Embedding-4B` output 1024-dimension vectors, so **no schema changes** are needed. However, **all embeddings must be regenerated** because:

1. Different models produce different vector representations
2. Mixing embeddings from different models breaks semantic similarity search
3. Query embeddings would use the new 4B model while stored embeddings use the old 0.6B model

---

## Migration Steps

### Phase 1: Preparation (No Downtime)

#### Step 1.1: Verify API Access
- Confirm SiliconFlow API key is valid: `sk-ymdlyrjlwbzomkdlccgpvgpiabzokwtrfgwtovmvawvuksqq`
- Test embedding generation with Qwen3-Embedding-4B model
- Check rate limits (SiliconFlow typically allows 100-1000 req/min)

#### Step 1.2: Backup Current State
```sql
-- In Caroline Cory's Supabase (vtwehoceecybuaqjsqxa)
-- Create backup of current embeddings (optional, for rollback)
CREATE TABLE document_chunks_embeddings_backup AS
SELECT id, embeddings FROM document_chunks WHERE embeddings IS NOT NULL;

CREATE TABLE conversation_transcripts_embeddings_backup AS
SELECT id, embeddings FROM conversation_transcripts WHERE embeddings IS NOT NULL;
```

### Phase 2: Configuration Update

#### Step 2.1: Update Client Settings
Update the `additional_settings.embedding` in the platform database:

```sql
-- In Platform Supabase (eukudpgfpihxsypulopm)
UPDATE clients
SET additional_settings = jsonb_set(
  additional_settings,
  '{embedding}',
  '{
    "provider": "siliconflow",
    "dimension": 1024,
    "document_model": "Qwen/Qwen3-Embedding-4B",
    "conversation_model": "Qwen/Qwen3-Embedding-4B"
  }'::jsonb
)
WHERE id = '4abb05ac-08dc-4928-ae30-249e2e7d9cc1';
```

### Phase 3: Document Chunk Regeneration

#### Step 3.1: Clear Existing Embeddings
```sql
-- In Caroline Cory's Supabase
-- Clear embeddings to force regeneration
UPDATE document_chunks SET embeddings = NULL;
```

#### Step 3.2: Regenerate Embeddings
Use the existing backfill script or create a migration-specific script:

```python
# Option A: Use existing backfill_embeddings_standalone.py with modifications
# Option B: Create dedicated migration script (recommended)

# Key parameters:
# - Client ID: 4abb05ac-08dc-4928-ae30-249e2e7d9cc1
# - Model: Qwen/Qwen3-Embedding-4B
# - Provider: siliconflow
# - Batch size: 50 chunks per API call
# - Rate limiting: 2-second delay between batches
# - Total chunks: ~17,000 (estimated time: 30-60 minutes)
```

#### Step 3.3: Verify Document Embeddings
```sql
-- Verify all chunks have embeddings
SELECT COUNT(*) as total,
       COUNT(embeddings) as with_embeddings,
       COUNT(*) - COUNT(embeddings) as missing
FROM document_chunks;

-- Verify dimension consistency
SELECT array_length(embeddings, 1) as dimension, COUNT(*)
FROM document_chunks
WHERE embeddings IS NOT NULL
GROUP BY 1;
-- Should show: 1024 | 17150
```

### Phase 4: Conversation Transcript Regeneration

#### Step 4.1: Clear and Regenerate
```sql
-- Clear existing conversation embeddings
UPDATE conversation_transcripts SET embeddings = NULL;
```

#### Step 4.2: Regenerate Conversation Embeddings
```python
# Similar process to document chunks
# - Total transcripts: 635
# - Estimated time: 5-10 minutes
```

### Phase 5: Index Optimization

#### Step 5.1: Rebuild HNSW Indexes
```sql
-- Drop and recreate indexes for optimal performance
DROP INDEX IF EXISTS document_chunks_embeddings_hnsw;
DROP INDEX IF EXISTS conversation_transcripts_embeddings_hnsw;

-- Recreate with HNSW
CREATE INDEX document_chunks_embeddings_hnsw
ON document_chunks USING hnsw (embeddings vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX conversation_transcripts_embeddings_hnsw
ON conversation_transcripts USING hnsw (embeddings vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

### Phase 6: Validation

#### Step 6.1: Test RAG Retrieval
```python
# Test queries that should return known documents
test_queries = [
    "What is the divine plan?",
    "How does consciousness work?",
    "Explain spiritual awakening"
]

# For each query:
# 1. Generate embedding with new model
# 2. Run similarity search
# 3. Verify relevant documents are returned
# 4. Check citation quality
```

#### Step 6.2: End-to-End Test
- Open Superhuman AI embed
- Ask questions about Caroline Cory's content
- Verify citations appear correctly
- Test voice and text modes

### Phase 7: Cleanup (Optional)

#### Step 7.1: Remove Backups
```sql
-- After confirming migration success (wait 7+ days)
DROP TABLE IF EXISTS document_chunks_embeddings_backup;
DROP TABLE IF EXISTS conversation_transcripts_embeddings_backup;
```

---

## Rollback Plan

If issues arise after migration:

### Quick Rollback (Within Same Day)
```sql
-- Restore from backup tables
UPDATE document_chunks dc
SET embeddings = backup.embeddings
FROM document_chunks_embeddings_backup backup
WHERE dc.id = backup.id;

-- Revert configuration
UPDATE clients
SET additional_settings = jsonb_set(
  additional_settings,
  '{embedding}',
  '{
    "provider": "siliconflow",
    "dimension": 1024,
    "document_model": "Qwen/Qwen3-Embedding-0.6B",
    "conversation_model": "Qwen/Qwen3-Embedding-0.6B"
  }'::jsonb
)
WHERE id = '4abb05ac-08dc-4928-ae30-249e2e7d9cc1';
```

---

## Estimated Timeline

| Phase | Duration | Notes |
|-------|----------|-------|
| Phase 1: Preparation | 15 min | API verification, backup |
| Phase 2: Config Update | 5 min | SQL update |
| Phase 3: Document Regeneration | 30-60 min | ~17,000 chunks |
| Phase 4: Conversation Regeneration | 5-10 min | ~635 transcripts |
| Phase 5: Index Rebuild | 5-10 min | HNSW index creation |
| Phase 6: Validation | 15-30 min | Testing |
| **Total** | **1.5-2 hours** | |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| API rate limiting | Medium | Medium | Use conservative batch sizes, add delays |
| Embedding dimension mismatch | Low | High | Verify dimensions after regeneration |
| RAG quality degradation | Low | Medium | Test thoroughly before removing backups |
| Downtime during regeneration | Medium | Low | RAG works with partial embeddings |

---

## Success Criteria

- [ ] All 17,150 document chunks have 1024-dim embeddings
- [ ] All 635 conversation transcripts have 1024-dim embeddings
- [ ] HNSW indexes created successfully
- [ ] RAG retrieval returns relevant results for test queries
- [ ] Citations display correctly in embed interface
- [ ] No errors in FastAPI logs during embedding operations
