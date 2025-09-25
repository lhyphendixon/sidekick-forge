# Changelog

All notable changes to the Autonomite Agent Platform will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.1.2] - 2025-09-25

## [2.1.3] - 2025-09-25

### Added
- Agent image upload feature in the admin (attach/update agent image from the editor) and expose image URL in agent metadata.

### Fixed
- Agent interruption handling in voice sessions to correctly cut off TTS and resume agent speech without overlaps.

### Added
- Crypto Price Check ability exposed in the Abilities platform and wired into LiveKit tool dispatch.
- Perplexity Search integration via hosted webhook (replacing legacy MCP path), with client-level API key support in admin.

### Fixed
- Intermittent audio start issues in voice sessions by priming playback and avoiding awaited start on user gesture.

### Changed
- Admin tools UI updates for MCP-style abilities, including Perplexity configuration and client overrides.
- Updated platform versioning and health endpoints to report 2.1.2.

### Added
- Initial repository setup
- FastAPI backend with multi-tenant support
- Admin dashboard with HTMX and Tailwind CSS
- LiveKit integration for voice conversations
- Supabase integration for data persistence
- Redis caching layer
- WordPress plugin integration endpoints
- Agent configuration management
- Client management system
- Docker containerization
- Nginx reverse proxy configuration

### Changed
- Migrated from heavy WordPress plugin to thin-client architecture
- Centralized business logic in FastAPI backend
- Improved agent configuration UI with dropdowns instead of JSON

### Security
- Implemented JWT authentication
- Added API key authentication for WordPress sites
- Configured CORS properly
- Added rate limiting via Nginx

## [2.1.1] - 2025-09-15

### Fixed
- LiveKit voice embed hang by making audio start non-blocking under user gesture.
- Voice button indefinite loading fixed by avoiding awaited audio start calls.

### Changed
- SSE transcript reliability: single EventSource connection with history prefetch and dedupe.
- Added diagnostics for LiveKit connection, audio playback status, and subscriptions.
- Enforced no-fallback: `conversation_id` must be present in trigger-agent voice response.

### Removed
- Unmute overlay; autoplay now primed via dedicated hidden audio element.

## [1.0.0] - TBD

### Added
- First stable release
- Complete API documentation
- Comprehensive test suite
- Production deployment guides

### Changed
- TBD

### Deprecated
- TBD

### Removed
- TBD

### Fixed
- TBD

### Security
- TBD