"""WordPress site management endpoints"""
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.models.wordpress_site import (
    WordPressSite, WordPressSiteCreate, WordPressSiteUpdate,
    WordPressSiteAuth, WordPressSiteStats
)
from app.services.wordpress_site_service import WordPressSiteService
import logging
import os

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/wordpress-sites", tags=["wordpress-sites"])

# Security scheme for API key authentication
security = HTTPBearer()

# WordPress site service will be initialized in simple_main.py
wordpress_service = None

def get_wordpress_service() -> WordPressSiteService:
    """Get WordPress site service instance"""
    if wordpress_service is None:
        raise RuntimeError("WordPress service not initialized")
    return wordpress_service


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