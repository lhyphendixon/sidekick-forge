# Autonomite SaaS Backend Platform

A FastAPI-based multi-tenant SaaS platform that hosts AI agents for WordPress integration and beyond. This platform serves as the central processing hub for the Autonomite ecosystem, providing voice and text AI agent capabilities through a thin-client architecture.

## 🚀 Latest Release: v1.1.0 (July 24, 2025)

### Major Updates
- **Thin-Client Architecture Transformation**: Platform now serves as central processing hub
- **Enhanced Container Management**: Session-based isolation with pool pre-warming
- **Room Management API**: Complete LiveKit room lifecycle control
- **Improved Error Handling**: Circuit breaker pattern and comprehensive error reporting
- **Background Services**: Room monitoring, keepalive, and maintenance mode

See [CHANGELOG.md](CHANGELOG.md) for full release details.

## 🎯 Features

### Core Capabilities
- **Multi-Tenant Architecture**: Complete client isolation with dedicated agent containers
- **Thin-Client Support**: WordPress plugins and other frontends connect as lightweight UI layers
- **Voice & Text AI Agents**: Support for both voice interactions (via LiveKit) and text chat
- **Container-Based Isolation**: Each client gets dedicated containers with resource limits by tier
- **Real-Time Communication**: LiveKit integration for low-latency voice interactions

### Platform Services
- **Comprehensive API**: RESTful API with WebSocket support for real-time features
- **Admin Dashboard**: HTMX-based interface for client and agent management
- **Health Monitoring**: Detailed health checks and diagnostics
- **Background Tasks**: Async task processing with monitoring
- **Error Reporting**: Centralized error tracking and alerting

### Security & Reliability
- **JWT Authentication**: Supabase-based auth with API key support
- **SSL/HTTPS**: Let's Encrypt integration with auto-renewal
- **Rate Limiting**: Nginx-based rate limiting for API protection
- **Circuit Breakers**: Resilient external service integration
- **No-Fallback Policy**: Direct error reporting without masking issues

## 🏗️ Architecture

### Thin-Client Transformation

The platform has evolved from a heavy WordPress plugin architecture to a thin-client model:

**Before (Heavy Client)**:
- WordPress plugin directly connected to Supabase and LiveKit
- Plugin handled AI processing and database operations
- Difficult to scale and maintain

**After (Thin Client)**:
- WordPress plugin → FastAPI Backend → Services
- Plugin is now a lightweight UI layer only
- All processing happens in the backend platform
- Enables multi-platform support (web, mobile, etc.)

### Container Architecture

Each client gets isolated agent containers with tier-based resources:

| Tier | RAM | CPU | Features |
|------|-----|-----|----------|
| Basic | 512MB | 0.5 | Standard agents, basic RAG |
| Pro | 1GB | 1.0 | Advanced agents, full RAG |
| Enterprise | 2GB | 2.0 | Custom agents, priority support |

### System Components

1. **FastAPI Backend**: Core application server (4 workers in production)
2. **Redis**: Session storage and caching layer
3. **Supabase**: Database, authentication, and vector storage
4. **LiveKit**: Real-time voice/video communication
5. **Nginx**: Reverse proxy with SSL, rate limiting, and CORS
6. **Docker**: Container orchestration for apps and agents

## 📋 Prerequisites

