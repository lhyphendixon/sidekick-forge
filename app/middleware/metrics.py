"""
Metrics middleware for Prometheus integration
"""
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import time
from prometheus_client import Counter, Histogram

# Define metrics here to avoid circular import
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"]
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to track request metrics"""
    
    async def dispatch(self, request: Request, call_next):
        # Skip metrics endpoint itself
        if request.url.path == "/metrics":
            return await call_next(request)
            
        # Start timer
        start_time = time.time()
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration = time.time() - start_time
        
        # Extract endpoint (normalize path)
        endpoint = request.url.path
        if endpoint.startswith("/api/v1/"):
            # Normalize dynamic paths
            parts = endpoint.split("/")
            if len(parts) > 4 and parts[4] in ["client", "agents"]:
                # Replace IDs with placeholders
                endpoint = "/".join(parts[:5] + ["{id}"] + parts[6:])
        
        # Record metrics
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=endpoint,
            status=response.status_code
        ).inc()
        
        REQUEST_LATENCY.labels(
            method=request.method,
            endpoint=endpoint
        ).observe(duration)
        
        return response