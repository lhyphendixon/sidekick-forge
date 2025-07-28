# Frontend Audio Debugging Instructions

## The Issue
Based on the symptoms:
1. **Audio level bar does not respond when you speak** - This indicates the frontend isn't capturing microphone audio
2. **No Deepgram logs** - Confirms audio isn't reaching the backend STT service  
3. **Agent shows 0 participants** - The user isn't properly connected to the room

## Quick Diagnosis Steps

### 1. Browser Console Check
Open browser console (F12) and look for:
- Microphone permission errors
- Failed getUserMedia calls
- WebRTC connection errors

### 2. Common Frontend Audio Issues

#### A. Microphone Permission Denied
```javascript
// The frontend should request microphone like this:
navigator.mediaDevices.getUserMedia({ audio: true })
  .then(stream => {
    // Success - should show audio levels
  })
  .catch(error => {
    console.error('Microphone access denied:', error);
  });
```

#### B. Audio Track Not Published
```javascript
// After getting the stream, it must be published:
const audioTrack = stream.getAudioTracks()[0];
await room.localParticipant.publishTrack(audioTrack);
```

#### C. Wrong Audio Device Selected
```javascript
// List available devices:
navigator.mediaDevices.enumerateDevices()
  .then(devices => {
    const audioInputs = devices.filter(d => d.kind === 'audioinput');
    console.log('Available microphones:', audioInputs);
  });
```

## Quick Fix Checklist

1. **Check Browser Permissions**
   - Click the lock icon in the address bar
   - Ensure microphone is set to "Allow"
   - Reload the page

2. **Test Microphone Outside App**
   - Go to https://webrtc.github.io/samples/src/content/getusermedia/audio/
   - If this doesn't work, it's a browser/system issue

3. **Try Different Browser**
   - Chrome/Edge usually work best
   - Firefox sometimes has WebRTC issues

4. **Check HTTPS**
   - Microphone access requires HTTPS (except localhost)
   - Ensure you're accessing via https://

## Debug HTML Tool

I've created a debug tool at:
`/root/sidekick-forge/scripts/debug_frontend_audio.html`

To use it:
1. Serve it via HTTP: `python3 -m http.server 8080`
2. Open http://localhost:8080/scripts/debug_frontend_audio.html
3. Follow the tests in order

## Root Cause Analysis

The issue is that the frontend is failing at the very first step - capturing microphone audio. This could be due to:

1. **Browser Permissions** - Most likely cause
2. **HTTPS Requirement** - If not on HTTPS
3. **Audio Device Issues** - Wrong device selected
4. **Browser Compatibility** - Older browsers
5. **JavaScript Errors** - Check console for errors before audio init

## Next Steps

1. Check browser console for errors
2. Verify microphone permissions are granted
3. Test with the debug HTML tool
4. Ensure you're on HTTPS (or localhost)
5. Try a different browser

The agent backend is working correctly - it's waiting for audio that never arrives because the frontend isn't capturing/publishing it.