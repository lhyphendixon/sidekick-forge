# CLAUDE.md (Compact)

Guidance for AI coding assistants working in this repo. Keep it tight, correct, and aligned with the platform architecture.

## What this repo is
- Sidekick Forge staging implementation: multi-tenant SaaS for AI agents (FastAPI + Docker).
- All development happens in `/root/sidekick-forge/`. The `/opt/autonomite-saas/` path is deprecated.

## Architecture essentials
- Multi-tenant: platform Supabase stores client configs/credentials; each client has its own Supabase project (full data isolation).
- Stateless worker pool: generic `agent-worker` containers load client/agent config dynamically at job time.
- RAG storage: Supabase `pgvector` only. Similarity via RPC in client DB. No local vector stores/models.
- Credentials: always fetched dynamically from Supabase; env vars only for initial bootstrap to the platform Supabase.
- LiveKit: use v1.0+ patterns with `AgentSession` and explicit agent dispatch.

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

## When in doubt
- Prefer explicit, stateless, testable flows.
- Fail fast with clear errors rather than adding fallbacks.
- Keep repo keys out of code; source from Supabase at runtime.