- Docker and Docker Compose
- Python 3.12+
- Valid Supabase project with configured tables
- LiveKit Cloud account (or self-hosted LiveKit server)
- SSL certificate (Let's Encrypt recommended for production)
- Domain name with DNS configured

## 🛠️ Installation

### Quick Start

1. **Clone the repository**:
   ```bash
   git clone https://github.com/autonomite-ai/autonomite-agent-platform.git
   cd autonomite-agent-platform
   ```

2. **Set up environment**:
   ```bash
   cp env/.env.example env/.env
   # Edit env/.env with your configuration
   ```

3. **Run with Docker**:
   ```bash
   # Development
   cd docker
   docker-compose up -d

   # Production
   docker-compose -f docker-compose.production.yml up -d
   ```

4. **Verify installation**:
   ```bash
   # Check health
   curl http://localhost:8000/health

   # Run tests
   python3 scripts/test_mission_critical.py --quick
   ```

### Production Deployment

See [docs/deployment.md](docs/deployment.md) for detailed production deployment instructions.

## 📡 API Reference

### Core Endpoints

#### Agent Management
- `POST /api/v1/trigger-agent` - Trigger voice or text agent
- `GET /api/v1/agents` - List all agents
- `GET /api/v1/agents/{slug}` - Get agent details

#### Container Management
- `GET /api/v1/containers` - List all containers
- `GET /api/v1/containers/{id}/status` - Container status
- `POST /api/v1/containers/{id}/stop` - Stop container
- `GET /api/v1/containers/{id}/logs` - Get container logs

#### Room Management
- `POST /api/v1/rooms/create` - Create LiveKit room
- `GET /api/v1/rooms/{name}/status` - Room status
- `DELETE /api/v1/rooms/{name}` - Delete room

#### System
- `GET /health` - Basic health check
- `GET /health/detailed` - Detailed service status
- `POST /api/v1/maintenance/enable` - Enable maintenance mode

### Authentication

All API requests require authentication:
- **Web Clients**: Supabase JWT tokens
- **WordPress**: API keys in headers
- **Admin Access**: Supabase admin credentials

## 🧪 Testing

### Mission Critical Tests

Run the comprehensive test suite:
```bash
python3 scripts/test_mission_critical.py
```

Quick test for CI/CD:
```bash
python3 scripts/test_mission_critical.py --quick
```

### Test Categories
- Health & Connectivity
- Client Management
- Agent Operations
- LiveKit Integration
- Data Persistence
- API Key Synchronization

## 🔧 Development

### Local Development Setup

```bash
# Start services
docker-compose up -d

# View logs
docker-compose logs -f fastapi

# Enter container
docker-compose exec fastapi bash

# Run tests inside container
pytest --cov=app

# Hot reload is enabled - edit code and see changes
```

### Project Structure

```
/opt/autonomite-saas/
├── app/
│   ├── main.py              # Application entry point
│   ├── api/v1/              # API endpoints
│   ├── services/            # Business logic layer
│   ├── integrations/        # External service clients
│   ├── models/              # Pydantic models
│   ├── middleware/          # Auth, CORS, logging
│   └── templates/           # HTMX admin templates
├── docker/                  # Docker configurations
├── scripts/                 # Utility and test scripts
├── agent-runtime/           # Agent container runtime
└── docs/                    # Documentation
```

### Coding Standards

- Follow PEP 8 for Python code
- Use type hints for all functions
- Write docstrings for public APIs
- No workarounds - fix root causes
- Test new features with mission critical suite

## 🐛 Known Issues

### 1. Voice Setup Error in Admin Preview
- **Symptom**: "Invalid voice settings" error despite valid configuration
- **Impact**: UI only - voice agents work correctly via API
- **Workaround**: Use API directly or ignore UI error
- **Status**: Under investigation

### 2. Worker Authentication
- **Symptom**: Workers get 401 errors loading API keys from Supabase
- **Impact**: Using backend configuration instead of database
- **Workaround**: API keys configured in environment
- **Status**: Authentication context being improved

### 3. Pydantic Serialization Warning
- **Symptom**: Warning about subprocess.Popen serialization
- **Impact**: Cosmetic - no functional impact
- **Status**: Low priority fix planned

## 🚀 Deployment

### System Requirements

- **OS**: Ubuntu 22.04+ or similar Linux
- **RAM**: 4GB minimum, 8GB recommended
- **CPU**: 2 cores minimum, 4 cores recommended
- **Storage**: 20GB minimum for containers
- **Network**: Ports 80, 443, 8000 accessible

### Production Checklist

- [ ] Configure environment variables
- [ ] Set up SSL certificates
- [ ] Configure firewall rules
- [ ] Set up monitoring (optional)
- [ ] Configure backups
- [ ] Test with mission critical suite
- [ ] Enable maintenance mode during deployment

## 📚 Documentation

- [API Documentation](https://your-domain/docs) - Interactive API docs
- [Admin Guide](docs/admin-guide.md) - Platform administration
- [Developer Guide](docs/developer-guide.md) - Development setup
- [Deployment Guide](docs/deployment.md) - Production deployment

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Development Workflow
1. Fork the repository
2. Create a feature branch
3. Make changes with tests
4. Run mission critical tests
5. Submit pull request

## 📄 License

This project is proprietary software. All rights reserved by Autonomite AI.

## 📧 Support

- **Email**: support@autonomite.ai
- **Documentation**: https://docs.autonomite.ai
- **Issues**: https://github.com/autonomite-ai/autonomite-agent-platform/issues

## 🙏 Acknowledgments

- **LiveKit** - Excellent real-time communication infrastructure
- **Supabase** - Robust backend-as-a-service platform
- **FastAPI** - High-performance Python web framework
- **HTMX** - Modern UI interactions without complexity

---

Built with ❤️ by the Autonomite team

🤖 Generated with [Claude Code](https://claude.ai/code)