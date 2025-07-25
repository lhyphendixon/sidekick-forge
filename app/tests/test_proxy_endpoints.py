"""Tests for WordPress proxy endpoints"""
import pytest
from fastapi.testclient import TestClient
import json
from datetime import datetime
import uuid

# Mock the Redis client
class MockRedis:
    def __init__(self):
        self.data = {}
        
    def get(self, key):
        return self.data.get(key)
        
    def setex(self, key, ttl, value):
        self.data[key] = value
        return True
        
    def lpush(self, key, value):
        if key not in self.data:
            self.data[key] = []
        self.data[key].append(value)
        return len(self.data[key])
        
    def lrange(self, key, start, end):
        if key not in self.data:
            return []
        return self.data[key][start:end+1]
        
    def expire(self, key, ttl):
        return True
        
    def delete(self, key):
        if key in self.data:
            del self.data[key]
            return 1
        return 0
        
    def lrem(self, key, count, value):
        if key in self.data and isinstance(self.data[key], list):
            self.data[key] = [v for v in self.data[key] if v != value]
        return 0


# Mock services
class MockWordPressSite:
    def __init__(self, id="wp-site-1", domain="example.com", client_id="autonomite"):
        self.id = id
        self.domain = domain
        self.client_id = client_id
        self.site_name = "Example Site"
        self.is_active = True
        self.api_key = "wp_test_key_123"
        self.api_secret = "wp_test_secret_456"


class MockClient:
    def __init__(self):
        self.settings = type('obj', (object,), {
            'livekit': type('obj', (object,), {
                'server_url': 'wss://test.livekit.cloud',
                'api_key': 'APITest123',
                'api_secret': 'SecretTest456'
            })()
        })()


class MockAgent:
    def __init__(self, slug="test-agent"):
        self.slug = slug
        self.name = "Test Agent"
        self.system_prompt = "You are a helpful assistant"
        self.model_provider = "openai"
        self.enabled = True


# Create test app
from app.simple_main import app
from app.api.v1 import conversations_proxy, documents_proxy, text_chat_proxy, livekit_proxy

# Initialize test client
client = TestClient(app)

# Mock Redis and services
mock_redis = MockRedis()
mock_wp_site = MockWordPressSite()
mock_client_obj = MockClient()
mock_agent = MockAgent()

# Inject mocks
conversations_proxy.redis_client = mock_redis
documents_proxy.redis_client = mock_redis
text_chat_proxy.redis_client = mock_redis


# Mock validate_wordpress_auth to return our test site
async def mock_validate_auth(*args, **kwargs):
    return mock_wp_site


# Mock service methods
async def mock_get_client(client_id):
    return mock_client_obj


async def mock_get_client_agents(client_id):
    return [mock_agent]


# Apply mocks
from unittest.mock import patch, AsyncMock


@pytest.fixture
def auth_headers():
    """Provide auth headers for tests"""
    return {
        "X-API-Key": "wp_test_key_123"
    }


