# Sidekick Forge Voice Playback Fix Notes

## Symptom
- Admin preview and embedded Sidekick sessions connected to LiveKit without errors but the agent remained silent.
- Console logs showed repeated `[voice] attachExistingRemoteAudio participants Array(1)` with empty `trackMap`/`audioMap` data and no audio element ever received a remote stream.

## Root Cause
- The LiveKit JS SDK v2 no longer exposes remote publications via the legacy `participant.tracks` / `participant.audioTracks` map shape that our embed script depended on.
- Agent participants were advertising audio on the new `trackPublications`/`audioTrackPublications` collections, so our crawler never saw a `pub.track`, never called `setSubscribed(true)`, and consequently never attached an `<audio>` element for playback.

## Fix Summary
1. **Publication Iteration Helpers** (`app/templates/embed/sidekick.html:163-266`)
   - Added `iterateTrackPublications`, `collectAudioPublications`, `participantHasAudioTrack`, `ensureAudioSubscribed`, and `resolvePublicationTrack` to normalize LiveKit v1/v2 participant structures.
2. **Robust Remote-Audio Attachment** (`app/templates/embed/sidekick.html:520-594`)
   - `attachExistingRemoteAudio` now enumerates all publication sources, forces subscription, and pipes resolved audio tracks through the existing `handleRemoteAudioTrack` flow.
3. **Event Hook Updates** (`app/templates/embed/sidekick.html:1081-1172` & `1240-1310`)
   - `TrackPublished`, `RemoteTrackPublished`, and `TrackUnsubscribed` handlers call the shared helpers so any audio publication—legacy or new—subscribes and re-attaches automatically.
4. **Cleanup & Status Logic** (`app/templates/embed/sidekick.html:1391-1429`)
   - Ensured hang-up and status checks also use the new helpers so local/remote state stays in sync across reconnects.

## Validation
- After rebuilding the embed script, refreshed the admin preview and confirmed:
  - Agent joins trigger audio subscriptions (console prints with `hasTrack: true`).
  - Hidden `<audio>` element receives `srcObject` and playback succeeds without manual unmute.

## Follow-Up Tips
- When upgrading LiveKit SDK versions, verify participant APIs for breaking changes.
- Keep an eye on console diagnostics: the helper logs make it clear when a publication lacks an attached track or subscription.
