# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the staging implementation of **Sidekick Forge** - a multi-tenant SaaS platform for AI agents. Sidekick Forge provides AI agent infrastructure as a service, allowing clients to deploy and manage their own AI agents through a centralized platform.

**IMPORTANT: All development takes place in `/root/sidekick-forge/`. The `/opt/autonomite-saas/` directory is DEPRECATED and should NOT be used.**

### Multi-Tenant SaaS Architecture ✅ IMPLEMENTED

**Sidekick Forge** operates as a true multi-tenant SaaS platform with the following architecture:

**Platform Database (Sidekick Forge):**
- **Database**: Dedicated Supabase project for Sidekick Forge platform management
- **URL**: Configured via `SUPABASE_URL` environment variable
- **Purpose**: Manages clients, their configurations, and encrypted credentials
- **Tables**: `clients` table with encrypted client-specific Supabase credentials

**Client Architecture:**
- **Each client** (e.g., Autonomite, future clients) has their own separate Supabase project
- **Client data isolation**: Complete separation of client data and configurations
- **Dynamic credential loading**: Platform loads client-specific credentials from encrypted storage
- **Thin client integration**: Clients connect via WordPress plugins, mobile apps, etc.

**Platform Services:**
- **Admin Dashboard**: Multi-tenant management interface at `/admin`
- **API Endpoints**: Both v1 (legacy) and v2 (multi-tenant) endpoint support
- **Agent Workers**: Containerized agents that load client-specific configurations
- **LiveKit Integration**: Backend coordinates with client-specific LiveKit instances

**Data Flow:**
1. Client request → Sidekick Forge Platform
2. Platform loads client configuration from platform database
3. Platform connects to client's Supabase using encrypted credentials
4. Platform dispatches agents with client-specific settings
5. Response sent back through platform to client

**Example Clients:**
- **Autonomite**: First client, migrated to this architecture
- **Future Clients**: Each gets isolated Supabase project and configuration

## Common Development Commands

### Local Development
```bash
# From /root/sidekick-forge/ directory
docker-compose up -d                    # Start Sidekick Forge services
docker-compose logs -f fastapi         # View FastAPI logs
docker-compose exec fastapi bash       # Enter FastAPI container
docker-compose down                    # Stop services
```

### Sidekick Forge Admin Dashboard
```bash
# Access the multi-tenant admin dashboard
curl http://localhost:8000/admin       # Admin interface
curl http://localhost:8000/health      # Platform health check
```

### Client Management
```bash
# Managing clients via API
curl http://localhost:8000/api/v2/clients              # List all clients
curl http://localhost:8000/api/v2/clients/{id}/agents  # Client's agents
```

### Testing
```bash
pytest                          # Run all tests
pytest --cov=app               # Run with coverage
pytest -v                      # Verbose output
```

### Health Checks
```bash
curl http://localhost:8000/health              # Basic health check
curl http://localhost:8000/health/detailed     # Detailed service status
```

## Sidekick Forge Architecture Overview

### Application Structure
The Sidekick Forge FastAPI platform follows this structure:
- `/root/sidekick-forge/app/main.py` - Sidekick Forge platform entry point
- `/root/sidekick-forge/app/api/v1/` - Legacy API endpoints (backward compatibility)
- `/root/sidekick-forge/app/api/v2/` - Multi-tenant API endpoints (clients, agents, dispatch)
- `/root/sidekick-forge/app/admin/` - Multi-tenant admin dashboard
- `/root/sidekick-forge/app/services/` - Multi-tenant business logic
- `/root/sidekick-forge/app/integrations/` - External service clients (LiveKit, client Supabase connections)
- `/root/sidekick-forge/app/middleware/` - Auth, CORS, logging, rate limiting

### Key Components
1. **Sidekick Forge Platform** - FastAPI backend on port 8000 managing multiple clients
2. **Platform Database** - Sidekick Forge Supabase project storing client configurations
3. **Client Databases** - Individual Supabase projects for each client (e.g., Autonomite)
4. **Redis** - Session storage and caching for the platform
5. **LiveKit** - Real-time communication (platform coordinates client instances)
6. **Admin Dashboard** - Multi-tenant management interface at `/admin`
7. **Docker** - Container orchestration for platform and agent workers

