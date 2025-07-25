from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import List

from app.config import settings

def setup_cors(app: FastAPI):
    """Configure CORS middleware for the application"""
    
    # Parse allowed origins
    origins = settings.cors_allowed_origins
    
    # Add WordPress sites dynamically (in production, fetch from database)
    # For now, we'll use the configured origins
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-Request-ID",
            "X-WordPress-Site",
            "X-WordPress-User"
        ],
        expose_headers=[
            "X-Total-Count",
            "X-Page",
            "X-Per-Page",
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-Response-Time"
        ],
        max_age=3600  # Cache preflight requests for 1 hour
    )