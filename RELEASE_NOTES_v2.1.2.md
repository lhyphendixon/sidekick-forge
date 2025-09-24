# v2.1.2 – 2025-09-24

## Highlights
- Ship the new Abilities platform for managing LiveKit tools across global and client scopes.
- Add the Perplexity Search (n8n) webhook ability and retire the legacy Perplexity MCP implementation.
- Harden voice sessions: pre-prime LiveKit audio, enforce no-fallback policies, and eliminate duplicate greeting transcripts.

## Changes
- **Abilities platform**
  - Rewrite `/api/v1/tools` endpoints and Supabase service layers to support global + tenant abilities, hidden instructions, and agent assignments.
  - Refresh admin UI (`/admin/tools`, agent detail) for ability CRUD, assignment, and better visibility.
  - Add `app/utils/tool_prompts.py` to append hidden tool instructions into agent system prompts automatically during voice dispatch.
- **Automation toolchain**
  - Seed `20250924_add_perplexity_search_n8n.sql` migration for the new global webhook ability and remove the legacy Perplexity MCP ability (`20250924_remove_perplexity_ask_mcp.sql`).
  - Add `perplexity_mcp_manager` helper and LiveKit `tool_registry` wiring so workers can discover abilities at runtime.
  - Provide new troubleshooting and operator docs (`docs/perplexity-mcp.md`, `troubleshooting_notes.md`).
- **Voice pipeline**
  - Prime `RoomIO` before starting agent sessions, wrap LiveKit audio sinks with diagnostics, and enforce API-key / provider validation prior to dispatch.
  - Remove proactive greeting double-write to stop duplicate transcript rows.
  - Update embed template to use new LiveKit helpers, remove the redundant mute button, and improve audio attachment & SSE handling.
- **Infrastructure**
  - Mount `tool_registry.py` into the agent container, expose the Docker socket for dynamic MCP tooling, and add new static assets (crypto price icon, Perplexity icon).

## Impact
- Database: run `migrations/20250924_add_perplexity_search_n8n.sql` and `migrations/20250924_remove_perplexity_ask_mcp.sql` on the platform database.
- Services: restart the worker container after deploying so the new tool registry and audio instrumentation load.
- No new environment variables; ensure existing Perplexity API keys remain configured on clients using the search ability.

## Verification
1. Apply migrations and restart API + worker services.
2. Assign "Crypto Price Check" and "Perplexity Search (n8n)" abilities to an agent.
3. Start a voice session and confirm:
   - Voice playback begins within 1s and console shows `RoomIO primed` logs.
   - Agent transcript logs contain one greeting entry.
   - When requesting real-time info, the agent hits the n8n webhook (LiveKit tool logs) and relays the response.

## Links
- Applies to staging branch `staging/v2.1.2`
- GitHub compare: [v2.1.1…v2.1.2](https://github.com/lhyphendixon/sidekick-forge/compare/v2.1.1...v2.1.2)
