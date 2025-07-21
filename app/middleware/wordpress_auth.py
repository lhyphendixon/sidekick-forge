"""WordPress authentication middleware"""
from typing import Optional
from fastapi import Request, HTTPException, Header
from app.models.wordpress_site import WordPressSite
from app.services.wordpress_site_service import WordPressSiteService
import logging

logger = logging.getLogger(__name__)


class WordPressAuthMiddleware:
    """Middleware to authenticate WordPress sites"""
    
    def __init__(self, wordpress_service: WordPressSiteService):
        self.wordpress_service = wordpress_service
        
    async def __call__(
        self,
        request: Request,
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
        site = self.wordpress_service.validate_api_key(api_key)
        if not site:
            raise HTTPException(status_code=401, detail="Invalid API key")
            
        # Add site info to request state for downstream use
        request.state.wordpress_site = site
        
        return site


def get_current_wordpress_site(request: Request) -> WordPressSite:
    """Get the current authenticated WordPress site from request state"""
    if not hasattr(request.state, "wordpress_site"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return request.state.wordpress_site