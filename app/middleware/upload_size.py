"""
Middleware to handle large file uploads
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from fastapi import HTTPException
from starlette.responses import Response
import logging

from app.constants import DOCUMENT_MAX_UPLOAD_BYTES

logger = logging.getLogger(__name__)

class UploadSizeMiddleware(BaseHTTPMiddleware):
    """Middleware to set maximum upload size"""
    
    def __init__(self, app, max_upload_size: int = DOCUMENT_MAX_UPLOAD_BYTES):
        super().__init__(app)
        self.max_upload_size = max_upload_size
    
    async def dispatch(self, request: Request, call_next):
        # Check if this is an upload endpoint
        if "/upload" in request.url.path or "/knowledge-base" in request.url.path:
            # Check content length header
            content_length = request.headers.get("content-length")
            if content_length:
                content_length = int(content_length)
                if content_length > self.max_upload_size:
                    logger.warning(f"Upload too large: {content_length} bytes > {self.max_upload_size} bytes")
                    return Response(
                        content=f"File too large. Maximum size is {self.max_upload_size // (1024*1024)}MB",
                        status_code=413
                    )
        
        response = await call_next(request)
        return response
