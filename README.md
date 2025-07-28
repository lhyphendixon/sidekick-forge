# Sidekick Forge

**Sidekick Forge** is a multi-tenant SaaS platform for deploying and managing AI agents. It provides AI agent infrastructure as a service, allowing clients to deploy and manage their own AI agents through a centralized platform.

## Features

- 🏢 **Multi-Tenant Architecture**: Complete isolation of client data and configurations
- 🤖 **Voice & Text AI Agents**: Support for both voice chat (via LiveKit) and text-based interactions
- 🎯 **Multiple LLM Providers**: Support for OpenAI, Groq, Anthropic, DeepInfra, and more
- 🎙️ **Voice Providers**: Integration with ElevenLabs, Deepgram, Cartesia, and Speechify
- 📊 **Admin Dashboard**: Web-based interface for managing clients and agents
- 🔌 **API-First Design**: RESTful API for all operations
- 🐳 **Container-Based Agent Deployment**: Isolated agent workers with resource limits
- ⚡ **Dynamic Configuration**: Real-time configuration updates without restarts

## Architecture

Sidekick Forge operates as a true multi-tenant SaaS platform:

- **Platform Database**: Centralized Supabase instance managing client configurations
- **Client Isolation**: Each client has their own separate Supabase project
- **Agent Workers**: Containerized agents that load client-specific configurations
- **LiveKit Integration**: Real-time voice communication infrastructure

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
```

4. Start the services:
```bash
docker-compose up -d
```

5. Access the admin dashboard at `http://localhost:8000/admin`

## API Documentation

The API documentation is available at `http://localhost:8000/docs` when running locally.

### Key Endpoints

- `/api/v1/trigger-agent` - Trigger an AI agent (voice or text mode)
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
│   ├── agent/               # Agent container runtime
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

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run mission critical tests
python scripts/test_mission_critical.py

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
- Container-based agent execution
- Resource limits by client tier
- Complete data isolation between clients
- Secure credential storage and access

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