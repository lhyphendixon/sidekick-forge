# Frontend Integration Guide

## IMPORTANT: All Room Creation Must Go Through API Endpoints

To ensure proper agent functionality, **ALL** frontend clients (WordPress plugin, mobile apps, etc.) MUST use our API endpoints. Do NOT create LiveKit rooms directly.

## Correct Flow for Voice Chat

### 1. Trigger Agent Endpoint

**Endpoint**: `POST /api/v1/trigger-agent`

**Purpose**: Creates a LiveKit room with proper metadata and triggers the AI agent to join.

**Request Body**:
```json
{
  "agent_slug": "string",        // Required: The agent identifier
  "mode": "voice",               // Required: "voice" or "text"
  "room_name": "string",         // Required for voice mode
  "user_id": "string",           // Required: User identifier
  "client_id": "string",         // Optional: Will auto-detect if not provided
  "session_id": "string",        // Optional: Session tracking
  "conversation_id": "string",   // Optional: Conversation tracking
  "context": {}                  // Optional: Additional context
}
```

**Response**:
```json
{
  "success": true,
  "message": "Agent triggered successfully",
  "data": {
    "mode": "voice",
    "room_name": "your-room-name",
    "livekit_config": {
      "server_url": "wss://...",
      "user_token": "jwt-token-for-user",
      "configured": true
    },
    "room_info": {
      "status": "created",
      "message": "Room ready"
    },
    "agent_context": {
      // Full agent configuration
    }
  }
}
```

### 2. Connect to Room

Use the `user_token` from the response to connect to the LiveKit room:

```javascript
// Example using LiveKit JS SDK
const room = new Room();
await room.connect(serverUrl, userToken);
```

## Why This Is Required

1. **Metadata**: The API ensures rooms are created with proper agent configuration metadata
2. **API Keys**: The backend manages all API keys securely - clients don't need them
3. **Agent Dispatch**: The API triggers the correct agent worker to join the room
4. **Multi-tenant**: Each client's agents are properly isolated

## Common Mistakes to Avoid

❌ **DON'T**: Create LiveKit rooms directly using client-side LiveKit credentials
❌ **DON'T**: Try to trigger agents by creating rooms without metadata
❌ **DON'T**: Use preview/test room names without going through the API

✅ **DO**: Always use the `/api/v1/trigger-agent` endpoint
✅ **DO**: Pass all required parameters in the request
✅ **DO**: Use the returned user token to connect to the room

## Testing

You can test the endpoint with curl:

```bash
curl -X POST "http://your-api-domain/api/v1/trigger-agent" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "agent_slug": "your-agent",
    "mode": "voice",
    "room_name": "test-room-12345",
    "user_id": "test-user"
  }'
```

## Support

If you're having issues:
1. Check that you're using the API endpoint, not creating rooms directly
2. Verify all required fields are present in your request
3. Check the agent slug exists for your client
4. Contact the backend team with the full request/response for debugging