from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Dict, Any, Optional
import jwt
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Security scheme
security = HTTPBearer()

# Admin JWT secret (in production, this would be from environment)
ADMIN_JWT_SECRET = os.getenv("ADMIN_JWT_SECRET", "your-admin-secret-key")
ADMIN_JWT_ALGORITHM = "HS256"

# Hardcoded admin users for now (in production, use database)
ADMIN_USERS = {
    "admin": {
        "username": "admin",
        "password_hash": "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW",  # "secret"
        "role": "superadmin"
    }
}

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token for admin"""
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=8)  # 8 hour sessions
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, ADMIN_JWT_SECRET, algorithm=ADMIN_JWT_ALGORITHM)
    return encoded_jwt

def verify_token(token: str) -> Dict[str, Any]:
    """Verify and decode JWT token"""
    try:
        payload = jwt.decode(token, ADMIN_JWT_SECRET, algorithms=[ADMIN_JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def get_current_admin_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """Get current admin user from JWT token"""
    
    # Verify token
    payload = verify_token(credentials.credentials)
    username = payload.get("sub")
    
    # Get user info
    user = ADMIN_USERS.get(username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return {
        "username": user["username"],
        "role": user["role"],  # superadmin maps to platform-wide
        "authenticated_at": datetime.utcnow().isoformat()
    }

async def get_current_admin_user_optional(
    request: Request
) -> Optional[Dict[str, Any]]:
    """Get current admin user, return None if not authenticated"""
    try:
        # Try to get token from Authorization header
        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        
        token = auth_header.split(" ")[1]
        payload = verify_token(token)
        username = payload.get("sub")
        
        user = ADMIN_USERS.get(username)
        if user is None:
            return None
        
        return {
            "username": user["username"],
            "role": user["role"],
            "authenticated_at": datetime.utcnow().isoformat()
        }
    except:
        return None

def require_admin_role(required_role: str = "admin"):
    """Decorator to require specific admin role"""
    def role_checker(user: Dict[str, Any] = Depends(get_current_admin_user)) -> Dict[str, Any]:
        user_role = user.get("role", "")
        
        # Superadmin can access everything
        if user_role == "superadmin":
            return user
        
        # Check specific role
        if user_role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {required_role}"
            )
        
        return user
    
    return role_checker

# For simple HTML form authentication (for login page)
async def authenticate_admin_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Authenticate admin user with username/password"""
    user = ADMIN_USERS.get(username)
    if not user:
        return None
    
    # In production, use proper password hashing (bcrypt)
    # For now, simple comparison
    if password == "secret":  # This would be: bcrypt.checkpw(password.encode(), user["password_hash"].encode())
        return {
            "username": user["username"],
            "role": user["role"]
        }
    
    return None

# Session-based auth for HTML forms (alternative to JWT)
async def get_admin_user_from_session(request: Request) -> Optional[Dict[str, Any]]:
    """Get admin user from session (for HTML form auth)"""
    # This would check session storage or signed cookies
    # For now, return None (not implemented)
    return None

