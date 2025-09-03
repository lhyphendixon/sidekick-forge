from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
from datetime import datetime
import os
from dotenv import load_dotenv

# Load environment variables from .env file BEFORE importing settings
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from app.config import settings
from app.api.v1 import api_router
from app.middleware.auth import AuthenticationMiddleware
from app.middleware.rate_limiting import RateLimitMiddleware
from app.middleware.logging import LoggingMiddleware
from app.utils.exceptions import APIException
from app.integrations.supabase_client import supabase_manager
from app.integrations.livekit_client import livekit_manager
# Container manager removed - using worker pool architecture
import redis.asyncio as aioredis

# Configure logging
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("autonomite_saas")

# Initialize Redis client
redis_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    global redis_client
    
    # Startup
    logger.info("Starting Autonomite SaaS Backend")
    
    # Initialize Redis
    redis_client = await aioredis.from_url(settings.redis_url)
    
    # Initialize connections
    await supabase_manager.initialize()
    try:
        await livekit_manager.initialize()
    except Exception as e:
        logger.warning(f"LiveKit initialization failed (non-critical): {e}")
    # Container manager removed - workers are managed separately
    
    # Initialize services for proxy endpoints
    from app.services.client_service_supabase_enhanced import ClientService
    from app.services.agent_service_supabase import AgentService
    from app.services.wordpress_site_service import WordPressSiteService
    
    client_service = ClientService(settings.supabase_url, settings.supabase_service_role_key, redis_client)
    agent_service = AgentService(client_service, redis_client)
    wordpress_site_service = WordPressSiteService(settings.supabase_url, settings.supabase_service_role_key, redis_client)
    
    # Inject services into proxy modules
    import app.api.v1.livekit_proxy as livekit_proxy_api
    import app.api.v1.conversations_proxy as conversations_proxy_api
    import app.api.v1.documents_proxy as documents_proxy_api
    import app.api.v1.text_chat_proxy as text_chat_proxy_api
    import app.api.v1.wordpress_sites as wordpress_sites_api
    
    # Initialize proxy services
    livekit_proxy_api.client_service = client_service
    livekit_proxy_api.agent_service = agent_service
    
    conversations_proxy_api.redis_client = redis_client
    conversations_proxy_api.client_service = client_service
    
    documents_proxy_api.redis_client = redis_client
    
    text_chat_proxy_api.redis_client = redis_client
    text_chat_proxy_api.client_service = client_service
    text_chat_proxy_api.agent_service = agent_service
    
    wordpress_sites_api.wordpress_service = wordpress_site_service
    
    logger.info("All services initialized successfully")
    
    # Verify platform has valid LiveKit credentials
    try:
        from app.services.platform_credential_sync import PlatformCredentialSync
        logger.info("Verifying platform LiveKit credentials...")
        valid = await PlatformCredentialSync.verify_platform_credentials()
        if valid:
            logger.info("✅ Platform LiveKit credentials are valid")
        else:
            logger.warning("⚠️ Platform LiveKit credentials are invalid or missing")
            logger.warning("   Please update LIVEKIT_* variables in .env")
    except Exception as e:
        logger.error(f"Error verifying platform credentials: {e}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Autonomite SaaS Backend")
    await supabase_manager.close()
    await livekit_manager.close()
    if redis_client:
        await redis_client.close()
    # Workers are managed separately and don't need closing here

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
        {"name": "knowledge-base", "description": "Knowledge base document upload and management"},
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

# Force no-cache on admin pages to avoid CDN/browser serving stale admin HTML
@app.middleware("http")
async def _no_cache_admin_pages(request: Request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path or ""
        if path.startswith("/admin"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Admin-Cache"] = "bypassed"
    except Exception:
        pass
    return response

# Include API router
app.include_router(api_router, prefix="/api")

# Include multi-tenant routes (gradual migration)
try:
    from app.api.v1.multitenant_routes import trigger_router, agents_router, clients_router
    app.include_router(trigger_router, prefix="/api/v2", tags=["trigger-v2"])
    app.include_router(agents_router, prefix="/api/v2", tags=["agents-v2"]) 
    app.include_router(clients_router, prefix="/api/v2", tags=["clients-v2"])
    logger.info("✅ Multi-tenant routes (v2) loaded successfully")
except Exception as e:
    logger.warning(f"Multi-tenant routes not loaded: {e}")

# Mount static files
import os
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    logger.warning(f"Static directory not found at {static_dir}, skipping static file mount")

# Include admin dashboard (full version with all features)
# Temporarily disabled multi-tenant admin to restore original styling
# try:
#     # Try to load multi-tenant admin routes
#     from app.admin.routes_multitenant import router as admin_router_multitenant
#     app.include_router(admin_router_multitenant)
#     logger.info("✅ Multi-tenant admin interface loaded")
# except Exception as e:
#     # Fallback to legacy admin routes
#     logger.warning(f"Multi-tenant admin not loaded, using legacy: {e}")
from app.admin.routes import router as admin_router
app.include_router(admin_router)
logger.info("✅ Original admin interface loaded")

# Custom exception handler for admin authentication redirects
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi import Request

@app.exception_handler(401)
async def auth_exception_handler(request: Request, exc):
    """Redirect to login page for unauthorized HTML requests to admin"""
    accept_header = request.headers.get("accept", "")
    is_browser_request = "text/html" in accept_header
    is_admin_path = request.url.path.startswith("/admin") and not request.url.path.endswith("/login")
    
    if is_browser_request and is_admin_path:
        # Redirect to login page
        return RedirectResponse(url="/admin/login", status_code=303)
    else:
        # Return normal JSON error for API requests
        return JSONResponse(
            status_code=401,
            content={"detail": str(exc.detail) if hasattr(exc, 'detail') else "Unauthorized"}
        )

# Include webhook routers
from app.api.webhooks import livekit_router, supabase_router
from app.api.embed import router as embed_router
app.include_router(livekit_router, prefix="/webhooks", tags=["webhooks"])
app.include_router(supabase_router, prefix="/webhooks", tags=["webhooks"])
app.include_router(embed_router)

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