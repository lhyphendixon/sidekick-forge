"""
Core dependencies using Supabase project discovery (no clients table needed)
"""
import os
from typing import Generator

from app.services.supabase_project_service import SupabaseProjectService


def get_project_service() -> SupabaseProjectService:
    """Get project service using Supabase Management API"""
    access_token = os.getenv("SUPABASE_ACCESS_TOKEN")
    organization_id = os.getenv("SUPABASE_ORG_ID")
    
    if not access_token:
        # For development, show how to get the token
        print("⚠️  No SUPABASE_ACCESS_TOKEN found. Generate one at:")
        print("   https://supabase.com/dashboard/account/tokens")
        print("   Then set: export SUPABASE_ACCESS_TOKEN='your-token'")
    
    return SupabaseProjectService(access_token, organization_id)


# Compatibility functions for existing code
def get_client_service():
    """Compatibility function - returns project service"""
    return get_project_service()


def get_agent_service():
    """Compatibility function - returns project service"""
    return get_project_service()


def get_redis_client():
    """Compatibility function - returns None since we're not using Redis"""
    return None