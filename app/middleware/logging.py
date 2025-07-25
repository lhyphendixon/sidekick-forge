from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import time
import logging
import json
import uuid
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)

class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for structured logging of requests and responses"""
    
    async def dispatch(self, request: Request, call_next):
        # Generate request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        # Start timer
        start_time = time.time()
        
        # Extract request details
        request_details = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "client_host": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Get authentication info if available
        if hasattr(request.state, "auth"):
            auth = request.state.auth
            request_details["auth_type"] = auth.type
            request_details["user_id"] = str(auth.user_id) if auth.user_id else None
            request_details["site_id"] = str(auth.site_id) if auth.site_id else None
        
        # Log request
        logger.info("API Request", extra=request_details)
        
        # Process request
        response = None
        error_details = None
        
        try:
            response = await call_next(request)
            
        except Exception as e:
            # Log exception
            error_details = {
                "request_id": request_id,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "path": request.url.path
            }
            logger.error("Request processing error", extra=error_details, exc_info=True)
            raise
        
        finally:
            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000
            
            # Log response
            response_details = {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code if response else 500,
                "duration_ms": round(duration_ms, 2)
            }
            
            if error_details:
                response_details["error"] = error_details
            
            # Add response headers
            if response:
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
            
            # Log based on status code
            if response and response.status_code >= 500:
                logger.error("API Response - Server Error", extra=response_details)
            elif response and response.status_code >= 400:
                logger.warning("API Response - Client Error", extra=response_details)
            else:
                logger.info("API Response", extra=response_details)
            
            # Log slow requests
            if duration_ms > 1000:  # Log requests taking more than 1 second
                logger.warning(
                    f"Slow request detected: {request.method} {request.url.path} took {duration_ms:.2f}ms",
                    extra=response_details
                )
        
        return response

class SupabaseAuthLogger:
    """Logger for Supabase Auth events"""
    
    def __init__(self):
        self.logger = logging.getLogger("autonomite_saas.auth")
    
    def log_auth_event(self, event_type: str, user_id: str = None, site_id: str = None, **kwargs):
        """Log authentication events for audit trail"""
        context = {
            "event_type": event_type,
            "user_id": str(user_id) if user_id else None,
            "site_id": str(site_id) if site_id else None,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        }
        self.logger.info(f"Auth Event: {event_type}", extra=context)
    
    def log_signup(self, email: str, user_id: str):
        """Log user signup"""
        self.log_auth_event("user_signup", user_id=user_id, email=email)
    
    def log_login(self, email: str, user_id: str, success: bool):
        """Log login attempt"""
        self.log_auth_event(
            "user_login",
            user_id=user_id,
            email=email,
            success=success
        )
    
    def log_api_key_generation(self, site_domain: str, site_id: str):
        """Log API key generation"""
        self.log_auth_event(
            "api_key_generated",
            site_id=site_id,
            site_domain=site_domain
        )
    
    def log_token_refresh(self, user_id: str):
        """Log token refresh"""
        self.log_auth_event("token_refresh", user_id=user_id)

# Create singleton instance
auth_logger = SupabaseAuthLogger()

def setup_logging():
    """Configure structured logging for the application"""
    # Set up JSON formatter for production
    if settings.app_env == "production":
        handler = logging.StreamHandler()
        handler.setFormatter(JSONLogFormatter())
        
        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.handlers = [handler]
        root_logger.setLevel(settings.log_level)
        
        # Configure app logger
        app_logger = logging.getLogger("autonomite_saas")
        app_logger.handlers = [handler]
        app_logger.setLevel(settings.log_level)

class JSONLogFormatter(logging.Formatter):
    """JSON formatter for structured logging"""
    
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # Add extra fields
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id
        
        if hasattr(record, "user_id"):
            log_data["user_id"] = record.user_id
            
        if hasattr(record, "site_id"):
            log_data["site_id"] = record.site_id
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add any extra fields from the record
        for key, value in record.__dict__.items():
            if key not in ["name", "msg", "args", "created", "filename", "funcName", 
                          "levelname", "levelno", "lineno", "module", "msecs", "message",
                          "pathname", "process", "processName", "relativeCreated", "stack_info",
                          "thread", "threadName", "exc_info", "exc_text"]:
                log_data[key] = value
        
        return json.dumps(log_data)