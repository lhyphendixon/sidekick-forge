"""
Original dependencies for the main platform
"""
import os
import redis
from typing import Generator

# Check if these services exist and restore them
try:
    from app.services.client_service_hybrid import ClientService
    from app.services.agent_service_supabase import AgentService
except ImportError:
    # Fallback to enhanced versions if hybrid not available
    from app.services.client_service_supabase_enhanced import ClientService
    from app.services.agent_service_supabase import AgentService

def get_redis_client() -> redis.Redis:
    """Get Redis client"""
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"), 
        port=int(os.getenv("REDIS_PORT", 6379)),
        decode_responses=True
    )

def get_client_service() -> ClientService:
    """Get client service"""
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    
    # Try to get Redis client, if it fails, use None
    try:
        redis_client = get_redis_client()
    except:
        redis_client = None
    
    return ClientService(supabase_url, supabase_key, redis_client)

def get_agent_service() -> AgentService:
    """Get agent service"""
    client_service = get_client_service()
    return AgentService(client_service)