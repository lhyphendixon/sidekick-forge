from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import hashlib
import jwt
import logging
from typing import Optional, Tuple
from datetime import datetime

import os

from app.config import settings
from app.models.user import AuthContext
from app.integrations.supabase_client import supabase_manager
from app.utils.exceptions import AuthenticationError, AuthorizationError

logger = logging.getLogger(__name__)

class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Middleware for handling authentication"""
    
    # Public endpoints that don't require authentication
    PUBLIC_PATHS = [
        "/",
        "/health",
        "/health/detailed",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/embed",
        "/api/embed/",
        # Public admin pages (login, signup, docs)
        "/admin/login",
        "/admin/signup",
        "/admin/reset-password",
        "/admin/docs",
        "/api/v1/auth/signup",
        "/api/v1/auth/login",
        "/api/v1/wordpress/register",
        "/webhooks/",
        # Wizard API uses admin auth (get_admin_user) directly, not middleware
        "/api/v1/wizard",
    ]
    
    async def dispatch(self, request: Request, call_next):
        # Skip auth for public endpoints
        if self._is_public_path(request.url.path):
            response = await call_next(request)
            return response
        
        try:
            # Extract and verify authentication
            auth_context = await self._authenticate_request(request)
            
            if not auth_context or not auth_context.is_authenticated:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "success": False,
                        "error": {
                            "error": "Authentication Required",
                            "message": "Please provide valid authentication credentials",
                            "code": "AUTHENTICATION_REQUIRED"
                        }
                    }
                )
            
            # Add auth context to request state
            request.state.auth = auth_context
            
            # Log authenticated request
            logger.info(
                f"Authenticated request: {request.method} {request.url.path}",
                extra={
                    "auth_type": auth_context.type,
                    "user_id": auth_context.user_id,
                    "site_id": auth_context.site_id
                }
            )
            
            response = await call_next(request)
            return response
            
        except Exception as e:
            logger.error(f"Authentication middleware error: {e}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "success": False,
                    "error": {
                        "error": "Internal Server Error",
                        "message": "An error occurred during authentication",
                        "code": "AUTH_ERROR"
                    }
                }
            )
    
    def _is_public_path(self, path: str) -> bool:
        """Check if path is public"""
        for public_path in self.PUBLIC_PATHS:
            if path.startswith(public_path):
                return True
        return False
    
    async def _authenticate_request(self, request: Request) -> Optional[AuthContext]:
        """Authenticate the request and return auth context"""
        # Check for API key authentication (WordPress sites)
        api_key = self._extract_api_key(request)
        if api_key:
            return await self._authenticate_api_key(api_key)

        # Check for Bearer token authentication (Supabase Auth)
        bearer_token = self._extract_bearer_token(request)
        if bearer_token:
            return await self._authenticate_bearer_token(bearer_token)

        return None
    
    def _extract_api_key(self, request: Request) -> Optional[str]:
        """Extract API key from request headers"""
        # Check X-API-Key header
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return api_key
        
        # Check Authorization header for API key
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("ApiKey "):
            return auth_header[7:]
        
        return None
    
    def _extract_bearer_token(self, request: Request) -> Optional[str]:
        """Extract Bearer token from request headers"""
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
        return None
    
    async def _authenticate_api_key(self, api_key: str) -> Optional[AuthContext]:
        """Authenticate using API key (for WordPress sites)"""
        try:
            # Hash the API key for comparison
            api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            
            # Verify API key in database
            site = await supabase_manager.verify_api_key(api_key_hash)
            
            if site:
                # Update last seen timestamp
                await supabase_manager.execute_query(
                    supabase_manager.admin_client.table("wordpress_sites")
                    .update({"last_seen_at": datetime.utcnow().isoformat()})
                    .eq("id", site["id"])
                )
                
                return AuthContext(
                    type="api_key",
                    site_id=site["id"],
                    site_domain=site["domain"],
                    permissions=site.get("permissions", [])
                )
            
            return None
            
        except Exception as e:
            logger.error(f"API key authentication failed: {e}")
            return None
    
    async def _authenticate_bearer_token(self, token: str) -> Optional[AuthContext]:
        """Authenticate using Bearer token (Supabase Auth)"""
        try:
            # First try Supabase Auth verification
            user_data = await supabase_manager.verify_jwt_token(token)
            
            if user_data:
                return AuthContext(
                    type="supabase",
                    user_id=user_data["id"],
                    permissions=["all"]  # Authenticated users have full access
                )
            
            # If Supabase Auth fails, try custom JWT
            try:
                payload = jwt.decode(
                    token,
                    settings.jwt_secret_key,
                    algorithms=[settings.jwt_algorithm]
                )
                
                return AuthContext(
                    type="jwt",
                    user_id=payload.get("sub"),
                    permissions=payload.get("permissions", [])
                )
                
            except jwt.InvalidTokenError:
                pass
            
            return None
            
        except Exception as e:
            logger.error(f"Bearer token authentication failed: {e}")
            return None

# Security scheme for API documentation
security = HTTPBearer()

async def get_current_auth(request: Request) -> AuthContext:
    """Dependency to get current authentication context"""
    if not hasattr(request.state, "auth"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    return request.state.auth

async def require_user_auth(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
    """Dependency to require user authentication"""
    if not auth.is_user_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User authentication required"
        )
    return auth

async def require_site_auth(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
    """Dependency to require WordPress site authentication"""
    if not auth.is_site_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Site API key authentication required"
        )
    return auth
