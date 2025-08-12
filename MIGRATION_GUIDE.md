# Sidekick Forge Platform Migration Guide

## Overview

This guide documents the architectural transformation from a single-tenant "Autonomite Agent Platform" to the multi-tenant "Sidekick Forge" platform.

## Architecture Changes

### Before (Single-Tenant)
- Single Supabase database for all data
- Hardcoded credentials in environment variables
- Direct database access from services
- Mixing of platform and client data

### After (Multi-Tenant)
- **Platform Database**: Sidekick Forge Supabase project manages clients
- **Client Databases**: Each client has their own Supabase project
- **ClientConnectionManager**: Routes requests to correct database
- **Dynamic Credentials**: API keys loaded from platform database

## Components Created

### 1. Database Schema
- **Location**: `/scripts/create_clients_table.sql`
- **Purpose**: Defines the `clients` table for the platform database
- **Status**: ✅ Created (requires manual execution)

### 2. ClientConnectionManager
- **Location**: `/app/services/client_connection_manager.py`
- **Purpose**: Core component managing multi-tenant database connections
- **Key Features**:
  - Dynamic client database connections
  - API key management
  - Client discovery by agent slug
  - No fallbacks - fails fast with clear errors

### 3. Multi-Tenant Services
- **AgentService**: `/app/services/agent_service_multitenant.py`
- **ClientService**: `/app/services/client_service_multitenant.py`
- **Status**: ✅ Created and ready

### 4. Updated API Endpoints
- **Location**: `/app/api/v1/trigger_multitenant.py`
- **Changes**: 
  - Auto-detects client from agent slug
  - Uses ClientConnectionManager for all operations
  - Passes client API keys to agent workers

## Migration Steps

### Phase 1: Database Setup (Manual)
1. Execute `create_clients_table.sql` in Sidekick Forge Supabase dashboard
2. Execute `insert_autonomite_client.sql` to add Autonomite as first client
3. Verify with `python3 scripts/migrate_autonomite_client.py`

### Phase 2: Code Integration (Remaining Work)

#### 1. Update Main Application
```python
# In app/main.py, update imports:
from app.core.dependencies_multitenant import get_agent_service, get_client_service
from app.api.v1 import trigger_multitenant

# Replace old routes with multi-tenant versions
app.include_router(trigger_multitenant.router, prefix="/api/v1")
```

#### 2. Update Other API Endpoints
Each endpoint needs updating to accept `client_id`:
- `/api/v1/agents/*` - Update to use multi-tenant AgentService
- `/api/v1/clients/*` - Update to use multi-tenant ClientService
- `/api/v1/conversations/*` - Add multi-tenant support
- `/api/v1/documents/*` - Add multi-tenant support

#### 3. Update Admin Interface
The HTMX admin interface needs updating:
- Add client selector dropdown
- Update all service calls to include client_id
- Add client management pages

#### 4. Update Docker Agent
The agent worker needs updating to use platform credentials:
```python
# In docker/agent/api_key_loader.py
# Update to use ClientConnectionManager for loading keys
```

### Phase 3: Testing

#### 1. Create Test Client
```python
# Create a test client in the platform database
test_client = {
    'name': 'Test Client',
    'supabase_url': 'https://test.supabase.co',
    'supabase_service_role_key': 'test_key',
    # Add test API keys
}
```

#### 2. Update Mission Critical Tests
- Update `test_mission_critical.py` to use multi-tenant endpoints
- Add tests for client isolation
- Test auto-detection of client from agent slug

### Phase 4: Final Cleanup

#### 1. Remove Old Files
- `app/services/agent_service_supabase.py` (replaced by multi-tenant version)
- `app/services/client_service_supabase.py` (replaced by multi-tenant version)
- `app/api/v1/trigger.py` (replaced by multi-tenant version)

#### 2. Update Environment Variables
- Remove client-specific credentials from `.env`
- Keep only platform credentials

#### 3. Rename to Sidekick Forge
- Update all references from "Autonomite" to "Sidekick Forge"
- Update Docker image names
- Update documentation

## Critical Principles

### 1. No Fallbacks
- Never fall back to environment variables for API keys
- Fail fast with clear errors when configuration is missing
- This ensures configuration issues are immediately visible

### 2. Tenant Isolation
- Each client's data must be completely isolated
- Never mix data between clients
- Always validate client_id before operations

### 3. Dynamic Configuration
- All credentials loaded from platform database
- No hardcoded API keys or URLs
- Configuration changes take effect immediately

## Next Steps for Dev Agent

1. **Immediate**: Execute database setup SQL in Supabase dashboard
2. **Next**: Update `app/main.py` to use multi-tenant services
3. **Then**: Update remaining API endpoints one by one
4. **Finally**: Run comprehensive tests and rename to Sidekick Forge

## Troubleshooting

### "Client not found" Errors
- Ensure client exists in platform database
- Check UUID format is correct
- Verify platform credentials are valid

### "Table does not exist" Errors
- Execute database setup SQL first
- Verify you're connected to correct database

### API Key Loading Issues
- Check client has API keys configured in platform database
- Verify no test/dummy keys are being used
- Ensure ClientConnectionManager is initialized

This migration establishes a robust, scalable multi-tenant architecture that can support unlimited clients with complete isolation and dynamic configuration.