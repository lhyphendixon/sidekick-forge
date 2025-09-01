# RAG Citations Feature - Deployment Guide

## Overview

This guide covers the deployment of the RAG Citations feature, which adds source attribution to AI assistant responses based on the actual context chunks used during RAG retrieval.

## Features Implemented

### Core Functionality
- ✅ **Supabase RPC function** for vector search with citation metadata
- ✅ **Backend RAG service** with citation tracking (`app/integrations/rag/citations_service.py`)  
- ✅ **Agent integration** with citation capture during `on_user_turn_completed` hook
- ✅ **Streaming SSE protocol** extension to include citations in final response
- ✅ **UI component** for displaying citations in both admin and embed contexts
- ✅ **Feature flag** (`show_citations`) configurable per agent
- ✅ **Analytics & observability** with citation usage tracking
- ✅ **Multi-tenant isolation** ensuring citations respect client data boundaries

### Citation Data Structure
```json
{
  "chunk_id": "uuid",
  "doc_id": "uuid", 
  "title": "Document Title",
  "source_url": "https://example.com/doc",
  "source_type": "web|pdf|md",
  "chunk_index": 3,
  "page_number": 5,
  "char_start": 120,
  "char_end": 340,
  "similarity": 0.82
}
```

## Deployment Steps

### 1. Database Schema Setup

#### Platform Database (Main Supabase)
Apply the analytics schema:
```bash
# Apply analytics tables for citation tracking
psql -f scripts/supabase_citations_analytics_schema.sql "$PLATFORM_DB_URL"
```

#### Client Databases  
Apply the RPC function to each client's Supabase project:
```bash
# Apply to each client database
psql -f scripts/supabase_rag_citations_rpc.sql "$CLIENT_DB_URL"
```

**Key changes:**
- Adds `match_document_chunks` RPC function for citation-aware vector search
- Adds citation metadata columns to `document_chunks` table if missing
- Creates optimized indexes for vector similarity search
- Sets up analytics tables in platform database

### 2. Backend Deployment

**New files to deploy:**
```
app/integrations/rag/
├── __init__.py
└── citations_service.py

app/integrations/analytics/  
├── __init__.py
└── citations_analytics.py

app/models/citation.py

app/static/js/citations.js
app/static/css/citations.css
```

**Modified files:**
```
docker/agent/sidekick_agent.py         # Citation integration in agent
app/api/embed.py                       # SSE protocol extension
app/templates/embed/sidekick.html      # UI integration
app/models/agent.py                    # Feature flag support
```

### 3. Feature Flag Configuration

Citations can be enabled/disabled per agent:

```python
# In agent configuration
agent_config = {
    "show_citations": True,  # Enable citations for this agent
    "dataset_ids": ["uuid1", "uuid2"]  # Datasets to search
}
```

### 4. Frontend Assets

Ensure static assets are deployed:
```bash
# Copy to your static file serving location
cp app/static/js/citations.js /path/to/static/js/
cp app/static/css/citations.css /path/to/static/css/
```

### 5. Environment Variables

No new environment variables required. Uses existing Supabase credentials from client settings.

## Testing the Feature

### 1. Database Testing
```sql
-- Test the RPC function
SELECT public.test_match_document_chunks();

-- Test with sample data
SELECT * FROM public.match_document_chunks(
  ARRAY_FILL(0.1, ARRAY[1024])::vector,
  ARRAY['your-dataset-id']::uuid[],
  5
);
```

### 2. Backend Testing
```bash
# Run citation tests
pytest tests/test_citations.py -v

# Test citation service directly
python -c "
from app.integrations.rag.citations_service import rag_citations_service
import asyncio
# asyncio.run(test_function())
"
```

### 3. Frontend Testing

**In Admin/Embed:**
1. Send a message that should trigger RAG retrieval
2. Verify citations appear below the assistant response
3. Test citation links and tooltips
4. Check expand/collapse functionality

### 4. Analytics Verification
```sql
-- Check citation analytics
SELECT COUNT(*) FROM agent_message_citations WHERE created_at > NOW() - INTERVAL '24 hours';

-- Get client stats
SELECT * FROM get_client_citation_stats('client_id', 7);
```

