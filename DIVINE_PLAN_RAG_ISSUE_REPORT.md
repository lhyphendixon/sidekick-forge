# Divine Plan Document RAG Search Issue - Root Cause Analysis

**Date:** December 6, 2025
**Client:** Caroline Cory (Superhuman AI)
**Issue:** Document "Divine Plan_Int_printer_052118 PRINTER READY" not appearing in RAG search results

---

## Executive Summary

The Divine Plan document is **NOT assigned to the agent** in the `agent_documents` junction table, which prevents it from appearing in RAG (Retrieval-Augmented Generation) search results. This affects **187 documents total** (18.7% of the knowledge base).

### Quick Stats
- **Total documents:** 1,000
- **Documents in agent_documents table:** 1,000 (assigned to agent)
- **Wait, what?** Actually **813 unique documents** are in the junction table
- **Unassigned documents:** 187 documents (18.7%)
- **Divine Plan status:** ❌ NOT assigned to agent

---

## Root Cause

The RAG system filters documents based on the `agent_documents` junction table. The SQL RPC function `match_documents` performs a JOIN:

```sql
FROM document_chunks dc
JOIN documents d ON d.id = dc.document_id
JOIN agent_documents ad ON ad.document_id = d.id
WHERE ad.agent_id = p_agent_id
  AND ad.enabled = true
```

**The Divine Plan document** (`9b6d82d0-38ea-40dd-b8f0-af7c52fabcbe`) **is missing from the `agent_documents` table**, so it gets filtered out completely.

---

## Secondary Issues Found

### 1. Embeddings Format Issue
- **Current state:** Embeddings stored as JSON strings
  ```
  embeddings: "[0.060437888,-0.041011423,...]"  (type: string)
  ```
- **Expected state:** PostgreSQL vector type
  ```
  embeddings_vec: vector(1024)
  ```
- **Impact:** The RPC function must parse JSON strings on every query instead of using native vector operations
- **Performance impact:** Slower similarity searches
- **Recommendation:** Migrate to vector type using the existing migration scripts

### 2. Database Schema Differences
Caroline Cory's database uses a different schema than other clients:
- Uses `agent_documents` junction table (not `agent_permissions` array column)
- Uses `agent_id` column on documents table (legacy field, not currently used)
- No `embeddings_vec` column (migration pending)

---

## Impact Assessment

### Documents Affected
**187 documents** cannot be found via RAG search, including:
- Divine Plan_Int_printer_052118 PRINTER READY ⚠️ **YOUR BENCHMARK DOCUMENT**
- SESSION-GH71-REPROGRAMMING-MONEY-AND-ABUNDANCE
- SESSIONS-130-133-DIABETES-BLOOD-SUGAR-WEIGHT-ISSUES
- SESSION-GH10-ALLERGIES-EAR-NOSE-EYES
- OML-G02-Divine Mother Calming and Expanding
- OML-G01-Divine Mother and Cleansing Energy
- ... and 181 more documents

### User Impact
- Users asking questions related to these 187 documents will **NOT get relevant citations**
- The AI will respond based only on its base knowledge, missing critical domain-specific context
- Previously successful queries may now fail to return expected results

---

## Solution

### Immediate Fix (5 minutes)
Run this SQL in Caroline Cory's Supabase SQL Editor:

```sql
-- Fix just the Divine Plan document
INSERT INTO agent_documents (agent_id, document_id, access_type, enabled)
VALUES ('fad73422-0f7c-4771-a98b-5165f4369d8a', '9b6d82d0-38ea-40dd-b8f0-af7c52fabcbe', 'read', true)
ON CONFLICT DO NOTHING;
```

### Complete Fix (5 minutes)
Assign all 187 unassigned documents to the agent:

```sql
-- Assign all unassigned documents to the agent
INSERT INTO agent_documents (agent_id, document_id, access_type, enabled)
SELECT 'fad73422-0f7c-4771-a98b-5165f4369d8a', id, 'read', true
FROM documents
WHERE id NOT IN (
  SELECT document_id FROM agent_documents WHERE agent_id = 'fad73422-0f7c-4771-a98b-5165f4369d8a'
)
ON CONFLICT DO NOTHING;
```

### Verification Query
```sql
-- Verify the fix
SELECT COUNT(*) as assigned_documents
FROM agent_documents
WHERE agent_id = 'fad73422-0f7c-4771-a98b-5165f4369d8a';

-- Should return 1000 (all documents)
```

---

## Long-Term Recommendations

### 1. Automated Document Assignment
Create a database trigger to automatically assign new documents to the agent:

```sql
CREATE OR REPLACE FUNCTION auto_assign_document_to_agent()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO agent_documents (agent_id, document_id, access_type, enabled)
  SELECT id, NEW.id, 'read', true
  FROM agents
  LIMIT 1
  ON CONFLICT DO NOTHING;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER auto_assign_document
  AFTER INSERT ON documents
  FOR EACH ROW
  EXECUTE FUNCTION auto_assign_document_to_agent();
```

### 2. Migrate Embeddings to Vector Type
Run the existing migration scripts to convert JSON string embeddings to native PostgreSQL vector types:
- `/root/sidekick-forge/align_kcg_schema.sql`
- `/root/sidekick-forge/convert_embeddings_to_vector.py`

### 3. Monitor Assignment Coverage
Add monitoring to alert when documents are uploaded but not assigned to any agent.

---

## Testing After Fix

After running the fix SQL, test with this query:
```
"Does divine intervention interfere with human free will"
```

**Expected result:** Citations should include "Divine Plan_Int_printer_052118 PRINTER READY"

---

## Prevention

**Why did this happen?**
Documents were uploaded to the knowledge base but the `agent_documents` assignment step was skipped or failed. This could happen if:
1. Documents were bulk-imported directly into the database
2. The document upload process didn't complete the agent assignment step
3. A migration or data recovery process restored documents but not the junction table entries

**Recommendation:** Audit the document upload code path to ensure `agent_documents` entries are always created.

---

## Files Generated During Investigation

- `/root/sidekick-forge/test_divine_intervention_query.py` - Test script for KCG client
- `/root/sidekick-forge/investigate_caroline_cory_chat.py` - Caroline Cory chat investigation
- `/root/sidekick-forge/diagnose_divine_plan_issue.py` - Diagnostic report script
- `/root/sidekick-forge/audit_caroline_agent_documents.py` - Agent documents audit script
- `/root/sidekick-forge/check_document_schema.py` - Schema inspection script

---

## Summary

**Problem:** 187 documents (18.7%) not assigned to agent in `agent_documents` table
**Primary Impact:** Divine Plan document and 186 others won't appear in RAG searches
**Fix:** Run SQL to insert missing agent_documents entries
**Time to Fix:** 5 minutes
**Time to Test:** 2 minutes
**Recommended:** Also fix all 187 documents, not just Divine Plan