### API Architecture
- **Multi-tenant RESTful API** with `/api/v1/` (legacy) and `/api/v2/` (multi-tenant) prefixes
- **Platform Authentication** via Sidekick Forge Supabase for admin access
- **Client Isolation** via dynamic credential loading per client
- **WebSocket support** for real-time features
- **CORS configured** for thin client integration (WordPress plugins, mobile apps)
- **Rate limiting** and **tenant isolation** at middleware level

### Agent Worker Architecture: Stateless Worker Pool
Sidekick Forge uses a **stateless worker pool** for maximum scalability and reliability. The old "container-per-client" model is DEPRECATED.

**Worker Architecture:**
- **Stateless Pool**: A group of identical `agent-worker` containers, scaled via `docker-compose`.
- **Dynamic Configuration**: Workers are generic. On job acceptance, they receive a `client_id` and load the specific client and agent configuration from the platform's database.
- **Centralized Vector Store**: All vector embeddings are stored in the client's Supabase database using the `pgvector` extension. Workers query this database via RPC.
- **Remote Embeddings**: Workers do NOT generate embeddings locally. They call external, API-driven services (e.g., SiliconFlow) as configured for the specific agent.
- **NO Local Vector Stores**: Use of local vector stores like `ChromaDB` is strictly forbidden.
- **NO Local Models**: Use of local embedding models like `sentence-transformers` is strictly forbidden.

## Development Workflow

1. Infrastructure changes: Modify scripts in `/root/sidekick-forge/scripts/`
2. Docker configs: Edit files in `/root/sidekick-forge/docker-compose.yml`
3. Nginx configs: Update `/root/sidekick-forge/nginx/site.conf.template`
4. Application code: Edit files in `/root/sidekick-forge/app/`
5. Environment variables: Configure in `/root/sidekick-forge/.env` file

## Core Development Principle: NO WORKAROUNDS

**CRITICAL**: When debugging issues, always identify and fix the root cause. Never implement workarounds that mask or hide underlying problems. The goal is to eliminate core issues, not circumvent them.

- ❌ Do NOT create temporary fixes that bypass the real problem
- ❌ Do NOT use fallback solutions when the primary system should work
- ❌ Do NOT mask errors or symptoms without addressing the cause
- ❌ Do NOT add artificial delays (sleep/wait) unless as an absolute last resort after explicit agreement
- ✅ DO identify the exact root cause of every issue
- ✅ DO implement proper fixes that resolve the underlying problem
- ✅ DO ensure solutions are sustainable and maintainable
- ✅ DO use proper event-driven patterns instead of timing-based solutions

This principle ensures system reliability, maintainability, and prevents technical debt accumulation.

## Dynamic API Key Loading Policy

**CRITICAL**: All API keys and credentials MUST be loaded dynamically from Supabase. Never use hardcoded defaults or environment variables as primary sources.

- ❌ Do NOT hardcode API keys or credentials in config files
- ❌ Do NOT use environment variables as the primary source for credentials
- ❌ Do NOT implement fallbacks to environment variables when Supabase is available
- ✅ DO always fetch credentials from the client configuration in Supabase
- ✅ DO use environment variables ONLY for initial bootstrap (Supabase connection itself)
- ✅ DO update credentials dynamically when they change in the dashboard

**Rationale**: Hardcoded or environment-based credentials become stale and cause authentication failures when keys are updated in the dashboard. Dynamic loading ensures the system always uses the current, valid credentials.

## No-Fallback Policy for Provider Initialization

**IMPORTANT**: When initializing service providers (STT, TTS, LLM, etc.), do NOT implement automatic fallbacks to alternative providers. If the configured provider fails to initialize:

- ❌ Do NOT silently fall back to another provider
- ❌ Do NOT use a different provider than what was configured
- ✅ DO raise a clear error explaining the initialization failure
- ✅ DO fail fast with descriptive error messages
- ✅ DO ensure configuration mismatches are immediately visible

**Rationale**: Silent fallbacks can lead to:
- Unexpected behavior (e.g., using Deepgram when Cartesia was configured)
- Hidden configuration issues that go undetected
- Confusion about which services are actually being used
- Billing/usage surprises when the wrong provider is used

This policy ensures configuration integrity and makes failures immediately visible for proper resolution.

## Frontend Framework Preferences

For the admin dashboard and UI components, use one of the following frameworks:
- **HTMX** - For server-side rendered pages with dynamic updates (preferred for admin dashboard)
- **Streamlit** - For data-heavy dashboards and analytics pages  
- **Reflex** - For more complex interactive UIs

