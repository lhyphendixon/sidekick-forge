from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import asyncio
import logging
import os
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables before importing settings
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'), override=True)

from app.config import settings
from app.middleware.auth import AuthenticationMiddleware
from app.middleware.logging import LoggingMiddleware
from app.utils.exceptions import APIException
from app.integrations.livekit_client import livekit_manager
# Redis is intentionally not used in this deployment. Keeping the import commented for reference only.
# import redis.asyncio as aioredis

# Configure logging
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("sidekick_forge")

# Redis disabled
redis_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    global redis_client
    worker = None
    worker_task = None
    
    # Startup
    logger.info("Starting Sidekick Forge Platform")
    
    # Redis disabled (was used for caching/rate limiting)
    redis_client = None
    
    # Initialize platform services
    from app.services.client_connection_manager import get_connection_manager
    
    try:
        # Initialize connection manager (validates platform credentials)
        connection_manager = get_connection_manager()
        logger.info("✅ Client Connection Manager initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Client Connection Manager: {e}")
        raise
    
    # Initialize LiveKit for backend operations
    try:
        await livekit_manager.initialize()
        logger.info("✅ LiveKit manager initialized")
    except Exception as e:
        logger.warning(f"⚠️ LiveKit initialization failed (non-critical): {e}")
    
    logger.info("All services initialized successfully")

    # Start provisioning worker if credentials allow
    try:
        from app.services.onboarding.provisioning_worker import ProvisioningWorker
        worker = ProvisioningWorker()
        worker_task = asyncio.create_task(worker.run())
        logger.info("Provisioning worker task started")
    except RuntimeError as e:
        logger.warning(f"Provisioning worker disabled: {e}")

    try:
        yield
    finally:
        if worker:
            worker.stop()
        if worker_task:
            await worker_task

        # Shutdown
        logger.info("Shutting down Sidekick Forge Platform")
        await livekit_manager.close()
        if redis_client:
            await redis_client.close()

# Create FastAPI app
app = FastAPI(
    title="Sidekick Forge Platform API",
    description="Multi-tenant AI Agent management platform with LiveKit integration",
    version="2.2.1",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "agents", "description": "Agent management operations"},
        {"name": "clients", "description": "Client (tenant) management"},
        {"name": "sessions", "description": "LiveKit session creation and management"},
        {"name": "conversations", "description": "Conversation storage and retrieval"},
        {"name": "documents", "description": "RAG document processing"},
        {"name": "knowledge-base", "description": "Knowledge base document upload and management"},
        {"name": "tools", "description": "Tool configuration and proxy"},
        {"name": "wordpress", "description": "WordPress plugin integration"},
        {"name": "auth", "description": "Authentication and authorization"},
        {"name": "containers", "description": "Container management for client agents"},
        {"name": "health", "description": "Health check and monitoring"},
        {"name": "webhooks", "description": "Webhook endpoints for external services"},
        {"name": "trigger", "description": "Agent triggering endpoints"}
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

# Add custom middleware (Redis-backed rate limiting removed)
app.add_middleware(LoggingMiddleware)
app.add_middleware(AuthenticationMiddleware)

# Include multi-tenant API routers
from app.api.v1 import (
    trigger_multitenant,
    agents_multitenant,
    clients_multitenant,
    # Keep existing routers for now during migration
    sessions,
    conversations,
    documents,
    auth,
    containers,
    tools,
    livekit_proxy,
    conversations_proxy,
    documents_proxy,
    text_chat_proxy,
    knowledge_base
)
from app.api import embed as embed_router
from app.marketing import routes as marketing_routes
from app.api import admin_preview_standalone

# Mount multi-tenant routes
app.include_router(trigger_multitenant.router, prefix="/api/v1", tags=["trigger"])
app.include_router(agents_multitenant.router, prefix="/api/v1", tags=["agents"])
app.include_router(clients_multitenant.router, prefix="/api/v1", tags=["clients"])

