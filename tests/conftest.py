"""
Pytest configuration and fixtures for Autonomite Agent Platform tests
"""
import asyncio
import pytest
from typing import AsyncGenerator, Generator
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import redis

from app.main import app
from app.core.dependencies import get_db, get_redis_client


# Test database URL
TEST_DATABASE_URL = "postgresql://test:test@localhost/autonomite_test"


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_db():
    """Create test database connection"""
    engine = create_engine(TEST_DATABASE_URL)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Create tables
    # Base.metadata.create_all(bind=engine)
    
    yield TestingSessionLocal()
    
    # Drop tables
    # Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session")
def test_redis():
    """Create test Redis connection"""
    return redis.Redis(host="localhost", port=6379, db=1, decode_responses=True)


@pytest.fixture
def client(test_db, test_redis) -> TestClient:
    """Create test client with dependency overrides"""
    
    def override_get_db():
        try:
            yield test_db
        finally:
            test_db.close()
    
    def override_get_redis():
        return test_redis
    
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_client] = override_get_redis
    
    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    """Create authentication headers for testing"""
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def sample_agent_data():
    """Sample agent data for testing"""
    return {
        "name": "Test Agent",
        "slug": "test-agent",
        "description": "A test agent",
        "system_prompt": "You are a test agent.",
        "voice_settings": {
            "provider": "openai",
            "voice_id": "alloy",
            "temperature": 0.7
        },
        "enabled": True
    }


@pytest.fixture
def sample_client_data():
    """Sample client data for testing"""
    return {
        "id": "test-client",
        "name": "Test Client",
        "domain": "testclient.com",
        "settings": {
            "supabase": {
                "url": "https://test.supabase.co",
                "anon_key": "test-anon-key",
                "service_role_key": "test-service-key"
            },
            "livekit": {
                "server_url": "wss://test.livekit.cloud",
                "api_key": "test-api-key",
                "api_secret": "test-secret"
            }
        }
    }