Current implementation uses HTMX with Tailwind CSS for styling.

## WordPress Plugin Integration Requirements

### Critical Endpoints Needed for Thin-Client Transformation

1. **`/trigger-agent` Endpoint**
   - Used for both voice and text chat agent dispatch
   - Voice mode: Triggers Python LiveKit agent to join a room
   - Text mode: Processes text messages through the agent
   - Expected payload for voice:
     ```json
     {
       "room_name": "string",
       "agent_slug": "string", 
       "user_id": "string",
       "conversation_id": "string",
       "platform": "livekit"
     }
     ```
   - Expected payload for text:
     ```json
     {
       "message": "string",
       "agent_slug": "string",
       "session_id": "string",
       "user_id": "string",
       "conversation_id": "string",
       "mode": "text"
     }
     ```

2. **Text Chat Support**
   - Backend needs dedicated text chat handling
   - Should integrate with conversation/message storage
   - Must support real-time text interactions via LiveKit data channels

3. **Authentication Flow**
   - WordPress sites will use API keys for backend authentication
   - All other auth handled by Supabase Auth
   - Backend must validate WordPress API keys and map to Supabase users

4. **Deprecated Features**
   - Ultravox support has been completely removed (LiveKit only) - do not include ultravox_api_key in any models, forms, or configurations
   - Direct Supabase connections from WordPress being eliminated

## Testing Policy

### Mission Critical Functionality Testing
**IMPORTANT**: Before and after implementing any feature updates, you MUST run the mission critical test suite:

```bash
python3 /root/sidekick-forge/scripts/test_mission_critical.py
```

This test suite verifies:
- ✅ Health & Connectivity (API endpoints, admin interface)
- ✅ Client Management (listing, details, updates)
- ✅ Agent Management (listing, sync, updates)
- ✅ LiveKit Integration (trigger endpoint, agent spawning)
- ✅ Data Persistence (Supabase-only, no Redis)
- ✅ API Key Synchronization

**Testing Protocol:**
1. Run tests BEFORE making changes to establish baseline
2. Implement feature/fix
3. Run tests AFTER to ensure no regression
4. Only proceed if all tests pass
5. Document any new test requirements

**Quick Test Option:**
```bash
python3 /root/sidekick-forge/scripts/test_mission_critical.py --quick
```

**Reference File:** `/root/sidekick-forge/scripts/test_mission_critical.py`

## Important Notes

- This repository contains the **Sidekick Forge** platform source code and infrastructure
- All development happens in `/root/sidekick-forge/`
- **Platform Database**: Sidekick Forge Supabase project (`https://eukudpgfpihxsypulopm.supabase.co`)
- **Client Databases**: Each client has their own isolated Supabase project
- **Multi-tenant Architecture**: Complete separation of client data and configurations
- **Admin Dashboard**: Available at `/admin` for platform management
- WordPress plugin code is located in `/root/wordpress-plugin/autonomite-agent/` for reference (Autonomite is now a client of the platform)

## Multi-Tenant Database Architecture

### Platform Database (Sidekick Forge)
- **URL**: `https://eukudpgfpihxsypulopm.supabase.co`
- **Purpose**: Stores client configurations and encrypted credentials
- **Key Tables**:
  - `clients`: Client information and encrypted Supabase credentials
  - Platform logs and metrics
  - Admin user management

### Client Database Example (Autonomite)
- **Purpose**: Stores client-specific data (agents, conversations, documents, etc.)
- **Access**: Via encrypted credentials stored in platform database
- **Isolation**: Complete separation from other clients

### Credential Management
- **Platform Credentials**: Stored in `/root/sidekick-forge/.env`
- **Client Credentials**: Encrypted in the platform database, loaded dynamically by the main FastAPI app.
- **Provider API Keys**: Stored in the client's own Supabase database. These are fetched at runtime by the agent worker after it receives a job for that client.

## Core Development Principle: NO WORKAROUNDS

**CRITICAL**: When debugging issues, always identify and fix the root cause. Never implement workarounds that mask or hide underlying problems. The goal is to eliminate core issues, not circumvent them.

- ❌ Do NOT create temporary fixes that bypass the real problem.
- ❌ Do NOT use fallback solutions when the primary system should work.
- ❌ Do NOT mask errors or symptoms without addressing the cause.
- ❌ Do NOT add artificial delays (sleep/wait).
- ❌ Do NOT introduce new libraries or technologies (e.g., `ChromaDB`) to solve a problem that should be handled by our existing architecture.
- ✅ DO identify the exact root cause of every issue.
- ✅ DO implement proper fixes that resolve the underlying problem.
- ✅ DO ensure solutions are sustainable and maintainable.

