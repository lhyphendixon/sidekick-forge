"""
Enhanced diagnostic logging middleware
"""
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
import time
import logging
import json
from typing import Callable
import uuid
from datetime import datetime

from app.utils.diagnostics import agent_diagnostics, diagnostic_context

logger = logging.getLogger(__name__)


class DiagnosticLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that adds comprehensive diagnostic logging to all requests"""
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        # Skip logging for static assets and health checks
        if request.url.path in ["/health", "/favicon.ico"] or request.url.path.startswith("/static"):
            return await call_next(request)
            
        # Start diagnostic context for important endpoints
        if any(path in request.url.path for path in ["/trigger-agent", "/preview", "/voice"]):
            operation = request.url.path.replace("/", "_").strip("_")
            async with diagnostic_context(f"request_{operation}", request_id=request_id) as diag:
                # Log request details
                diag.add_event("request_start", f"{request.method} {request.url.path}", {
                    "method": request.method,
                    "path": request.url.path,
                    "query_params": dict(request.query_params),
                    "headers": {k: v for k, v in request.headers.items() if k.lower() not in ["authorization", "cookie"]}
                })
                
                # Process request
                start_time = time.time()
                response = await call_next(request)
                duration = time.time() - start_time
                
                # Log response
                diag.add_event("request_complete", f"Status {response.status_code}", {
                    "status_code": response.status_code,
                    "duration_ms": int(duration * 1000)
                })
                
                # Add diagnostic headers
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Response-Time"] = f"{duration:.3f}s"
                
                return response
        else:
            # Regular logging for other endpoints
            start_time = time.time()
            
            logger.info(
                f"Request started: {request.method} {request.url.path}",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "client": request.client.host if request.client else None
                }
            )
            
            response = await call_next(request)
            duration = time.time() - start_time
            
            logger.info(
                f"Request completed: {request.method} {request.url.path} - {response.status_code}",
                extra={
                    "request_id": request_id,
                    "status_code": response.status_code,
                    "duration_ms": int(duration * 1000)
                }
            )
            
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time"] = f"{duration:.3f}s"
            
            return response


class VoiceAgentLoggingMiddleware(BaseHTTPMiddleware):
    """Specialized middleware for voice agent operations"""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Only process voice agent related endpoints
        if not any(path in request.url.path for path in ["/trigger-agent", "/agents", "/voice", "/preview"]):
            return await call_next(request)
            
        # Extract relevant information
        request_data = {}
        if request.method == "POST":
            try:
                body = await request.body()
                request.state.body = body  # Store for later use
                request_data = json.loads(body) if body else {}
            except:
                pass
                
        # Log voice agent operation
        logger.info(
            f"Voice agent operation: {request.url.path}",
            extra={
                "operation": request.url.path,
                "agent_slug": request_data.get("agent_slug"),
                "mode": request_data.get("mode"),
                "room_name": request_data.get("room_name"),
                "client_id": request_data.get("client_id"),
                "user_id": request_data.get("user_id")
            }
        )
        
        # Process request
        response = await call_next(request)
        
        # Log response for debugging
        if response.status_code >= 400:
            logger.warning(
                f"Voice agent operation failed: {request.url.path} - {response.status_code}",
                extra={
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "request_data": request_data
                }
            )
            
        return response