## Sidekick Forge v2.0.0

This public release showcases the Sidekick Forge multi-tenant platform with the following working capabilities:

- Text chat: Stable HTMX-driven text chat with correct RAG injection and no fallback keyword search
- Voice chat: LiveKit-backed low-latency, bi-directional voice chat and preview flows
- Context: Deterministic context assembly with strict provider key mapping and verified LLM plugin initialization
- Knowledgebase uploads: Document ingestion and embeddings with verified storage and retrieval

Highlights
- Multi-tenant isolation across clients and agent configurations
- Unified storage paths and verified Supabase client usage
- Admin dashboard with Sidekicks management, preview, and knowledgebase tooling
- Mission-critical tests confirming text, voice, context, and knowledgebase flows

Breaking/Behavioral Notes
- No-fallback RAG policy enforced; retrieval must succeed or return an error
- STT-driven turn detection for voice sessions with proper finalization semantics

Upgrade Guidance
- Review `.env.example` and provider credentials; do not commit secrets
- Rebuild containers and workers to pick up new configuration surfaces

Acknowledgements: Platform rebranded from Autonomite Agent Platform to Sidekick Forge.

