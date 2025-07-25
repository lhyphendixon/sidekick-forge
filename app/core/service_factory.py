"""
Service factory to switch between Redis-cached and Supabase-only implementations
"""
import os
from typing import Union
from app.config import settings

# Import both implementations
from app.services.client_service_hybrid import ClientService as ClientServiceHybrid
from app.services.client_service_supabase import ClientService as ClientServiceSupabase
from app.services.agent_service import AgentService as AgentServiceHybrid
from app.services.agent_service_supabase import AgentService as AgentServiceSupabase
from app.services.wordpress_site_service import WordPressSiteService as WordPressSiteServiceHybrid
from app.services.wordpress_site_service_supabase import WordPressSiteService as WordPressSiteServiceSupabase

# Import Redis dependency
from app.core.dependencies import get_redis_client


def use_supabase_only() -> bool:
    """Check if we should use Supabase-only mode"""
    return os.getenv("USE_SUPABASE_ONLY", "false").lower() == "true"


def get_client_service(redis_client=None) -> Union[ClientServiceHybrid, ClientServiceSupabase]:
    """Get the appropriate client service based on configuration"""
    supabase_url = os.getenv("SUPABASE_URL", settings.supabase_url)
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", settings.supabase_service_role_key)
    
    if use_supabase_only():
        return ClientServiceSupabase(supabase_url, supabase_key)
    else:
        # For hybrid mode, we need Redis client
        if redis_client is None:
            # This is a synchronous context, we can't use Depends here
            import redis
            redis_client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                decode_responses=True
            )
        return ClientServiceHybrid(supabase_url, supabase_key, redis_client)


def get_agent_service(client_service=None, redis_client=None) -> Union[AgentServiceHybrid, AgentServiceSupabase]:
    """Get the appropriate agent service based on configuration"""
    if client_service is None:
        client_service = get_client_service(redis_client)
    
    if use_supabase_only():
        return AgentServiceSupabase(client_service)
    else:
        # For hybrid mode, we need Redis client
        if redis_client is None:
            import redis
            redis_client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                decode_responses=True
            )
        return AgentServiceHybrid(client_service, redis_client)


def get_wordpress_site_service(redis_client=None) -> Union[WordPressSiteServiceHybrid, WordPressSiteServiceSupabase]:
    """Get the appropriate WordPress site service based on configuration"""
    supabase_url = os.getenv("SUPABASE_URL", settings.supabase_url)
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", settings.supabase_service_role_key)
    
    if use_supabase_only():
        return WordPressSiteServiceSupabase(supabase_url, supabase_key)
    else:
        # For hybrid mode, we need Redis client
        if redis_client is None:
            import redis
            redis_client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                decode_responses=True
            )
        return WordPressSiteServiceHybrid(supabase_url, supabase_key, redis_client)