# Autonomite Agent Platform

A FastAPI-based SaaS platform for hosting AI agents with WordPress integration. This platform provides a centralized backend for managing AI agents, handling LiveKit/voice integrations, and serving as the backbone for the thin-client WordPress plugin architecture.

## Architecture Overview

The platform follows a thin-client architecture where:
- **WordPress Plugin**: Lightweight UI layer only
- **FastAPI Backend**: Centralizes all business logic including:
  - Database operations with Supabase
  - LiveKit session management
  - AI agent container orchestration
  - Document processing and RAG
  - User authentication and multi-tenant support

## Features

- 🤖 **Multi-tenant AI Agent Management**: Each client can have multiple AI agents with different configurations
- 🎙️ **Voice Integration**: LiveKit integration for real-time voice conversations
- 💬 **Text Chat Support**: WebSocket-based text chat with AI agents
- 📄 **Document Processing**: RAG (Retrieval Augmented Generation) support
- 🔐 **Authentication**: Supabase Auth with API key support for WordPress sites
- 🐳 **Container Management**: Dynamic Docker container creation per agent instance
- 📊 **Admin Dashboard**: HTMX-based admin interface with Tailwind CSS

## Tech Stack

- **Backend**: FastAPI (Python 3.12+)
- **Database**: Supabase (PostgreSQL)
- **Cache**: Redis
- **Real-time**: LiveKit for voice/video
- **Frontend**: HTMX + Tailwind CSS
- **Deployment**: Docker + Nginx

## Project Structure

```
autonomite-agent-platform/
├── app/                      # FastAPI application
│   ├── api/v1/              # API endpoints
│   ├── admin/               # Admin dashboard
│   ├── services/            # Business logic
│   ├── models/              # Pydantic models
│   ├── middleware/          # Auth, CORS, logging
│   └── templates/           # HTMX templates
├── docker/                   # Docker configurations
│   ├── agent/               # Agent container template
│   └── docker-compose.*.yml # Compose configs
├── scripts/                  # Utility scripts
├── migrations/              # Database migrations
└── tests/                   # Test suite
```

## Quick Start

### Prerequisites

- Python 3.12+
- Docker and Docker Compose
- Redis
- Supabase account

### Development Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/autonomite-agent-platform.git
cd autonomite-agent-platform
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. Start the development server:
```bash
cd docker
docker-compose up -d
```

6. Access the application:
- API: http://localhost:8000
- Admin Dashboard: http://localhost:8000/admin
- API Documentation: http://localhost:8000/docs

## Configuration

### Environment Variables

Key environment variables:

```env
# Supabase Configuration
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-key

# LiveKit Configuration
LIVEKIT_URL=wss://your-livekit-server.com
LIVEKIT_API_KEY=your-api-key
LIVEKIT_API_SECRET=your-api-secret

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379

# Application Settings
APP_ENV=development
DEBUG=true
```

### Client Configuration

Each client requires:
- Unique client ID
- Supabase project credentials
- LiveKit credentials
- API keys for various AI providers

## API Endpoints

### Authentication
- `POST /api/v1/auth/login` - Login with credentials
- `POST /api/v1/auth/wordpress` - WordPress site authentication

### Agents
- `GET /api/v1/agents` - List all agents
- `POST /api/v1/agents` - Create new agent
- `GET /api/v1/agents/{agent_id}` - Get agent details
- `PUT /api/v1/agents/{agent_id}` - Update agent
- `DELETE /api/v1/agents/{agent_id}` - Delete agent

### Trigger Agent
- `POST /trigger-agent` - Trigger agent for voice or text chat

### Clients
- `GET /api/v1/clients` - List all clients
- `POST /api/v1/clients` - Create new client
- `PUT /api/v1/clients/{client_id}` - Update client

## Admin Dashboard

The admin dashboard provides:
- Client management
- Agent configuration
- Real-time monitoring
- API key management
- Settings synchronization

Access at: `/admin` (requires authentication)

## Deployment

### Production Deployment

1. Build the Docker images:
```bash
docker-compose -f docker/docker-compose.production.yml build
```

2. Deploy with Docker Compose:
```bash
docker-compose -f docker/docker-compose.production.yml up -d
```

3. Set up Nginx reverse proxy (see `nginx/autonomite-saas.conf`)

4. Configure SSL with Let's Encrypt

## Development

### Running Tests

```bash
pytest
pytest --cov=app  # With coverage
```

### Code Style

The project uses:
- Black for code formatting
- isort for import sorting
- pylint for linting

```bash
black app/
isort app/
pylint app/
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

## License

This project is proprietary software. All rights reserved.

## Support

For support, email support@autonomite.ai or visit our documentation.