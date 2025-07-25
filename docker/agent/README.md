# Autonomite Agent Implementation

This is the unified agent implementation for the Autonomite platform, migrated from the legacy codebase to follow modern LiveKit patterns.

## Architecture

### Key Components

1. **entrypoint.py** - Main worker registration and job handling
   - Registers with LiveKit as "autonomite-agent"
   - Handles incoming job requests
   - Manages agent lifecycle

2. **health_server.py** - Health check endpoint
   - Runs on port 8080
   - Provides /health and /ready endpoints
   - Used by Docker and Kubernetes for container health monitoring

3. **requirements-agent.txt** - Python dependencies
   - LiveKit SDK and plugins
   - AI/ML libraries (OpenAI, Groq, etc.)
   - Voice processing (Deepgram, ElevenLabs, Cartesia)

## Configuration

The agent is configured via environment variables:

- `LIVEKIT_URL` - LiveKit server URL (required)
- `LIVEKIT_API_KEY` - LiveKit API key (required)
- `LIVEKIT_API_SECRET` - LiveKit API secret (required)
- `OPENAI_API_KEY` - OpenAI API key (optional)
- `GROQ_API_KEY` - Groq API key (optional)
- `DEEPGRAM_API_KEY` - Deepgram API key (optional)
- `ELEVENLABS_API_KEY` - ElevenLabs API key (optional)
- `CARTESIA_API_KEY` - Cartesia API key (optional)
- `LOG_LEVEL` - Logging level (default: INFO)

## Building

```bash
./build.sh
```

Or manually:
```bash
docker build -f docker/agent/Dockerfile -t autonomite/agent:latest .
```

## Running

### With Docker Compose
```bash
docker-compose up agent
```

### Standalone
```bash
docker run --rm -it \
  -e LIVEKIT_URL=wss://your-livekit-server.com \
  -e LIVEKIT_API_KEY=your-api-key \
  -e LIVEKIT_API_SECRET=your-api-secret \
  autonomite/agent:latest
```

## Agent Behavior

1. **Registration**: On startup, the agent registers with LiveKit as "autonomite-agent"
2. **Job Filtering**: Accepts jobs dispatched to "autonomite-agent" or with no specific agent name
3. **Session Handling**: Uses AgentSession for voice interactions with:
   - VAD (Voice Activity Detection) via Silero
   - STT (Speech-to-Text) via Deepgram or Cartesia
   - LLM via OpenAI or Groq
   - TTS (Text-to-Speech) via Cartesia or ElevenLabs
4. **Interruptions**: Supports user interruptions with configurable thresholds
5. **Event Logging**: Logs all user speech and agent responses for monitoring

## Migration Notes

This implementation was migrated from the legacy `/opt/autonomite-saas/agent-runtime/session_agent_rag.py` with the following changes:

1. **Proper Worker Registration**: Uses LiveKit's official worker pattern
2. **Simplified Architecture**: Removed complex multi-file dependencies
3. **Standard Entrypoint**: Single entrypoint.py instead of shell script wrapper
4. **Health Checks**: Integrated health server for container orchestration
5. **Clean Dependencies**: Updated to latest LiveKit SDK versions

## Future Enhancements

- RAG (Retrieval Augmented Generation) integration
- Multi-tenant support with per-client configurations
- Metrics and monitoring integration
- Advanced conversation memory