# Supabase-based admin authentication
async def get_admin_user(request: Request) -> Dict[str, Any]:
    """Get admin user using Supabase authentication"""
    
    def _dev_bypass_allowed(req: Request) -> bool:
        if os.getenv("DEVELOPMENT_MODE", "false").lower() != "true":
            return False
        client = getattr(req, "client", None)
        host = getattr(client, "host", None)
        allowed_hosts = {h.strip() for h in os.getenv("DEV_BYPASS_ALLOWED_HOSTS", "127.0.0.1,::1,localhost").split(',') if h.strip()}
        if host and host in allowed_hosts:
            return True
        return False

    # PREFER cookie token over header token - cookie is more reliably refreshed by browser
    # This fixes issues where JavaScript sends stale localStorage tokens in headers
    cookie_token = request.cookies.get("admin_token")
    auth_header = request.headers.get("authorization")
    header_token = auth_header.split(" ")[1] if auth_header and auth_header.startswith("Bearer ") else None

    # Use cookie token first if available, fall back to header
    if cookie_token:
        token = cookie_token
        token_source = "cookie"
    elif header_token:
        token = header_token
        token_source = "header"
    else:
        if _dev_bypass_allowed(request):
            return {
                "user_id": "dev-admin",
                "email": "admin@autonomite.ai",
                "role": "superadmin",
                "first_name": "Dev",
                "full_name": "Dev Admin",
                "auth_method": "development",
                "authenticated_at": datetime.utcnow().isoformat()
            }
        logger.warning(f"[AUTH] No token found for {request.url.path}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required - please login",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Log token info for debugging (first/last 10 chars only for security)
    token_preview = f"{token[:10]}...{token[-10:]}" if len(token) > 20 else "short-token"
    logger.info(f"[AUTH] {request.url.path} - Token from {token_source}: {token_preview}")

    # Check for development token
    if token == "dev-token":
        if not _dev_bypass_allowed(request):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Development token is not permitted from this host",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return {
            "user_id": "dev-admin",
            "email": "admin@autonomite.ai",
            "role": "superadmin",
            "first_name": "Dev",
            "full_name": "Dev Admin",
            "auth_method": "development",
            "authenticated_at": datetime.utcnow().isoformat()
        }
    
    try:
        # Verify token with Supabase
        from app.integrations.supabase_client import supabase_manager
        # Ensure initialized
        if not getattr(supabase_manager, "_initialized", False):
            try:
                import asyncio
                if asyncio.get_event_loop().is_running():
                    # In async context
                    await supabase_manager.initialize()
                else:
                    asyncio.run(supabase_manager.initialize())
            except Exception:
                pass
        # Use Supabase to verify the JWT token (use auth_client with anon key, not admin)
        logger.info(f"[AUTH] Calling Supabase auth.get_user for {request.url.path}")
        user_response = supabase_manager.auth_client.auth.get_user(token)
        logger.info(f"[AUTH] Supabase response for {request.url.path}: user={'present' if user_response.user else 'None'}")

        if user_response.user:
            user = user_response.user
            meta = getattr(user, 'user_metadata', None) or {}
            full_name = (
                meta.get('full_name')
                or meta.get('name')
                or meta.get('display_name')
                or (
                    meta.get('custom_claims', {}).get('full_name')
                    if isinstance(meta.get('custom_claims'), dict)
                    else None
                )
            )
            first_name = meta.get('first_name')
            if not first_name and isinstance(full_name, str) and full_name.strip():
                first_name = full_name.strip().split()[0]
            if not first_name:
                nickname = meta.get('nickname')
                if isinstance(nickname, str) and nickname.strip():
                    first_name = nickname.strip()
            if not first_name and user.email:
                first_name = user.email.split('@')[0]
            # Determine role using RBAC if available, else fallback to user_metadata
            role = "subscriber"
            try:
                admin_client = supabase_manager.admin_client
                # Check platform super_admin role via RBAC
                try:
                    role_row = (
                        admin_client.table('roles')
                        .select('id')
                        .eq('key', 'super_admin')
                        .single()
                        .execute()
                        .data
                    )
                    super_admin_role_id = role_row.get('id') if role_row else None
                except Exception:
                    super_admin_role_id = None
                if super_admin_role_id:
                    pr = (
                        admin_client.table('platform_role_memberships')
                        .select('role_id')
                        .eq('user_id', user.id)
                        .eq('role_id', super_admin_role_id)
                        .execute()
                        .data
                    )
                    if pr:
                        role = 'superadmin'
                # If not superadmin, check metadata and tenant assignments
                if role != 'superadmin':
                    meta = getattr(user, 'user_metadata', None) or {}
                    platform_role = (meta.get('platform_role') or '').lower() if isinstance(meta, dict) else ''
                    if platform_role == 'super_admin':
                        role = 'superadmin'
                    else:
                        ta = meta.get('tenant_assignments') if isinstance(meta, dict) else None
                        if isinstance(ta, dict):
                            admin_ids = ta.get('admin_client_ids') or []
                            role = 'admin' if admin_ids else 'subscriber'
                        else:
                            role = 'subscriber'
            except Exception:
                # Safe fallback to metadata only
                meta = getattr(user, 'user_metadata', None) or {}
                platform_role = (meta.get('platform_role') or '').lower() if isinstance(meta, dict) else ''
                if platform_role == 'super_admin':
                    role = 'superadmin'
                else:
                    ta = meta.get('tenant_assignments') if isinstance(meta, dict) else None
                    if isinstance(ta, dict) and (ta.get('admin_client_ids') or []):
                        role = 'admin'
                    else:
                        role = 'subscriber'

            def _normalize_ids(raw_ids: Any) -> list:
                if isinstance(raw_ids, (list, tuple, set)):
                    return [str(i) for i in raw_ids if i]
                if isinstance(raw_ids, str) and raw_ids.strip():
                    return [raw_ids.strip()]
                return []

            meta_dict = meta if isinstance(meta, dict) else {}
            tenant_assignments_raw = meta_dict.get('tenant_assignments') if isinstance(meta_dict, dict) else {}
            tenant_assignments: Dict[str, Any] = {}
            if isinstance(tenant_assignments_raw, dict):
                tenant_assignments = {
                    'admin_client_ids': _normalize_ids(tenant_assignments_raw.get('admin_client_ids')),
                    'subscriber_client_ids': _normalize_ids(tenant_assignments_raw.get('subscriber_client_ids')),
                }

            if 'admin_client_ids' not in tenant_assignments:
                tenant_assignments['admin_client_ids'] = []
            if 'subscriber_client_ids' not in tenant_assignments:
                tenant_assignments['subscriber_client_ids'] = []

            visible_client_ids = []
            if role == 'admin':
                visible_client_ids = tenant_assignments.get('admin_client_ids', [])
            elif role != 'superadmin':
                visible_client_ids = tenant_assignments.get('subscriber_client_ids', [])

            # Determine if user is Adventurer-only (for UI customization)
            is_adventurer_only = False
            user_tier = None
            primary_client_id = None
            if role == 'admin' and visible_client_ids and len(visible_client_ids) == 1:
                # Single-client admin - check their client's tier
                primary_client_id = visible_client_ids[0]
                try:
                    client_result = admin_client.table('clients').select('tier').eq('id', primary_client_id).single().execute()
                    if client_result.data:
                        user_tier = client_result.data.get('tier')
                        is_adventurer_only = (user_tier == 'adventurer')
                except Exception as tier_err:
                    logger.warning(f"Failed to fetch client tier for {primary_client_id}: {tier_err}")

            return {
                "user_id": user.id,
                "email": user.email,
                "role": role,
                "first_name": first_name,
                "full_name": full_name,
                "auth_method": "supabase",
                "authenticated_at": datetime.utcnow().isoformat(),
                "tenant_assignments": tenant_assignments,
                "visible_client_ids": visible_client_ids,
                "is_super_admin": role == 'superadmin',
                "is_adventurer_only": is_adventurer_only,
                "user_tier": user_tier,
                "primary_client_id": primary_client_id,
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )
            
    except Exception as e:
        # For development, allow bypass only from explicitly trusted hosts
        if _dev_bypass_allowed(request):
            return {
                "user_id": "dev-admin",
                "email": "dev@autonomite.ai",
                "role": "superadmin",
                "first_name": "Dev",
                "full_name": "Dev Admin",
                "auth_method": "development"
            }
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed - please login again",
            headers={"WWW-Authenticate": "Bearer"},
        )
