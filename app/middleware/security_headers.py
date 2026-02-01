"""Security Headers Middleware

Adds security-related HTTP headers to all responses to protect against
common web vulnerabilities like XSS, clickjacking, and MIME sniffing attacks.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import logging

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all HTTP responses."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Prevent clickjacking attacks
        # DENY = page cannot be displayed in a frame
        # SAMEORIGIN = page can only be displayed in a frame on the same origin
        response.headers["X-Frame-Options"] = "SAMEORIGIN"

        # Prevent MIME type sniffing
        # Stops browsers from trying to guess MIME types, which can be exploited
        response.headers["X-Content-Type-Options"] = "nosniff"

        # XSS Protection (legacy, but still useful for older browsers)
        # 1; mode=block = enable protection and block rendering if attack detected
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer Policy - control how much referrer info is sent
        # strict-origin-when-cross-origin = send full URL to same origin,
        # only origin to cross-origin, nothing to less secure destinations
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions Policy (formerly Feature-Policy)
        # Restrict access to browser features
        response.headers["Permissions-Policy"] = (
            "geolocation=(), "
            "midi=(), "
            "camera=(self), "  # Allow camera for video chat features
            "microphone=(self), "  # Allow microphone for voice chat features
            "usb=(), "
            "payment=()"
        )

        # Content Security Policy (CSP)
        # This is a basic policy - may need adjustment based on app requirements
        # Note: Using report-only mode initially is recommended to avoid breaking functionality
        path = request.url.path or ""

        # Skip CSP for API endpoints to avoid issues with JSON responses
        if not path.startswith("/api/"):
            csp_directives = [
                "default-src 'self'",
                # Allow scripts from self, inline (needed for HTMX/Alpine), and trusted CDNs
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com https://use.typekit.net",
                # Allow styles from self, inline, and trusted CDNs
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com https://use.typekit.net https://p.typekit.net",
                # Allow images from self, data URIs, and common image hosts
                "img-src 'self' data: blob: https: http:",
                # Allow fonts from self and trusted CDNs
                "font-src 'self' https://use.typekit.net https://p.typekit.net data:",
                # Allow connections to self, LiveKit, and Supabase
                "connect-src 'self' https://*.livekit.cloud wss://*.livekit.cloud https://*.supabase.co wss://*.supabase.co https://api.openai.com https://api.anthropic.com",
                # Allow frames from self (for embeds/previews)
                "frame-src 'self' https://*.livekit.cloud",
                # Allow media from self
                "media-src 'self' blob: https:",
                # Restrict object/embed/applet
                "object-src 'none'",
                # Restrict base URI
                "base-uri 'self'",
                # Restrict form submissions
                "form-action 'self'",
                # Upgrade insecure requests in production
                "upgrade-insecure-requests",
            ]
            response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

        # Strict Transport Security (HSTS)
        # Only enable in production with HTTPS
        # max-age=31536000 = 1 year, includeSubDomains = apply to all subdomains
        # Note: Be careful with preload - it's difficult to undo
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

        return response
