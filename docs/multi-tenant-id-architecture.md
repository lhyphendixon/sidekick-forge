# Sidekick Forge Multi-Tenant ID Architecture

## Overview

Sidekick Forge implements a multi-tenant architecture where:
- **Clients** own multiple **Agents**
- **Users** can interact with **Agents** across different **Clients**
- Each entity is identified by a UUID

## Key Relationships

### 1. Client → Agent (One-to-Many)
- A client (e.g., Autonomite) owns multiple AI agents
- Each agent belongs to exactly one client
- Agent slug must be unique within a client, but can be reused across clients

### 2. User → Agent (Many-to-Many)
- Users can interact with agents from different clients
- A single user might use agents from multiple clients
- User context is loaded dynamically based on the user_id

### 3. Client → Database (One-to-One)
- Each client has their own isolated Supabase database
- Platform database stores encrypted credentials for each client database
- Complete data isolation between clients

## ID Usage Patterns

### Client ID
- **Type**: UUID (e.g., `11389177-e4d8-49a9-9a00-f77bb4de6592`)
- **Purpose**: Identifies which tenant/organization owns resources
- **Used for**:
  - Loading client-specific Supabase credentials
  - Isolating agents, documents, and configurations
  - Determining which database to connect to
  - Resource quotas and billing

### User ID
- **Type**: UUID (e.g., `351bb07b-03fc-4fb4-b09b-748ef8a72084`)
- **Purpose**: Identifies individual users across the platform
- **Used for**:
  - Loading user profile and preferences
  - Tracking conversation history
  - Document ownership and access control
  - Personalization context for agents

### Agent ID/Slug
- **Type**: String (unique within client)
- **Purpose**: Identifies specific AI agents
- **Scoping**: Must be unique within a client, but can be duplicated across clients

## Critical Code Paths

### 1. Agent Trigger (`/api/v1/trigger-agent`)
```python
# Request includes:
- agent_slug: Which agent to trigger
- client_id: Optional, auto-detected if not provided
- user_id: Required for user context

# Flow:
1. If no client_id, search all clients for agent_slug
2. Load client configuration from platform DB
3. Connect to client's Supabase using encrypted credentials
4. Pass both client_id and user_id to agent context
```

### 2. Agent Context Loading (`docker/agent/context.py`)
```python
# Initialization requires:
- supabase_client: Client's database connection
- user_id: For profile and conversation lookup
- client_id: For multi-tenant isolation
- agent_config: Agent-specific settings

# Context includes:
1. User profile from client's database
2. Conversation history for user
3. RAG documents assigned to agent
4. Agent-specific system prompt
```

### 3. Client Service (`app/services/client_service_supabase.py`)
```python
# Platform database operations:
- get_client(client_id): Load client config and credentials
- get_client_supabase_client(client_id): Create client DB connection
- Auto-sync: Fetch latest settings from client's database
```

## Data Flow Example

1. **WordPress Plugin** sends request:
   ```json
   {
     "agent_slug": "clarence-coherence",
     "user_id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
     "mode": "voice",
     "room_name": "room-123"
   }
   ```

2. **Platform** processes request:
   - Finds agent "clarence-coherence" belongs to client "11389177-e4d8-49a9-9a00-f77bb4de6592"
   - Loads Autonomite's Supabase credentials from platform DB
   - Creates connection to Autonomite's database

3. **Agent Context** loads:
   - User profile for "351bb07b-03fc-4fb4-b09b-748ef8a72084" from Autonomite's DB
   - Conversation history between this user and Clarence
   - Documents assigned to Clarence in Autonomite's DB

4. **Agent** receives full context:
   - Knows user's name, preferences, history
   - Has access to relevant knowledge base
   - Operates within client's isolated environment

## Security Considerations

1. **Client Isolation**:
   - Agents cannot access data from other clients
   - Each client connection uses separate credentials
   - No cross-client queries possible

2. **User Privacy**:
   - User data stays within client databases
   - Platform only stores client configurations, not user data
   - User IDs are consistent across clients but data is isolated

3. **Credential Management**:
   - Client database credentials encrypted in platform DB
   - Credentials loaded dynamically per request
   - No hardcoded credentials in code

## Common Issues and Solutions

### Issue 1: Wrong Client ID
**Symptom**: Agent can't find user profile or documents
**Cause**: Hardcoded or incorrect client_id
**Solution**: Always use dynamic client_id from database or request

### Issue 2: Missing User Context
**Symptom**: Agent doesn't know user's name or history
**Cause**: user_id not passed to agent context
**Solution**: Ensure user_id flows from request → trigger → dispatch → agent

### Issue 3: Cross-Client Data Leakage
**Symptom**: Agent accesses wrong client's data
**Cause**: Reusing database connections across clients
**Solution**: Create new connection for each client using their credentials

## Best Practices

1. **Never hardcode IDs**:
   - Load client_id from platform database
   - Accept user_id from request
   - Auto-detect when possible

2. **Validate IDs**:
   - Check UUID format
   - Verify client exists before operations
   - Handle missing user gracefully

3. **Log ID usage**:
   - Include client_id and user_id in log context
   - Track ID resolution and lookups
   - Monitor for ID mismatches

4. **Test multi-tenancy**:
   - Create test scenarios with multiple clients
   - Verify data isolation
   - Test user access across clients