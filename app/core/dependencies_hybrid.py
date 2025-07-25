"""
Hybrid dependencies - Original platform + Project-based client discovery
"""
import os
import redis
from typing import Generator

# Original services for the main platform
from app.services.client_service_hybrid import ClientService
from app.services.agent_service_supabase import AgentService

# New project service for admin interface
from app.services.supabase_project_service import SupabaseProjectService

def get_redis_client() -> redis.Redis:
    """Get Redis client for the main platform"""
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        decode_responses=True
    )

def get_client_service() -> ClientService:
    """Get original client service for main platform"""
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    redis_client = get_redis_client()
    
    return ClientService(supabase_url, supabase_key, redis_client)

def get_agent_service() -> AgentService:
    """Get original agent service for main platform"""
    client_service = get_client_service()
    return AgentService(client_service)

# New function for admin interface only
def get_project_service() -> SupabaseProjectService:
    """Get project service for admin interface client discovery"""
    access_token = os.getenv("SUPABASE_ACCESS_TOKEN")
    organization_id = os.getenv("SUPABASE_ORG_ID")
    
    return SupabaseProjectService(access_token, organization_id)