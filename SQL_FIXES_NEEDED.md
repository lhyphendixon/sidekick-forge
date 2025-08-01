# SQL Function Fixes Required

## Critical Security Issues

### 1. match_documents Function

**Issue**: The `match_documents` function searches across ALL documents in the database, ignoring the `agent_slug` parameter. This causes agents to see documents from other agents, breaking data isolation.

**Current Implementation** (problematic):
```sql
CREATE OR REPLACE FUNCTION match_documents(
  query_embedding vector(384),
  agent_slug text,
  match_threshold float,
  match_count int
)
RETURNS TABLE (
  id uuid,
  title text,
  content text,
  similarity float
)
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    documents.id,
    documents.title,
    documents.content,
    1 - (documents.embeddings <=> query_embedding) as similarity
  FROM documents
  WHERE 1 - (documents.embeddings <=> query_embedding) > match_threshold
  ORDER BY documents.embeddings <=> query_embedding
  LIMIT match_count;
END;
$$ LANGUAGE plpgsql;
```

**Required Fix** (with corrected parameter names):
```sql
CREATE OR REPLACE FUNCTION match_documents(
  p_query_embedding vector(384),
  p_agent_slug text,
  p_match_threshold float,
  p_match_count int
)
RETURNS TABLE (
  id uuid,
  title text,
  content text,
  similarity float
)
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    d.id,
    d.title,
    d.content,
    1 - (d.embeddings <=> p_query_embedding) as similarity
  FROM documents d
  JOIN agent_documents ad ON d.id = ad.document_id
  JOIN agents a ON ad.agent_id = a.id
  WHERE a.slug = p_agent_slug
    AND 1 - (d.embeddings <=> p_query_embedding) > p_match_threshold
  ORDER BY d.embeddings <=> p_query_embedding
  LIMIT p_match_count;
END;
$$ LANGUAGE plpgsql;
```

### 2. match_conversation_transcripts_secure Function

**Issue**: The function has an unsafe COALESCE fallback that returns ALL conversations when agent_id is NULL.

**Current Implementation** (problematic):
```sql
WHERE user_id = user_id_param 
  AND agent_id = COALESCE(agent_id_param, agent_id)
```

**Required Fix** (with proper parameter names and signature):
```sql
CREATE OR REPLACE FUNCTION match_conversation_transcripts_secure(
  query_embeddings vector(384),
  agent_slug_param text,
  user_id_param uuid,
  match_count int DEFAULT 5
)
RETURNS TABLE (
  conversation_id uuid,
  user_message text,
  agent_response text,
  similarity float,
  created_at timestamp
)
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    c.id as conversation_id,
    m1.content as user_message,
    m2.content as agent_response,
    1 - (m1.embeddings <=> query_embeddings) as similarity,
    c.created_at
  FROM conversations c
  JOIN messages m1 ON m1.conversation_id = c.id AND m1.role = 'user'
  JOIN messages m2 ON m2.conversation_id = c.id AND m2.role = 'assistant'
  JOIN agents a ON c.agent_id = a.id
  WHERE c.user_id = user_id_param 
    AND a.slug = agent_slug_param  -- Use agent slug for consistency
    AND m1.embeddings IS NOT NULL
  ORDER BY m1.embeddings <=> query_embeddings
  LIMIT match_count;
END;
$$ LANGUAGE plpgsql;
```

## Implementation Status

- The context.py file has been updated per the "No Fallbacks" policy:
  1. **REMOVED** all fallback strategies and workarounds
  2. Only uses the correct RPC functions with proper parameter names
  3. Fails fast if functions don't exist or return errors

## Action Required

1. Database team MUST create/update these SQL functions in Supabase BEFORE the application will work
2. Both functions must be implemented exactly as specified above
3. Test thoroughly to ensure agent data isolation is maintained
4. No application code changes needed once SQL functions are properly implemented