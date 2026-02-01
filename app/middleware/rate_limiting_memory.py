"""In-Memory Rate Limiting Middleware

This is a simple in-memory rate limiter suitable for single-instance deployments.
For multi-instance deployments, implement a database-backed rate limiter using
Supabase or another shared storage.

Note: Per project policy, Redis is not used. This in-memory implementation
provides basic protection against abuse for single-instance deployments.
"""

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import time
import logging
import os
from collections import defaultdict
from threading import Lock
from typing import Tuple

logger = logging.getLogger(__name__)


class InMemoryRateLimiter:
    """Thread-safe in-memory rate limiter using sliding window algorithm."""

    def __init__(self, default_limit: int = 60, window_seconds: int = 60):
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        self._requests = defaultdict(list)  # client_id -> list of timestamps
        self._lock = Lock()
        # Clean up old entries periodically
        self._last_cleanup = time.time()
        self._cleanup_interval = 300  # 5 minutes

    def _cleanup_old_entries(self):
        """Remove entries older than the window."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        cutoff = now - self.window_seconds
        with self._lock:
            to_delete = []
            for client_id, timestamps in self._requests.items():
                # Filter out old timestamps
                self._requests[client_id] = [t for t in timestamps if t > cutoff]
                if not self._requests[client_id]:
                    to_delete.append(client_id)
            for client_id in to_delete:
                del self._requests[client_id]
            self._last_cleanup = now

    def check_rate_limit(self, client_id: str, limit: int = None) -> Tuple[bool, int, int]:
        """
        Check if client has exceeded rate limit.

        Returns: (allowed, remaining, retry_after_seconds)
        """
        if limit is None:
            limit = self.default_limit

        now = time.time()
        cutoff = now - self.window_seconds

        # Periodic cleanup
        self._cleanup_old_entries()

        with self._lock:
            # Filter out old timestamps and add new one
            timestamps = [t for t in self._requests[client_id] if t > cutoff]

            if len(timestamps) >= limit:
                # Calculate retry after (time until oldest request expires)
                retry_after = int(timestamps[0] + self.window_seconds - now) + 1
                remaining = 0
                return False, remaining, max(1, retry_after)

            # Add current request
            timestamps.append(now)
            self._requests[client_id] = timestamps
            remaining = limit - len(timestamps)
            return True, remaining, 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware using in-memory storage.

    Enable by setting RATE_LIMIT_ENABLED=true in environment.
    Configure limits with RATE_LIMIT_PER_MINUTE (default: 60).
    """

    def __init__(self, app):
        super().__init__(app)
        self.enabled = os.getenv("RATE_LIMIT_ENABLED", "false").lower() == "true"
        self.default_limit = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
        self.limiter = InMemoryRateLimiter(
            default_limit=self.default_limit,
            window_seconds=60
        )

        # Endpoints to skip rate limiting
        self.skip_paths = [
            "/health",
            "/health/detailed",
            "/docs",
            "/redoc",
            "/openapi.json",
        ]

        if self.enabled:
            logger.info(f"Rate limiting enabled: {self.default_limit} requests/minute")
        else:
            logger.info("Rate limiting disabled (set RATE_LIMIT_ENABLED=true to enable)")

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        path = request.url.path

        # Skip rate limiting for certain paths
        if any(path.startswith(skip) for skip in self.skip_paths):
            return await call_next(request)

        # Skip rate limiting for admin paths (they have their own auth)
        if path.startswith("/admin"):
            return await call_next(request)

        try:
            # Get client identifier
            client_id = self._get_client_identifier(request)

            # Get endpoint-specific limit
            limit = self._get_endpoint_limit(path)

            # Check rate limit
            allowed, remaining, retry_after = self.limiter.check_rate_limit(client_id, limit)

            if not allowed:
                logger.warning(f"Rate limit exceeded for {client_id} on {path}")
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "success": False,
                        "error": {
                            "error": "Rate Limit Exceeded",
                            "message": f"Too many requests. Please try again in {retry_after} seconds.",
                            "code": "RATE_LIMIT_EXCEEDED"
                        }
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(time.time()) + retry_after)
                    }
                )

            # Process request
            response = await call_next(request)

            # Add rate limit headers to response
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)

            return response

        except Exception as e:
            logger.error(f"Rate limiting error: {e}")
            # On error, allow request to proceed
            return await call_next(request)

    def _get_client_identifier(self, request: Request) -> str:
        """Get unique identifier for the client."""
        # Try to get authenticated user/site ID from request state
        if hasattr(request.state, "auth"):
            auth = request.state.auth
            if hasattr(auth, "user_id") and auth.user_id:
                return f"user:{auth.user_id}"
            elif hasattr(auth, "site_id") and auth.site_id:
                return f"site:{auth.site_id}"

        # Fall back to IP address
        client_ip = request.client.host if request.client else "unknown"

        # Check for forwarded IP (behind proxy/load balancer)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take the first IP (client's real IP)
            client_ip = forwarded_for.split(",")[0].strip()

        return f"ip:{client_ip}"

    def _get_endpoint_limit(self, path: str) -> int:
        """Get rate limit for specific endpoint type."""
        # More restrictive limits for expensive operations
        if "/upload" in path or "/documents" in path:
            return max(10, self.default_limit // 6)  # ~10 uploads/minute

        # Higher limits for real-time chat/messaging
        if "/messages" in path or "/conversations" in path:
            return self.default_limit * 3  # 3x for messages

        # Higher limits for session management
        if "/sessions" in path:
            return self.default_limit * 2  # 2x for sessions

        return self.default_limit
