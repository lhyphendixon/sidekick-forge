## Sidekick Forge v2.7.0 (staging)

Key updates (Jan 17, 2026):
- LiveAvatar (HeyGen) integration for video chat: LiveKit upgraded to 1.3.11 with liveavatar plugin support, new agent avatar provider option and avatar ID settings, plus admin client-level API key field for HeyGen LiveAvatar.
- Video session handling improvements: avatar providers now control audio routing for video mode, with RoomIO output toggles and explicit LiveAvatar/Beyond Presence/Bithuman initialization flows.
- Transcript storage hardening: normalize non-UUID user IDs, ensure conversation rows exist before transcript inserts, and gracefully handle shared-vs-dedicated tenant schemas when client_id columns are absent.
- Embed and trigger refinements: embed now resolves platform-to-client user mappings, pulls agent chat mode flags for UI, and multi-tenant trigger flow better separates text/voice/video paths with usage checks.
