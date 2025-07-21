"""
Dependencies for the main platform - Supabase only (no Redis)
"""
import os

# Import pure Supabase services (no Redis)
from app.services.client_service_supabase import ClientService
from app.services.agent_service_supabase import AgentService

def get_client_service() -> ClientService:
    """Get client service (Supabase only)"""
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    
    return ClientService(supabase_url, supabase_key)

def get_agent_service() -> AgentService:
    """Get agent service (Supabase only)"""
    client_service = get_client_service()
    return AgentService(client_service)