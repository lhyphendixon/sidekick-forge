# CLAUDE.md (Compact)

Guidance for AI coding assistants working in this repo. Keep it tight, correct, and aligned with the platform architecture.

## What this repo is
- Sidekick Forge staging implementation: multi-tenant SaaS for AI agents (FastAPI + Docker).
- All development happens in `/root/sidekick-forge/`. The `/opt/autonomite-saas/` path is deprecated.

## Architecture essentials
- **Tiered multi-tenant**: Two client tiers with different isolation models (see Infrastructure Overview below).
- Stateless worker pool: generic `agent-worker` containers load client/agent config dynamically at job time.
- RAG storage: Supabase `pgvector` only. Similarity via RPC in client DB. No local vector stores/models.
- Credentials: always fetched dynamically from Supabase; env vars only for initial bootstrap to the platform Supabase.
- LiveKit: use v1.0+ patterns with `AgentSession` and explicit agent dispatch.

## Infrastructure Overview

### Platform Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PLATFORM SUPABASE                                  │
│  (Central control plane - stores client configs, credentials, billing)       │
│  - clients table (all tiers)                                                 │
│  - client_credentials (API keys, Supabase URLs for Champions)                │
│  - platform-level analytics                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
┌───────────────────────────────┐   ┌───────────────────────────────────────┐
│      ADVENTURER TIER          │   │           CHAMPION TIER                │
│      (Shared Pool)            │   │         (Dedicated Supabase)           │
│                               │   │                                        │
│  ┌─────────────────────────┐  │   │  ┌────────────────────────────────┐   │
│  │   Shared Supabase DB    │  │   │  │  Client's Own Supabase Project │   │
│  │                         │  │   │  │                                │   │
│  │  - All adventurer data  │  │   │  │  - Full data isolation         │   │
│  │  - RLS by client_id     │  │   │  │  - Custom HNSW indexes         │   │
│  │  - NO HNSW indexes      │  │   │  │  - Direct DB access            │   │
│  │    (security risk)      │  │   │  │  - Schema sync applied         │   │
│  │  - Exact vector search  │  │   │  │  - Full pgvector optimization  │   │
│  └─────────────────────────┘  │   │  └────────────────────────────────┘   │
└───────────────────────────────┘   └───────────────────────────────────────┘
```

### Client Tiers

#### Adventurer Tier (Shared Pool)
- **Database**: Shared Supabase instance with Row-Level Security (RLS)
- **Isolation**: Logical isolation via `client_id` column on all tables
- **Vector Search**: **NO HNSW indexes** - uses exact/brute-force similarity search
  - HNSW on shared tables is a security risk (index scans may touch other tenants' vectors)
  - Timing attacks and information leakage possible with shared indexes
  - Exact search filters by `client_id` first, then computes similarity
- **Performance**: Slower RAG queries (acceptable for tier pricing)
- **Use case**: Small-scale users, trials, low-volume agents

#### Champion Tier (Dedicated)
- **Database**: Client's own Supabase project (full isolation)
- **Isolation**: Complete database-level isolation
- **Vector Search**: **Full HNSW indexes** supported and recommended
  - `CREATE INDEX ON document_chunks USING hnsw (embedding vector_cosine_ops)`
  - Tunable parameters: `ef_search`, `m`, `ef_construction`
- **Performance**: Fast RAG queries with proper index tuning
- **Schema Sync**: `app/services/schema_sync.py` applies `match_documents` RPC and migrations
- **Use case**: Production clients, high-volume, enterprise

### Database Schema (Per-Tenant)

Core tables (exist in both shared pool and dedicated DBs):
- `agents` - Agent configurations
- `documents` - Uploaded documents metadata
- `document_chunks` - Chunked content with embeddings (pgvector)
- `agent_documents` - Join table for agent-document assignments (canonical relationship)
- `conversations` - Conversation sessions
- `conversation_transcripts` - Message history

### Vector Search Architecture

```
Champion (Dedicated DB):
  Query → HNSW Index Scan → Top K vectors → Return results
  (Fast, ~10-50ms for typical queries)

