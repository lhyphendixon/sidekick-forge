"""Admin dependencies for authentication and authorization."""
from fastapi import Request, HTTPException
from typing import Optional


async def get_admin_user(request: Request) -> Optional[dict]:
    """
    Get the current admin user from session.
    
    For now, this is a placeholder that allows access.
    In production, this should check proper authentication.
    """
    # TODO: Implement proper admin authentication
    return {"username": "admin", "is_admin": True}


async def require_admin(request: Request) -> dict:
    """
    Require admin authentication for routes.
    
    Raises HTTPException if user is not authenticated as admin.
    """
    user = await get_admin_user(request)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user