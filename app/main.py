from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
from datetime import datetime

from app.config import settings
from app.api.v1 import api_router
from app.middleware.auth import AuthenticationMiddleware
from app.middleware.rate_limiting import RateLimitMiddleware
from app.middleware.logging import LoggingMiddleware
from app.utils.exceptions import APIException
from app.integrations.supabase_client import supabase_manager
from app.integrations.livekit_client import livekit_manager
from app.services.container_manager import container_manager

# Configure logging
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("autonomite_saas")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    logger.info("Starting Autonomite SaaS Backend")
    
    # Initialize connections
    await supabase_manager.initialize()
    await livekit_manager.initialize()
    await container_manager.initialize()
    
    yield
    
    # Shutdown
    logger.info("Shutting down Autonomite SaaS Backend")
    await supabase_manager.close()
    await livekit_manager.close()
    # Note: container_manager doesn't need explicit closing

# Create FastAPI app
app = FastAPI(
    title="Autonomite Agent SaaS API",
    description="AI Agent management platform with LiveKit integration for WordPress plugins",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "agents", "description": "Agent management operations"},
        {"name": "sessions", "description": "LiveKit session creation and management"},
        {"name": "conversations", "description": "Conversation storage and retrieval"},
        {"name": "documents", "description": "RAG document processing"},
        {"name": "tools", "description": "Tool configuration and proxy"},
        {"name": "wordpress", "description": "WordPress plugin integration"},
        {"name": "auth", "description": "Authentication and authorization"},
        {"name": "containers", "description": "Container management for client agents"},
        {"name": "health", "description": "Health check and monitoring"},
        {"name": "webhooks", "description": "Webhook endpoints for external services"}
    ]
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count", "X-Page", "X-Per-Page"]
)

# Add custom middleware
app.add_middleware(LoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthenticationMiddleware)

# Include API router
app.include_router(api_router, prefix="/api")

# Mount static files
app.mount("/static", StaticFiles(directory="/opt/autonomite-saas/app/static"), name="static")

# Include admin dashboard (full version with all features)
from app.admin.routes import router as admin_router
app.include_router(admin_router)

# Include webhook routers
from app.api.webhooks import livekit_router, supabase_router
app.include_router(livekit_router, prefix="/webhooks", tags=["webhooks"])
app.include_router(supabase_router, prefix="/webhooks", tags=["webhooks"])

# Root endpoint
@app.get("/", tags=["health"])
async def root():
    return {
        "service": "Autonomite SaaS Backend",
        "version": "1.0.0",
        "status": "operational",
        "timestamp": datetime.utcnow().isoformat()
    }

# Health check endpoints
@app.get("/health", tags=["health"])
async def health_check():
    """Basic health check endpoint"""
    return {
        "status": "healthy",
        "service": "autonomite-saas",
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/health/detailed", tags=["health"])
async def detailed_health_check():
    """Detailed health check with service status"""
    checks = {
        "supabase": await supabase_manager.health_check(),
        "livekit": await livekit_manager.health_check(),
        "database": await supabase_manager.check_database_connection()
    }
    
    overall_status = "healthy" if all(checks.values()) else "degraded"
    
    return {
        "status": overall_status,
        "checks": checks,
        "timestamp": datetime.utcnow().isoformat()
    }

# Global exception handlers
@app.exception_handler(APIException)
async def api_exception_handler(request: Request, exc: APIException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "error": exc.error_type,
                "message": exc.message,
                "code": exc.error_code,
                "details": exc.details
            }
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "error": "Internal Server Error",
                "message": "An unexpected error occurred",
                "code": "INTERNAL_ERROR"
            }
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
        workers=settings.api_workers
    )