"""
Dependencies for the main platform - Supabase only (no Redis)
"""
import os

# Import pure Supabase services (no Redis)
from app.services.client_service_supabase import ClientService
from app.services.agent_service_supabase import AgentService

def get_client_service() -> ClientService:
    """Get client service for Sidekick Forge platform database"""
    # Use Sidekick Forge platform database credentials from environment
    from app.config import settings
    
    return ClientService(settings.supabase_url, settings.supabase_service_role_key)

def get_agent_service() -> AgentService:
    """Get agent service (Supabase only)"""
    client_service = get_client_service()
    return AgentService(client_service)