from typing import Optional, Dict, Any

class APIException(Exception):
    """Base exception for API errors"""
    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_type: str = "API_ERROR",
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.error_code = error_code or error_type
        self.details = details or {}
        super().__init__(self.message)

class ValidationError(APIException):
    """Raised when request validation fails"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=400,
            error_type="Validation Error",
            error_code="VALIDATION_ERROR",
            details=details
        )

class AuthenticationError(APIException):
    """Raised when authentication fails"""
    def __init__(self, message: str = "Authentication required", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=401,
            error_type="Authentication Error",
            error_code="AUTHENTICATION_ERROR",
            details=details
        )

class AuthorizationError(APIException):
    """Raised when user lacks required permissions"""
    def __init__(self, message: str = "Insufficient permissions", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=403,
            error_type="Authorization Error",
            error_code="AUTHORIZATION_ERROR",
            details=details
        )

class NotFoundError(APIException):
    """Raised when requested resource is not found"""
    def __init__(self, message: str = "Resource not found", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=404,
            error_type="Not Found",
            error_code="NOT_FOUND",
            details=details
        )

class ConflictError(APIException):
    """Raised when there's a conflict with existing data"""
    def __init__(self, message: str = "Resource conflict", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=409,
            error_type="Conflict",
            error_code="CONFLICT",
            details=details
        )

class RateLimitError(APIException):
    """Raised when rate limit is exceeded"""
    def __init__(self, message: str = "Rate limit exceeded", retry_after: Optional[int] = None):
        details = {}
        if retry_after:
            details["retry_after"] = retry_after
        
        super().__init__(
            message=message,
            status_code=429,
            error_type="Rate Limit Exceeded",
            error_code="RATE_LIMIT_EXCEEDED",
            details=details
        )

class ServiceUnavailableError(APIException):
    """Raised when an external service is unavailable"""
    def __init__(self, message: str = "Service temporarily unavailable", service: Optional[str] = None):
        details = {}
        if service:
            details["service"] = service
        
        super().__init__(
            message=message,
            status_code=503,
            error_type="Service Unavailable",
            error_code="SERVICE_UNAVAILABLE",
            details=details
        )

class DatabaseError(APIException):
    """Raised when database operations fail"""
    def __init__(self, message: str = "Database operation failed", operation: Optional[str] = None):
        details = {}
        if operation:
            details["operation"] = operation
        
        super().__init__(
            message=message,
            status_code=500,
            error_type="Database Error",
            error_code="DATABASE_ERROR",
            details=details
        )

class WebhookError(APIException):
    """Raised when webhook processing fails"""
    def __init__(self, message: str = "Webhook processing failed", webhook_type: Optional[str] = None):
        details = {}
        if webhook_type:
            details["webhook_type"] = webhook_type
        
        super().__init__(
            message=message,
            status_code=500,
            error_type="Webhook Error",
            error_code="WEBHOOK_ERROR",
            details=details
        )