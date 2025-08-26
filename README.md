**Sidekick Forge** is a multi-tenant SaaS platform for deploying and managing AI agents. It provides AI agent infrastructure as a service, allowing clients to deploy and manage their own AI agents through a centralized platform.

## Features

- 🏢 **Multi-Tenant Architecture**: Complete isolation of client data and configurations
- 🤖 **Voice & Text AI Agents**: Support for both voice chat (via LiveKit) and text-based interactions
- 🎯 **Multiple LLM Providers**: Support for OpenAI, Groq, Anthropic, DeepInfra, and more
- 🎙️ **Voice Providers**: Integration with ElevenLabs, Deepgram, Cartesia, and Speechify
- 📊 **Admin Dashboard**: Web-based interface for managing clients and agents
- 🔌 **API-First Design**: RESTful API for all operations
- 🧩 **Stateless Worker Pool**: Scalable agent workers; explicit dispatch via LiveKit
- ⚡ **Dynamic Configuration**: Real-time configuration updates without restarts

## Architecture

Sidekick Forge is a true multi-tenant SaaS with a stateless worker pool and explicit agent dispatch:

- **Single Source of Truth**: All running code lives in `sidekick-forge/`. Legacy paths and container-per-client patterns are deprecated.
- **Platform Database (Supabase)**: Centralized platform DB stores clients, agents, and credentials. Each client typically has its own Supabase project for data isolation and `pgvector` search.
- **Stateless Worker Pool**: A pooled `agent-worker` service (scale-out via Docker) runs generic workers. Workers fetch client/agent config on job start. No per-client containers.
- **Explicit LiveKit Dispatch**: Rooms are created without automatic dispatch. The backend explicitly dispatches a job to workers using `agent_name` and rich metadata. This prevents “double agents” and ensures correct context.
- **RAG via Supabase**: Context is built in `docker/agent/context.py` using remote embeddings and Supabase RPCs (`match_documents`, `match_conversation_transcripts_secure`). Local vector stores are not used.
- **No Fallbacks Policy**: Critical components (credentials, embedding, RPCs) fail fast on misconfiguration instead of silently degrading.

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Python 3.11+
- Supabase account
- LiveKit Cloud account (for voice features)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/sidekick-forge.git
cd sidekick-forge
```

2. Copy the environment template:
```bash
cp .env.example .env
```

3. Configure your environment variables in `.env`:
```env
# Sidekick Forge Platform Database
SUPABASE_URL=your_platform_supabase_url
SUPABASE_KEY=your_platform_supabase_key

# LiveKit Configuration
LIVEKIT_URL=your_livekit_server_url
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# Optional logging / mode
APP_NAME=sidekick-forge
PLATFORM_NAME=Sidekick Forge
DEVELOPMENT_MODE=false
```

4. Start the services:
```bash
docker-compose up -d
```

5. Access the admin dashboard at `http://localhost:8000/admin`

## API Documentation

The API documentation is available at `http://localhost:8000/docs` when running locally.

### Key Endpoints

- `/api/v1/trigger-agent` - Trigger an AI agent (voice or text mode) with explicit dispatch
- `/api/v1/sessions/end` - Proactively end a session and delete a LiveKit room
- `/api/v2/clients` - Manage clients (multi-tenant endpoints)
- `/api/v2/agents` - Manage agents for clients
- `/admin` - Web-based admin interface

## Project Structure

```
sidekick-forge/
├── app/                      # FastAPI application
│   ├── api/v1/              # Legacy API endpoints
│   ├── api/v2/              # Multi-tenant API endpoints
│   ├── admin/               # Admin dashboard
│   ├── services/            # Business logic
│   ├── models/              # Pydantic models
│   ├── middleware/          # Auth, CORS, logging
│   └── templates/           # HTMX templates
├── docker/                   # Docker configurations
│   ├── agent/               # Agent worker runtime (stateless)
│   └── docker-compose.yml   # Service orchestration
├── scripts/                  # Utility and test scripts
└── nginx/                   # Nginx configurations
```

