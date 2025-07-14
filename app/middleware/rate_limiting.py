from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import redis.asyncio as redis
import time
import logging
from typing import Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for rate limiting requests"""
    
    def __init__(self, app):
        super().__init__(app)
        self.redis_client = None
        self._connect_redis()
    
    def _connect_redis(self):
        """Initialize Redis connection"""
        try:
            self.redis_client = redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.redis_client = None
    
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path in ["/health", "/health/detailed"]:
            return await call_next(request)
        
        # If Redis is not available, skip rate limiting
        if not self.redis_client:
            return await call_next(request)
        
        try:
            # Get client identifier
            client_id = self._get_client_identifier(request)
            
            # Check rate limits
            allowed, retry_after = await self._check_rate_limit(client_id, request.url.path)
            
            if not allowed:
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "success": False,
                        "error": {
                            "error": "Rate Limit Exceeded",
                            "message": f"Too many requests. Please try again in {retry_after} seconds",
                            "code": "RATE_LIMIT_EXCEEDED"
                        }
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(settings.rate_limit_per_minute),
                        "X-RateLimit-Remaining": "0"
                    }
                )
            
            # Process request
            response = await call_next(request)
            
            # Add rate limit headers
            remaining = await self._get_remaining_requests(client_id)
            response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            
            return response
            
        except Exception as e:
            logger.error(f"Rate limiting error: {e}")
            # On error, allow request to proceed
            return await call_next(request)
    
    def _get_client_identifier(self, request: Request) -> str:
        """Get unique identifier for the client"""
        # Try to get authenticated user/site ID
        if hasattr(request.state, "auth"):
            auth = request.state.auth
            if auth.user_id:
                return f"user:{auth.user_id}"
            elif auth.site_id:
                return f"site:{auth.site_id}"
        
        # Fall back to IP address
        client_ip = request.client.host
        if "X-Forwarded-For" in request.headers:
            client_ip = request.headers["X-Forwarded-For"].split(",")[0].strip()
        
        return f"ip:{client_ip}"
    
    async def _check_rate_limit(self, client_id: str, path: str) -> Tuple[bool, int]:
        """Check if client has exceeded rate limit"""
        # Different limits for different endpoints
        limit = self._get_endpoint_limit(path)
        window = 60  # 1 minute window
        
        # Create Redis key
        key = f"rate_limit:{client_id}:{int(time.time() // window)}"
        
        try:
            # Increment counter
            current = await self.redis_client.incr(key)
            
            # Set expiry on first request
            if current == 1:
                await self.redis_client.expire(key, window)
            
            # Check limit
            if current > limit:
                ttl = await self.redis_client.ttl(key)
                return False, ttl if ttl > 0 else window
            
            return True, 0
            
        except Exception as e:
            logger.error(f"Redis error in rate limiting: {e}")
            return True, 0  # Allow on error
    
    async def _get_remaining_requests(self, client_id: str) -> int:
        """Get remaining requests for client"""
        window = 60
        key = f"rate_limit:{client_id}:{int(time.time() // window)}"
        
        try:
            current = await self.redis_client.get(key)
            if current:
                return max(0, settings.rate_limit_per_minute - int(current))
            return settings.rate_limit_per_minute
            
        except Exception:
            return settings.rate_limit_per_minute
    
    def _get_endpoint_limit(self, path: str) -> int:
        """Get rate limit for specific endpoint"""
        # Higher limits for certain endpoints
        if path.startswith("/api/v1/sessions"):
            return settings.rate_limit_per_minute * 2  # Double limit for sessions
        elif path.startswith("/api/v1/conversations/messages"):
            return settings.rate_limit_per_minute * 3  # Triple for messages
        elif path.startswith("/api/v1/documents/upload"):
            return 10  # Lower limit for uploads
        
        return settings.rate_limit_per_minute

class APIKeyRateLimiter:
    """Rate limiter for API key based authentication"""
    
    def __init__(self):
        self.redis_client = None
        self._connect_redis()
    
    def _connect_redis(self):
        """Initialize Redis connection"""
        try:
            self.redis_client = redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
    
    async def check_api_key_limit(self, api_key_hash: str) -> Tuple[bool, int]:
        """Check rate limit for API key"""
        if not self.redis_client:
            return True, 0
        
        # API keys get higher limits
        limit = settings.rate_limit_per_hour
        window = 3600  # 1 hour
        
        key = f"api_rate_limit:{api_key_hash}:{int(time.time() // window)}"
        
        try:
            current = await self.redis_client.incr(key)
            
            if current == 1:
                await self.redis_client.expire(key, window)
            
            if current > limit:
                ttl = await self.redis_client.ttl(key)
                return False, ttl if ttl > 0 else window
            
            return True, 0
            
        except Exception as e:
            logger.error(f"Redis error in API key rate limiting: {e}")
            return True, 0

# Create singleton instance
api_key_rate_limiter = APIKeyRateLimiter()