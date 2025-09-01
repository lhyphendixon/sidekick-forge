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

## Non-negotiable policies
- No workarounds: fix root causes; no masking, no artificial delays, no “temporary” hacks.
- Dynamic API keys: never hardcode or rely on env as primary source; fetch from Supabase.
- No-fallback providers: if a configured STT/TTS/LLM fails, error out (no silent fallback).
- Forbidden tech: `ChromaDB`, `sentence-transformers` (violates stateless/service-based design).
- RAG no-fallback: if retrieval fails, surface an error rather than keyword search.

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