class TestConversationsProxy:
    """Test conversation management endpoints"""
    
    @patch('app.api.v1.wordpress_sites.validate_wordpress_auth', mock_validate_auth)
    def test_create_conversation(self, auth_headers):
        """Test creating a new conversation"""
        response = client.post(
            "/api/v1/conversations/create",
            headers=auth_headers,
            json={
                "agent_slug": "test-agent",
                "user_id": "user-123",
                "user_email": "test@example.com",
                "user_name": "Test User",
                "metadata": {"source": "chat_widget"}
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "conversation_id" in data
        assert data["agent_slug"] == "test-agent"
        assert data["user_id"] == "user-123"
        assert data["message_count"] == 0
        
    @patch('app.api.v1.wordpress_sites.validate_wordpress_auth', mock_validate_auth)
    def test_add_message(self, auth_headers):
        """Test adding a message to conversation"""
        # First create a conversation
        conv_id = str(uuid.uuid4())
        conv_data = {
            "conversation_id": conv_id,
            "wordpress_site_id": mock_wp_site.id,
            "agent_slug": "test-agent",
            "user_id": "user-123",
            "messages": [],
            "message_count": 0
        }
        mock_redis.setex(f"conversation:{mock_wp_site.id}:{conv_id}", 3600, json.dumps(conv_data))
        
        # Add a message
        response = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=auth_headers,
            json={
                "role": "user",
                "content": "Hello, I need help",
                "metadata": {}
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "message_id" in data
        assert data["role"] == "user"
        assert data["content"] == "Hello, I need help"
        
    @patch('app.api.v1.wordpress_sites.validate_wordpress_auth', mock_validate_auth)
    def test_get_conversation(self, auth_headers):
        """Test getting a conversation"""
        # Create a conversation
        conv_id = str(uuid.uuid4())
        conv_data = {
            "conversation_id": conv_id,
            "wordpress_site_id": mock_wp_site.id,
            "agent_slug": "test-agent",
            "user_id": "user-123",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"}
            ],
            "message_count": 2
        }
        mock_redis.setex(f"conversation:{mock_wp_site.id}:{conv_id}", 3600, json.dumps(conv_data))
        
        # Get the conversation
        response = client.get(
            f"/api/v1/conversations/{conv_id}",
            headers=auth_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["conversation_id"] == conv_id
        assert len(data["messages"]) == 2
        assert data["message_count"] == 2
        
    @patch('app.api.v1.wordpress_sites.validate_wordpress_auth', mock_validate_auth)
    def test_list_conversations(self, auth_headers):
        """Test listing conversations"""
        # Add some conversation IDs to the list
        mock_redis.lpush(f"site_conversations:{mock_wp_site.id}", "conv-1")
        mock_redis.lpush(f"site_conversations:{mock_wp_site.id}", "conv-2")
        
        # Add conversation data
        for i in range(1, 3):
            conv_data = {
                "conversation_id": f"conv-{i}",
                "wordpress_site_id": mock_wp_site.id,
                "agent_slug": "test-agent",
                "user_id": f"user-{i}",
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
                "metadata": {},
                "message_count": i
            }
            mock_redis.setex(f"conversation:{mock_wp_site.id}:conv-{i}", 3600, json.dumps(conv_data))
        
        # List conversations
        response = client.get(
            "/api/v1/conversations/",
            headers=auth_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        

class TestTextChatProxy:
    """Test text chat endpoints"""
    
    @patch('app.api.v1.wordpress_sites.validate_wordpress_auth', mock_validate_auth)
    @patch.object(text_chat_proxy, 'get_agent_service')
    @patch.object(text_chat_proxy, 'get_client_service')
    def test_send_text_message(self, mock_client_svc, mock_agent_svc, auth_headers):
        """Test sending a text message"""
        # Setup mocks
        mock_agent_svc.return_value = AsyncMock()
        mock_agent_svc.return_value.get_client_agents = mock_get_client_agents
        mock_client_svc.return_value = AsyncMock()
        mock_client_svc.return_value.get_client = mock_get_client
        
        response = client.post(
            "/api/v1/text-chat/send",
            headers=auth_headers,
            json={
                "message": "Hello, I need help",
                "agent_slug": "test-agent",
                "user_id": "user-123",
                "user_metadata": {"name": "Test User"}
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "conversation_id" in data
        assert "message_id" in data
        assert "agent_response_id" in data
        

class TestDocumentsProxy:
    """Test document management endpoints"""
    
    @patch('app.api.v1.wordpress_sites.validate_wordpress_auth', mock_validate_auth)
    def test_list_documents(self, auth_headers):
        """Test listing documents"""
        # Add some documents
        doc_ids = ["doc-1", "doc-2"]
        for doc_id in doc_ids:
            mock_redis.lpush(f"site_documents:{mock_wp_site.id}", doc_id)
            doc_data = {
                "document_id": doc_id,
                "wordpress_site_id": mock_wp_site.id,
                "filename": f"test-{doc_id}.pdf",
                "content_type": "application/pdf",
                "size": 1024,
                "checksum": "abc123",
                "uploaded_at": datetime.utcnow().isoformat(),
                "metadata": {},
                "status": "uploaded"
            }
            mock_redis.setex(f"document:{mock_wp_site.id}:{doc_id}", 3600, json.dumps(doc_data))
        
        response = client.get(
            "/api/v1/documents/",
            headers=auth_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["filename"] in ["test-doc-1.pdf", "test-doc-2.pdf"]
        

class TestLiveKitProxy:
    """Test LiveKit proxy endpoints"""
    
    @patch('app.api.v1.wordpress_sites.validate_wordpress_auth', mock_validate_auth)
    @patch.object(livekit_proxy, 'get_client_service')
    def test_create_room(self, mock_client_svc, auth_headers):
        """Test creating a LiveKit room"""
        # Setup mock
        mock_client_svc.return_value = AsyncMock()
        mock_client_svc.return_value.get_client = mock_get_client
        
        response = client.post(
            "/api/v1/livekit/rooms/create",
            headers=auth_headers,
            json={
                "room_name": "test-room-123",
                "max_participants": 2,
                "metadata": {"purpose": "support"}
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["room_name"] == "test-room-123"
        assert "token" in data
        assert data["url"] == "wss://test.livekit.cloud"
        
    @patch('app.api.v1.wordpress_sites.validate_wordpress_auth', mock_validate_auth)
    @patch.object(livekit_proxy, 'get_client_service')
    def test_generate_room_token(self, mock_client_svc, auth_headers):
        """Test generating a room token"""
        # Setup mock
        mock_client_svc.return_value = AsyncMock()
        mock_client_svc.return_value.get_client = mock_get_client
        
        response = client.post(
            "/api/v1/livekit/rooms/token",
            headers=auth_headers,
            json={
                "room_name": "test-room-123",
                "participant_name": "John Doe",
                "participant_identity": "user-123",
                "can_publish": True,
                "can_subscribe": True
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "token" in data
        assert "url" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])