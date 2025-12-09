## Sidekick Forge v2.2.1

Voice, formatting, and citation reliability improvements.

- Voice: fixed duplicate proactive greetings with per-room locking and stronger echo filtering so the agent's own TTS is not transcribed as user speech; longer VAD endpointing and shorter speech minimums reduce mid-utterance truncation.
- Text streaming: embed text chat now streams deltas from LiveKit metadata instead of waiting for full replies; batching metadata updates removes per-token overhead.
- Citations: increased context budget and per-chunk truncation to keep multiple sources in RAG results; citations now flow through transcripts and embeds.
- UI/formatting: deferred admin video load and safe image preview src handling to remove infinite loaders; embed UI uses compact dark styling with transcript markdown formatting and citations panel.
- Ops: multi-tenant trigger wiring includes rerank/embedding settings and tools payloads; agent version/health endpoints report 2.2.1.

Upgrade notes:
- Rebuild and redeploy both FastAPI and agent-worker images (voice fixes live in the worker).
- Ensure env vars for LiveKit/Supabase are present; no new required keys were added.