This principle ensures system reliability and prevents technical debt.

## RAG and Embedding: The Supabase-Centric Strategy

Our Retrieval-Augmented Generation (RAG) system is designed to be robust and scalable, relying entirely on our Supabase infrastructure. **Local vector stores or embedding models are strictly forbidden.**

-   **Vector Store**: **Supabase `pgvector`** is the ONLY vector database. All embeddings for documents and conversation history are stored here, within each client's isolated database.
-   **Vector Search**: All similarity searches MUST be performed via dedicated Supabase RPC functions (`match_documents`, `match_conversation_transcripts_secure`). The agent worker calls these functions.
-   **Embedding Generation**: Embeddings are generated by **remote, API-driven services** (e.g., SiliconFlow, Novita, Jina).
    -   The `AgentContextManager` in `context.py` acts as a factory.
    -   It reads the configured `embedding_provider` and API key from the agent's configuration.
    -   It instantiates the appropriate Python client for that service and uses it to generate embeddings at runtime.
-   **FORBIDDEN TECHNOLOGIES**:
    -   ❌ **`ChromaDB`**: Do NOT use. It is a local vector store and violates our stateless worker principle.
    -   ❌ **`sentence-transformers`**: Do NOT use for embeddings. It is a local model and violates our service-based architecture. Agent containers should not be downloading and running local embedding models.

## Current Development Status (Simplified)

### ✅ Core Architecture
1.  **Stateless Worker Pool**: The platform correctly uses a scalable pool of generic `agent-worker` containers.
2.  **Multi-Tenancy**: Client data is fully isolated in separate Supabase projects.
3.  **Dynamic Configuration**: Agents load their configuration at runtime based on the job they receive.
4.  **LiveKit Integration**: The system uses the modern `AgentSession` and `JobContext` patterns from the LiveKit SDK v1.0+.
5.  **Explicit Dispatch**: All agent jobs use explicit dispatch for reliability.

### 🔧 Next Steps
1.  **End-to-End RAG Testing**: Verify that the newly corrected `AgentContextManager` (using remote embeddings) works as expected.
2.  **Performance Tuning**: Instrument and optimize the context-building pipeline.
3.  **Frontend Integration**: Ensure the WordPress plugin and other clients interact correctly with the backend APIs.

## LiveKit SDK Pattern Requirements

### CRITICAL: Always Use LiveKit Python SDK v1.0+ Patterns

**IMPORTANT**: When implementing any LiveKit functionality, you MUST use the patterns from the LiveKit Python SDK v1.0+ for any functionality it supports. This is critically important for proper job dispatch and agent lifecycle management.

Reference implementation: `/root/wordpress-plugin/autonomite-agent/livekit-agents/autonomite_agent_v1_1_19_text_support.py`

Key patterns to follow:
1. **Job Request Handling**: Use `request_filter` function with proper job acceptance/rejection
2. **Worker Registration**: Use `WorkerOptions` with `entrypoint_fnc` and `request_fnc`
3. **Room Entry**: Use `JobContext` pattern for handling room lifecycle
4. **Agent Dispatch**: Jobs are dispatched when participants join rooms, not just on room creation
5. **Metadata Access**: Check both job metadata and participant metadata for context

### Explicit Agent Dispatch Policy

**IMPORTANT**: Sidekick Forge uses **EXPLICIT DISPATCH** mode for all agent jobs. This ensures proper agent-to-room assignment in our multi-tenant environment.

**Policy Details**:
- ✅ All rooms MUST be created with `agent_name="sidekick-agent"` parameter
- ✅ Workers MUST register with `agent_name="sidekick-agent"` in WorkerOptions
- ✅ Request filters MUST check that `job_request.agent_name == "sidekick-agent"`
- ❌ Never use automatic dispatch (rooms without agent_name)
- ❌ Never accept jobs without matching agent names

