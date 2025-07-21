# Autonomite SaaS Platform

A FastAPI-based platform that hosts AI agents for WordPress integration with multi-tenant support.

## 🚀 Recent Updates

### Voice Agent Preview Fixes (v1.0.0)

Major improvements to the voice agent preview functionality:

- **Multi-Session Container Isolation**: Each preview session now gets its own dedicated container
- **Audio Playback Support**: Fixed agent audio responses not playing in browser
- **Room Name Synchronization**: Resolved mismatches between user rooms and agent deployment
- **Enhanced Timeout Handling**: Increased timeouts to support container creation times

## Features

- Multi-tenant architecture with per-client agent containers
- LiveKit integration for real-time voice/video communication
- Supabase backend for data persistence
- HTMX-based admin interface
- Docker containerization for agent isolation
- Support for multiple STT/TTS providers (Deepgram, Cartesia, etc.)

## Architecture

The platform uses a thin-client approach where:
- WordPress plugins act as lightweight UI layers
- The FastAPI backend handles all business logic and processing
- Each client gets isolated agent containers with their own LiveKit credentials
- Agents can be configured with different LLM, STT, and TTS providers

## Installation

1. Clone the repository
2. Copy `.env.example` to `.env` and configure
3. Run with Docker: `docker-compose -f docker/docker-compose.production.yml up -d`

## API Documentation

Once running, visit:
- Admin Interface: `https://your-domain/admin`
- API Docs: `https://your-domain/docs`
- Health Check: `https://your-domain/health`

## Testing

Run the mission critical test suite:
```bash
python3 scripts/test_mission_critical.py
```

## License

Proprietary - Autonomite AI Platform

---

🤖 Generated with [Claude Code](https://claude.ai/code)