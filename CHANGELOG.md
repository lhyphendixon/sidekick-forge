# Changelog

All notable changes to the Autonomite SaaS Backend platform will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2025-07-24

### Overview
This release represents significant progress in the thin-client transformation, infrastructure improvements, and bug fixes. The platform has evolved from a heavy WordPress plugin architecture to a robust SaaS backend that serves thin clients.

### Added
- **Room Management API** (`/api/v1/rooms/`) - New endpoints for LiveKit room lifecycle management
- **Maintenance Mode System** - Graceful shutdown and maintenance capabilities
- **Container Pool Management** - Pre-warmed container pools for faster agent startup
- **Background Task System** - Improved async task handling and monitoring
- **Circuit Breaker Pattern** - Resilient error handling for external service failures
- **Room Keepalive Service** - Prevents premature room termination
- **Error Reporting Service** - Centralized error tracking and reporting
- **API Key Validation Service** - Comprehensive API key verification
- **Agent Fallback System** - Graceful degradation when primary agents fail
- **LiveKit Client Manager** - Centralized LiveKit client instance management
- **Room Monitoring Service** - Real-time room status tracking
- **Vector Search Service** - RAG implementation foundation

### Changed
- **Thin-Client Architecture** - Platform now serves as the central processing hub
  - WordPress plugin transformed to lightweight UI layer
  - All business logic moved to FastAPI backend
  - Direct LiveKit/Supabase connections removed from WordPress
- **Container Architecture** - Enhanced multi-tenant container isolation
  - Session-based container naming with unique IDs
  - Improved resource limits by tier (Basic/Pro/Enterprise)
  - Better health monitoring and automatic restart policies
- **LiveKit Integration** - Significant improvements
  - Fixed JWT token generation using `with_grants()` method
  - Added comprehensive audio track subscription handlers
  - Improved room name synchronization
  - Enhanced connection error handling
- **Admin Interface** - Multiple enhancements
  - Increased HTMX timeouts from 5 to 30 seconds for voice mode
  - Fixed audio playback in voice preview
  - Improved loading states and error messaging
  - Better session isolation for concurrent previews

### Fixed
- **Multi-Session Container Isolation** - Containers no longer shared across preview sessions
- **Audio Playback** - Agent voice responses now play correctly in browser
- **Room Synchronization** - Agents reliably join correct rooms
- **Container Creation Timeouts** - Extended timeouts prevent premature failures
- **WordPress API Key Sync** - All provider API keys now sync properly to Supabase
- **Provider Initialization** - Removed fallback logic per no-workaround policy

### Infrastructure
- **Docker Improvements**
  - Updated production compose file for better container management
  - Enhanced agent runtime with new dependencies
  - Improved build process for agent containers
- **Nginx Configuration**
  - Rate limiting optimizations
  - CORS handling improvements
  - SSL/TLS enhancements
- **Redis Integration**
  - Session storage optimization
  - Caching improvements for better performance

### Security
- **Authentication Enhancements**
  - Better JWT token validation
  - Improved API key security
  - Enhanced multi-tenant isolation

### Documentation
- **RAG Implementation Guides** - Comprehensive documentation for RAG system
- **Client Credential Isolation** - Detailed security documentation
- **Phase Implementation Summaries** - Development progress tracking

### Known Issues
- **Voice Setup Error** - Persistent "Invalid voice settings" error in admin preview
  - Occurs despite valid Cartesia/Deepgram configuration
  - Agent containers still spawn successfully
  - Voice interactions work when triggered via API
  - UI validation issue, not affecting core functionality
- **Supabase Authentication in Workers** - Workers have 401 errors loading API keys
  - Currently using dummy API keys as workaround
  - Needs proper authentication context
- **Pydantic Serialization Warning** - Non-critical warning in worker API responses

### Testing
- All 19 mission critical tests passing
- New test suites added for:
  - Phase 1-4 implementations
  - Room management
  - Container isolation
  - Audio pipeline
  - State isolation

### Dependencies
- LiveKit Python SDK 1.0+ (using AgentSession, not deprecated VoicePipelineAgent)
- Updated agent requirements for better compatibility
- Enhanced Python dependencies for RAG support

## [1.0.0] - 2025-07-21

### Initial Public Release
- Multi-tenant SaaS architecture
- Container-based agent isolation
- LiveKit Cloud integration
- WordPress thin-client support
- Admin dashboard with HTMX
- Comprehensive API endpoints
- Redis/Supabase hybrid storage
- SSL/HTTPS with Let's Encrypt
- Automated backup system
- Health monitoring endpoints

---

For detailed commit history, see: https://github.com/autonomite-ai/autonomite-agent-platform/commits/main