## Sidekick Forge v2.5.1 (staging)

Key updates (Jan 11, 2026):
- Restored Perplexity and other tool-based searches in text chat by executing detected native function calls, streaming tool results back through the LLM, and retaining built tool registries for follow-up replies.
- Added Supabase branching support for staging deployments: staging/production Docker Compose overrides plus an environment-aware deploy script to target the staging Supabase branch separately from production.
