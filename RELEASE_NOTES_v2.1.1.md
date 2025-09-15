# v2.1.1 – 2025-09-15

## Highlights
- Fix LiveKit voice embed hang by making audio start non-blocking under user gesture
- Ensure SSE transcript stream uses a single EventSource with history prefetch and dedupe
- Add dedicated, pre-primed hidden audio element for autoplay reliability
- Expand LiveKit diagnostics (connection state, audio playback, subscription events)
- Enforce no-fallback policy: trigger-agent voice must return `conversation_id`

## Changes
- Voice embed (`app/templates/embed/sidekick.html`):
  - Avoid awaiting `startAudio()` and element `play()` to prevent UI blocking
  - Register early LiveKit diagnostics before `connect()` and add a 15s connect timeout
  - Attach remote audio to a pre-primed hidden element with volume ensured
  - Improve user status messaging during connect and when agent/remote audio appears
  - Remove “tap to enable audio” overlay
- Transcript streaming (embed):
  - Single SSE connection guard; prefetch recent history on connect
  - Deduplicate transcript items by id
- Backend (`/api/v1/trigger-agent`):
  - Validate voice trigger result includes `conversation_id` (no-fallback)

## Impact
- No database migrations required
- No configuration changes required
- Applies to staging branch `staging/v2.1.0`

## Verification
1. Open the embed preview for an agent
2. Click “Start Voice Chat”
3. Expect to see:
   - LiveKit connection state logs in the browser console
   - Remote audio plays without user overlay
   - Transcript bubbles appear via SSE (with history backfill)

## Links
- GitHub compare: [v2.1.0…v2.1.1](https://github.com/lhyphendixon/sidekick-forge/compare/v2.1.0...v2.1.1)

