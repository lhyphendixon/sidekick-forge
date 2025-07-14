"""
Core dependencies for the application using Supabase only
"""
import os
from typing import Generator

from app.services.client_service_supabase_enhanced import ClientService
from app.services.agent_service_supabase import AgentService


def get_client_service() -> ClientService:
    """Get client service using Supabase"""
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    
    return ClientService(supabase_url, supabase_key)


def get_agent_service() -> AgentService:
    """Get agent service using Supabase"""
    client_service = get_client_service()
    return AgentService(client_service)


# Compatibility functions for existing code
def get_redis_client():
    """Compatibility function - returns None since we're not using Redis"""
    return None