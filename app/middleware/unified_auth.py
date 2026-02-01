"""Unified authentication middleware for WordPress and admin requests"""
from typing import Optional, Union, Callable
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging
import os

logger = logging.getLogger(__name__)

# Bearer token security scheme
security = HTTPBearer(auto_error=False)


class UnifiedAuthMiddleware:
    """Middleware to handle both WordPress API key and admin token authentication"""
    
    def __init__(self, app):
        self.app = app
        self.admin_token = os.getenv("ADMIN_AUTH_TOKEN")
        if not self.admin_token:
            raise ValueError("ADMIN_AUTH_TOKEN environment variable is required")
        
    async def __call__(self, request: Request, call_next):
        """Process request and handle authentication"""
        path = request.url.path
        
        # Skip auth for public endpoints
        public_paths = ["/", "/health", "/docs", "/redoc", "/openapi.json", "/static"]
        if any(path.startswith(p) for p in public_paths):
            return await call_next(request)
            
        # API endpoints require authentication
        if path.startswith("/api/"):
            # WordPress API endpoints use API key
            if any(path.startswith(p) for p in ["/api/v1/livekit", "/api/v1/conversations", 
                                                 "/api/v1/documents", "/api/v1/text-chat"]):
                # These are handled by the endpoint-specific auth
                pass
            # Admin API endpoints use bearer token
            elif path.startswith("/api/v1/wordpress-sites/register"):
                # Admin-only endpoint
                auth_header = request.headers.get("authorization")
                if not auth_header or not self._validate_admin_token(auth_header):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid admin credentials"}
                    )
        
        # Admin dashboard requires auth
        if path.startswith("/admin") and path != "/admin/login":
            # Check for session cookie or bearer token
            session_token = request.cookies.get("admin_session")
            auth_header = request.headers.get("authorization")
            
            if not session_token and not auth_header:
                # Redirect to login
                if request.method == "GET":
                    return RedirectResponse(url="/admin/login", status_code=303)
                else:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Authentication required"}
                    )
                    
            # Validate session or token
            if session_token and not self._validate_session(session_token):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or expired session"}
                )
            elif auth_header and not self._validate_admin_token(auth_header):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid admin credentials"}
                )
        
        response = await call_next(request)
        return response
        
    def _validate_admin_token(self, auth_header: str) -> bool:
        """Validate admin bearer token"""
        try:
            scheme, token = auth_header.split()
            if scheme.lower() != "bearer":
                return False
            return token == self.admin_token
        except:
            return False
            
    def _validate_session(self, session_token: str) -> bool:
        """Validate admin session cookie"""
        # In production, this would check Redis or a session store
        # For now, simple validation
        return session_token == "admin-session-valid"


def get_current_admin(authorization: Optional[str] = None) -> bool:
    """Dependency to validate admin authentication"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization")

    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authentication scheme")

        admin_token = os.getenv("ADMIN_AUTH_TOKEN")
        if not admin_token:
            raise HTTPException(status_code=500, detail="Server misconfiguration: ADMIN_AUTH_TOKEN not set")
        if token != admin_token:
            raise HTTPException(status_code=401, detail="Invalid admin credentials")

        return True
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header")


from fastapi.responses import JSONResponse, RedirectResponse


async def validate_admin_auth(authorization: Optional[str]) -> bool:
    """Validate admin authorization header"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing admin authorization")

    admin_token = os.getenv("ADMIN_AUTH_TOKEN")
    if not admin_token:
        raise HTTPException(status_code=500, detail="Server misconfiguration: ADMIN_AUTH_TOKEN not set")

    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer" or token != admin_token:
            raise HTTPException(status_code=401, detail="Invalid admin credentials")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization format")

    return True