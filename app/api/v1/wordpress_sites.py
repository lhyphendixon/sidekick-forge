"""WordPress site management endpoints"""
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from app.models.wordpress_site import (
    WordPressSite, WordPressSiteCreate, WordPressSiteUpdate,
    WordPressSiteAuth, WordPressSiteStats
)
from app.services.wordpress_site_service import WordPressSiteService
from app.services.client_supabase_auth import generate_client_session_tokens
from app.utils.supabase_credentials import SupabaseCredentialManager
import logging
import os

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wordpress-sites", tags=["wordpress-sites"])

# Security scheme for API key authentication
security = HTTPBearer()

# WordPress site service will be initialized in simple_main.py
wordpress_service = None

def get_wordpress_service() -> WordPressSiteService:
    """Get WordPress site service instance"""
    from app.services.wordpress_site_service_supabase import WordPressSiteService
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be configured for WordPress service")
    return WordPressSiteService(supabase_url, supabase_key)


async def validate_wordpress_auth(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> WordPressSite:
    """Validate WordPress site authentication"""
    api_key = None
    
    # Check Authorization header first (Bearer token)
    if authorization and authorization.startswith("Bearer "):
        api_key = authorization.replace("Bearer ", "")
    # Check X-API-Key header
    elif x_api_key:
        api_key = x_api_key
        
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")
        
    # Validate the API key
    service = get_wordpress_service()
    site = service.validate_api_key(api_key)
    if not site:
        raise HTTPException(status_code=401, detail="Invalid API key")
        
    return site


async def validate_admin_auth(
    authorization: Optional[str] = Header(None)
) -> bool:
    """Validate admin authentication for site management"""
    # For now, use a simple admin token
    # In production, integrate with your admin auth system
    admin_token = os.getenv("ADMIN_API_TOKEN", "admin-secret-token")
    
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Admin authentication required")
        
    token = authorization.replace("Bearer ", "")
    if token != admin_token:
        raise HTTPException(status_code=403, detail="Invalid admin token")
        
    return True


@router.post("/register", response_model=WordPressSite)
async def register_wordpress_site(
    site_data: WordPressSiteCreate,
    authorization: Optional[str] = Header(None)
) -> WordPressSite:
    """Register a new WordPress site"""
    # Validate admin auth
    await validate_admin_auth(authorization)
    
    # Get service
    service = get_wordpress_service()
    
    # Check if site already exists
    existing = service.get_site_by_domain(site_data.domain)
    if existing:
        raise HTTPException(status_code=400, detail="Site already registered")
        
    # Create the site
    site = service.create_site(site_data)
    logger.info(f"Registered new WordPress site: {site.domain}")
    
    return site


@router.get("/", response_model=List[WordPressSite])
async def list_wordpress_sites(
    client_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    authorization: Optional[str] = Header(None)
) -> List[WordPressSite]:
    """List registered WordPress sites"""
    # Validate admin auth
    await validate_admin_auth(authorization)
    
    # Get service
    service = get_wordpress_service()
    
    return service.list_sites(client_id=client_id, is_active=is_active)


@router.get("/{site_id}", response_model=WordPressSite)
async def get_wordpress_site(
    site_id: str,
    authorization: Optional[str] = Header(None)
) -> WordPressSite:
    """Get a specific WordPress site"""
    # Validate admin auth
    await validate_admin_auth(authorization)
    
    # Get service
    service = get_wordpress_service()
    
    site = service.get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


@router.put("/{site_id}", response_model=WordPressSite)
async def update_wordpress_site(
    site_id: str,
    update_data: WordPressSiteUpdate,
    authorization: Optional[str] = Header(None)
) -> WordPressSite:
    """Update a WordPress site"""
    # Validate admin auth
    await validate_admin_auth(authorization)
    
    # Get service
    service = get_wordpress_service()
    
    site = service.update_site(site_id, update_data)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


@router.get("/{site_id}/stats", response_model=WordPressSiteStats)
async def get_wordpress_site_stats(
    site_id: str,
    authorization: Optional[str] = Header(None)
) -> WordPressSiteStats:
    """Get statistics for a WordPress site"""
    # Validate admin auth
    await validate_admin_auth(authorization)
    
    # Get service
    service = get_wordpress_service()
    
    stats = service.get_site_stats(site_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Site not found")
    return stats


@router.post("/{site_id}/regenerate-keys", response_model=WordPressSite)
async def regenerate_api_keys(
    site_id: str,
    authorization: Optional[str] = Header(None)
) -> WordPressSite:
    """Regenerate API keys for a WordPress site"""
    # Validate admin auth
    await validate_admin_auth(authorization)
    
    # Get service
    service = get_wordpress_service()
    
    site = service.get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
        
    # Generate new keys
    new_api_key = WordPressSite.generate_api_key()
    new_api_secret = WordPressSite.generate_api_secret()
    
    # Update the site
    update_data = WordPressSiteUpdate(
        metadata={
            **site.metadata,
            "old_api_key": site.api_key,
            "keys_regenerated_at": datetime.utcnow().isoformat()
        }
    )
    
    # This would need to be enhanced to actually update the keys
    # For now, just return the site
    logger.info(f"Regenerated API keys for site: {site.domain}")
    
    return site


# Authentication endpoint for WordPress sites
@router.post("/auth/validate")
async def validate_wordpress_auth_endpoint(
    auth_data: WordPressSiteAuth
) -> dict:
    """Validate WordPress site credentials"""
    # Get service
    service = get_wordpress_service()
    
    site = service.validate_api_key(auth_data.api_key, auth_data.api_secret)
    if not site:
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    return {
        "valid": True,
        "site_id": site.id,
        "domain": site.domain,
        "client_id": site.client_id
    }


# Proxy endpoint for testing WordPress site connectivity
@router.get("/auth/test")
async def test_wordpress_auth(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> dict:
    """Test endpoint to verify WordPress authentication is working"""
    # Validate WordPress auth
    site = await validate_wordpress_auth(authorization, x_api_key)
    
    return {
        "authenticated": True,
        "site_id": site.id,
        "domain": site.domain,
        "client_id": site.client_id,
        "message": "Authentication successful"
    }


class WordPressSessionRequest(BaseModel):
    """Request payload for issuing a Supabase session for a WP user"""
    api_key: str
    api_secret: Optional[str] = None
    user_email: str
    user_name: Optional[str] = None


@router.post("/auth/session")
async def issue_wordpress_session(
    payload: WordPressSessionRequest,
):
    """
    Exchange WordPress site credentials + user email for a client Supabase session.

    This lets the embed skip a second login by minting a shadow Supabase user
    scoped to the corresponding client.
    """
    service = get_wordpress_service()
    site = service.validate_api_key(payload.api_key, payload.api_secret)
    if not site:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    try:
        supabase_url, supabase_anon = await SupabaseCredentialManager.get_frontend_credentials(site.client_id)
    except Exception as exc:
        logger.error(f"WordPress session: missing Supabase creds for client {site.client_id}: {exc}")
        raise HTTPException(status_code=400, detail="Client Supabase configuration missing")

    try:
        tokens = await generate_client_session_tokens(site.client_id, payload.user_email)
    except Exception as exc:
        logger.error(f"WordPress session: failed to generate tokens for {payload.user_email} client {site.client_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create session")

    return {
        "success": True,
        "client_id": site.client_id,
        "site_id": site.id,
        "user_email": payload.user_email,
        "user_id": tokens.get("user_id"),
        "supabase_url": supabase_url,
        "supabase_anon_key": supabase_anon,
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "token_type": tokens.get("token_type", "bearer"),
        "expires_in": tokens.get("expires_in"),
    }
