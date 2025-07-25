# Container Management and Agent Lookup Findings

## Issues Identified and Resolved

### 1. Multiple Container Issue
**Problem**: Multiple containers were running for the same agent (4 containers for clarence_coherence)
**Cause**: Previous failed attempts left containers running
**Solution**: Manually stopped old containers using `docker stop`
**Prevention**: The container manager should check for existing containers before spawning new ones

### 2. Agent Lookup Issue
**Problem**: "Agent not found" errors when triggering agents
**Cause**: 
- Agent slug mismatch (using `clarence_coherence` instead of `clarence-coherence`)
- The system expects agents to be in the client's Supabase, but the test client uses the same Supabase as the platform

**Current Architecture**:
- Client "Autonomite" uses the SAME Supabase instance as the platform
- Agents are stored in the platform's `agents` table with proper slugs
- Agent configurations are in `agent_configurations` table but lack slugs

**Available Agents**:
- `farah` - Farah agent
- `litebridge` - Litebridge agent  
- `clarence-coherence` - Clarence Coherence agent (note the hyphen)

### 3. LiveKit Manager Fix Status
**Issue**: The 500 error "LiveKitManager.__init__() got an unexpected keyword argument 'url'" was resolved
**Solution**: Modified `get_client_livekit_manager()` to create instance first, then override attributes
**Status**: Working correctly - agent trigger successful

## Current System State

### Working:
- ✅ Agent trigger endpoint with correct slug
- ✅ Container spawning and registration
- ✅ LiveKit room creation
- ✅ Token generation with client credentials
- ✅ Agent dispatch to rooms

### Container Naming Convention:
Format: `agent_{client_id_short}_{agent_slug}_{room_suffix}`
Example: `agent_df91fd06_clarence_coherence_testroo`

### Recommendations:

1. **Container Management**:
   - Implement container reuse for same agent/client combinations
   - Add automatic cleanup of stale containers
   - Check for existing containers before spawning new ones

2. **Agent Management**:
   - Standardize agent slug format (use hyphens consistently)
   - Consider adding validation for agent slugs
   - Document available agents for each client

3. **Multi-tenant Architecture**:
   - For true multi-tenancy, clients should have their own Supabase instances
   - Current setup works but shares data between platform and client

## Testing Commands

```bash
# List agent containers
docker ps | grep agent_

# Test agent trigger (correct slug)
curl -X POST "http://localhost:8000/api/v1/trigger-agent" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_slug": "clarence-coherence",
    "mode": "voice",
    "room_name": "test-room-'$(date +%s)'",
    "user_id": "test-user-123",
    "client_id": "df91fd06-816f-4273-a903-5a4861277040"
  }'

# Check container logs
docker logs <container_name> --tail 50
```