"""
API v1 package with centralized router
"""
from fastapi import APIRouter

# Import all routers
from .agents import router as agents_router
from .auth import router as auth_router
from .clients import router as clients_router
from .containers import router as containers_router
from .conversations import router as conversations_router
from .conversations_proxy import router as conversations_proxy_router
from .documents import router as documents_router
from .documents_proxy import router as documents_proxy_router
from .knowledge_base import router as knowledge_base_router
from .livekit_proxy import router as livekit_proxy_router
from .sessions import router as sessions_router
from .text_chat_proxy import router as text_chat_proxy_router
from .tools import router as tools_router
from .trigger import router as trigger_router
from .users import router as users_router
# from .workers import router as workers_router  # Removed - using containerized worker pool
from .wordpress import router as wordpress_router
from .wordpress_sites import router as wordpress_sites_router
from .diagnostics import router as diagnostics_router

# Create main API router
api_router = APIRouter(prefix="/v1")

# Include all routers
api_router.include_router(agents_router)
api_router.include_router(auth_router)
api_router.include_router(clients_router)
api_router.include_router(containers_router)
api_router.include_router(conversations_router)
api_router.include_router(conversations_proxy_router)
api_router.include_router(documents_router)
api_router.include_router(documents_proxy_router)
api_router.include_router(knowledge_base_router)
api_router.include_router(livekit_proxy_router)
api_router.include_router(sessions_router)
api_router.include_router(text_chat_proxy_router)
api_router.include_router(tools_router)
api_router.include_router(trigger_router)
api_router.include_router(users_router, prefix="/users", tags=["users"])
# api_router.include_router(workers_router, prefix="/workers")  # Removed - using containerized worker pool
api_router.include_router(wordpress_router)
api_router.include_router(wordpress_sites_router)
api_router.include_router(diagnostics_router)

__all__ = ["api_router"]