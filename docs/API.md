# API Documentation

## Base URL

```
https://api.autonomite.ai
```

## Authentication

The API uses two authentication methods:

1. **JWT Token** (for admin users)
2. **API Key** (for WordPress sites)

### JWT Authentication

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "admin@example.com",
  "password": "password"
}
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

Use the token in subsequent requests:
```http
Authorization: Bearer <access_token>
```

### API Key Authentication

For WordPress sites:
```http
X-API-Key: your-api-key
X-Client-ID: your-client-id
```

## Endpoints

### Agents

#### List Agents
```http
GET /api/v1/agents
```

Query parameters:
- `client_id` (optional): Filter by client
- `active` (optional): Filter by active status

Response:
```json
[
  {
    "id": "123",
    "slug": "assistant",
    "name": "Assistant",
    "description": "AI assistant",
    "client_id": "client-123",
    "enabled": true,
    "voice_settings": {
      "provider": "openai",
      "voice_id": "alloy",
      "temperature": 0.7
    }
  }
]
```

#### Get Agent
```http
GET /api/v1/agents/{agent_id}
```

#### Create Agent
```http
POST /api/v1/agents
Content-Type: application/json

{
  "name": "New Agent",
  "slug": "new-agent",
  "description": "A new AI agent",
  "system_prompt": "You are a helpful assistant.",
  "voice_settings": {
    "provider": "openai",
    "voice_id": "alloy",
    "temperature": 0.7
  }
}
```

#### Update Agent
```http
PUT /api/v1/agents/{agent_id}
Content-Type: application/json

{
  "name": "Updated Agent",
  "enabled": false
}
```

#### Delete Agent
```http
DELETE /api/v1/agents/{agent_id}
```

### Trigger Agent

#### Voice Chat
```http
POST /trigger-agent
Content-Type: application/json

{
  "room_name": "room-123",
  "agent_slug": "assistant",
  "user_id": "user-123",
  "conversation_id": "conv-123",
  "platform": "livekit"
}
```

#### Text Chat
```http
POST /trigger-agent
Content-Type: application/json

{
  "message": "Hello, how are you?",
  "agent_slug": "assistant",
  "session_id": "session-123",
  "user_id": "user-123",
  "conversation_id": "conv-123",
  "mode": "text"
}
```

### Clients

#### List Clients
```http
GET /api/v1/clients
```

#### Get Client
```http
GET /api/v1/clients/{client_id}
```

#### Create Client
```http
POST /api/v1/clients
Content-Type: application/json

{
  "id": "new-client",
  "name": "New Client",
  "domain": "newclient.com",
  "settings": {
    "supabase": {
      "url": "https://project.supabase.co",
      "anon_key": "anon-key",
      "service_role_key": "service-key"
    },
    "livekit": {
      "server_url": "wss://livekit.cloud",
      "api_key": "api-key",
      "api_secret": "api-secret"
    }
  }
}
```

#### Update Client
```http
PUT /api/v1/clients/{client_id}
Content-Type: application/json

{
  "name": "Updated Client Name",
  "active": false
}
```

### Health Check

```http
GET /health
```

Response:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2025-01-14T12:00:00Z"
}
```

## Error Responses

All errors follow this format:
```json
{
  "detail": "Error message",
  "status_code": 400,
  "type": "validation_error"
}
```

Common status codes:
- `400` - Bad Request
- `401` - Unauthorized
- `403` - Forbidden
- `404` - Not Found
- `422` - Validation Error
- `500` - Internal Server Error

## Rate Limiting

API endpoints are rate limited:
- General API: 100 requests per second
- Auth endpoints: 10 requests per second

Rate limit headers:
```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 99
X-RateLimit-Reset: 1642000000
```

## WebSocket

For real-time text chat:
```
wss://api.autonomite.ai/ws/chat/{session_id}
```

Message format:
```json
{
  "type": "message",
  "content": "Hello",
  "user_id": "user-123",
  "timestamp": "2025-01-14T12:00:00Z"
}
```