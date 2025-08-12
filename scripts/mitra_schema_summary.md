# Mitra Politi Schema Configuration Summary

## Vector Dimensions (Corrected to match Autonomite)

### Table Column Dimensions:
- **conversation_transcripts.embeddings**: `vector(1024)`
- **documents.embedding**: `vector(4096)` (legacy column)
- **documents.embeddings**: `vector(1024)` (active column)
- **document_chunks.embeddings**: `vector(1024)`

## RAG Functions (Exact match with Autonomite)

### 1. match_documents (Simple Version)
```sql
match_documents(query_embedding vector, match_count integer DEFAULT 5)
```
- Searches document_chunks table
- Returns: id, content, metadata, similarity
- Uses 1024-dimensional vectors

### 2. match_documents (Agent-Filtered Version)
```sql
match_documents(p_query_embedding vector, p_agent_slug text, p_match_threshold float8, p_match_count integer)
```
- Searches documents table with agent filtering
- Returns: id, title, content, similarity
- Joins with agent_documents and agents tables
- Uses 1024-dimensional vectors

### 3. match_conversation_transcripts_secure
```sql
match_conversation_transcripts_secure(query_embeddings vector, agent_slug_param text, user_id_param uuid, match_count integer DEFAULT 5)
```
- Searches conversation history
- Returns: conversation_id, user_message, agent_response, similarity, created_at
- Joins user and assistant messages
- Filters by agent slug and user ID
- Uses 1024-dimensional vectors

### 4. match_conversation_transcripts_agent
```sql
match_conversation_transcripts_agent(query_embeddings vector, user_id_param uuid, agent_slug_param text, match_count integer DEFAULT 3)
```
- Alternative conversation search function
- Returns: id, conversation_id, content, role, metadata, created_at, similarity
- Uses conversation metadata for agent filtering
- Uses 1024-dimensional vectors

## Key Differences from Original

1. **Vector Dimensions**: Changed from 1536 to 1024 for active embedding columns
2. **Agent ID Type**: Changed from TEXT to UUID in conversation_transcripts
3. **Transcript Column**: Changed from TEXT to JSONB in conversation_transcripts
4. **Function Signatures**: All vector parameters are untyped in function signatures (just `vector` not `vector(1024)`)
5. **Index Type**: Using ivfflat indexes with vector_cosine_ops for better performance

## Files Updated

1. **mitra_politi_full_schema.sql** - Complete schema with corrected dimensions
2. **migrate_mitra_politi_schema.py** - Python migration script with correct dimensions
3. **verify_mitra_schema.py** - Verification script updated for 1024 dimensions
4. **MITRA_POLITI_SETUP.md** - Documentation updated with correct information

## Application Instructions

1. Apply the schema using: `/root/sidekick-forge/scripts/mitra_politi_full_schema.sql`
2. Verify using: `MITRA_SERVICE_KEY='key' python3 /root/sidekick-forge/scripts/verify_mitra_schema.py`
3. The schema is now consistent with Autonomite Agent's exact implementation