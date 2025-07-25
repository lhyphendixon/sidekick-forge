from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List, TypeVar, Generic
from datetime import datetime

T = TypeVar('T')

class APIError(BaseModel):
    """Standard API error response"""
    error: str
    message: str
    code: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    
    class Config:
        schema_extra = {
            "example": {
                "error": "Validation Error",
                "message": "Invalid input parameters",
                "code": "VALIDATION_ERROR",
                "details": {
                    "field": "email",
                    "reason": "Invalid email format"
                }
            }
        }

class APIResponse(BaseModel, Generic[T]):
    """Standard API response wrapper"""
    success: bool
    data: Optional[T] = None
    error: Optional[APIError] = None
    meta: Optional[Dict[str, Any]] = None
    
    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "data": {"id": "123", "name": "Example"},
                "meta": {"timestamp": "2024-01-01T00:00:00Z"}
            }
        }

class PaginationParams(BaseModel):
    """Pagination parameters for list endpoints"""
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)
    order_by: Optional[str] = "created_at"
    order_direction: str = Field(default="desc", pattern="^(asc|desc)$")

class PaginationMeta(BaseModel):
    """Pagination metadata for responses"""
    page: int
    per_page: int
    total: int
    total_pages: int
    has_next: bool
    has_prev: bool

class HealthStatus(BaseModel):
    """Health check status"""
    status: str = Field(..., pattern="^(healthy|degraded|unhealthy)$")
    service: str
    timestamp: datetime
    details: Optional[Dict[str, Any]] = None

class BatchRequest(BaseModel):
    """Batch operation request"""
    operations: List[Dict[str, Any]]
    continue_on_error: bool = False
    
    class Config:
        schema_extra = {
            "example": {
                "operations": [
                    {
                        "method": "POST",
                        "path": "/api/v1/agents",
                        "body": {"name": "Agent 1", "slug": "agent-1"}
                    },
                    {
                        "method": "PUT",
                        "path": "/api/v1/agents/123",
                        "body": {"enabled": True}
                    }
                ],
                "continue_on_error": True
            }
        }

class BatchResponse(BaseModel):
    """Batch operation response"""
    results: List[Dict[str, Any]]
    success_count: int
    error_count: int
    
class WebhookPayload(BaseModel):
    """Generic webhook payload"""
    event_type: str
    event_id: str
    timestamp: datetime
    data: Dict[str, Any]
    
class ErrorCode:
    """Standard error codes"""
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    
class SuccessResponse(BaseModel):
    """Simple success response"""
    success: bool = True
    message: Optional[str] = None
    
class DeleteResponse(BaseModel):
    """Response for delete operations"""
    success: bool = True
    deleted_id: str
    deleted_at: datetime