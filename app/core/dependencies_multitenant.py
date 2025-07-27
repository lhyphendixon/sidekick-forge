"""
Dependency injection for multi-tenant Sidekick Forge Platform
"""
from functools import lru_cache
from typing import Optional

from app.services.agent_service_multitenant import AgentService
from app.services.client_service_multitenant import ClientService
from app.services.client_connection_manager import get_connection_manager


@lru_cache()
def get_agent_service() -> AgentService:
    """Get multi-tenant agent service instance"""
    return AgentService()


@lru_cache()
def get_client_service() -> ClientService:
    """Get multi-tenant client service instance"""
    return ClientService()


# For backward compatibility during migration
def get_supabase_client():
    """
    DEPRECATED: Use ClientConnectionManager instead
    
    This function is kept for backward compatibility but should not be used
    in new code. Use get_connection_manager().get_client_db_client(client_id)
    """
    import warnings
    warnings.warn(
        "get_supabase_client() is deprecated. Use ClientConnectionManager for multi-tenant access.",
        DeprecationWarning,
        stacklevel=2
    )
    # Return platform client as fallback
    return get_connection_manager().platform_client


def get_redis_client():
    """
    DEPRECATED: Redis is no longer used for primary storage
    
    Returns None for backward compatibility
    """
    return None