## Configuration

### Platform Configuration

The platform configuration is stored in the `.env` file:

```env
# Sidekick Forge Platform Database
SUPABASE_URL=your_platform_supabase_url
SUPABASE_KEY=your_platform_supabase_key

# LiveKit Configuration (Backend Infrastructure)
LIVEKIT_URL=your_livekit_server_url
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# Redis Configuration
REDIS_HOST=redis
REDIS_PORT=6379
```

### Client Configuration

Each client's configuration is stored in the platform database and includes:
- Supabase credentials for the client's database
- LiveKit credentials (if using client-specific LiveKit)
- API keys for various AI providers
- Agent configurations

Agent worker API keys are dynamically loaded per job from platform/client configuration via metadata; do not hardcode provider keys into worker environment.

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run pipeline performance test
python scripts/test_pipeline_performance.py

# Run with coverage
pytest --cov=app
```

### Code Style

The project uses standard Python formatting:
```bash
black .
isort .
```

## Deployment

### Docker Deployment

The platform is designed to run in Docker containers:

```bash
# Build and start all services
docker-compose up -d --build

# View logs
docker-compose logs -f

# Stop services
docker-compose down

# Scale agent workers
docker-compose up -d --scale agent-worker=3
```

### Production Considerations

- Use environment-specific `.env` files
- Enable SSL/TLS for all endpoints
- Configure proper CORS settings
- Set up monitoring and logging
- Use production-grade databases
- Configure rate limiting
- Enable authentication for admin endpoints

## Multi-Tenant Features

### Client Management
- Create and manage multiple clients
- Each client gets isolated data storage
- Dynamic credential management
- Client-specific API key storage

### Agent Management
- Agents belong to specific clients
- Support for multiple LLM providers per agent
- Voice configuration per agent
- Webhook support for custom integrations

### Resource Isolation
- Stateless worker pool for agent execution (no per-client containers)
- Resource limits by client tier
- Complete data isolation between clients
- Secure credential storage and access

## Voice Agent Flow (Explicit Dispatch)

- Backend creates a LiveKit room without `enable_agent_dispatch` (dashboard “Features” column will be blank).
- Backend generates a user token and returns it to the client.
- Backend explicitly dispatches a job to the worker pool with `agent_name=sidekick-agent` and metadata including `client_id`, `agent_slug`, `user_id`, `system_prompt`, and provider configuration.
- Worker accepts only jobs matching its `AGENT_NAME` and loads client configuration on start.

## RAG and Context

- `docker/agent/context.py` centralizes context creation.
- Embeddings are generated via remote providers (e.g., SiliconFlow/OpenAI) – no local models.
- Vector search is performed by Supabase RPCs:
  - `match_documents(p_query_embedding, p_agent_slug, p_match_threshold, p_match_count)`
  - `match_conversation_transcripts_secure(query_embeddings, agent_slug_param, user_id_param, match_count)`
- Context is formatted as clean markdown and injected into the system prompt per turn. No fallback to keyword search. Fail fast if RPCs or credentials are missing.

## Proactive Room Closure

- Use the endpoint to close rooms when a session ends:
  - `POST /api/v1/sessions/end` with `{ "room_name": "<name>" }`

## Notes and Cleanups

- Container-per-client and custom container pool managers are deprecated and removed.
- Legacy directories (e.g., `autonomite-agent-platform/`) are not used.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Built on [FastAPI](https://fastapi.tiangolo.com/)
- Real-time communication powered by [LiveKit](https://livekit.io/)
- Database infrastructure by [Supabase](https://supabase.com/)
- Originally developed from the Autonomite Agent Platform

## Support

For support, please open an issue in the GitHub repository or contact the maintainers.

---

**Note**: This platform was formerly known as the Autonomite Agent Platform and has been rebranded as Sidekick Forge to reflect its multi-tenant SaaS nature.