## Configuration Options

### Agent-Level Settings
```json
{
  "show_citations": true,
  "dataset_ids": ["dataset-uuid-1", "dataset-uuid-2"],
  "max_citations_shown": 3,
  "similarity_threshold": 0.5
}
```

### UI Customization
```css
/* Customize citation appearance */
.citations-container.compact {
  padding: 6px;
  font-size: 0.8rem;
}

.citation-item:hover {
  background: rgba(1, 164, 166, 0.1);
}
```

## Monitoring & Observability

### Key Metrics to Monitor
1. **Citation retrieval latency** - Target <250ms
2. **Citation accuracy** - Similarity scores distribution
3. **User engagement** - Citation click rates
4. **Error rates** - RAG retrieval failures

### Log Messages to Watch
```
INFO: RAG retrieval completed: 4 citations from 2 documents in 180.5ms
INFO: Retrieved 4 citations for message abc-123
ERROR: Citation retrieval failed: <error details>
WARN: No dataset IDs configured for citations
```

### Analytics Queries
```sql
-- Daily citation usage
SELECT DATE(created_at), COUNT(*) FROM agent_message_citations 
GROUP BY DATE(created_at) ORDER BY DATE(created_at) DESC;

-- Top performing documents
SELECT doc_id, COUNT(*) as citation_count, AVG(similarity_score) as avg_similarity
FROM agent_message_citations 
GROUP BY doc_id ORDER BY citation_count DESC LIMIT 10;
```

## Troubleshooting

### Common Issues

**1. No citations appearing**
- Check `show_citations` flag is `true` for the agent
- Verify `dataset_ids` are configured
- Check RAG retrieval logs for errors
- Confirm RPC function exists in client database

**2. Vector search errors**  
- Verify embedding dimensions match (1024)
- Check pgvector extension is enabled
- Ensure vector indexes are built: `ANALYZE document_chunks;`

**3. UI not loading**
- Verify static assets are deployed correctly
- Check browser console for JavaScript errors
- Confirm CSS/JS files are accessible

**4. Analytics not working**
- Check platform database has analytics tables
- Verify service role permissions
- Check analytics logging doesn't throw errors

### Performance Optimization

**Database:**
```sql
-- Rebuild vector index if needed
REINDEX INDEX idx_document_chunks_embedding_cosine;

-- Update statistics
ANALYZE document_chunks;
```

**Application:**
- Monitor RAG retrieval latency
- Adjust similarity thresholds based on quality
- Limit citation counts for performance

## Rollback Plan

If issues occur:

1. **Disable feature flag**:
   ```python
   agent.show_citations = False
   ```

2. **Remove UI components**:
   ```html
   <!-- Comment out in templates -->
   <!-- <script src="/static/js/citations.js"></script> -->
   ```

3. **Database rollback** (if needed):
   ```sql
   DROP FUNCTION IF EXISTS public.match_document_chunks;
   DROP TABLE IF EXISTS public.agent_message_citations;
   ```

## Security Considerations

- ✅ **Multi-tenant isolation**: Citations respect client data boundaries
- ✅ **RLS policies**: Analytics tables have proper row-level security  
- ✅ **No PII exposure**: Only document metadata and IDs stored in analytics
- ✅ **HTTPS enforcement**: All citation links should use HTTPS
- ✅ **XSS protection**: Citation content is sanitized before display

## Performance Benchmarks

**Expected Performance:**
- Vector search: <200ms for typical queries
- Citation rendering: <50ms for 3-5 citations  
- Analytics logging: <10ms (async)
- Memory usage: +~2MB per active agent

## Support

For issues or questions:
1. Check application logs for detailed error messages
2. Verify database connectivity and RPC function availability
3. Test with minimal configuration first
4. Use analytics queries to diagnose citation retrieval issues

## Version Compatibility

- **Requires**: Supabase with pgvector extension
- **Compatible with**: LiveKit Agent SDK 1.0+
- **Tested on**: Python 3.10+, FastAPI 0.68+
- **Browser support**: Modern browsers with ES6 support