# Temporary compatibility aliases so the admin UI can safely call /api/v2/*
# while the multitenant server still primarily operates under /api/v1.
app.include_router(trigger_multitenant.router, prefix="/api/v2", tags=["trigger-v2"])
app.include_router(agents_multitenant.router, prefix="/api/v2", tags=["agents-v2"])
app.include_router(clients_multitenant.router, prefix="/api/v2", tags=["clients-v2"])

# Keep existing routes during migration
app.include_router(sessions.router, prefix="/api/v1", tags=["sessions"])
app.include_router(conversations.router, prefix="/api/v1", tags=["conversations"])
app.include_router(documents.router, prefix="/api/v1", tags=["documents"])
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
app.include_router(containers.router, prefix="/api/v1", tags=["containers"])
app.include_router(tools.router, prefix="/api/v1", tags=["tools"])
app.include_router(livekit_proxy.router, prefix="/api/v1", tags=["sessions"])
app.include_router(conversations_proxy.router, prefix="/api/v1", tags=["conversations"])
app.include_router(documents_proxy.router, prefix="/api/v1", tags=["documents"])
app.include_router(text_chat_proxy.router, prefix="/api/v1", tags=["text-chat"])
app.include_router(knowledge_base.router, prefix="/api/v1", tags=["knowledge-base"])
app.include_router(embed_router.router)
# Expose admin preview helper endpoints (used by preview modal)
app.include_router(admin_preview_standalone.router)

# Marketing site routes (must be before WordPress to catch root path)
app.include_router(marketing_routes.router)

# Mount static files
static_dir_candidates = [
    "/app/static",  # container volume mount
    os.path.join(os.path.dirname(__file__), "static"),  # relative to source
]
static_dir = next((d for d in static_dir_candidates if os.path.exists(d)), None)
if static_dir:
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info(f"Mounted static directory at {static_dir}")
else:
    logger.warning(f"Static directory not found at any of {static_dir_candidates}, skipping static file mount")

# Include admin dashboard (will need updating for multi-tenant)
from app.admin.routes import router as admin_router
app.include_router(admin_router)

# Custom exception handler for admin authentication redirects
from fastapi.responses import RedirectResponse

@app.exception_handler(401)
async def auth_exception_handler(request: Request, exc):
    """Redirect to login page for unauthorized HTML requests to admin"""
    accept_header = request.headers.get("accept", "")
    is_browser_request = "text/html" in accept_header
    is_admin_path = request.url.path.startswith("/admin") and not request.url.path.endswith("/login")
    
    if is_browser_request and is_admin_path:
        return RedirectResponse(url="/admin/login", status_code=303)
    else:
        return JSONResponse(
            status_code=401,
            content={"detail": str(exc.detail) if hasattr(exc, 'detail') else "Unauthorized"}
        )

# Include webhook routers
from app.api.webhooks import livekit_router, supabase_router
app.include_router(livekit_router, prefix="/webhooks", tags=["webhooks"])
app.include_router(supabase_router, prefix="/webhooks", tags=["webhooks"])

# Root endpoint
@app.get("/", tags=["health"])
async def root():
    return {
        "service": "Sidekick Forge Platform",
        "version": "2.2.1",
        "status": "operational",
        "timestamp": datetime.utcnow().isoformat()
    }

# Health check endpoints
@app.get("/health", tags=["health"])
async def health_check():
    """Basic health check endpoint"""
    return {
        "status": "healthy",
        "service": "sidekick-forge",
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/health/detailed", tags=["health"])
async def detailed_health_check():
    """Detailed health check with service status"""
    from app.services.client_connection_manager import get_connection_manager
    
    checks = {}
    
    # Check platform database
    try:
        connection_manager = get_connection_manager()
        # Try a simple query
        connection_manager.platform_client.table('clients').select('id').limit(1).execute()
        checks["platform_database"] = True
    except Exception as e:
        logger.error(f"Platform database check failed: {e}")
        checks["platform_database"] = False
    
    # Check LiveKit
    checks["livekit"] = await livekit_manager.health_check()
    
    # Check Redis
    try:
        if redis_client:
            await redis_client.ping()
            checks["redis"] = True
        else:
            checks["redis"] = False
    except Exception:
        checks["redis"] = False
    
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
