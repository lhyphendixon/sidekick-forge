# Release Notes - v1.0.0

## Public Release: Multi-Session Container Isolation & Audio Playback Fix

### Release Date: July 21, 2025

This release represents a significant milestone in the Autonomite Agent Platform, fixing critical issues with voice agent functionality and enabling proper multi-session support for the admin preview interface.

## 🎯 Major Fixes

### 1. Multi-Session Container Isolation
- **Problem**: Containers were being reused across different preview rooms, causing agents to be in the wrong rooms
- **Solution**: Implemented session-based container naming that includes unique session IDs
- **Impact**: Multiple users can now preview agents simultaneously without conflicts

### 2. Audio Playback Support
- **Problem**: Agent audio responses were not playing in the browser despite successful generation
- **Solution**: Added proper LiveKit track subscription handlers for agent audio
- **Impact**: Users can now hear agent responses in voice mode

### 3. Room Name Synchronization
- **Problem**: Mismatch between rooms where users joined and where agents were deployed
- **Solution**: Pass room names directly to agent configuration and validate container assignments
- **Impact**: Agents now reliably join the correct rooms

### 4. Timeout Handling
- **Problem**: HTMX timeouts prevented container creation from completing
- **Solution**: Increased timeouts from 5 to 30 seconds for voice mode initialization
- **Impact**: Container creation now completes successfully even under load

## 🔧 Technical Details

### Container Management Updates
- Container names now follow format: `agent_{site_id}_{agent_slug}_{session_id}`
- Session IDs extracted from room names for unique identification
- Proper cleanup of old containers before creating new ones

### LiveKit Integration Improvements
- Fixed JWT token generation using `with_grants()` method
- Added comprehensive event handlers for audio track management
- Improved connection error handling and status updates

### No-Fallback Policy
- Removed all fallback logic for provider initialization
- Ensures failures are visible rather than silently masked
- Maintains system reliability by addressing root causes

## 📊 Test Results
- All 19 mission critical tests passing
- Multi-session container isolation verified
- STT/TTS provider configuration properly respected
- LiveKit credentials correctly sourced from client configurations

## 🚀 Getting Started

1. Clone the repository:
   ```bash
   git clone https://github.com/autonomite-ai/autonomite-agent-platform.git
   cd autonomite-agent-platform
   ```

2. Set up environment:
   ```bash
   cp .env.example .env
   # Configure your environment variables
   ```

3. Run with Docker:
   ```bash
   docker-compose -f docker/docker-compose.production.yml up -d
   ```

4. Access the admin interface:
   ```
   https://your-domain/admin
   ```

## 🧪 Testing

Run the mission critical test suite:
```bash
python3 scripts/test_mission_critical.py
```

## 📝 Commit Details

- Commit Hash: `62b03a8`
- Author: Claude <noreply@anthropic.com>
- Files Changed: 15+ core files
- Lines Added: 500+
- Lines Removed: 200+

## 🙏 Acknowledgments

This release was made possible through extensive debugging and testing sessions. Special thanks to the development team for their patience in tracking down the root causes of these issues.

---

🤖 Generated with [Claude Code](https://claude.ai/code)