**Implementation**:
```python
# Worker registration (entrypoint.py)
worker_options = WorkerOptions(
    entrypoint_fnc=agent_job_handler,
    request_fnc=request_filter,
    agent_name="sidekick-agent",  # EXPLICIT: Only receive jobs for this agent
)

# Request filter (entrypoint.py)
async def request_filter(job_request: JobRequest) -> None:
    our_agent_name = os.getenv("AGENT_NAME", "sidekick-agent")
    if job_request.agent_name == our_agent_name:
        await job_request.accept()
    else:
        await job_request.reject()

# Room creation (livekit_client.py)
room_info = await livekit_manager.create_room(
    name=room_name,
    metadata=metadata_json,
    enable_agent_dispatch=True,
    agent_name="sidekick-agent"  # EXPLICIT DISPATCH
)
```

**Rationale**: Explicit dispatch prevents cross-client job leakage and ensures predictable agent assignment in multi-tenant deployments.

### LiveKit Agent SDK Documentation Reference

The complete LiveKit Agent SDK documentation is available at `/root/autonomite-agent-platform/LiveKit-Agent-SDK-Python.txt?rlkey=whyd2r32t1m8x6m76yuevl0p6`. This documentation includes:

1. **Agent Dispatch Patterns**:
   - ~~Automatic dispatch (default)~~ **DEPRECATED - Use explicit dispatch only**
   - Explicit dispatch with `agent_name` **REQUIRED**
   - Dispatch via API using `AgentDispatchService`
   - Dispatch on participant connection via token configuration
   - Metadata passing through dispatch for context

2. **Worker and Job Lifecycle**:
   - Worker registration with LiveKit server
   - Job subprocess spawning on dispatch request
   - Proper connection handling with `ctx.connect()`
   - Shutdown hooks and cleanup

3. **Voice Pipeline vs Multimodal Agents**:
   - `VoicePipelineAgent`: STT → LLM → TTS pipeline with full control
   - `MultimodalAgent`: Direct audio processing (e.g., OpenAI Realtime API)
   - Event handling for both agent types
   - Interruption and turn detection patterns

4. **Critical Implementation Details**:
   - Use `request_filter` for job acceptance logic
   - Handle both job and participant metadata
   - Implement proper event listeners for agent lifecycle
   - Follow async/await patterns consistently
   - Use proper error handling and logging

### LiveKit Infrastructure

**CURRENT STATE**: The platform uses **LiveKit Cloud** for all voice/video infrastructure
- Each client has their own LiveKit Cloud credentials stored in their configuration
- The platform should use the client's LiveKit credentials when creating rooms/sessions for that client
- Agent dispatch must work with LiveKit Cloud's requirements

**FUTURE STATE**: Will transition to self-hosted LiveKit, but for now, LiveKit Cloud is the standard

### 🛠️ Testing Commands:

```bash
# Check running containers
docker ps | grep agent_

# List all agent containers via API
curl http://localhost:8000/api/v1/containers

# Check container health
curl http://localhost:8000/api/v1/containers/health

# Test trigger endpoint (will spawn container)
curl -X POST "http://localhost:8000/api/v1/trigger-agent" \
  -H "Content-Type: application/json" \
  -d '{"agent_slug": "test", "mode": "voice", "room_name": "test-room", "user_id": "user-1", "client_id": "df91fd06-816f-4273-a903-5a4861277040"}'

# View container logs
docker logs -f agent_<client_id>_<agent_slug>

# View FastAPI logs
docker-compose logs -f fastapi

# Build agent runtime image
cd /root/autonomite-agent-platform && docker-compose build agent-worker

# Test container locally
docker run --env-file .env autonomite/agent-runtime:latest
```

## LiveKit Agent Development Guidelines

### IMPORTANT: Use AgentSession, NOT VoicePipelineAgent

As of LiveKit Agents v1.0, `VoicePipelineAgent` has been deprecated and replaced with `AgentSession`. All agent implementations must use `AgentSession` for compatibility with LiveKit 1.0 and later.

**Key Points:**
- ❌ Do NOT use `VoicePipelineAgent` - it is deprecated
- ✅ DO use `AgentSession` - it provides a superset of functionality
- `AgentSession` is the single, unified agent orchestrator for all agent types
- It supports both pipelined and speech-to-speech models

**Reference:** See the [LiveKit v0.x migration guide](https://docs.livekit.io/agents/start/v0-migration.md) for details on migrating from VoicePipelineAgent to AgentSession.

### Current Implementation Status

The Autonomite agent platform currently uses `AgentSession` in the `session_agent_rag.py` implementation. However, there is a known issue where the event handlers (particularly `user_speech_committed`) are not firing properly even though transcripts are being received. This needs to be debugged and fixed while staying within the `AgentSession` pattern.