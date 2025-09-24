# Voice Audio Issue Analysis

## Summary

During the latest Sidekick voice sessions, the agent continued to synthesize
Cartesia audio (confirmed by non-zero RMS values logged in the worker). The
browser, however, never rendered the remote audio track, resulting in silent
responses for the user.

## Findings

- Worker telemetry showed `🎧 capture_frame` entries with healthy RMS data,
  proving audio left the agent successfully.
- Browser console logs revealed immediate `TrackUnsubscribed` events and a lack
  of `TrackSubscribed` handling that attached the media stream to an audio
  element.
- Autoplay policies and missing resubscribe logic caused the remote audio track
  to be dropped or to never play through the hidden speaker element.

## Remediations Implemented

1. **Force Subscription on Remote Audio Tracks**
   - Added a `RoomEvent.RemoteTrackPublished` handler that calls
     `setSubscribed(true)` for audio publications and logs the result.
   - If a track unsubscribes unexpectedly, the handler reissues
     `setSubscribed(true)` (unless the user manually ended the call).

2. **Robust Track Attachment & Playback**
   - Reuse a single hidden `<audio>` element, clearing any stale attachments and
     wrapping the underlying `MediaStreamTrack` in a fresh `MediaStream` before
     assigning `srcObject`.
   - Retry `startAudio()`/`audio.play()` to surface autoplay blocks in the
     console and keep trying until playback succeeds.

3. **Cleanup on Unsubscribe**
   - Detach all audio nodes and null the shared element’s `srcObject` so
     reconnects always start from a clean state.

## Result

After the embed script updates and a FastAPI restart, a hard refresh of the
Sidekick preview allowed the browser to remain subscribed to the agent’s audio
track, and voice responses now play as expected.

