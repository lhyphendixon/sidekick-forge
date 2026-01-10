## Sidekick Forge v2.5.0 (staging)

Major updates (Jan 6–10, 2025):
- HNSW migration for pgvector similarity search (document_chunks and conversation_transcripts) with per-client HNSW indexes to boost recall and latency; all clients verified with five HNSW indexes each.
- Usage tracking overhaul: new livekit_events table; client-level aggregation via get_client_aggregated_usage() and check_client_quota(); embed text tracking fixed; embedding usage tracked in document processing; voice usage recorded on room completion; quotas enforced per-client with agent-level visibility via agent_usage foundation.
- Tier quota system with tier_quotas (Adventurer, Champion, Paragon/BYOK) and managed API key handling for paid tiers.
- Supertab paywall for voice sessions: embed config fetch, /api/embed/supertab/create-user endpoint, frontend SDK integration in sidekick.html, and per-agent Supertab settings to gate voice chat.
- Bithuman AI avatar support: agent voice settings for avatar provider/image/model, Beyond Presence avatar_id, video chat toggle, and admin UI section for avatar/video configuration.

Upgrade/deploy notes:
- Apply migrations (20250107_add_agent_usage_tracking.sql, 20250110_fix_usage_tracking.sql, HNSW index migrations); ensure pgvector HNSW support and reindex per client.
- Deploy updated backend/embed and worker images; configure LiveKit/Supabase credentials; set Supertab/Bithuman keys and assets as needed.
