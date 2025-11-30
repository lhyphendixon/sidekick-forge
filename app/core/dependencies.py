"""
Dependencies for the main platform - Supabase only (no Redis)
"""
import os

# Import pure Supabase services (no Redis)
from app.services.client_service_supabase import ClientService
from app.services.agent_service_supabase import AgentService
from fastapi import Depends, HTTPException, status
from typing import Optional
from app.middleware.auth import get_current_auth
from app.models.user import AuthContext
from app.permissions.rbac import has_permission

def get_db():
    """Relational DB access is not configured in this deployment."""
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Database access not configured; override get_db in tests.",
    )

def get_redis_client():
    """Redis is not part of the Supabase-only stack."""
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Redis access not configured; override get_redis_client in tests.",
    )

def get_client_service() -> ClientService:
    """Get client service for Sidekick Forge platform database"""
    # Use Sidekick Forge platform database credentials from environment
    from app.config import settings
    
    return ClientService(settings.supabase_url, settings.supabase_service_role_key)

def get_agent_service() -> AgentService:
    """Get agent service (Supabase only)"""
    client_service = get_client_service()
    return AgentService(client_service)


async def require_permission(
    permission_key: str,
    client_id_param: Optional[str] = None,
):
    """Factory that returns a dependency enforcing a permission.

    Usage:
        @router.get(..., dependencies=[Depends(require_permission('agents:write', client_id_param='client_id'))])
    """
    async def _checker(auth: AuthContext = Depends(get_current_auth), client_id: Optional[str] = None):
        # Allow platform_admin via platform perms
        cid = client_id or client_id_param
        allowed = await has_permission(str(auth.user_id), permission_key, cid)
        if not allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return True
    return _checker