Adventurer (Shared Pool):
  Query → Filter by client_id → Exact similarity on subset → Return results
  (Slower, ~100-500ms depending on document count, but SECURE)
```

**Critical**: Never add HNSW indexes to the shared pool database. This is a security boundary, not a performance oversight.

### Recommended Indexes

**Champion DBs only** (add via schema sync or manual setup):
```sql
-- HNSW for vector similarity (Champion only!)
CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
ON document_chunks USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Standard B-tree indexes (both tiers)
CREATE INDEX IF NOT EXISTS idx_conversation_transcripts_client_agent_created
ON conversation_transcripts (client_id, agent_id, created_at);

CREATE INDEX IF NOT EXISTS idx_document_chunks_client_doc_idx
ON document_chunks (client_id, document_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_agent_documents_client_agent_enabled
ON agent_documents (client_id, agent_id, enabled);
```

**Shared pool**: Only B-tree indexes on `client_id` prefixed columns. No HNSW.

### Worker Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    AGENT WORKER POOL                             │
│  (Stateless containers - scale horizontally)                     │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │   Worker 1  │  │   Worker 2  │  │   Worker N  │              │
│  │             │  │             │  │             │              │
│  │  LiveKit    │  │  LiveKit    │  │  LiveKit    │              │
│  │  Agent SDK  │  │  Agent SDK  │  │  Agent SDK  │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│         │                │                │                      │
│         └────────────────┼────────────────┘                      │
│                          ▼                                       │
│              Per-job credential fetch                            │
│              from Platform Supabase                              │
└─────────────────────────────────────────────────────────────────┘
```

- Workers have NO persistent state
- Each job fetches: agent config, client credentials, target Supabase URL
- RAG queries go to the appropriate DB (shared pool or dedicated)
- LLM/TTS/STT API keys fetched per-job from client credentials

## Document-Agent Assignment (Critical)
- **Standard pattern**: Use the `agent_documents` join table for document-agent relationships.
- The `match_documents` RPC function joins: `documents` → `agent_documents` → `agents`
- Do NOT use `documents.agent_id` directly for RAG queries - this column may exist but is not the canonical relationship.
- When assigning documents to agents, always insert into `agent_documents` table with `enabled=true`.
- Schema sync (`app/services/schema_sync.py`) applies the `match_documents` function to all tenant databases.
- If a client's documents aren't appearing in RAG, check that `agent_documents` entries exist and are enabled.

## Non-negotiable policies
- No workarounds: fix root causes; no masking, no artificial delays, no "temporary" hacks.
- Dynamic API keys: never hardcode or rely on env as primary source; fetch from Supabase.
- Forbidden tech: `ChromaDB`, `sentence-transformers` (violates stateless/service-based design).
- Redis removed: do not reintroduce Redis for rate limiting, caching, or dedupe. If global limits or caches are needed later, use a DB-backed approach or explicit idempotency keys. In-process dedupe is sufficient for single-instance.

## NO FALLBACK Policy (Critical)
This policy prevents silent failures that cause hallucinated responses and complicate troubleshooting.

**Principle**: If any part of the RAG/response pipeline fails, return an explicit error to the user rather than silently degrading to a non-RAG path that produces hallucinated content.

**Where enforced**:
1. **Agent entrypoint** (`docker/agent/entrypoint.py`): If RAG context retrieval returns empty, raise `ValueError` instead of continuing without context.
2. **Agent voice mode** (`docker/agent/sidekick_agent.py`): If `_retrieve_with_citations` fails or returns empty context, raise an error instead of catching and continuing.
3. **Embed streaming** (`app/api/embed.py`): If LiveKit streaming fails, return an error response instead of falling back to `handle_text_trigger` or `handle_text_trigger_via_livekit` which lack RAG integration.
4. **Provider failures**: If configured STT/TTS/LLM fails, error out (no silent fallback to different provider).

**Why this matters**:
- Fallbacks that skip RAG produce hallucinated responses (e.g., wrong person info, made-up facts)
- Silent failures make debugging extremely difficult - logs show correct processing but user sees wrong output
- Users prefer an explicit error over confidently wrong information

**Implementation pattern**:
```python
# BAD - silent fallback
try:
    context = await get_rag_context(query)
except Exception as e:
    logger.warning(f"RAG failed: {e}")
    context = ""  # Continues without RAG - will hallucinate!

# GOOD - explicit failure
context = await get_rag_context(query)
if not context:
    raise ValueError("RAG context retrieval failed - empty context returned")
```

## Key paths
- App entry: `/root/sidekick-forge/app/main.py`
- APIs: `/root/sidekick-forge/app/api/v1/` (legacy), `/root/sidekick-forge/app/api/v2/` (multi-tenant)
- Admin UI: `/root/sidekick-forge/app/admin/`
- Integrations: `/root/sidekick-forge/app/integrations/`
- Middleware: `/root/sidekick-forge/app/middleware/`
- Scripts: `/root/sidekick-forge/scripts/`

## Minimal commands
```bash
# Start/stop
docker-compose up -d
docker-compose logs -f fastapi
docker-compose down

# Health
curl http://localhost:8000/health

# Admin
curl http://localhost:8000/admin
```

## LiveKit requirements (v1.0+)
- Use `AgentSession` and `JobContext` patterns.
- Explicit dispatch only. Workers register with `agent_name="sidekick-agent"`; rooms include the same `agent_name`; request filter accepts matching jobs.
- STT-driven turn detection in `AgentSession`; do not block on deprecated patterns.

## Implementation checklist (high-signal)
- Multi-tenant isolation respected at all layers.
- Agent workers load config/keys dynamically per job.
- SSE and Nginx configured for streaming where applicable.
- Admin preview and embed share the same codepath (iframe-based preview with Supabase Auth handoff).

## Security Requirements (Production)

### Secrets Management
- **NEVER commit `.env` files** to version control. Use `.env.example` for templates.
- **NEVER use hardcoded default values** for sensitive tokens (e.g., `ADMIN_AUTH_TOKEN`).
- **Required environment variables** (will error if missing):
  - `ADMIN_AUTH_TOKEN` - must be a strong, unique token (32+ random chars)
  - `JWT_SECRET_KEY` - cryptographically secure secret for JWT signing
  - `SUPABASE_SERVICE_ROLE_KEY` - fetched dynamically, bootstrap only
- **For production**: Use a secrets manager (AWS Secrets Manager, HashiCorp Vault, GCP Secret Manager).
- **Rotate secrets regularly**: especially after any suspected compromise.

### Authentication & Session Security
- Admin session cookies use `httponly=True`, `secure=True`, `samesite=lax`.
- No development mode bypasses (`dev-token`) in production builds.
- API keys are never logged; only hashed values for comparison.

### Rate Limiting
- Enable with `RATE_LIMIT_ENABLED=true` and configure `RATE_LIMIT_PER_MINUTE`.
- Uses in-memory rate limiting (suitable for single-instance).
- For multi-instance: implement DB-backed rate limiting via Supabase.

### File Upload Security
- MIME type validation against whitelist (prevents extension spoofing).
- Path traversal protection on filenames.
- File size limits enforced.
- Store uploads outside webroot with randomized names.

### Security Headers
- `SecurityHeadersMiddleware` adds: CSP, X-Frame-Options, X-Content-Type-Options, HSTS, etc.
- CSP policy configured for admin UI requirements (adjust if embedding changes).

### Template Security (XSS Prevention)
- Always use `{{ variable|tojson }}` for variables in JavaScript contexts.
- HTML attribute contexts are auto-escaped by Jinja2.
- Never use `|safe` filter on user-controlled data.

## When in doubt
- Prefer explicit, stateless, testable flows.
- Fail fast with clear errors rather than adding fallbacks.
- Keep repo keys out of code; source from Supabase at runtime.
