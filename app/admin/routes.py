from fastapi import APIRouter, Request, Depends, Form, HTTPException, File, UploadFile, Query, Body, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Any, List, Optional, Set
import redis.asyncio as aioredis
import redis
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from livekit import api
from pathlib import Path
import mimetypes
import re

# These would be actual imports in the FastAPI app
# from app.dependencies.admin_auth import get_admin_user
# from app.services.container_orchestrator import get_orchestrator
# from app.services.supabase_service import get_all_clients

# Import from the app services
# Container manager removed - using worker pool architecture
from app.services.wordpress_site_service_supabase import WordPressSiteService
from app.models.wordpress_site import WordPressSite, WordPressSiteCreate, WordPressSiteUpdate
from app.utils.default_ids import get_default_client_id, get_user_id_from_request
from app.permissions.rbac import get_platform_permissions
from app.integrations.supabase_client import supabase_manager
from app.models.tools import ToolCreate, ToolUpdate, ToolAssignmentRequest
from app.services.tools_service_supabase import ToolsService
from app.services.asana_oauth_service import AsanaOAuthService, AsanaOAuthError
from app.services.helpscout_oauth_service import HelpScoutOAuthService, HelpScoutOAuthError
from app.utils.supabase_credentials import SupabaseCredentialManager
from app.constants import DOCUMENT_MAX_UPLOAD_BYTES, DOCUMENT_MAX_UPLOAD_MB
from app.services.client_supabase_auth import generate_client_session_tokens
from app.services.client_connection_manager import get_connection_manager
from app.config import settings

logger = logging.getLogger(__name__)

# In-memory pending Telegram verification codes (ephemeral)
_pending_telegram_codes: Dict[str, Dict[str, Any]] = {}

# In-memory profile cache fallback when Supabase profile/auth records are unavailable (dev/superadmin)
_profile_cache: Dict[str, Dict[str, Any]] = {}

def _pending_key(user_id: Optional[str], email: Optional[str]) -> str:
    return str(user_id or email or "unknown").lower()

# Simple compatibility class for container operations
class ContainerOrchestrator:
    """Compatibility layer for worker pool architecture"""
    
    async def stop_container(self, client_id: str):
        """No-op - workers are managed by the pool"""
        logger.info(f"Container stop requested for {client_id} - using worker pool")
        return True
    
    async def start_container(self, client_id: str, client_config: dict = None):
        """No-op - workers are managed by the pool"""
        logger.info(f"Container start requested for {client_id} - using worker pool")
        return True
    
    async def get_container_logs(self, client_id: str, lines: int = 100):
        """Return info about worker pool logs"""
        return [
            f"Logs for client {client_id}",
            "Agents now use a shared worker pool.",
            "Check worker logs with: docker logs agent-worker-1",
            "Or: docker logs agent-worker-2",
            "Or: docker logs agent-worker-3"
        ]
    
    async def list_containers(self):
        """List worker pool status"""
        try:
            import docker
            client = docker.from_env()
            containers = []
            for container in client.containers.list():
                if "agent-worker" in container.name:
                    containers.append({
                        "name": container.name,
                        "status": container.status,
                        "id": container.short_id
                    })
            return containers
        except:
            return []

def get_wordpress_service() -> WordPressSiteService:
    """Get WordPress site service with platform Supabase credentials"""
    # Use platform credentials from config (no defaults)
    from app.config import settings
    return WordPressSiteService(settings.supabase_url, settings.supabase_service_role_key)


def get_asana_oauth_service() -> AsanaOAuthService:
    from app.core.dependencies import get_client_service

    return AsanaOAuthService(get_client_service())


def get_helpscout_oauth_service() -> HelpScoutOAuthService:
    from app.core.dependencies import get_client_service

    return HelpScoutOAuthService(get_client_service())


def admin_is_super(admin_user: Dict[str, Any]) -> bool:
    """Return True if the admin user has platform-wide access."""
    if not admin_user:
        return False

    # Explicit boolean flag takes priority
    flag = admin_user.get("is_super_admin")
    if isinstance(flag, bool):
        if flag:
            return True
    elif isinstance(flag, str) and flag.strip().lower() in {"true", "1", "yes"}:
        return True

    # Treat role/role_key indicators as equivalent
    role = (admin_user.get("role") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if role in {
        "superadmin",
        "super_admin",
        "platform_admin",
        "platformadmin",
        "platform_super_admin",
        "platformsuperadmin",
    }:
        return True

    role_key = (admin_user.get("role_key") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if role_key in {"superadmin", "super_admin", "platform_super_admin"}:
        return True

    client_access = (admin_user.get("client_access") or "").strip().lower()
    if client_access in {"all", "global"}:
        return True

    permissions = admin_user.get("permissions")
    if isinstance(permissions, (list, tuple, set)):
        lowered = {str(p).strip().lower() for p in permissions}
        if {"super_admin", "platform_admin", "superadmin"} & lowered:
            return True
    elif isinstance(permissions, str) and permissions.strip().lower() in {"super_admin", "superadmin", "platform_admin"}:
        return True

    return False


def get_scoped_client_ids(admin_user: Dict[str, Any]) -> Optional[Set[str]]:
    """Return the set of client IDs the admin can access, or None for full access."""
    if not admin_user:
        return None
    if admin_is_super(admin_user):
        return None

    assignments = admin_user.get("tenant_assignments") or {}
    visible_ids = admin_user.get("visible_client_ids") or []

    if (admin_user.get("role") or "").lower() == "admin":
        scoped = assignments.get("admin_client_ids") or visible_ids
    else:
        scoped = assignments.get("subscriber_client_ids") or visible_ids

    if isinstance(scoped, str):
        scoped = [scoped]

    return {str(cid) for cid in scoped if cid}


def ensure_client_access(client_id: str, admin_user: Dict[str, Any]) -> None:
    """Raise 403 if the admin user does not have access to the given client."""
    scoped_ids = get_scoped_client_ids(admin_user)
    if scoped_ids is None or client_id in scoped_ids:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions for requested client",
    )


def ensure_client_or_global_access(client_id: str, admin_user: Dict[str, Any]) -> None:
    """Allow global access for super admins otherwise enforce per-client permissions."""
    if client_id == "global":
        if not admin_is_super(admin_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Global resources require super admin privileges",
            )
        return
    ensure_client_access(client_id, admin_user)


def _get_client_supabase_credentials(client: Any) -> tuple[Optional[str], Optional[str]]:
    """Resolve Supabase URL/key for a tenant client."""
    if client and getattr(client, "settings", None) and getattr(client.settings, "supabase", None):
        supabase_cfg = client.settings.supabase
        if supabase_cfg.url and supabase_cfg.service_role_key:
            return str(supabase_cfg.url), str(supabase_cfg.service_role_key)
    if getattr(client, "supabase_project_url", None) and getattr(client, "supabase_service_role_key", None):
        return client.supabase_project_url, client.supabase_service_role_key
    if getattr(client, "supabase_url", None) and getattr(client, "supabase_service_role_key", None):
        return client.supabase_url, client.supabase_service_role_key
    return None, None


def _default_personality_payload() -> Dict[str, int]:
    return {
        "openness": 50,
        "conscientiousness": 50,
        "extraversion": 50,
        "agreeableness": 50,
        "neuroticism": 50,
    }


def _fetch_agent_personality(agent_id: Optional[str], client: Any) -> Dict[str, int]:
    """Fetch personality values for an agent from tenant Supabase."""
    payload = _default_personality_payload()
    if not agent_id or not client:
        return payload
    supabase_url, supabase_key = _get_client_supabase_credentials(client)
    if not supabase_url or not supabase_key:
        return payload
    try:
        from supabase import create_client as create_supabase_client
        client_sb = create_supabase_client(supabase_url, supabase_key)
        response = (
            client_sb
            .table("agent_personality")
            .select("openness, conscientiousness, extraversion, agreeableness, neuroticism")
            .eq("agent_id", agent_id)
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        if not rows:
            return payload
        row = rows[0] or {}
        for key in payload:
            try:
                payload[key] = int(row.get(key, payload[key]))
            except (TypeError, ValueError):
                payload[key] = payload[key]
    except Exception as exc:
        logger.warning("Failed to fetch agent personality: %s", exc)
    return payload


def _upsert_agent_personality(agent_id: Optional[str], client: Any, personality: Dict[str, Any]) -> None:
    """Upsert personality values for an agent in tenant Supabase."""
    if not agent_id or not client:
        return
    supabase_url, supabase_key = _get_client_supabase_credentials(client)
    if not supabase_url or not supabase_key:
        return
    payload = _default_personality_payload()
    for key in payload:
        try:
            payload[key] = max(0, min(100, int(personality.get(key, payload[key]))))
        except (TypeError, ValueError):
            payload[key] = payload[key]
    payload["agent_id"] = agent_id
    try:
        from supabase import create_client as create_supabase_client
        client_sb = create_supabase_client(supabase_url, supabase_key)
        client_sb.table("agent_personality").upsert(payload, on_conflict="agent_id").execute()
    except Exception as exc:
        logger.warning("Failed to upsert agent personality: %s", exc)


async def _get_transcript_supabase_context(client_id: str) -> Dict[str, str]:
    """Resolve Supabase credentials for realtime transcript streaming."""
    try:
        url, anon = await SupabaseCredentialManager.get_frontend_credentials(
            client_id,
            allow_platform_ids={"debug-client"},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return {
        "client_supabase_url": url,
        "client_supabase_anon_key": anon,
    }


async def resolve_document_client(document_id: str, admin_user: Dict[str, Any]) -> Optional[str]:
    """Return the client_id for a document if accessible, enforcing tenant scope."""
    scoped_ids = get_scoped_client_ids(admin_user)
    if scoped_ids is None:
        return None  # super admins can operate across all clients

    if not scoped_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No client assignments associated with this admin user",
        )

    from app.services.document_processor import document_processor

    for cid in scoped_ids:
        try:
            supabase = await document_processor._get_client_supabase(cid)
            if not supabase:
                continue
            result = (
                supabase
                .table('documents')
                .select('id')
                .eq('id', document_id)
                .limit(1)
                .execute()
            )
            data = getattr(result, 'data', None)
            if data:
                return str(cid)
        except Exception as exc:
            logger.debug(f"Document lookup failed for client {cid}: {exc}")
            continue

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Document not found for accessible clients",
    )


async def ensure_tool_access(
    tool_id: str,
    admin_user: Dict[str, Any],
    tools_service: ToolsService,
    client_id: Optional[str] = None,
) -> Optional[str]:
    """Validate tool access within tenant scope and return resolved client_id."""
    scoped_ids = get_scoped_client_ids(admin_user)
    target_sb, existing, scope, resolved_client_id = await tools_service._find_tool_record(tool_id, client_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")

    resolved_client_str = str(resolved_client_id) if resolved_client_id else None

    if scoped_ids is None:
        return resolved_client_str

    if scope == "client":
        if not resolved_client_str:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to determine tool tenant")
        ensure_client_access(resolved_client_str, admin_user)

    return resolved_client_str

# Initialize router
router = APIRouter(prefix="/admin", tags=["admin"])

AGENT_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB limit
ALLOWED_AGENT_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}
ALLOWED_AGENT_IMAGE_EXTENSIONS = {ext for ext in ALLOWED_AGENT_IMAGE_TYPES.values()} | {".jpeg"}
AGENT_IMAGE_STORAGE_DIR = (
    Path(__file__).resolve().parent.parent / "static" / "images" / "agents"
)

# Initialize template engine
import os
template_dir_main = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")  # app/templates
template_dir_admin = os.path.join(os.path.dirname(__file__), "templates")  # app/admin/templates
# Use both template roots so child templates can extend shared bases like "admin/base.html"
templates = Jinja2Templates(directory=[template_dir_main, template_dir_admin])
# Inject Supabase settings into Jinja globals so base.html can use them everywhere
try:
    from app.config import settings as _settings_for_templates
    templates.env.globals['supabase_url'] = _settings_for_templates.supabase_url
    templates.env.globals['supabase_anon_key'] = _settings_for_templates.supabase_anon_key
except Exception:
    pass

# Redis connection
redis_client = None

async def get_redis():
    """Get Redis client"""
    global redis_client
    if redis_client is None:
        redis_client = await aioredis.from_url("redis://localhost:6379")
    return redis_client

# Import proper admin authentication
from app.admin.auth import get_admin_user, require_admin_role

# Login/Logout Routes
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Admin login page"""
    from app.config import settings
    import os
    return templates.TemplateResponse(
        "admin/login.html",
        {
            "request": request,
            "supabase_url": settings.supabase_url,
            "supabase_anon_key": settings.supabase_anon_key,
            "development_mode": os.getenv("DEVELOPMENT_MODE", "false").lower() == "true",
        },
    )

@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    """Password reset page"""
    from app.config import settings
    return templates.TemplateResponse("admin/reset-password.html", {
        "request": request,
        "supabase_url": settings.supabase_url,
        "supabase_anon_key": settings.supabase_anon_key,
    })

@router.post("/login")
async def login(request: Request):
    """Handle login form submission via Supabase"""
    from app.config import settings
    from fastapi.responses import RedirectResponse

    # Get form data
    form = await request.form()
    email = form.get("email", "")
    password = form.get("password", "")

    if not email or not password:
        return templates.TemplateResponse(
            "admin/login.html",
            {
                "request": request,
                "error": "Email and password are required",
                "supabase_url": settings.supabase_url,
                "supabase_anon_key": settings.supabase_anon_key,
                "development_mode": os.getenv("DEVELOPMENT_MODE", "false").lower() == "true",
            },
        )

    try:
        # Authenticate with Supabase
        import httpx
        async with httpx.AsyncClient() as client:
            auth_response = await client.post(
                f"{settings.supabase_url}/auth/v1/token?grant_type=password",
                headers={
                    "apikey": settings.supabase_anon_key,
                    "Content-Type": "application/json",
                },
                json={"email": email, "password": password},
            )

            if auth_response.status_code == 200:
                auth_data = auth_response.json()
                access_token = auth_data.get("access_token")

                if access_token:
                    # Set cookie and redirect to admin dashboard
                    response = RedirectResponse(url="/admin/", status_code=303)
                    response.set_cookie(
                        key="admin_token",
                        value=access_token,
                        max_age=28800,  # 8 hours
                        path="/",
                        httponly=True,  # Prevent XSS token theft
                        secure=True,  # HTTPS only
                        samesite="lax",
                    )
                    return response

            # Authentication failed
            error_data = auth_response.json() if auth_response.status_code != 200 else {}
            error_msg = error_data.get("msg", "Invalid login credentials")

    except Exception as e:
        error_msg = f"Authentication error: {str(e)}"

    return templates.TemplateResponse(
        "admin/login.html",
        {
            "request": request,
            "error": error_msg,
            "supabase_url": settings.supabase_url,
            "supabase_anon_key": settings.supabase_anon_key,
            "development_mode": os.getenv("DEVELOPMENT_MODE", "false").lower() == "true",
        },
    )

@router.post("/logout")
async def logout(request: Request):
    """Admin logout"""
    response = RedirectResponse(url="/admin/login", status_code=303)
    # Delete cookie with same flags used when setting it to ensure proper removal
    response.delete_cookie(
        key="admin_token",
        path="/",
        secure=True,
        httponly=True,
        samesite="lax"
    )
    return response

@router.get("/auth/check")
async def check_auth(request: Request):
    """Check if user is authenticated"""
    try:
        user = await get_admin_user(request)
        return {"authenticated": True, "user": user}
    except HTTPException:
        return {"authenticated": False}


# Subscriber landing page - shows assigned sidekicks for chat
@router.get("/my-sidekicks", response_class=HTMLResponse)
async def subscriber_sidekicks_page(
    request: Request,
    user: Dict[str, Any] = Depends(get_admin_user)
):
    """
    Subscriber landing page showing their assigned sidekicks.
    Subscribers can only chat with sidekicks they're assigned to.

    Uses ClientConnectionManager to properly query from:
    - Shared pool (Adventurer tier)
    - Dedicated databases (Champion/Paragon tier)
    """
    from uuid import UUID

    # Get the subscriber's assigned client IDs
    subscriber_client_ids = user.get("visible_client_ids", [])
    tenant_assignments = user.get("tenant_assignments", {})
    if not subscriber_client_ids:
        subscriber_client_ids = tenant_assignments.get("subscriber_client_ids", [])

    logger.info(f"[MY-SIDEKICKS] Fetching sidekicks for subscriber {user.get('email')}, client_ids={subscriber_client_ids}")

    # Fetch sidekicks (agents) for the subscriber's assigned clients
    # Use connection manager to get proper database for each client
    sidekicks = []
    if subscriber_client_ids:
        connection_manager = get_connection_manager()
        for client_id in subscriber_client_ids:
            try:
                client_uuid = UUID(client_id) if isinstance(client_id, str) else client_id
                # Get the proper database client (shared pool or dedicated)
                client_db = connection_manager.get_client_db_client(client_uuid)

                # Query agents - filter by client_id (required for shared pool multi-tenancy)
                result = client_db.table('agents').select(
                    'id, name, agent_image, description, slug'
                ).eq('client_id', str(client_uuid)).execute()

                if result.data:
                    # Add client_id to each sidekick for the template
                    for sidekick in result.data:
                        sidekick['client_id'] = str(client_uuid)
                    sidekicks.extend(result.data)
                    logger.info(f"[MY-SIDEKICKS] Found {len(result.data)} sidekicks for client {client_id}")
                else:
                    logger.info(f"[MY-SIDEKICKS] No sidekicks found for client {client_id}")

            except Exception as e:
                logger.error(f"[MY-SIDEKICKS] Failed to fetch sidekicks for client {client_id}: {e}")
                continue

    return templates.TemplateResponse("admin/subscriber_sidekicks.html", {
        "request": request,
        "user": user,
        "sidekicks": sidekicks,
    })


# Users management page (admin-only)
@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    user: Dict[str, Any] = Depends(require_admin_role),
    page: int = 1,
    search: str = ""
):
    """Users page with pagination and search. Requires admin role."""
    await supabase_manager.initialize()
    import httpx
    headers = {
        'apikey': os.getenv('SUPABASE_SERVICE_ROLE_KEY', ''),
        'Authorization': f"Bearer {os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')}",
    }

    # Pagination settings
    per_page = 20

    # Fetch all users (Supabase doesn't have good server-side search, so we filter client-side)
    all_users: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Fetch more users to allow for filtering
            r = await client.get(f"{os.getenv('SUPABASE_URL')}/auth/v1/admin/users", headers=headers, params={"per_page": 1000})
            if r.status_code == 200:
                data = r.json()
                all_users = data.get('users', [])
    except Exception:
        all_users = []

    # Apply search filter if provided
    search_lower = search.strip().lower()
    if search_lower:
        all_users = [u for u in all_users if search_lower in (u.get('email') or '').lower()]

    scoped_ids = get_scoped_client_ids(user)
    allowed_ids: Set[str] = set()
    if scoped_ids is not None:
        allowed_ids = {str(cid) for cid in scoped_ids}

        def extract_assignment_ids(meta: Dict[str, Any], keys: List[str]) -> List[str]:
            collected: List[str] = []
            assignments = meta.get('tenant_assignments') or {}
            if isinstance(assignments, dict):
                for key in keys:
                    raw = assignments.get(key)
                    if isinstance(raw, str) and raw.strip():
                        collected.append(raw.strip())
                    elif isinstance(raw, (list, tuple, set)):
                        collected.extend([str(v) for v in raw if v])
            return collected

        filtered_users: List[Dict[str, Any]] = []
        for record in all_users:
            metadata = record.get('user_metadata') or {}
            admin_ids = extract_assignment_ids(metadata, ['admin_client_ids'])
            subscriber_ids = extract_assignment_ids(metadata, ['subscriber_client_ids'])
            combined = {str(cid) for cid in admin_ids + subscriber_ids}
            if combined & allowed_ids:
                filtered_users.append(record)
        all_users = filtered_users

    # Calculate pagination
    total_users = len(all_users)
    total_pages = max(1, (total_users + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    users = all_users[start_idx:end_idx]
    # Prepare role id cache for RBAC if available
    role_id_map: Dict[str, Optional[str]] = {"super_admin": None, "admin": None, "subscriber": None}
    try:
        admin_client = supabase_manager.admin_client
        for key in list(role_id_map.keys()):
            try:
                row = (
                    admin_client.table('roles')
                    .select('id,key')
                    .eq('key', key)
                    .single()
                    .execute()
                    .data
                )
                role_id_map[key] = row.get('id') if row else None
            except Exception:
                role_id_map[key] = None
    except Exception:
        pass

    enriched = []
    for u in users:
        user_id = u.get('id')
        user_email = u.get('email')
        created_at = u.get('created_at')
        metadata = u.get('user_metadata') or {}

        roles_display: List[str] = []

        # Try RBAC first
        try:
            admin_client = supabase_manager.admin_client
            # Platform super_admin
            sa_id = role_id_map.get('super_admin')
            if sa_id:
                pr = (
                    admin_client.table('platform_role_memberships')
                    .select('role_id')
                    .eq('user_id', user_id)
                    .eq('role_id', sa_id)
                    .execute()
                    .data
                )
                if pr:
                    roles_display.append('Super Admin')

            # Tenant memberships
            admin_id = role_id_map.get('admin')
            sub_id = role_id_map.get('subscriber')
            try:
                tm_rows = (
                    admin_client.table('tenant_memberships')
                    .select('client_id,role_id,status')
                    .eq('user_id', user_id)
                    .execute()
                    .data
                ) or []
            except Exception:
                tm_rows = []
            if allowed_ids:
                tm_rows = [row for row in tm_rows if str(row.get('client_id')) in allowed_ids]
            if tm_rows:
                admin_count = sum(1 for r in tm_rows if r.get('role_id') == admin_id)
                sub_count = sum(1 for r in tm_rows if r.get('role_id') == sub_id)
                if admin_count:
                    roles_display.append(f'Admin ({admin_count})')
                if sub_count:
                    roles_display.append(f'Subscriber ({sub_count})')
        except Exception:
            # Ignore RBAC errors
            pass

        # Fallback to Auth metadata if no RBAC-derived roles
        if not roles_display and isinstance(metadata, dict):
            if (metadata.get('platform_role') or '').lower() == 'super_admin':
                roles_display.append('Super Admin')
            ta = metadata.get('tenant_assignments') or {}
            if isinstance(ta, dict):
                admin_ids = ta.get('admin_client_ids') or []
                subscriber_ids = ta.get('subscriber_client_ids') or []
                if admin_ids:
                    roles_display.append(f'Admin ({len(admin_ids)})')
                if subscriber_ids:
                    roles_display.append(f'Subscriber ({len(subscriber_ids)})')

        enriched.append({
            "id": user_id,
            "email": user_email,
            "created_at": created_at,
            "roles": roles_display if roles_display else [],
        })
    # Fetch clients for client-scoped role assignment
    try:
        from app.core.dependencies import get_client_service
        clients = await get_client_service().get_all_clients()
        if allowed_ids:
            clients = [c for c in clients if str(getattr(c, 'id', '')) in allowed_ids]
        clients_ctx = [{"id": c.id, "name": c.name} for c in clients]
    except Exception:
        clients_ctx = []
    from app.config import settings
    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "user": user,
        "users": enriched,
        "clients": clients_ctx,
        "supabase_url": settings.supabase_url,
        "supabase_anon_key": settings.supabase_anon_key,
        "page": page,
        "total_pages": total_pages,
        "total_users": total_users,
        "search": search,
        "per_page": per_page
    })

@router.post("/users/create")
async def users_create(request: Request, admin: Dict[str, Any] = Depends(require_admin_role)):
    """Create a new user via Supabase Admin API, then assign platform role membership. Requires admin role."""
    try:
        data = await request.json()
        full_name = (data.get('full_name') or '').strip()
        email = (data.get('email') or '').strip()
        role_key = (data.get('role_key') or 'subscriber').strip()  # expected: super_admin | admin | subscriber
        client_ids_raw = data.get('client_ids') or []
        if isinstance(client_ids_raw, str):
            client_ids_raw = [client_ids_raw]
        client_ids = [str(cid) for cid in client_ids_raw if cid]
        if not full_name or not email:
            raise HTTPException(status_code=400, detail="Email is required")

        scoped_ids = get_scoped_client_ids(admin)
        # Adventurer accounts have no client picker — auto-assign their own client(s)
        if not client_ids and scoped_ids and role_key in ('admin', 'subscriber'):
            client_ids = [str(cid) for cid in scoped_ids]
        if scoped_ids is not None:
            allowed_ids = {str(cid) for cid in scoped_ids}
            if role_key == 'super_admin':
                raise HTTPException(status_code=403, detail="Insufficient permissions to assign super admin role")
            invalid_ids = [cid for cid in client_ids if cid not in allowed_ids]
            if invalid_ids:
                raise HTTPException(status_code=403, detail="One or more client IDs are not accessible to this admin")

        import httpx
        from app.config import settings
        supabase_url = settings.supabase_url
        service_key = settings.supabase_service_role_key
        if not supabase_url or not service_key:
            return HTMLResponse(status_code=500, content="Supabase credentials not configured")
        headers = {'apikey': service_key, 'Authorization': f'Bearer {service_key}', 'Content-Type': 'application/json'}

        # Create user (no password -> magic link invite disabled here; using email only)
        # Include role/client assignments in initial user_metadata so roles persist even without RBAC tables
        initial_user_metadata = {"full_name": full_name}
        if role_key == 'super_admin':
            initial_user_metadata.update({
                'platform_role': 'super_admin',
                'tenant_assignments': None
            })
        elif role_key in ('admin', 'subscriber'):
            initial_user_metadata.update({
                'platform_role': None,
                'tenant_assignments': {
                    'admin_client_ids': client_ids if role_key == 'admin' else [],
                    'subscriber_client_ids': client_ids if role_key == 'subscriber' else [],
                }
            })
        # Create user with a temporary password, then send password reset so they set their own
        import secrets
        temp_password = secrets.token_urlsafe(24)
        create_payload = {
            "email": email,
            "password": temp_password,
            "email_confirm": True,
            "user_metadata": initial_user_metadata,
        }
        is_new_user = False
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{supabase_url}/auth/v1/admin/users", headers=headers, json=create_payload)
        if r.status_code in (200, 201):
            user = r.json()
            user_id = user.get('id')
            is_new_user = True
        else:
            # User may already exist — look them up
            try:
                if r.status_code in (400, 409, 422):
                    async with httpx.AsyncClient(timeout=10) as client:
                        r_lookup = await client.get(
                            f"{supabase_url}/auth/v1/admin/users",
                            headers=headers,
                            params={"email": email}
                        )
                    if r_lookup.status_code == 200:
                        data = r_lookup.json()
                        existing = [u for u in data.get("users", []) if u.get("email", "").lower() == email.lower()]
                        if existing:
                            user_id = existing[0].get("id")
                        else:
                            return HTMLResponse(status_code=500, content=f"Failed to create user: {r.text}")
                    else:
                        return HTMLResponse(status_code=500, content=f"Failed to create user: {r.text}")
                else:
                    return HTMLResponse(status_code=500, content=f"Failed to create user: {r.text}")
            except Exception:
                return HTMLResponse(status_code=500, content="Error creating user")

        # Send password recovery email so the user can set their own password
        if is_new_user:
            try:
                domain = os.getenv("DOMAIN_NAME", "staging.sidekickforge.com")
                recovery_payload = {
                    "email": email,
                    "redirect_to": f"https://{domain}/admin/reset-password",
                }
                async with httpx.AsyncClient(timeout=10) as client:
                    recover_r = await client.post(
                        f"{supabase_url}/auth/v1/recover",
                        headers=headers,
                        json=recovery_payload,
                    )
                if recover_r.status_code in (200, 201):
                    logger.info(f"Password recovery email sent to {email}")
                else:
                    logger.warning(f"Recovery email failed ({recover_r.status_code}): {recover_r.text}")
            except Exception as e:
                logger.warning(f"Failed to send recovery email to {email}: {e}")

        # Helper to ensure basic roles exist
        def _seed_core_roles(client):
            try:
                core = [
                    {"key": "super_admin", "scope": "platform", "description": "Platform-wide administrator"},
                    {"key": "admin", "scope": "tenant", "description": "Tenant administrator"},
                    {"key": "subscriber", "scope": "tenant", "description": "Use-only role"},
                ]
                for r in core:
                    client.table('roles').upsert(r, on_conflict='key').execute()
            except Exception:
                pass

        # Assign roles based on selection (best-effort; don't fail user creation)
        try:
            await supabase_manager.initialize()
            admin_client = supabase_manager.admin_client
            # Platform Super Admin
            if role_key == 'super_admin':
                role_row = None
                try:
                    role_row = admin_client.table('roles').select('id').eq('key', role_key).single().execute().data
                except Exception:
                    _seed_core_roles(admin_client)
                    role_row = admin_client.table('roles').select('id').eq('key', role_key).single().execute().data
                if role_row:
                    admin_client.table('platform_role_memberships').upsert({
                        'user_id': user_id,
                        'role_id': role_row['id']
                    }).execute()
            # Tenant-scoped roles: admin or subscriber
            elif role_key in ('admin','subscriber') and client_ids:
                role_row = None
                try:
                    role_row = admin_client.table('roles').select('id').eq('key', role_key).single().execute().data
                except Exception:
                    _seed_core_roles(admin_client)
                    role_row = admin_client.table('roles').select('id').eq('key', role_key).single().execute().data
                if role_row:
                    # Upsert tenant memberships for each selected client
                    for cid in client_ids:
                        admin_client.table('tenant_memberships').upsert({
                            'user_id': user_id,
                            'client_id': cid,
                            'role_id': role_row['id'],
                            'status': 'active'
                        }).execute()
        except Exception as assign_error:
            logger.error(f"Role assignment failed for {email}: {assign_error}", exc_info=True)
            # Fallback: persist role info in Supabase Auth user_metadata so UI and auth can resolve roles
            try:
                meta_update = {}
                if role_key == 'super_admin':
                    meta_update = {
                        'user_metadata': {
                            'platform_role': 'super_admin',
                            'tenant_assignments': None
                        }
                    }
                elif role_key in ('admin', 'subscriber'):
                    meta_update = {
                        'user_metadata': {
                            'platform_role': None,
                            'tenant_assignments': {
                                'admin_client_ids': client_ids if role_key == 'admin' else [],
                                'subscriber_client_ids': client_ids if role_key == 'subscriber' else [],
                            }
                        }
                    }
                if meta_update:
                    async with httpx.AsyncClient(timeout=10) as client:
                        r_meta = await client.patch(f"{supabase_url}/auth/v1/admin/users/{user_id}", headers=headers, json=meta_update)
                    if r_meta.status_code not in (200, 201):
                        async with httpx.AsyncClient(timeout=10) as client:
                            r_meta = await client.put(f"{supabase_url}/auth/v1/admin/users/{user_id}", headers=headers, json=meta_update)
                    if r_meta.status_code in (200, 201):
                        logger.info(f"User metadata role assignment saved for {email} as {role_key}")
                    else:
                        logger.error(f"Failed to save user metadata for {email}: {r_meta.status_code} {r_meta.text}")
            except Exception as meta_err:
                logger.error(f"Metadata fallback for role assignment failed for {email}: {meta_err}", exc_info=True)

        return HTMLResponse(status_code=201, content="Created")
    except HTTPException:
        raise
    except Exception as e:
        return HTMLResponse(status_code=500, content="Error creating user")

@router.get("/users/{user_id}/assignments")
async def get_user_assignments(user_id: str, admin: Dict[str, Any] = Depends(get_admin_user)):
    """Return current role assignment for a user to prefill the edit modal."""
    try:
        scoped_ids = get_scoped_client_ids(admin)
        allowed_ids: Optional[Set[str]] = None if scoped_ids is None else {str(cid) for cid in scoped_ids}

        # Read from Supabase Auth user_metadata
        from app.config import settings
        import httpx
        headers = {
            'apikey': settings.supabase_service_role_key,
            'Authorization': f'Bearer {settings.supabase_service_role_key}',
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{settings.supabase_url}/auth/v1/admin/users/{user_id}", headers=headers)
        if r.status_code == 200:
            user = r.json()
            meta = user.get('user_metadata', {}) or {}
            platform_role = meta.get('platform_role')
            if platform_role == 'super_admin':
                return {"role_key": "super_admin", "client_ids": []}
            ta = meta.get('tenant_assignments', {}) or {}
            admin_ids = ta.get('admin_client_ids') or []
            subscriber_ids = ta.get('subscriber_client_ids') or []
            if admin_ids:
                if allowed_ids is not None:
                    admin_ids = [cid for cid in admin_ids if str(cid) in allowed_ids]
                if admin_ids:
                    return {"role_key": "admin", "client_ids": admin_ids}
            if subscriber_ids:
                if allowed_ids is not None:
                    subscriber_ids = [cid for cid in subscriber_ids if str(cid) in allowed_ids]
                if subscriber_ids:
                    return {"role_key": "subscriber", "client_ids": subscriber_ids}

        # Default if no assignments found
        return {"role_key": "subscriber", "client_ids": []}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch user assignments for {user_id}: {e}")
        return HTMLResponse(status_code=500, content="Failed to fetch user assignments")

@router.post("/users/update")
async def update_user_roles(request: Request, admin: Dict[str, Any] = Depends(require_admin_role)):
    """Update a user's role assignments via Supabase Auth user_metadata. Requires admin role."""
    try:
        data = await request.json()
        user_id = (data.get("user_id") or "").strip()
        role_key = (data.get("role_key") or "").strip()
        client_ids_raw = data.get("client_ids") or []
        if isinstance(client_ids_raw, str):
            client_ids_raw = [client_ids_raw]
        client_ids = [str(cid) for cid in client_ids_raw if cid]

        if not user_id or role_key not in ("admin", "subscriber"):
            raise HTTPException(status_code=400, detail="Invalid payload")

        scoped_ids = get_scoped_client_ids(admin)
        # Adventurer accounts: auto-assign own client(s)
        if not client_ids and scoped_ids and role_key in ('admin', 'subscriber'):
            client_ids = [str(cid) for cid in scoped_ids]
        if not client_ids:
            raise HTTPException(status_code=400, detail="At least one client_id is required for this role")
        if scoped_ids is not None:
            allowed_ids = {str(cid) for cid in scoped_ids}
            invalid_ids = [cid for cid in client_ids if cid not in allowed_ids]
            if invalid_ids:
                raise HTTPException(status_code=403, detail="One or more client IDs are not accessible to this admin")

        from app.config import settings
        import httpx
        headers = {
            'apikey': settings.supabase_service_role_key,
            'Authorization': f'Bearer {settings.supabase_service_role_key}',
            'Content-Type': 'application/json',
        }
        meta_update = {
            'user_metadata': {
                'platform_role': None,
                'tenant_assignments': {
                    'admin_client_ids': client_ids if role_key == 'admin' else [],
                    'subscriber_client_ids': client_ids if role_key == 'subscriber' else [],
                }
            }
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(
                f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                headers=headers,
                json=meta_update
            )
        if r.status_code in (200, 201):
            return HTMLResponse(status_code=200, content="Updated")
        else:
            logger.error(f"Failed to update user {user_id}: {r.status_code} {r.text}")
            return HTMLResponse(status_code=500, content="Failed to update user")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update user: {e}")
        return HTMLResponse(status_code=500, content="Failed to update user")

@router.post("/users/delete")
async def delete_user(request: Request, admin: Dict[str, Any] = Depends(require_admin_role)):
    """Delete a user from Supabase Auth. Requires admin role."""
    try:
        data = await request.json()
        user_id = (data.get("user_id") or "").strip()
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        if user_id == admin.get("user_id"):
            raise HTTPException(status_code=403, detail="You cannot delete your own account")

        from app.config import settings
        import httpx
        headers = {
            'apikey': settings.supabase_service_role_key,
            'Authorization': f'Bearer {settings.supabase_service_role_key}',
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.delete(
                f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                headers=headers,
            )
        if r.status_code in (200, 204):
            return HTMLResponse(status_code=200, content="Deleted")
        else:
            logger.error(f"Failed to delete user {user_id}: {r.status_code} {r.text}")
            return HTMLResponse(status_code=500, content="Failed to delete user")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete user: {e}")
        return HTMLResponse(status_code=500, content="Failed to delete user")

@router.post("/users/set-password")
async def set_user_password(request: Request, admin: Dict[str, Any] = Depends(require_admin_role)):
    """Set a Supabase Auth password for a user (admin operation). Requires admin role."""
    try:
        payload = await request.json()
        user_id = (payload.get("user_id") or "").strip()
        email = (payload.get("email") or "").strip()
        new_password = payload.get("password") or ""
        if not user_id or not new_password:
            raise HTTPException(status_code=400, detail="user_id and password are required")
        if len(new_password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

        from app.config import settings
        import httpx
        headers = {
            'apikey': settings.supabase_service_role_key,
            'Authorization': f'Bearer {settings.supabase_service_role_key}',
            'Content-Type': 'application/json',
        }

        # Verify user exists and email matches
        async with httpx.AsyncClient(timeout=10) as client:
            r_get = await client.get(
                f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                headers=headers
            )
        if r_get.status_code != 200:
            return HTMLResponse(status_code=404, content="User not found")
        user = r_get.json()
        current_email = (user.get('email') or '').strip()
        # If email provided and differs, attempt to update both email and password together
        update_body: Dict[str, Any] = { 'password': new_password }
        if email and email.lower() != current_email.lower():
            update_body['email'] = email

        async with httpx.AsyncClient(timeout=10) as client:
            r_patch = await client.patch(
                f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                headers=headers,
                json=update_body
            )
            if r_patch.status_code not in (200, 201):
                # Some installations may require PUT
                r_put = await client.put(
                    f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                    headers=headers,
                    json=update_body
                )
                if r_put.status_code not in (200, 201):
                    return HTMLResponse(status_code=500, content=f"Failed to update password: {r_patch.text or r_put.text}")

        return HTMLResponse(status_code=200, content="Password updated")
    except HTTPException:
        raise
    except Exception as e:
        return HTMLResponse(status_code=500, content="Error updating password")

async def get_system_summary(admin_user: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Get system-wide summary statistics scoped to the current admin."""
    # Get all clients from Supabase
    from app.integrations.livekit_client import livekit_manager
    from app.services.client_service_multitenant import ClientService as PlatformClientService
    client_service = PlatformClientService()
    
    try:
        clients = await client_service.get_clients()
        scoped_ids = get_scoped_client_ids(admin_user)
        if scoped_ids is not None:
            scoped_strs = {str(cid) for cid in scoped_ids}
            clients = [c for c in clients if str(getattr(c, 'id', '')) in scoped_strs]
        total_clients = len(clients)
    except Exception as e:
        logger.warning(f"Failed to get clients: {e}")
        # Check if it's an auth error
        if "401" in str(e) or "Invalid API key" in str(e):
            logger.error("❌ CRITICAL: Cannot access platform database - Invalid Supabase credentials")
            logger.error("   Please update SUPABASE_SERVICE_ROLE_KEY in .env with the actual service role key")
        clients = []
        total_clients = 0
    
    # Check actual container status
    active_containers = 0
    stopped_containers = 0
    
    # With worker pool architecture, we show worker status instead
    try:
        import docker
        client = docker.from_env()
        
        # Count agent workers
        for container in client.containers.list():
            if "agent-worker" in container.name:
                if container.status == "running":
                    active_containers += 1
                else:
                    stopped_containers += 1
                    
    except Exception as e:
        logger.warning(f"Failed to get worker status: {e}")
        # Show default worker count
        active_containers = 3  # We started 3 workers
        active_containers = total_clients
    
    # Get active sessions from LiveKit
    total_sessions = 0
    try:
        # Initialize LiveKit if needed
        if not livekit_manager._initialized:
            await livekit_manager.initialize()
        
        # Get all rooms from LiveKit using the refactored manager
        livekit_api = livekit_manager._get_api_client()
        rooms = await livekit_api.room.list_rooms(api.ListRoomsRequest())
        
        # Count participants across all rooms
        for room in rooms.rooms:
            total_sessions += room.num_participants
            
    except Exception as e:
        logger.warning(f"Failed to get LiveKit sessions: {e}")
        total_sessions = 0
    
    # Mock metrics for now - in production these would come from actual monitoring
    total_cpu = active_containers * 15.5  # Mock 15.5% CPU per container
    total_memory = active_containers * 512  # Mock 512MB per container
    
    return {
        "total_clients": total_clients,
        "active_containers": active_containers,
        "stopped_containers": stopped_containers,
        "total_sessions": total_sessions,
        "avg_cpu": round(total_cpu / max(active_containers, 1), 1),
        "total_memory_gb": round(total_memory / 1024, 2),
        "timestamp": datetime.now().isoformat()
    }

async def get_all_clients_with_containers(admin_user: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Get all clients with their container status"""
    # Use the existing client service
    from app.integrations.livekit_client import livekit_manager
    from app.services.client_service_multitenant import ClientService as PlatformClientService

    client_service = PlatformClientService()
    scoped_ids = get_scoped_client_ids(admin_user)

    try:
        # Get all clients from platform database
        logger.info("Fetching all clients from platform database...")
        clients = await client_service.get_clients()
        logger.info(f"✅ Successfully fetched {len(clients)} clients from platform database")

        if scoped_ids is not None:
            clients = [
                client for client in clients
                if getattr(client, 'id', None) and str(getattr(client, 'id')) in scoped_ids
            ]

        # Get LiveKit room data for session counting
        room_sessions = {}
        try:
            if not livekit_manager._initialized:
                await livekit_manager.initialize()
            
            livekit_api = livekit_manager._get_api_client()
            rooms = await livekit_api.room.list_rooms(api.ListRoomsRequest())
            
            # Count sessions by client (assuming room name contains client id)
            for room in rooms.rooms:
                # Extract client id from room metadata or name
                # For now, count all participants in all rooms
                for client in clients:
                    if client.id in room.name or (room.metadata and client.id in room.metadata):
                        room_sessions[client.id] = room_sessions.get(client.id, 0) + room.num_participants
                        
        except Exception as e:
            logger.warning(f"Failed to get LiveKit room data: {e}")
        
        # Convert to dict format for templates
        clients_data = []
        for client in clients:
            try:
                additional_settings = {}
                if hasattr(client, 'settings') and getattr(client.settings, 'additional_settings', None):
                    additional_settings = dict(client.settings.additional_settings)

                livekit_config = None
                if hasattr(client, 'settings') and getattr(client.settings, 'livekit_config', None):
                    livekit_config = dict(client.settings.livekit_config)

                provisioning_status = getattr(client, 'provisioning_status', 'unknown') or 'unknown'
                supabase_url = getattr(client, 'supabase_project_url', None)

                client_dict = {
                    "id": client.id,
                    "name": client.name,
                    "slug": additional_settings.get('slug'),
                    "domain": additional_settings.get('domain', ''),
                    "description": additional_settings.get('description'),
                    "status": provisioning_status,
                    "active": provisioning_status == 'ready',
                    "created_at": client.created_at.isoformat() if hasattr(client, 'created_at') and client.created_at else None,
                    "client_id": client.id,
                    "client_name": client.name,
                    "cpu_usage": 15.5,
                    "memory_usage": 512,
                    "active_sessions": room_sessions.get(client.id, 0),  # Real session count from LiveKit
                    "supabase_project_url": supabase_url,
                    "supabase_project_ref": getattr(client, 'supabase_project_ref', None),
                    "supabase_ready": bool(supabase_url),
                    "livekit_ready": bool(livekit_config and livekit_config.get('url') and livekit_config.get('api_key')),
                    "provisioning_status": provisioning_status,
                    "provisioning_error": getattr(client, 'provisioning_error', None),
                    "auto_provision": bool(getattr(client, 'auto_provision', False)),
                    "settings": {
                        "livekit": livekit_config,
                        "additional_settings": additional_settings
                    }
                }
                logger.debug(f"Processed client: {client.name} (ID: {client.id})")
            except Exception as e:
                logger.error(f"Failed to process client {getattr(client, 'name', 'Unknown')}: {e}", exc_info=True)
                # Create minimal client dict to avoid complete failure
                client_dict = {
                    "id": getattr(client, 'id', 'unknown'),
                    "name": getattr(client, 'name', 'Unknown Client'),
                    "slug": None,
                    "domain": '',
                    "description": None,
                    "status": "error",
                    "active": False,
                    "created_at": None,
                    "client_id": getattr(client, 'id', 'unknown'),
                    "client_name": getattr(client, 'name', 'Unknown Client'),
                    "cpu_usage": 0,
                    "memory_usage": 0,
                    "active_sessions": 0,
                    "settings": {
                        "livekit": None,
                        "additional_settings": {}
                    },
                    "supabase_project_url": None,
                    "supabase_project_ref": None,
                    "supabase_ready": False,
                    "livekit_ready": False,
                    "provisioning_status": "error",
                    "provisioning_error": str(e),
                    "auto_provision": False
                }
            
            clients_data.append(client_dict)
        
        return clients_data
    except Exception as e:
        logger.error(f"Error fetching clients: {e}")
        return []

async def get_container_detail(client_id: str) -> Dict[str, Any]:
    """Get worker pool information (containers are deprecated)"""
    # Return mock data explaining the new architecture
    return {
        "id": client_id,
        "name": f"Worker Pool (Client: {client_id})",
        "status": "active",
        "created_at": datetime.now().isoformat(),
        "cpu_usage": 0.0,
        "memory_usage": 0.0,
        "health": {"status": "healthy"},
        "message": "Agents now use a shared worker pool instead of individual containers"
    }


async def get_all_agents() -> List[Dict[str, Any]]:
    """Get all agents from all clients"""
    try:
        from uuid import UUID
        from app.services.client_service_multitenant import ClientService as PlatformClientService
        from app.services.agent_service_multitenant import AgentService as PlatformAgentService

        client_service = PlatformClientService()
        agent_service = PlatformAgentService()

        clients = await client_service.get_clients()
        all_agents: List[Dict[str, Any]] = []

        client_map = {client.id: client.name for client in clients}

        for client in clients:
            try:
                client_uuid = UUID(client.id)
                client_agents = await agent_service.get_agents(client_uuid)
                for agent in client_agents:
                    # Convert sound_settings to dict if it's a Pydantic model
                    sound_settings_raw = getattr(agent, 'sound_settings', {})
                    if hasattr(sound_settings_raw, 'model_dump'):
                        sound_settings_dict = sound_settings_raw.model_dump()
                    elif hasattr(sound_settings_raw, 'dict'):
                        sound_settings_dict = sound_settings_raw.dict()
                    else:
                        sound_settings_dict = sound_settings_raw or {}

                    agent_dict = {
                        "id": agent.id,
                        "slug": agent.slug,
                        "name": agent.name,
                        "description": getattr(agent, 'description', ''),
                        "agent_image": getattr(agent, 'agent_image', '') or '',
                        "client_id": agent.client_id,
                        "client_name": client_map.get(agent.client_id, client.name),
                        "status": "active" if getattr(agent, 'active', getattr(agent, 'enabled', True)) else "inactive",
                        "active": getattr(agent, 'active', getattr(agent, 'enabled', True)),
                        "enabled": getattr(agent, 'enabled', True),
                        "created_at": agent.created_at.isoformat() if hasattr(agent.created_at, 'isoformat') else str(agent.created_at),
                        "updated_at": getattr(agent, 'updated_at', ''),
                        "system_prompt": agent.system_prompt[:100] + "..." if agent.system_prompt and len(agent.system_prompt) > 100 else agent.system_prompt,
                        "voice_settings": getattr(agent, 'voice_settings', {}),
                        "sound_settings": sound_settings_dict,
                        "webhooks": getattr(agent, 'webhooks', {}),
                        "show_citations": getattr(agent, 'show_citations', True)
                    }
                    all_agents.append(agent_dict)
            except Exception as client_error:
                logger.warning(f"Failed to get agents for client {client.id}: {client_error}")
                continue

        return all_agents
    except Exception as e:
        logger.error(f"Error fetching agents: {e}")
        return []

# Routes

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Main admin dashboard with HTMX"""
    # Only superadmins can access the dashboard; all others go to sidekicks page
    if admin_user.get("role") != "superadmin":
        return RedirectResponse(url="/admin/agents", status_code=302)

    summary = await get_system_summary(admin_user)

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "summary": summary,
        "user": admin_user
    })


@router.get("/wizard", response_class=HTMLResponse)
async def wizard_page(
    request: Request,
    admin_user: Dict[str, Any] = Depends(require_admin_role)
):
    """Sidekick creation wizard page (full page - redirects to modal). Requires admin role."""
    # Check if user can create more sidekicks
    if not admin_user.get("can_create_sidekick", True):
        return RedirectResponse(url="/admin/agents?upgrade=1", status_code=302)
    # Redirect to agents page which will show the modal
    return RedirectResponse(url="/admin/agents?wizard=1", status_code=302)


@router.get("/wizard/modal", response_class=HTMLResponse)
async def wizard_modal(
    request: Request,
    admin_user: Dict[str, Any] = Depends(require_admin_role)
):
    """Sidekick creation wizard modal (HTMX partial). Requires admin role."""
    # Check if user can create more sidekicks
    if not admin_user.get("can_create_sidekick", True):
        # Return upgrade prompt instead of wizard
        return templates.TemplateResponse("admin/wizard/upgrade_prompt.html", {
            "request": request,
            "user": admin_user,
        })
    return templates.TemplateResponse("admin/wizard/wizard_modal.html", {
        "request": request,
        "user": admin_user,
    })


@router.get("/client-settings/{client_id}", response_class=HTMLResponse)
async def client_settings_redirect(
    client_id: str,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Redirect to the standard client detail page (for Adventurer nav compatibility)"""
    return RedirectResponse(url=f"/admin/clients/{client_id}", status_code=302)


@router.get("/clients", response_class=HTMLResponse)
async def clients_list(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Client management page"""
    # Non-superadmin users should go to their own client settings page
    if not admin_is_super(admin_user):
        client_id = admin_user.get("primary_client_id")
        if client_id:
            return RedirectResponse(url=f"/admin/clients/{client_id}", status_code=302)
        return RedirectResponse(url="/admin/agents", status_code=302)

    try:
        clients = await get_all_clients_with_containers(admin_user)
        logger.info(f"Admin Dashboard: Successfully prepared {len(clients)} clients for display")
        return templates.TemplateResponse("admin/clients.html", {
            "request": request,
            "clients": clients,
            "user": admin_user
        })
    except Exception as e:
        # CRITICAL: Log the actual error instead of failing silently
        logger.error(f"❌ Admin Dashboard: Failed to fetch clients: {e}", exc_info=True)
        # Return error to template
        return templates.TemplateResponse("admin/clients.html", {
            "request": request,
            "clients": [],
            "error": f"Failed to load clients: {e}",
            "user": admin_user
        })


@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail_page(
    client_id: str,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Client detail/configuration page with WordPress credentials"""
    try:
        ensure_client_access(client_id, admin_user)

        from app.core.dependencies import get_client_service, get_agent_service
        client_service = get_client_service()
        agent_service = get_agent_service()

        # Get client details
        client = await client_service.get_client(client_id, auto_sync=False)
        if not client:
            return RedirectResponse(
                url="/admin/clients?error=Client+not+found",
                status_code=303
            )

        # Normalize client to dict for template safety
        if hasattr(client, 'dict'):
            client_dict = client.dict()
        elif hasattr(client, 'model_dump'):
            client_dict = client.model_dump()
        else:
            client_dict = client

        # Get agents for this client
        agents = []
        try:
            agents = await agent_service.get_client_agents(client_id)
        except Exception as agent_err:
            logger.warning(f"Unable to load agents for client {client_id}: {agent_err}")

        # Masked API keys from connection manager
        masked_keys: Dict[str, Any] = {}
        uses_platform_inference = False
        try:
            connection_manager = get_connection_manager()
            api_keys = connection_manager.get_client_api_keys(uuid.UUID(client_id))

            # Check if client uses Sidekick Forge Inference (platform keys)
            if api_keys.get('_uses_platform_keys'):
                uses_platform_inference = True
                masked_keys['_uses_platform_keys'] = True
                masked_keys['_platform_inference_name'] = api_keys.get('_platform_inference_name', 'Sidekick Forge Inference')
            else:
                for key, value in api_keys.items():
                    if key.startswith('_'):  # Skip internal flags
                        continue
                    if value and isinstance(value, str) and len(value) > 10:
                        masked_keys[key] = f"{value[:4]}...{value[-4:]}"
                    else:
                        masked_keys[key] = "Not configured" if not value else value
        except Exception as e:
            logger.warning(f"Unable to load API keys for client {client_id}: {e}")

        # Load WordPress sites for this client
        wordpress_sites: List[Dict[str, Any]] = []
        wordpress_error: Optional[str] = None
        try:
            wp_service = get_wordpress_service()
            sites = wp_service.list_sites(client_id=client_id)
            for site in sites:
                site_dict = site.dict() if hasattr(site, "dict") else dict(site)
                for ts_field in ("created_at", "updated_at", "last_seen_at"):
                    if site_dict.get(ts_field):
                        site_dict[ts_field] = str(site_dict[ts_field])
                wordpress_sites.append(site_dict)
        except Exception as e:
            logger.error(f"Failed to load WordPress sites for client {client_id}: {e}")
            wordpress_error = str(e)

        wordpress_api_endpoint = f"https://{settings.domain_name}/api/v1/wordpress/session/exchange"

        # Load usage data for Adventurer tier clients
        usage = None
        if admin_user.get("is_adventurer_only"):
            try:
                from app.services.usage_tracking import usage_tracking_service, QuotaType
                await usage_tracking_service.initialize()
                quotas = await usage_tracking_service.get_all_quotas(client_id)

                # Format for template
                usage = {
                    "voice": {
                        "used": quotas["voice"].used,
                        "limit": quotas["voice"].limit,
                        "remaining": quotas["voice"].remaining,
                        "percent_used": quotas["voice"].percent_used,
                        "minutes_used": getattr(quotas["voice"], "minutes_used", 0),
                        "minutes_limit": getattr(quotas["voice"], "minutes_limit", 100),
                    },
                    "text": {
                        "used": quotas["text"].used,
                        "limit": quotas["text"].limit,
                        "remaining": quotas["text"].remaining,
                        "percent_used": quotas["text"].percent_used,
                    },
                    "embedding": {
                        "used": quotas["embedding"].used,
                        "limit": quotas["embedding"].limit,
                        "remaining": quotas["embedding"].remaining,
                        "percent_used": quotas["embedding"].percent_used,
                    }
                }
            except Exception as usage_err:
                logger.warning(f"Failed to load usage data for client {client_id}: {usage_err}")

        return templates.TemplateResponse("admin/client_detail.html", {
            "request": request,
            "user": admin_user,
            "client": client_dict,
            "agents": agents,
            "api_keys": masked_keys,
            "wordpress_sites": wordpress_sites,
            "wordpress_error": wordpress_error,
            "wordpress_api_endpoint": wordpress_api_endpoint,
            "wordpress_domain": settings.domain_name,
            "usage": usage,
        })
    except Exception as e:
        logger.error(f"Error loading client detail page: {e}")
        return RedirectResponse(
            url=f"/admin/clients?error={str(e)}",
            status_code=303
        )

@router.get("/agents", response_class=HTMLResponse)
async def agents_page(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Agent management page"""
    # Redirect subscribers to their limited view
    if admin_user.get("role") == "subscriber":
        return RedirectResponse(url="/admin/my-sidekicks", status_code=302)

    scoped_ids = get_scoped_client_ids(admin_user)
    visible_client_ids: List[str] = [] if scoped_ids is None else list(scoped_ids)

    # Get agents from visible clients only
    try:
        if admin_is_super(admin_user):
            agents = await get_all_agents()
        else:
            from app.core.dependencies import get_agent_service
            agent_service = get_agent_service()
            agents = []
            client_name_cache: Dict[str, str] = {}
            from app.core.dependencies import get_client_service
            client_service = get_client_service()
            for cid in visible_client_ids:
                client_agents = await agent_service.get_client_agents(cid)
                # Attach client_name via client service
                if cid not in client_name_cache:
                    try:
                        client = await client_service.get_client(cid)
                        if hasattr(client, 'name'):
                            client_name_cache[cid] = client.name
                        elif isinstance(client, dict):
                            client_name_cache[cid] = client.get('name', 'Unknown')
                        else:
                            client_name_cache[cid] = 'Unknown'
                    except Exception:
                        client_name_cache[cid] = 'Unknown'
                client_name = client_name_cache[cid]
                for a in client_agents:
                    a_dict = a.dict() if hasattr(a, 'dict') else a
                    a_dict['client_name'] = client_name
                    agents.append(a_dict)
    except Exception as e:
        logger.error(f"Failed to load agents: {e}")
        agents = []
    
    # Get all clients for the filter dropdown (restrict for non-superadmin)
    try:
        from app.core.dependencies import get_client_service
        client_service = get_client_service()
        if admin_is_super(admin_user):
            clients = await client_service.get_all_clients()
        else:
            clients_all = await client_service.get_all_clients()
            visible_set = {str(cid) for cid in visible_client_ids}
            clients = [c for c in clients_all if str(getattr(c, 'id', '')) in visible_set]
    except Exception as e:
        logger.error(f"Failed to load clients: {e}")
        # Return minimal client data if database is inaccessible
        clients = []
    
    # Get tier limits for sidekick creation
    from app.services.tier_features import get_tier_features
    user_tier = admin_user.get("user_tier", "champion")
    tier_features = get_tier_features(user_tier) if user_tier else {}
    max_sidekicks = tier_features.get("max_sidekicks")  # None = unlimited
    current_sidekick_count = len(agents)
    can_create_sidekick = max_sidekicks is None or current_sidekick_count < max_sidekicks

    # Check if any visible client is still provisioning
    provisioning_in_progress = False
    if visible_client_ids and not admin_is_super(admin_user):
        try:
            from app.integrations.supabase_client import supabase_manager
            _sb = supabase_manager.admin_client
            if _sb:
                _prov = _sb.table('clients').select('provisioning_status').in_('id', visible_client_ids).execute()
                for row in (_prov.data or []):
                    _ps = (row.get('provisioning_status') or 'ready').lower()
                    if _ps not in ('ready', 'failed'):
                        provisioning_in_progress = True
                        break
        except Exception:
            pass

    return templates.TemplateResponse("admin/agents.html", {
        "request": request,
        "agents": agents,
        "clients": clients,
        "user": admin_user,
        "disable_stats_poll": True,
        "max_sidekicks": max_sidekicks,
        "current_sidekick_count": current_sidekick_count,
        "can_create_sidekick": can_create_sidekick,
        "provisioning_in_progress": provisioning_in_progress,
    })


@router.get("/debug/auth")
async def debug_auth(admin_user: Dict[str, Any] = Depends(get_admin_user)):
    """Debug endpoint to check auth info including can_create_sidekick"""
    return {
        "user_id": admin_user.get("user_id"),
        "email": admin_user.get("email"),
        "role": admin_user.get("role"),
        "user_tier": admin_user.get("user_tier"),
        "primary_client_id": admin_user.get("primary_client_id"),
        "visible_client_ids": admin_user.get("visible_client_ids"),
        "tenant_assignments": admin_user.get("tenant_assignments"),
        "can_create_sidekick": admin_user.get("can_create_sidekick"),
        "sidekick_limit": admin_user.get("sidekick_limit"),
        "current_sidekick_count": admin_user.get("current_sidekick_count"),
        "is_adventurer_only": admin_user.get("is_adventurer_only"),
    }

@router.get("/debug/agents")
async def debug_agents():
    """Debug endpoint to check agent loading"""
    try:
        # Test the same function used in the main agents page
        all_agents = await get_all_agents()
        
        debug_info = {
            "method": "get_all_agents()",
            "total_agents": len(all_agents),
            "agents": []
        }
        
        for a in all_agents:
            agent_info = {
                "type": type(a).__name__,
                "slug": a.get("slug"),
                "name": a.get("name"),
                "client_id": a.get("client_id"),
                "description": a.get("description", "")[:100],
                "system_prompt": a.get("system_prompt", "")[:100]
            }
            debug_info["agents"].append(agent_info)
        
        return debug_info
        
    except Exception as e:
        return {"error": str(e), "type": str(type(e))}
    
@router.get("/debug/agent-data/{client_id}/{agent_slug}")
async def debug_agent_data(
    client_id: str,
    agent_slug: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Debug what data is passed to agent detail template"""
    try:
        # Copy the same logic from agent_detail function
        if client_id == "global":
            ensure_client_or_global_access(client_id, admin_user)
            all_agents = await get_all_agents()
            agent = None
            for a in all_agents:
                if a.get("slug") == agent_slug:
                    agent = a
                    break
            if not agent:
                return {"error": "Agent not found"}
            client = {"id": "global", "name": "Global Agents", "domain": "global.local"}
        else:
            ensure_client_or_global_access(client_id, admin_user)
            from app.core.dependencies import get_client_service, get_agent_service
            client_service = get_client_service()
            agent_service = get_agent_service()
            agent = await agent_service.get_agent(client_id, agent_slug)
            client = await client_service.get_client(client_id)
            if not agent:
                return {"error": "Agent not found"}

        # Convert agent to dict - same logic as agent_detail
        if isinstance(agent, dict):
            agent_data = {
                "id": agent.get("id"),
                "slug": agent.get("slug"),
                "name": agent.get("name"),
                "description": agent.get("description", ""),
                "agent_image": agent.get("agent_image") or "",
                "system_prompt": agent.get("system_prompt", ""),
                "active": agent.get("active", agent.get("enabled", True)),
                "enabled": agent.get("enabled", True),
                "created_at": agent.get("created_at", ""),
                "updated_at": agent.get("updated_at", ""),
                "voice_settings": agent.get("voice_settings", {}),
                "sound_settings": agent.get("sound_settings", {}),
                "webhooks": agent.get("webhooks", {}),
                "tools_config": agent.get("tools_config", {}),
                "show_citations": agent.get("show_citations", True),
                "rag_results_limit": agent.get("rag_results_limit", 5),
                "client_id": client_id,
                "client_name": client.get("name", "Unknown") if isinstance(client, dict) else (getattr(client, 'name', 'Unknown') if client else "Unknown")
            }
        else:
            agent_data = {
                "id": agent.id,
                "slug": agent.slug,
                "name": agent.name,
                "description": agent.description or "",
                "agent_image": agent.agent_image or "",
                "system_prompt": agent.system_prompt,
                "active": getattr(agent, 'active', agent.enabled),
                "enabled": agent.enabled,
                "created_at": agent.created_at.isoformat() if hasattr(agent.created_at, 'isoformat') else str(agent.created_at),
                "updated_at": agent.updated_at.isoformat() if hasattr(agent.updated_at, 'isoformat') else str(agent.updated_at),
                "voice_settings": agent.voice_settings,
                "sound_settings": getattr(agent, 'sound_settings', {}),
                "webhooks": agent.webhooks,
                "tools_config": agent.tools_config or {},
                "show_citations": getattr(agent, 'show_citations', True),
                "rag_results_limit": getattr(agent, "rag_results_limit", 5),
                "client_id": client_id,
                "client_name": client.name if client else "Unknown"
            }

        return {
            "agent_data": agent_data,
            "agent_type": type(agent).__name__,
            "has_show_citations": "show_citations" in agent_data,
            "show_citations_value": agent_data.get("show_citations")
        }
        
    except Exception as e:
        return {"error": str(e), "traceback": str(e)}

@router.get("/debug/agent/{client_id}/{agent_slug}")
async def debug_single_agent(
    client_id: str,
    agent_slug: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Debug single agent lookup"""
    try:
        debug_info = {
            "input": {"client_id": client_id, "agent_slug": agent_slug},
            "logic": {
                "is_uuid_format": len(client_id) == 36 and '-' in client_id and client_id.count('-') == 4,
                "is_global": client_id == "global"
            },
            "search_result": None
        }

        if client_id == "global":
            ensure_client_or_global_access(client_id, admin_user)
            all_agents = await get_all_agents()
            for a in all_agents:
                if a.get("slug") == agent_slug:
                    debug_info["search_result"] = {
                        "found": True,
                        "agent": {
                            "slug": a.get("slug"),
                            "name": a.get("name"),
                            "description": a.get("description", "")[:200],
                            "system_prompt": a.get("system_prompt", "")[:200],
                            "client_id": a.get("client_id")
                        }
                    }
                    break
            
            if not debug_info["search_result"]:
                debug_info["search_result"] = {
                    "found": False,
                    "available_slugs": [a.get("slug") for a in all_agents]
                }
        else:
            ensure_client_or_global_access(client_id, admin_user)
            from app.core.dependencies import get_agent_service
            agent_service = get_agent_service()
            agent = await agent_service.get_agent(client_id, agent_slug)
            if agent:
                debug_info["search_result"] = {
                    "found": True,
                    "agent": {
                        "slug": getattr(agent, "slug", None),
                        "name": getattr(agent, "name", None),
                        "description": getattr(agent, "description", "")[:200],
                        "client_id": client_id,
                    },
                }
            else:
                debug_info["search_result"] = {
                    "found": False,
                    "message": "Agent not found for client",
                }

        return debug_info

    except Exception as e:
        return {"error": str(e), "type": str(type(e))}

@router.get("/knowledge-base", response_class=HTMLResponse)
async def knowledge_base_page(
    request: Request,
    admin_user: Dict[str, Any] = Depends(require_admin_role)
):
    """Knowledge Base management page. Requires admin role."""
    import time
    response = templates.TemplateResponse("admin/knowledge_base.html", {
        "request": request,
        "user": admin_user,
        "cache_bust": int(time.time()),
        "max_upload_size_mb": DOCUMENT_MAX_UPLOAD_MB,
        "supported_formats": "PDF, DOC, DOCX, TXT, MD, SRT",
    })
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@router.get("/tools", response_class=HTMLResponse)
async def tools_page(
    request: Request,
    admin_user: Dict[str, Any] = Depends(require_admin_role)
):
    """Abilities (Tools) management page. Requires admin role."""
    scoped_ids = get_scoped_client_ids(admin_user)
    visible_client_ids: List[str] = [] if scoped_ids is None else list(scoped_ids)

    clients: List[Any] = []
    default_client_id: str = ""
    try:
        from app.core.dependencies import get_client_service
        client_service = get_client_service()
        all_clients = await client_service.get_all_clients()
        if admin_is_super(admin_user):
            clients = all_clients
        else:
            visible_set = {str(cid) for cid in visible_client_ids}
            clients = [c for c in all_clients if str(getattr(c, 'id', '')) in visible_set]
        if not admin_is_super(admin_user) and clients:
            default_client_id = clients[0].id
    except Exception as exc:
        logger.error(f"Error loading clients for tools page: {exc}")
        clients = []
        default_client_id = ""

    try:
        return templates.TemplateResponse(
            "admin/tools.html",
            {
                "request": request,
                "user": admin_user,
                "clients": clients,
                "default_client_id": default_client_id,
            },
        )
    except Exception as e:
        logger.error(f"Error loading tools page: {e}")
        return templates.TemplateResponse(
            "admin/tools.html",
            {
                "request": request,
                "user": admin_user,
                "clients": clients,
                "default_client_id": default_client_id,
                "error": str(e),
            },
        )

@router.get("/api/tools")
async def admin_list_tools(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
    client_id: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    from app.core.dependencies import get_client_service

    tools_service = ToolsService(get_client_service())
    scoped_ids = get_scoped_client_ids(admin_user)
    try:
        if scoped_ids is None:
            tools = await tools_service.list_tools(client_id=client_id, scope=scope, type=type, search=search)
            allowed_ids = None
        else:
            allowed_ids = list(scoped_ids)
            tools_map: Dict[str, Any] = {}

            if client_id:
                ensure_client_access(client_id, admin_user)
                selected_tools = await tools_service.list_tools(client_id=client_id, scope=scope, type=type, search=search)
                for tool in selected_tools:
                    tools_map[tool.id] = tool
            else:
                if scope in (None, "global"):
                    for tool in await tools_service.list_tools(client_id=None, scope="global", type=type, search=search):
                        tools_map[tool.id] = tool
                if scope in (None, "client"):
                    for cid in allowed_ids:
                        for tool in await tools_service.list_tools(client_id=cid, scope="client", type=type, search=search):
                            tools_map[tool.id] = tool
            tools = list(tools_map.values())

        # Get which agents have each tool enabled
        tool_ids = [t.id for t in tools]
        agents_by_tool = await tools_service.get_agents_for_tools(tool_ids, allowed_ids)

        # Build response with agent info
        result = []
        for tool in tools:
            tool_dict = tool.dict()
            tool_dict["enabled_agents"] = agents_by_tool.get(tool.id, [])
            result.append(tool_dict)

        return JSONResponse(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/asana/status")
async def admin_asana_status(
    client_id: str = Query(...),
    validate: bool = Query(default=True, description="Validate and refresh token if needed"),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    service = get_asana_oauth_service()

    # First check if we have a connection at all
    record = service.get_connection(client_id)
    if not record:
        return {"connected": False}

    # Optionally validate and refresh the token
    # This ensures the connection is actually working and keeps tokens fresh
    if validate:
        try:
            from app.services.asana_oauth_service import AsanaOAuthError
            bundle = await service.ensure_valid_token(client_id)
            if not bundle:
                return {"connected": False, "error": "Token validation failed"}
            # Re-fetch the record after potential refresh
            record = service.get_connection(client_id)
            if not record:
                return {"connected": False}
        except AsanaOAuthError as exc:
            logger.warning(f"Asana token validation failed for client {client_id}: {exc}")
            # Connection was invalidated (e.g., refresh token expired)
            return {"connected": False, "error": str(exc)}

    extra = record.get("extra") or {}
    has_refresh_token = bool(record.get("refresh_token"))
    return {
        "connected": True,
        "updated_at": record.get("updated_at"),
        "expires_at": record.get("expires_at"),
        "user_gid": extra.get("gid"),
        "user_name": extra.get("name") or extra.get("email"),
        "workspaces": extra.get("workspaces"),
        "token_valid": True if validate else None,
        "has_refresh_token": has_refresh_token,
    }


@router.delete("/api/asana/connection")
async def admin_disconnect_asana(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    service = get_asana_oauth_service()
    service.disconnect(client_id)
    return {"success": True}


@router.get("/api/asana/oauth/start")
async def admin_asana_oauth_start(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="Unable to determine admin user ID for OAuth state.")

    service = get_asana_oauth_service()
    try:
        authorization_url = service.build_authorization_url(client_id, user_id)
    except AsanaOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"authorization_url": authorization_url}


@router.get("/oauth/asana/callback")
async def admin_asana_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    service = get_asana_oauth_service()

    if error:
        return HTMLResponse(
            f"<p>Asana returned an error: {error}</p>",
            status_code=400,
        )

    if not state:
        return HTMLResponse("<p>Missing state parameter.</p>", status_code=400)

    try:
        state_data = service.parse_state(state)
    except AsanaOAuthError as exc:
        return HTMLResponse(f"<p>{exc}</p>", status_code=400)

    client_id = state_data.get("client_id")
    if not client_id:
        return HTMLResponse("<p>Invalid state payload: missing client reference.</p>", status_code=400)

    if not code:
        return HTMLResponse("<p>Missing authorization code.</p>", status_code=400)

    try:
        await service.exchange_code(client_id, code)
    except AsanaOAuthError as exc:
        return HTMLResponse(f"<p>Failed to complete Asana OAuth: {exc}</p>", status_code=400)

    success_markup = (
        "<script>"
        "if(window.opener){window.opener.postMessage('asana-connected','*');}"
        "window.close();"
        "</script>"
        "<p>Asana connected successfully. You can close this window.</p>"
    )
    return HTMLResponse(success_markup)


# ---------------------------------------------------------------------------
# HelpScout OAuth Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/helpscout/connection")
async def admin_helpscout_connection_status(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
    validate: bool = Query(False, description="Validate and refresh the token if needed"),
):
    """Check the HelpScout connection status for a client."""
    ensure_client_access(client_id, admin_user)
    service = get_helpscout_oauth_service()

    # Check if credentials are configured (per-client or global)
    has_credentials = service.has_credentials(client_id)

    # Check if we have a connection at all
    record = service.get_connection(client_id)
    if not record or not record.get("access_token"):
        return {"connected": False, "has_credentials": has_credentials}

    # Optionally validate and refresh the token
    if validate:
        try:
            bundle = await service.ensure_valid_token(client_id)
            if not bundle:
                return {"connected": False, "has_credentials": has_credentials, "error": "Token validation failed"}
            # Re-fetch the record after potential refresh
            record = service.get_connection(client_id)
            if not record:
                return {"connected": False, "has_credentials": has_credentials}
        except HelpScoutOAuthError as exc:
            logger.warning(f"HelpScout token validation failed for client {client_id}: {exc}")
            return {"connected": False, "has_credentials": has_credentials, "error": str(exc)}

    extra = record.get("extra") or {}
    has_refresh_token = bool(record.get("refresh_token"))
    return {
        "connected": True,
        "has_credentials": has_credentials,
        "updated_at": record.get("updated_at"),
        "expires_at": record.get("expires_at"),
        "token_valid": True if validate else None,
        "has_refresh_token": has_refresh_token,
    }


@router.delete("/api/helpscout/connection")
async def admin_disconnect_helpscout(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Disconnect HelpScout for a client."""
    ensure_client_access(client_id, admin_user)
    service = get_helpscout_oauth_service()
    service.disconnect(client_id)
    return {"success": True}


@router.post("/api/helpscout/credentials")
async def admin_save_helpscout_credentials(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
    body: Dict[str, Any] = Body(...),
):
    """Save HelpScout OAuth App credentials for a client."""
    ensure_client_access(client_id, admin_user)

    oauth_client_id = body.get("oauth_client_id", "").strip()
    oauth_client_secret = body.get("oauth_client_secret", "").strip()

    if not oauth_client_id or not oauth_client_secret:
        raise HTTPException(status_code=400, detail="Both App ID and App Secret are required.")

    service = get_helpscout_oauth_service()
    try:
        service.save_client_oauth_credentials(client_id, oauth_client_id, oauth_client_secret)
    except HelpScoutOAuthError as exc:
        logger.error(f"HelpScout credential save failed for client {client_id}: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"Unexpected error saving HelpScout credentials for client {client_id}")
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}") from exc

    return {"success": True, "message": "Credentials saved successfully."}


@router.get("/api/helpscout/oauth/start")
async def admin_helpscout_oauth_start(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Initiate HelpScout OAuth flow."""
    ensure_client_access(client_id, admin_user)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="Unable to determine admin user ID for OAuth state.")

    service = get_helpscout_oauth_service()
    try:
        authorization_url = service.build_authorization_url(client_id, user_id)
    except HelpScoutOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"authorization_url": authorization_url}


@router.get("/oauth/helpscout/callback")
async def admin_helpscout_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Handle HelpScout OAuth callback."""
    service = get_helpscout_oauth_service()

    if error:
        return HTMLResponse(
            f"<p>HelpScout returned an error: {error}</p>",
            status_code=400,
        )

    if not state:
        return HTMLResponse("<p>Missing state parameter.</p>", status_code=400)

    try:
        state_data = service.parse_state(state)
    except HelpScoutOAuthError as exc:
        return HTMLResponse(f"<p>{exc}</p>", status_code=400)

    client_id = state_data.get("client_id")
    if not client_id:
        return HTMLResponse("<p>Invalid state payload: missing client reference.</p>", status_code=400)

    if not code:
        return HTMLResponse("<p>Missing authorization code.</p>", status_code=400)

    try:
        await service.exchange_code(client_id, code)
    except HelpScoutOAuthError as exc:
        return HTMLResponse(f"<p>Failed to complete HelpScout OAuth: {exc}</p>", status_code=400)

    success_markup = (
        "<script>"
        "if(window.opener){window.opener.postMessage('helpscout-connected','*');}"
        "window.close();"
        "</script>"
        "<p>HelpScout connected successfully. You can close this window.</p>"
    )
    return HTMLResponse(success_markup)


@router.post("/api/tools")
async def admin_create_tool(
    payload: ToolCreate,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    from app.core.dependencies import get_client_service

    tools_service = ToolsService(get_client_service())
    if payload.scope == "client":
        if not payload.client_id:
            raise HTTPException(status_code=400, detail="client_id is required for client-scoped tools")
        ensure_client_access(payload.client_id, admin_user)
    try:
        tool = await tools_service.create_tool(payload)
        return JSONResponse(tool.dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/tools/{tool_id}")
async def admin_delete_tool(
    tool_id: str,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
    client_id: Optional[str] = Query(None),
):
    from app.core.dependencies import get_client_service

    tools_service = ToolsService(get_client_service())
    resolved_client_id = await ensure_tool_access(tool_id, admin_user, tools_service, client_id)
    try:
        await tools_service.delete_tool(client_id or resolved_client_id, tool_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"success": True})


@router.patch("/api/tools/{tool_id}")
async def admin_update_tool(
    tool_id: str,
    payload: ToolUpdate,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
    client_id: Optional[str] = Query(None),
):
    from app.core.dependencies import get_client_service

    tools_service = ToolsService(get_client_service())
    resolved_client_id = await ensure_tool_access(tool_id, admin_user, tools_service, client_id)
    try:
        tool = await tools_service.update_tool(client_id or resolved_client_id, tool_id, payload)
        return JSONResponse(tool.dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/agents/{agent_id}/tools")
async def admin_list_agent_tools(
    agent_id: str,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
    client_id: str = Query(...),
):
    from app.core.dependencies import get_client_service

    tools_service = ToolsService(get_client_service())
    try:
        tools = await tools_service.list_agent_tools(client_id, agent_id)
        return JSONResponse([tool.dict() for tool in tools])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/agents/{agent_id}/tools")
async def admin_set_agent_tools(
    agent_id: str,
    payload: ToolAssignmentRequest,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
    client_id: str = Query(...),
):
    from app.core.dependencies import get_client_service

    ensure_client_access(client_id, admin_user)

    tools_service = ToolsService(get_client_service())
    try:
        await tools_service.set_agent_tools(client_id, agent_id, payload.tool_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Check if UserSense tool was assigned - if so, enable UserSense for client and trigger learning
    usersense_learning_triggered = False
    try:
        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)

        # Check if any of the assigned tools is the UserSense tool
        if payload.tool_ids:
            usersense_tools = platform_sb.table("tools").select("id").eq("slug", "usersense").execute()
            usersense_tool_id = usersense_tools.data[0]["id"] if usersense_tools.data else None

            if usersense_tool_id and usersense_tool_id in payload.tool_ids:
                # Check if UserSense is already enabled for this client
                client_result = platform_sb.table("clients").select("usersense_enabled").eq("id", client_id).execute()
                was_enabled = client_result.data[0].get("usersense_enabled", False) if client_result.data else False

                if not was_enabled:
                    # Enable UserSense for the client
                    platform_sb.table("clients").update({"usersense_enabled": True}).eq("id", client_id).execute()
                    logger.info(f"🧠 UserSense auto-enabled for client {client_id} (ability added to agent {agent_id})")

                    # Queue initial learning
                    try:
                        result = platform_sb.rpc('queue_client_initial_learning', {'p_client_id': client_id}).execute()
                        logger.info(f"✅ Initial learning job queued for client {client_id}")
                        usersense_learning_triggered = True
                    except Exception as learn_err:
                        logger.error(f"⚠️ Failed to queue initial learning: {learn_err}")

    except Exception as usersense_err:
        logger.warning(f"UserSense auto-enable check failed: {usersense_err}")

    # Check if DocumentSense tool was assigned - if so, enable DocumentSense for client and trigger extraction
    documentsense_extraction_triggered = False
    try:
        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)

        # Check if any of the assigned tools is the DocumentSense tool
        if payload.tool_ids:
            documentsense_tools = platform_sb.table("tools").select("id").eq("slug", "documentsense").execute()
            documentsense_tool_id = documentsense_tools.data[0]["id"] if documentsense_tools.data else None

            if documentsense_tool_id and documentsense_tool_id in payload.tool_ids:
                # Check if DocumentSense is already enabled for this client
                client_result = platform_sb.table("clients").select("documentsense_enabled").eq("id", client_id).execute()
                was_enabled = client_result.data[0].get("documentsense_enabled", False) if client_result.data else False

                if not was_enabled:
                    # Enable DocumentSense for the client
                    platform_sb.table("clients").update({"documentsense_enabled": True}).eq("id", client_id).execute()
                    logger.info(f"📄 DocumentSense auto-enabled for client {client_id} (ability added to agent {agent_id})")

                    # Queue initial extraction for all documents
                    try:
                        result = platform_sb.rpc('queue_client_documentsense_extraction', {'p_client_id': client_id}).execute()
                        logger.info(f"✅ DocumentSense extraction job queued for client {client_id}")
                        documentsense_extraction_triggered = True
                    except Exception as extract_err:
                        logger.error(f"⚠️ Failed to queue DocumentSense extraction: {extract_err}")

    except Exception as documentsense_err:
        logger.warning(f"DocumentSense auto-enable check failed: {documentsense_err}")

    return JSONResponse({
        "success": True,
        "usersense_learning_triggered": usersense_learning_triggered,
        "documentsense_extraction_triggered": documentsense_extraction_triggered
    })



@router.get("/api/clients/{client_id}/perplexity-key")
async def admin_get_perplexity_key(
    client_id: str,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    from app.core.dependencies import get_client_service

    ensure_client_access(client_id, admin_user)

    client_service = get_client_service()
    client = await client_service.get_client(client_id, auto_sync=False)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    api_key_value: Optional[str] = None
    try:
        api_keys = getattr(client.settings, 'api_keys', None)
        if api_keys is not None:
            api_key_value = getattr(api_keys, 'perplexity_api_key', None)
    except Exception:
        pass
    if api_key_value is None and hasattr(client, 'dict'):
        api_key_value = client.dict().get('settings', {}).get('api_keys', {}).get('perplexity_api_key')
    elif api_key_value is None and isinstance(client, dict):
        api_key_value = client.get('settings', {}).get('api_keys', {}).get('perplexity_api_key')

    return {"api_key": api_key_value or ""}


@router.patch("/api/clients/{client_id}/perplexity-key")
async def admin_update_perplexity_key(
    client_id: str,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    from app.core.dependencies import get_client_service

    ensure_client_access(client_id, admin_user)

    payload = await request.json()
    value = (payload.get("api_key") or "").strip()

    try:
        client_service = get_client_service()
        client_service.supabase.table(client_service.table_name if hasattr(client_service, 'table_name') else 'clients').update({
            'perplexity_api_key': value or None
        }).eq('id', client_id).execute()
        return {"success": True, "api_key": value}
    except Exception as exc:
        logger.error(f"Failed to update Perplexity API key for client {client_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update Perplexity API key")


# # DUPLICATE - COMMENTED OUT - Using the version at line ~1528
# # @router.get("/agents/preview/{client_id}/{agent_slug}", response_class=HTMLResponse)
# # async def agent_preview_modal_old(
# #     request: Request,
# #     client_id: str,
# #     agent_slug: str,
# #     admin_user: Dict[str, Any] = Depends(get_admin_user)
# # ):
# #     """Return the agent preview modal"""
# #     import uuid
# #     from app.api.v1.trigger import TriggerAgentRequest, TriggerMode, handle_voice_trigger
# #     from app.core.dependencies import get_agent_service, get_client_service
#     
#     try:
#         # Get agent details
#         agent_service = get_agent_service()
#         agent = await agent_service.get_agent(client_id, agent_slug)
#         if not agent:
#             raise HTTPException(status_code=404, detail="Agent not found")
#         
#         # Get client details (needed for API keys in dispatch)
#         client_service = get_client_service()
#         client = await client_service.get_client(client_id)
#         if not client:
#             raise HTTPException(status_code=404, detail="Client not found")
#         
#         # Generate unique session and room for this preview
#         session_id = f"preview_{uuid.uuid4().hex[:12]}"
#         room_name = f"preview_{agent_slug}_{uuid.uuid4().hex[:8]}"
#         
#         # Create mock trigger request to dispatch the agent
#         trigger_request = TriggerAgentRequest(
#             agent_slug=agent_slug,
#             client_id=client_id,
#             mode=TriggerMode.VOICE,
#             room_name=room_name,
#             # Use the actual user's UUID for proper context loading
#             user_id='351bb07b-03fc-4fb4-b09b-748ef8a72084',  # Your UUID as default
#             session_id=session_id
#         )
#         
#         # Dispatch the agent and get connection details
#         trigger_result = await handle_voice_trigger(trigger_request, agent, client)
#         
#         # Extract connection details for frontend
#         livekit_config = trigger_result.get('livekit_config', {})
#         server_url = livekit_config.get('server_url')
#         user_token = livekit_config.get('user_token')
# 
#         return templates.TemplateResponse("admin/partials/agent_preview.html", {
#             "request": request,
#             "agent": agent,
#             "client_id": client_id,
#             "session_id": session_id,
#             "room_name": room_name,
#             "server_url": server_url,
#             "user_token": user_token
#         })
#         
#     except Exception as e:
#         logger.error(f"Error loading agent preview: {e}")
#         return templates.TemplateResponse("admin/partials/agent_preview.html", {
#             "request": request,
#             "error": str(e)
#         })
# 
@router.get("/agents/{client_id}/{agent_slug}", response_class=HTMLResponse)
async def agent_detail(
    request: Request,
    client_id: str,
    agent_slug: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Agent detail and configuration page"""
    # Subscribers can view detail but not edit; page template handles action buttons
    try:
        ensure_client_or_global_access(client_id, admin_user)

        # Simple approach: For "global" agents, use the same method as the agents list page
        if client_id == "global":
            # Load all agents and find the matching one
            all_agents = await get_all_agents()
            agent = None
            
            for a in all_agents:
                if a.get("slug") == agent_slug:
                    agent = a
                    break
            
            if not agent:
                raise HTTPException(status_code=404, detail=f"Global agent {agent_slug} not found")
            
            # Create virtual client for global agents
            client = {
                "id": "global",
                "name": "Global Agents", 
                "domain": "global.local"
            }
            
        else:
            # For UUID clients, use the original service
            from app.core.dependencies import get_client_service, get_agent_service
            client_service = get_client_service()
            agent_service = get_agent_service()
            
            agent = await agent_service.get_agent(client_id, agent_slug)
            client = await client_service.get_client(client_id)
            
            if not agent:
                raise HTTPException(status_code=404, detail=f"Agent {agent_slug} not found in client {client_id}")
        
        # Get Redis client for configuration cache
        try:
            redis_client = None  # Redis removed - using Supabase only
        except:
            redis_client = None
        
        # Get agent configuration from Redis (if exists)
        agent_config = None
        if redis_client:
            try:
                config_key = f"agent_config:{client_id}:{agent_slug}"
                config_data = redis_client.get(config_key)
                if config_data:
                    import json
                    agent_config = json.loads(config_data)
            except Exception as e:
                logger.warning(f"Failed to get agent config from Redis: {e}")
                agent_config = None
        
        # Convert agent to dict for template - handle both dict and object format
        if isinstance(agent, dict):
            agent_data = {
                "id": agent.get("id"),
                "slug": agent.get("slug"),
                "name": agent.get("name"),
                "description": agent.get("description", ""),
                "agent_image": agent.get("agent_image") or "",
                "system_prompt": agent.get("system_prompt", ""),
                "active": agent.get("active", agent.get("enabled", True)),
                "enabled": agent.get("enabled", True),
                "created_at": agent.get("created_at", ""),
                "updated_at": agent.get("updated_at", ""),
                "voice_settings": agent.get("voice_settings", {}),
                "sound_settings": agent.get("sound_settings", {}),
                "webhooks": agent.get("webhooks", {}),
                "tools_config": agent.get("tools_config", {}),
                "show_citations": agent.get("show_citations", True),
                "rag_results_limit": agent.get("rag_results_limit", 5),
                "supertab_enabled": agent.get("supertab_enabled", False),
                "supertab_voice_enabled": agent.get("supertab_voice_enabled", agent.get("supertab_enabled", False)),
                "supertab_text_enabled": agent.get("supertab_text_enabled", False),
                "supertab_video_enabled": agent.get("supertab_video_enabled", False),
                "supertab_experience_id": agent.get("supertab_experience_id"),
                "supertab_price": agent.get("supertab_price"),
                "supertab_cta": agent.get("supertab_cta"),
                "supertab_subscription_experience_id": agent.get("supertab_subscription_experience_id"),
                "supertab_subscription_price": agent.get("supertab_subscription_price"),
                "voice_chat_enabled": agent.get("voice_chat_enabled", True),
                "text_chat_enabled": agent.get("text_chat_enabled", True),
                "video_chat_enabled": agent.get("video_chat_enabled", False),
                "client_id": client_id,
                "client_name": client.get("name", "Unknown") if isinstance(client, dict) else (getattr(client, 'name', 'Unknown') if client else "Unknown")
            }
        else:
            # Object format - original service
            # Convert voice_settings to dict for JSON serialization in template
            voice_settings_for_template = agent.voice_settings
            if hasattr(voice_settings_for_template, 'dict'):
                try:
                    voice_settings_for_template = voice_settings_for_template.dict()
                except Exception:
                    voice_settings_for_template = {}
            elif hasattr(voice_settings_for_template, 'model_dump'):
                try:
                    voice_settings_for_template = voice_settings_for_template.model_dump()
                except Exception:
                    voice_settings_for_template = {}

            # Convert webhooks to dict as well
            webhooks_for_template = agent.webhooks
            if hasattr(webhooks_for_template, 'dict'):
                try:
                    webhooks_for_template = webhooks_for_template.dict()
                except Exception:
                    webhooks_for_template = {}
            elif hasattr(webhooks_for_template, 'model_dump'):
                try:
                    webhooks_for_template = webhooks_for_template.model_dump()
                except Exception:
                    webhooks_for_template = {}

            # Convert sound_settings to dict for template
            sound_settings_for_template = getattr(agent, 'sound_settings', None)
            if sound_settings_for_template:
                if hasattr(sound_settings_for_template, 'model_dump'):
                    try:
                        sound_settings_for_template = sound_settings_for_template.model_dump()
                    except Exception:
                        sound_settings_for_template = {}
                elif hasattr(sound_settings_for_template, 'dict'):
                    try:
                        sound_settings_for_template = sound_settings_for_template.dict()
                    except Exception:
                        sound_settings_for_template = {}
            else:
                sound_settings_for_template = {}

            agent_data = {
                "id": agent.id,
                "slug": agent.slug,
                "name": agent.name,
                "description": agent.description or "",
                "agent_image": agent.agent_image or "",
                "system_prompt": agent.system_prompt,
                "active": getattr(agent, 'active', agent.enabled),
                "enabled": agent.enabled,
                "created_at": agent.created_at.isoformat() if hasattr(agent.created_at, 'isoformat') else str(agent.created_at),
                "updated_at": agent.updated_at.isoformat() if hasattr(agent.updated_at, 'isoformat') else str(agent.updated_at),
                "voice_settings": voice_settings_for_template,
                "sound_settings": sound_settings_for_template,
                "webhooks": webhooks_for_template,
                "tools_config": agent.tools_config or {},
                "show_citations": getattr(agent, 'show_citations', True),
                "rag_results_limit": getattr(agent, "rag_results_limit", 5),
                "supertab_enabled": getattr(agent, 'supertab_enabled', False),
                "supertab_voice_enabled": getattr(agent, 'supertab_voice_enabled', getattr(agent, 'supertab_enabled', False)),
                "supertab_text_enabled": getattr(agent, 'supertab_text_enabled', False),
                "supertab_video_enabled": getattr(agent, 'supertab_video_enabled', False),
                "supertab_experience_id": getattr(agent, 'supertab_experience_id', None),
                "supertab_price": getattr(agent, 'supertab_price', None),
                "supertab_cta": getattr(agent, 'supertab_cta', None),
                "supertab_subscription_experience_id": getattr(agent, 'supertab_subscription_experience_id', None),
                "supertab_subscription_price": getattr(agent, 'supertab_subscription_price', None),
                "voice_chat_enabled": getattr(agent, 'voice_chat_enabled', True),
                "text_chat_enabled": getattr(agent, 'text_chat_enabled', True),
                "video_chat_enabled": getattr(agent, 'video_chat_enabled', False),
                "client_id": client_id,
                "client_name": client.name if client else "Unknown"
            }
        
        # Provide configuration for template - pull from agent's voice_settings first
        voice_settings_data = agent_data.get("voice_settings", {})
        
        # Convert VoiceSettings object to dict if needed
        if hasattr(voice_settings_data, '__dict__'):
            # It's a Pydantic model/object – leverage its dict representation to avoid dropping fields like `model`
            try:
                voice_settings_data = voice_settings_data.dict()
            except Exception:
                voice_settings_dict = {}
                for key in ['provider', 'voice_id', 'temperature', 'llm_provider', 'llm_model',
                            'stt_provider', 'stt_model', 'stt_language', 'tts_provider', 'model',
                            'output_format', 'stability', 'similarity_boost', 'loudness_normalization',
                            'text_normalization', 'provider_config', 'openai_voice', 'elevenlabs_voice_id',
                            'cartesia_voice_id']:
                    if hasattr(voice_settings_data, key):
                        voice_settings_dict[key] = getattr(voice_settings_data, key, None)
                voice_settings_data = voice_settings_dict
        elif isinstance(voice_settings_data, str):
            # It's a JSON string, parse it
            try:
                import json
                voice_settings_data = json.loads(voice_settings_data)
            except:
                voice_settings_data = {}
        elif not isinstance(voice_settings_data, dict):
            # Fallback to empty dict
            voice_settings_data = {}
        
        provider_config = voice_settings_data.get("provider_config", {}) if isinstance(voice_settings_data.get("provider_config", {}), dict) else {}
        effective_tts_provider = voice_settings_data.get("tts_provider", voice_settings_data.get("provider", "openai"))
        openai_voice = voice_settings_data.get("openai_voice", None)
        if not openai_voice and effective_tts_provider == "openai":
            openai_voice = voice_settings_data.get("voice_id", "alloy")
        elevenlabs_voice_id = voice_settings_data.get("elevenlabs_voice_id", None)
        if not elevenlabs_voice_id and effective_tts_provider == "elevenlabs":
            elevenlabs_voice_id = voice_settings_data.get("voice_id") or provider_config.get("elevenlabs_voice_id", "")

        latest_config = {
            "last_updated": "",
            "enabled": agent_data.get("enabled", True),
            "system_prompt": agent_data.get("system_prompt", ""),
            "provider_type": "livekit",
            "llm_provider": voice_settings_data.get("llm_provider", "groq"),
            "llm_model": voice_settings_data.get("llm_model", "llama-3.1-8b-instant"),
            "temperature": voice_settings_data.get("temperature", 0.7),
            "stt_provider": voice_settings_data.get("stt_provider", "deepgram"),
            "stt_model": voice_settings_data.get("stt_model", "nova-2"),
            "tts_provider": effective_tts_provider or "openai",
            "tts_model": voice_settings_data.get("model", "sonic-english"),
            "openai_voice": openai_voice or "alloy",
            "elevenlabs_voice_id": elevenlabs_voice_id or "",
            "cartesia_voice_id": voice_settings_data.get("voice_id", "248be419-c632-4f23-adf1-5324ed7dbf1d") if effective_tts_provider == "cartesia" else provider_config.get("cartesia_voice_id", "248be419-c632-4f23-adf1-5324ed7dbf1d")
        }
        latest_config_json = None
        
        # Process agent_config if available (for object-based agents only)
        if agent_config and not isinstance(agent, dict):
            try:
                # Only process for object-based agents (original service)
                agent_data["latest_config"] = agent_config
                
                # Parse configuration for template
                voice_settings = agent_config.get("voice_settings", {})
                if isinstance(voice_settings, str):
                    try:
                        import json
                        voice_settings = json.loads(voice_settings)
                    except:
                        voice_settings = {}
                
                # Update latest_config with actual values
                latest_config.update({
                    "last_updated": str(agent_config.get("last_updated", "")),
                    "enabled": bool(agent_config.get("enabled", True)),
                    "system_prompt": str(agent_config.get("system_prompt", agent_data.get("system_prompt", ""))),
                })
                latest_config_json = "Configuration available"
            except Exception as config_error:
                logger.warning(f"Failed to process agent config: {config_error}")
                # Keep the default latest_config
        
        try:
            logger.info(f"Preparing template response with agent_data: {type(agent_data)}")
            
            # Clean up agent_data to remove any problematic values
            cleaned_agent_data = {}
            for key, value in agent_data.items():
                try:
                    # Test if the value is JSON serializable
                    import json
                    json.dumps(value)
                    cleaned_agent_data[key] = value
                except (TypeError, ValueError):
                    # Replace problematic values with strings
                    cleaned_agent_data[key] = str(value) if value is not None else ""
            
            # Always use the full template now - placeholder logic completely removed
            # The following code block is completely disabled  
            if "NEVER_EXECUTE_THIS" == "NEVER":
                from fastapi.responses import HTMLResponse
                simple_html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>SIMPLE TEMPLATE - Agent {agent_data['name']} - CODE VERSION 2</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; margin: 40px; }}
                        .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }}
                        .button {{ background: #3b82f6; color: white; padding: 8px 16px; border: none; border-radius: 4px; text-decoration: none; display: inline-block; }}
                    </style>
                </head>
                <body>
                    <h1>Agent: {agent_data['name']}</h1>
                    <div class="card">
                        <h3>Basic Information</h3>
                        <p><strong>Slug:</strong> {agent_data['slug']}</p>
                        <p><strong>Description:</strong> {agent_data['description']}</p>
                        <p><strong>Client ID:</strong> {agent_data['client_id']}</p>
                        <p><strong>Status:</strong> {'Active' if agent_data['active'] else 'Inactive'}</p>
                        <p><strong>System Prompt:</strong> {agent_data['system_prompt'][:200]}{'...' if len(agent_data['system_prompt']) > 200 else ''}</p>
                    </div>
                    <div class="card">
                        <h3>Note</h3>
                        <p>This is a simplified view for project-based agents. Full configuration interface requires project access token setup.</p>
                        <a href="/admin/agents" class="button">← Back to Agents</a>
                        <a href="/admin/clients/{agent_data['client_id']}" class="button">View Client</a>
                    </div>
                </body>
                </html>
                """
                return HTMLResponse(content=simple_html)
            
            # For object-based agents, use the full template
            # Debug: log template root and selected template for diagnostics
            try:
                logger.info(f"[admin] Rendering agent_detail for {client_id}/{agent_slug} using templates dir: {templates.directory}")
            except Exception:
                logger.info(f"[admin] Rendering agent_detail for {client_id}/{agent_slug}")

            agent_personality = _fetch_agent_personality(cleaned_agent_data.get("id"), client)

            template_data = {
                "request": request,
                "agent": cleaned_agent_data,  # Use cleaned data
                "client": client,
                "user": admin_user,
                "disable_stats_poll": True,
                "latest_config": latest_config,
                "latest_config_json": latest_config_json,
                "has_config_updates": bool(agent_config) if agent_config else False,
                "agent_personality": agent_personality,
            }
            return templates.TemplateResponse("admin/agent_detail.html", template_data)
        except Exception as template_error:
            logger.error(f"Template rendering error: {template_error}")
            logger.error(f"Error type: {type(template_error)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            # Return working configuration page bypassing template issues
            from fastapi.responses import HTMLResponse
            import json
            
            # Parse voice settings if it's a string
            # Handle both dict and object formats
            if isinstance(agent, dict):
                voice_settings = agent.get('voice_settings', {})
            else:
                voice_settings = getattr(agent, 'voice_settings', {})
                # Convert VoiceSettings object to dict if needed
                if voice_settings and hasattr(voice_settings, 'dict'):
                    voice_settings = voice_settings.dict()
                elif voice_settings and not isinstance(voice_settings, dict):
                    # Manual conversion for VoiceSettings object
                    voice_settings = {
                        'provider': getattr(voice_settings, 'provider', 'openai'),
                        'voice_id': getattr(voice_settings, 'voice_id', 'alloy'),
                        'temperature': getattr(voice_settings, 'temperature', 0.7),
                        'llm_provider': getattr(voice_settings, 'llm_provider', 'groq'),
                        'llm_model': getattr(voice_settings, 'llm_model', 'llama3-70b-8192'),
                        'stt_provider': getattr(voice_settings, 'stt_provider', 'deepgram'),
                        'stt_language': getattr(voice_settings, 'stt_language', 'en'),
                        'model': getattr(voice_settings, 'model', 'sonic-english'),
                        'output_format': getattr(voice_settings, 'output_format', 'pcm_44100'),
                        'stability': getattr(voice_settings, 'stability', None),
                        'similarity_boost': getattr(voice_settings, 'similarity_boost', None),
                        'loudness_normalization': getattr(voice_settings, 'loudness_normalization', None),
                        'text_normalization': getattr(voice_settings, 'text_normalization', None),
                        'provider_config': getattr(voice_settings, 'provider_config', {})
                    }
            if isinstance(voice_settings, str):
                try:
                    voice_settings = json.loads(voice_settings)
                except:
                    voice_settings = {}
            
            # Extract specific settings with defaults
            tts_provider = voice_settings.get('provider', 'openai')
            # Handle enum types
            if hasattr(tts_provider, 'value'):
                tts_provider = tts_provider.value
            llm_provider = voice_settings.get('llm_provider', 'groq')
            llm_model = voice_settings.get('llm_model', 'llama3-70b-8192')
            stt_provider = voice_settings.get('stt_provider', 'deepgram')
            temperature = voice_settings.get('temperature', 0.7)
            
            # Agent status
            if isinstance(agent, dict):
                is_enabled = agent.get('enabled', True)
                agent_name_raw = agent.get('name', agent_slug)
                agent_slug_raw = agent.get('slug', 'N/A')
                system_prompt_raw = agent.get('system_prompt', 'N/A')
                agent_description_raw = agent.get('description', '')
                agent_image_url_raw = agent.get('agent_image') or ''
            else:
                is_enabled = getattr(agent, 'enabled', True)
                agent_name_raw = getattr(agent, 'name', agent_slug)
                agent_slug_raw = getattr(agent, 'slug', 'N/A')
                system_prompt_raw = getattr(agent, 'system_prompt', 'N/A')
                agent_description_raw = getattr(agent, 'description', '')
                agent_image_url_raw = getattr(agent, 'agent_image', '') or ''
            
            enabled_checked = 'checked' if is_enabled else ''
            
            # Provider-specific voice settings
            # Get the current voice_id if the provider matches, otherwise use provider_config for stored values
            provider_config = voice_settings.get('provider_config', {})
            
            openai_voice = voice_settings.get('voice_id', 'alloy') if tts_provider == 'openai' else provider_config.get('openai_voice_id', 'alloy')
            cartesia_voice_id = voice_settings.get('voice_id', '') if tts_provider == 'cartesia' else provider_config.get('cartesia_voice_id', '')
            cartesia_model = voice_settings.get('model', 'sonic-english') if tts_provider == 'cartesia' else 'sonic-english'
            elevenlabs_voice_id = voice_settings.get('voice_id', '') if tts_provider == 'elevenlabs' else provider_config.get('elevenlabs_voice_id', '')
            speechify_voice_id = voice_settings.get('voice_id', 'jack') if tts_provider == 'speechify' else provider_config.get('speechify_voice_id', 'jack')
            
            # Escape any problematic characters
            agent_name = str(agent_name_raw).replace('"', '&quot;')
            agent_slug_clean = str(agent_slug_raw).replace('"', '&quot;')
            system_prompt = str(system_prompt_raw).replace('<', '&lt;').replace('>', '&gt;')
            agent_description = str(agent_description_raw).replace('"', '&quot;')
            agent_image_url = str(agent_image_url_raw).replace('"', '&quot;')
            # client_id is already available as a function parameter - no escaping needed as it's a UUID
            
            working_html = f'''
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Agent Configuration: {agent_name}</title>
                <script src="https://cdn.tailwindcss.com"></script>
                <script src="https://unpkg.com/htmx.org@1.9.10"></script>
                <script src="/static/livekit-client.min.js"></script>
                <script>
                    tailwind.config = {{
                        theme: {{
                            extend: {{
                                colors: {{
                                    'dark-bg': '#000000',
                                    'dark-surface': 'rgb(20, 20, 20)',
                                    'dark-text': '#e5e5e5',
                                    'dark-border': '#374151'
                                }}
                            }}
                        }}
                    }}
                </script>
                <style>
                    /* Brand colors */
                    .text-brand-teal {{
                        color: #01a4a6;
                    }}
                    .hover\\:text-brand-teal:hover {{
                        color: #01a4a6;
                    }}
                    /* Navigation active state */
                    .nav-active {{
                        background-color: rgba(1, 164, 166, 0.05);
                        border-left: 3px solid #01a4a6;
                    }}
                    .toggle-switch {{
                        position: relative;
                        display: inline-block;
                        width: 60px;
                        height: 34px;
                    }}
                    .toggle-switch input {{
                        opacity: 0;
                        width: 0;
                        height: 0;
                    }}
                    .toggle-slider {{
                        position: absolute;
                        cursor: pointer;
                        top: 0;
                        left: 0;
                        right: 0;
                        bottom: 0;
                        background-color: #374151;
                        transition: .4s;
                        border-radius: 34px;
                    }}
                    .toggle-slider:before {{
                        position: absolute;
                        content: "";
                        height: 26px;
                        width: 26px;
                        left: 4px;
                        bottom: 4px;
                        background-color: white;
                        transition: .4s;
                        border-radius: 50%;
                    }}
                    input:checked + .toggle-slider {{
                        background-color: #3b82f6;
                    }}
                    input:checked + .toggle-slider:before {{
                        transform: translateX(26px);
                    }}
                    .form-section {{
                        margin-bottom: 2rem;
                        padding: 1.5rem;
                        background: rgb(20, 20, 20);
                        border-radius: 0.5rem;
                        border: 1px solid #374151;
                    }}
                    .provider-section {{
                        display: none;
                        margin-top: 1rem;
                        padding: 1rem;
                        background: #111827;
                        border-radius: 0.375rem;
                        border: 1px solid #4b5563;
                    }}
                    .provider-section.active {{
                        display: block;
                    }}
                </style>
            </head>
            <body class="bg-dark-bg text-dark-text min-h-screen">
                <!-- Navigation Header -->
                <nav class="bg-white border-b border-gray-200">
                    <div class="max-w-7xl mx-auto px-4">
                        <div class="flex justify-between h-16">
                            <div class="flex items-center">
                                <div class="flex-shrink-0">
                                    <img src="/static/images/sidekick-forge-logo.png" alt="Sidekick Forge" class="h-10" />
                                </div>
                                <div class="hidden md:block">
                                    <div class="ml-10 flex items-baseline space-x-2">
                                        <a href="/admin/" 
                                           class="text-gray-700 hover:text-brand-teal px-3 py-2 rounded-md text-sm font-medium transition-all">
                                            Dashboard
                                        </a>
                                        <a href="/admin/clients" 
                                           class="text-gray-700 hover:text-brand-teal px-3 py-2 rounded-md text-sm font-medium transition-all">
                                            Clients
                                        </a>
                                        <a href="/admin/agents" 
                                           class="nav-active text-brand-teal px-3 py-2 rounded-md text-sm font-medium transition-all">
                                            Agents
                                        </a>
                                        <a href="/admin/knowledge" 
                                           class="text-gray-700 hover:text-brand-teal px-3 py-2 rounded-md text-sm font-medium transition-all">
                                            Knowledge Base
                                        </a>
                                        <a href="/admin/wordpress-sites" 
                                           class="text-gray-700 hover:text-brand-teal px-3 py-2 rounded-md text-sm font-medium transition-all">
                                            WordPress Sites
                                        </a>
                                    </div>
                                </div>
                            </div>
                            <div class="flex items-center">
                                <div class="text-sm text-gray-700">
                                    <span class="font-medium">Admin</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </nav>
                
                <div class="container mx-auto px-4 py-8 max-w-6xl">
                    <div class="mb-8">
                        <nav class="flex items-center space-x-2 text-sm text-gray-400 mb-4">
                            <a href="/admin" class="hover:text-white">Admin Dashboard</a>
                            <span>›</span>
                            <a href="/admin/agents" class="hover:text-white">Agents</a>
                            <span>›</span>
                            <span class="text-white">{agent_name}</span>
                        </nav>
                        
                        <h1 class="text-3xl font-bold text-white mb-4">Agent Configuration</h1>
                        
                        <form class="space-y-6" onsubmit="saveAgentConfiguration(event)">
                            <!-- Basic Information -->
                            <div class="form-section">
                                <h2 class="text-xl font-bold text-white mb-4">Basic Information</h2>
                                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <div>
                                        <label class="block text-sm font-medium text-gray-300 mb-2">Agent Name</label>
                                        <input type="text" name="name" value="{agent_name}" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                    </div>
                                    <div>
                                        <label class="block text-sm font-medium text-gray-300 mb-2">Agent Slug</label>
                                        <input type="text" name="slug" value="{agent_slug_clean}" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500" readonly>
                                    </div>
                                    <div class="md:col-span-2">
                                        <label class="block text-sm font-medium text-gray-300 mb-2">Description</label>
                                        <textarea name="description" rows="3" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">{agent_description}</textarea>
                                    </div>
                                    <div class="md:col-span-2">
                                        <label class="block text-sm font-medium text-gray-300 mb-2">Agent Background Image URL</label>
                                        <input type="text" name="agent_image" value="{agent_image_url}" placeholder="https://example.com/image.jpg (optional)" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                        <p class="text-sm text-gray-400 mt-1">URL for the agent's background image (used in chat interfaces)</p>
                                    </div>
                                </div>
                            </div>

                            <!-- LLM Configuration -->
                            <div class="form-section">
                                <h2 class="text-xl font-bold text-white mb-4">LLM Provider</h2>
                                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <div>
                                        <label class="block text-sm font-medium text-gray-300 mb-2">Provider</label>
                                        <select name="llm_provider" id="llm-provider" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                            <option value="openai">OpenAI</option>
                                            <option value="groq" selected>Groq</option>
                                            <option value="cerebras">Cerebras</option>
                                            <option value="deepinfra">DeepInfra</option>
                                        </select>
                                    </div>
                                    <div>
                                        <label class="block text-sm font-medium text-gray-300 mb-2">Model</label>
                                        <select name="llm_model" id="llm-model" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                            <!-- OpenAI -->
                                            <option value="gpt-4o">GPT-4o (OpenAI)</option>
                                            <option value="gpt-4o-mini">GPT-4o Mini (OpenAI)</option>
                                            <!-- Groq (compat names mapped in worker) -->
                                            <option value="llama-3.3-70b-versatile" selected>Llama 3.3 70B (Groq)</option>
                                            <option value="llama3-8b-8192">Llama 3 8B (Groq)</option>
                                            <option value="mixtral-8x7b-32768">Mixtral 8x7B (Groq)</option>
                                            <!-- Cerebras documented chat models -->
                                            <option value="zai-glm-4.7">GLM 4.7 (Cerebras, Recommended)</option>
                                            <option value="llama3.1-8b">Llama 3.1 8B (Cerebras)</option>
                                            <option value="llama-3.3-70b">Llama 3.3 70B (Cerebras)</option>
                                            <option value="llama-4-scout-17b-16e-instruct">Llama 4 Scout 17B Instruct (Cerebras)</option>
                                            <option value="llama-4-maverick-17b-128e-instruct">Llama 4 Maverick 17B Instruct (preview, Cerebras)</option>
                                            <option value="qwen-3-32b">Qwen 3 32B (Cerebras)</option>
                                            <option value="qwen-3-235b-a22b-instruct-2507">Qwen 3 235B Instruct (preview, Cerebras)</option>
                                            <option value="qwen-3-235b-a22b-thinking-2507">Qwen 3 235B Thinking (preview, Cerebras)</option>
                                            <option value="qwen-3-coder-480b">Qwen 3 Coder 480B (preview, Cerebras)</option>
                                            <option value="gpt-oss-120b">GPT-OSS 120B (preview, Cerebras)</option>
                                        </select>
                                    </div>
                                    <div>
                                        <label class="block text-sm font-medium text-gray-300 mb-2">Temperature</label>
                                        <input type="range" name="temperature" min="0" max="1" step="0.1" value="0.7" class="w-full" id="temperature-range">
                                        <div class="flex justify-between text-sm text-gray-400">
                                            <span>Conservative (0)</span>
                                            <span id="temperature-value">0.7</span>
                                            <span>Creative (1)</span>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <!-- Speech-to-Text Configuration -->
                            <div class="form-section">
                                <h2 class="text-xl font-bold text-white mb-4">Speech-to-Text (STT)</h2>
                                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <div>
                                        <label class="block text-sm font-medium text-gray-300 mb-2">STT Provider</label>
                                        <select name="stt_provider" id="stt-provider" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                            <option value="groq">Groq (Fast)</option>
                                            <option value="deepgram" selected>Deepgram (Accurate)</option>
                                            <option value="cartesia">Cartesia (Low Latency)</option>
                                        </select>
                                    </div>
                                    <div>
                                        <label class="block text-sm font-medium text-gray-300 mb-2">Language</label>
                                        <select name="stt_language" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                            <option value="en" selected>English</option>
                                            <option value="es">Spanish</option>
                                            <option value="fr">French</option>
                                            <option value="de">German</option>
                                        </select>
                                    </div>
                                </div>
                            </div>

                            <!-- Text-to-Speech Configuration -->
                            <div class="form-section">
                                <h2 class="text-xl font-bold text-white mb-4">Text-to-Speech (TTS)</h2>
                                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <div>
                                        <label class="block text-sm font-medium text-gray-300 mb-2">TTS Provider</label>
                                        <select name="tts_provider" id="tts-provider" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500" onchange="toggleTTSProviderSettings()">
                                            <option value="openai">OpenAI</option>
                                            <option value="elevenlabs">ElevenLabs</option>
                                            <option value="cartesia">Cartesia</option>
                                            <option value="replicate">Replicate</option>
                                            <option value="speechify">Speechify</option>
                                        </select>
                                    </div>
                                </div>

                                <!-- OpenAI TTS Settings -->
                                <div id="tts-openai" class="provider-section active">
                                    <h3 class="text-lg font-semibold text-white mb-3">OpenAI TTS Settings</h3>
                                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Voice</label>
                                            <select name="openai_voice" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                                <option value="alloy" selected>Alloy (Balanced)</option>
                                                <option value="echo">Echo (Masculine)</option>
                                                <option value="fable">Fable (British)</option>
                                                <option value="onyx">Onyx (Deep)</option>
                                                <option value="nova">Nova (Feminine)</option>
                                                <option value="shimmer">Shimmer (Warm)</option>
                                            </select>
                                        </div>
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Model</label>
                                            <select name="openai_model" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                                <option value="tts-1" selected>TTS-1 (Fast)</option>
                                                <option value="tts-1-hd">TTS-1-HD (High Quality)</option>
                                            </select>
                                        </div>
                                    </div>
                                </div>

                                <!-- ElevenLabs TTS Settings -->
                                <div id="tts-elevenlabs" class="provider-section">
                                    <h3 class="text-lg font-semibold text-white mb-3">ElevenLabs TTS Settings</h3>
                                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Voice ID</label>
                                            <input type="text" name="elevenlabs_voice_id" value="{elevenlabs_voice_id}" placeholder="pNInz6obpgDQGcFmaJgB" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                            <p class="text-xs text-gray-400 mt-1">Default is Adam voice</p>
                                        </div>
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Model</label>
                                            <select name="elevenlabs_model" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                                <option value="eleven_turbo_v2" selected>Turbo v2 (Fast)</option>
                                                <option value="eleven_multilingual_v2">Multilingual v2</option>
                                                <option value="eleven_monolingual_v1">Monolingual v1</option>
                                            </select>
                                        </div>
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Stability</label>
                                            <input type="range" name="elevenlabs_stability" min="0" max="1" step="0.1" value="0.5" class="w-full">
                                        </div>
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Similarity Boost</label>
                                            <input type="range" name="elevenlabs_similarity" min="0" max="1" step="0.1" value="0.75" class="w-full">
                                        </div>
                                    </div>
                                </div>

                                <!-- Cartesia TTS Settings -->
                                <div id="tts-cartesia" class="provider-section">
                                    <h3 class="text-lg font-semibold text-white mb-3">Cartesia TTS Settings</h3>
                                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Voice ID</label>
                                            <input type="text" name="cartesia_voice_id" value="{cartesia_voice_id}" placeholder="248be419-c632-4f23-adf1-5324ed7dbf1d" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                            <p class="text-xs text-gray-400 mt-1">Default is Barbershop Man voice</p>
                                        </div>
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Model</label>
                                            <select name="cartesia_model" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                                <option value="sonic-english" selected>Sonic English (Fast)</option>
                                                <option value="sonic-multilingual">Sonic Multilingual</option>
                                                <option value="sonic-2">Sonic 2 (Latest)</option>
                                            </select>
                                        </div>
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Output Format</label>
                                            <select name="cartesia_format" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                                <option value="pcm_44100" selected>PCM 44.1kHz (Recommended)</option>
                                                <option value="pcm_22050">PCM 22kHz</option>
                                                <option value="pcm_16000">PCM 16kHz</option>
                                            </select>
                                        </div>
                                    </div>
                                </div>

                                <!-- Speechify TTS Settings -->
                                <div id="tts-speechify" class="provider-section">
                                    <h3 class="text-lg font-semibold text-white mb-3">Speechify TTS Settings</h3>
                                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Voice ID</label>
                                            <input type="text" name="speechify_voice_id" placeholder="jack" value="jack" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                        </div>
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Model</label>
                                            <select name="speechify_model" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                                <option value="simba-english" selected>Simba English (Fast)</option>
                                                <option value="simba-multilingual">Simba Multilingual</option>
                                            </select>
                                        </div>
                                        <div>
                                            <label class="flex items-center space-x-2">
                                                <input type="checkbox" name="speechify_loudness_normalization" class="text-blue-600 focus:ring-blue-500">
                                                <span class="text-white">Enable Loudness Normalization</span>
                                            </label>
                                        </div>
                                        <div>
                                            <label class="flex items-center space-x-2">
                                                <input type="checkbox" name="speechify_text_normalization" class="text-blue-600 focus:ring-blue-500">
                                                <span class="text-white">Enable Text Normalization</span>
                                            </label>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <!-- System Prompt -->
                            <div class="form-section">
                                <h2 class="text-xl font-bold text-white mb-4">System Prompt</h2>
                                <textarea name="system_prompt" rows="8" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500 font-mono text-sm" placeholder="You are a helpful AI assistant...">{system_prompt}</textarea>
                                <p class="text-sm text-gray-400 mt-2">Define the agent's personality, role, and behavior instructions.</p>
                            </div>

                            <!-- Agent Status -->
                            <div class="form-section">
                                <h2 class="text-xl font-bold text-white mb-4">Agent Status</h2>
                                <div class="flex items-center space-x-3">
                                    <label class="toggle-switch">
                                        <input type="checkbox" name="enabled" {enabled_checked}>
                                        <span class="toggle-slider"></span>
                                    </label>
                                    <span class="text-white font-medium">Agent Enabled</span>
                                    <span class="text-sm text-gray-400">Whether this agent is available for use</span>
                                </div>
                            </div>

                            <!-- Voice Preview -->
                            <div class="form-section">
                                <h2 class="text-xl font-bold text-white mb-4">Voice Preview</h2>
                                <div class="flex items-center space-x-4">
                                    <button type="button" 
                                            hx-get="/admin/agents/preview/{client_id}/{agent_slug_clean}" 
                                            hx-target="#modal-container" 
                                            hx-swap="innerHTML"
                                            class="px-6 py-3 bg-green-600 text-white rounded-md hover:bg-green-700 font-medium">
                                        🎤 Test Voice Preview
                                    </button>
                                    <span class="text-sm text-gray-400">Test the agent with live voice conversation</span>
                                </div>
                                <div id="modal-container"></div>
                            </div>

                            <!-- Action Buttons -->
                            <div class="flex space-x-4 pt-6">
                                <button type="submit" class="px-6 py-3 bg-blue-600 text-white rounded-md hover:bg-blue-700 font-medium">
                                    Save Agent Configuration
                                </button>
                                <button type="button" onclick="window.location.href='/admin/agents'" class="px-6 py-3 bg-gray-600 text-white rounded-md hover:bg-gray-700 font-medium">
                                    Cancel
                                </button>
                            </div>
                        </form>
                    </div>
                </div>

                <script>
                    // Temperature slider value display
                    const temperatureRange = document.getElementById('temperature-range');
                    const temperatureValue = document.getElementById('temperature-value');
                    
                    temperatureRange.addEventListener('input', function() {{
                        temperatureValue.textContent = this.value;
                    }});

                    // TTS Provider switching
                    function toggleTTSProviderSettings() {{
                        const provider = document.getElementById('tts-provider').value;
                        const sections = document.querySelectorAll('.provider-section');
                        
                        sections.forEach(section => section.classList.remove('active'));
                        
                        const activeSection = document.getElementById('tts-' + provider);
                        if (activeSection) {{
                            activeSection.classList.add('active');
                        }}
                    }}

                    // Save agent configuration
                    async function saveAgentConfiguration(event) {{
                        event.preventDefault();
                        
                        // Show loading state
                        const submitBtn = event.target.querySelector('button[type="submit"]');
                        const originalText = submitBtn.textContent;
                        submitBtn.textContent = 'Saving...';
                        submitBtn.disabled = true;
                        
                        // Collect form data
                        const formData = new FormData(event.target);
                        const configData = Object.fromEntries(formData);
                        
                        // Build voice settings object based on selected TTS provider
                        const ttsProvider = configData.tts_provider;
                        let voiceSettings = {{
                            provider: ttsProvider,
                            temperature: parseFloat(configData.temperature) || 0.7,
                            llm_provider: configData.llm_provider,
                            llm_model: configData.llm_model,
                            stt_provider: configData.stt_provider,
                            stt_language: configData.stt_language || 'en'
                        }};
                        
                        // Store all voice IDs in provider_config to preserve them when switching providers
                        voiceSettings.provider_config = {{
                            openai_voice_id: configData.openai_voice,
                            elevenlabs_voice_id: configData.elevenlabs_voice_id,
                            cartesia_voice_id: configData.cartesia_voice_id,
                            speechify_voice_id: configData.speechify_voice_id
                        }};
                        
                        // Add provider-specific settings
                        if (ttsProvider === 'openai') {{
                            voiceSettings.voice_id = configData.openai_voice;
                            voiceSettings.model = configData.openai_model;
                        }} else if (ttsProvider === 'elevenlabs') {{
                            voiceSettings.voice_id = configData.elevenlabs_voice_id;
                            voiceSettings.model = configData.elevenlabs_model;
                            voiceSettings.stability = parseFloat(configData.elevenlabs_stability) || 0.5;
                            voiceSettings.similarity_boost = parseFloat(configData.elevenlabs_similarity) || 0.75;
                        }} else if (ttsProvider === 'cartesia') {{
                            voiceSettings.voice_id = configData.cartesia_voice_id;
                            voiceSettings.model = configData.cartesia_model;
                            voiceSettings.output_format = configData.cartesia_format;
                        }} else if (ttsProvider === 'speechify') {{
                            voiceSettings.voice_id = configData.speechify_voice_id;
                            voiceSettings.model = configData.speechify_model;
                            voiceSettings.loudness_normalization = configData.speechify_loudness_normalization === 'on';
                            voiceSettings.text_normalization = configData.speechify_text_normalization === 'on';
                        }}
                        
                        // Build agent update payload
                        const updatePayload = {{
                            name: configData.name,
                            description: configData.description,
                            agent_image: configData.agent_image && configData.agent_image.trim() !== '' ? configData.agent_image : null,
                            system_prompt: configData.system_prompt,
                            enabled: configData.enabled === 'on',
                            voice_settings: voiceSettings
                        }};
                        
                        try {{
                            // Use the existing API endpoint
                            const response = await fetch('/api/v1/agents/{agent_slug_clean}?client_id={client_id}', {{
                                method: 'PUT',
                                headers: {{
                                    'Content-Type': 'application/json',
                                }},
                                body: JSON.stringify(updatePayload)
                            }});
                            
                            if (response.ok) {{
                                const result = await response.json();
                                alert('Configuration saved successfully!');
                                // Optionally reload the page to show updated data
                                // window.location.reload();
                            }} else {{
                                const error = await response.json();
                                
                                // Check if it's an API key validation error
                                if (response.status === 400 && error.detail && typeof error.detail === 'object' && error.detail.missing_keys) {{
                                    // Build error message for missing API keys
                                    let errorMessage = 'Cannot save configuration - Missing API keys:\\n\\n';
                                    error.detail.missing_keys.forEach(key => {{
                                        errorMessage += `• ${{key.provider_type}} provider "${{key.provider}}" requires ${{key.required_key}}\\n`;
                                    }});
                                    errorMessage += `\\nPlease add the missing API keys for client "${{error.detail.client_name}}" in the client settings.`;
                                    alert(errorMessage);
                                }} else {{
                                    // Generic error
                                    const errorMsg = typeof error.detail === 'string' ? error.detail : (error.detail?.error || 'Unknown error');
                                    alert(`Error saving configuration: ${{errorMsg}}`);
                                }}
                            }}
                        }} catch (error) {{
                            alert(`Error saving configuration: ${{error.message}}`);
                        }} finally {{
                            submitBtn.textContent = originalText;
                            submitBtn.disabled = false;
                        }}
                    }}

                    // LLM model options per provider
                    const modelOptionsByProvider = {{
                        openai: [
                            {{ value: 'gpt-4o', label: 'GPT-4o (OpenAI)' }},
                            {{ value: 'gpt-4o-mini', label: 'GPT-4o Mini (OpenAI)' }}
                        ],
                        groq: [
                            {{ value: 'llama-3.3-70b-versatile', label: 'Llama 3.3 70B (Groq)' }},
                            {{ value: 'llama3-8b-8192', label: 'Llama 3 8B (Groq)' }},
                            {{ value: 'mixtral-8x7b-32768', label: 'Mixtral 8x7B (Groq)' }}
                        ],
                        cerebras: [
                            {{ value: 'zai-glm-4.7', label: 'GLM 4.7 (Recommended)' }},
                            {{ value: 'llama-3.3-70b', label: 'Llama 3.3 70B' }},
                            {{ value: 'llama3.1-8b', label: 'Llama 3.1 8B (Fast)' }},
                            {{ value: 'qwen-3-32b', label: 'Qwen 3 32B' }},
                            {{ value: 'qwen-3-235b-a22b-instruct-2507', label: 'Qwen 3 235B Instruct (Preview)' }},
                            {{ value: 'gpt-oss-120b', label: 'GPT-OSS 120B (Preview)' }}
                        ],
                        deepinfra: [
                            {{ value: 'meta-llama/Llama-3.1-8B-Instruct', label: 'Llama 3.1 8B Instruct (DeepInfra)' }},
                            {{ value: 'mistralai/Mixtral-8x7B-Instruct-v0.1', label: 'Mixtral 8x7B Instruct (DeepInfra)' }}
                        ]
                    }};

                    function updateLLMModels(presetModel) {{
                        const providerSel = document.getElementById('llm-provider');
                        const modelSel = document.getElementById('llm-model');
                        const provider = providerSel.value || 'groq';
                        const options = modelOptionsByProvider[provider] || [];
                        const current = presetModel || modelSel.value || '{llm_model}';
                        // Rebuild options
                        modelSel.innerHTML = '';
                        options.forEach(opt => {{
                            const o = document.createElement('option');
                            o.value = opt.value; o.textContent = opt.label;
                            modelSel.appendChild(o);
                        }});
                        // Select current if present, else first
                        const hasCurrent = options.some(o => o.value === current);
                        modelSel.value = hasCurrent ? current : (options[0] ? options[0].value : '');
                    }}

                    // Initialize - Set current values and model list per provider
                    document.getElementById('tts-provider').value = '{tts_provider}';
                    document.getElementById('llm-provider').value = '{llm_provider}';
                    updateLLMModels('{llm_model}');
                    document.getElementById('stt-provider').value = '{stt_provider}';
                    document.getElementById('temperature-range').value = '{temperature}';
                    document.getElementById('temperature-value').textContent = '{temperature}';
                    document.getElementById('llm-provider').addEventListener('change', () => updateLLMModels());
                    toggleTTSProviderSettings();
                </script>
            </body>
            </html>
            '''
            agent_personality = _fetch_agent_personality(cleaned_agent_data.get("id"), client)

            template_data = {
                "request": request,
                "agent": cleaned_agent_data,
                "client": client,
                "user": admin_user,
                "latest_config": latest_config,
                "latest_config_json": latest_config_json,
                "has_config_updates": bool(agent_config) if agent_config else False,
                "agent_personality": agent_personality,
            }
            return templates.TemplateResponse("admin/agent_detail.html", template_data)
    
    except Exception as e:
        logger.error(f"Error in agent_detail: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# HTMX Partial Routes

@router.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Stats partial for HTMX updates"""
    summary = await get_system_summary(admin_user)
    
    return templates.TemplateResponse("admin/partials/stats.html", {
        "request": request,
        "summary": summary
    })

@router.get("/partials/client-list", response_class=HTMLResponse)
async def client_list_partial(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Client list partial for HTMX updates"""
    clients = await get_all_clients_with_containers(admin_user)

    return templates.TemplateResponse("admin/partials/client_list.html", {
        "request": request,
        "clients": clients
    })

@router.get("/partials/health", response_class=HTMLResponse)
async def health_partial(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """System health partial for HTMX updates - with real health checks"""
    from app.core.dependencies import get_client_service
    from app.integrations.livekit_client import livekit_manager
    import httpx

    client_service = get_client_service()
    scoped_ids = get_scoped_client_ids(admin_user)

    health_statuses = []

    # Global LiveKit health check (done once)
    livekit_healthy = False
    try:
        if not livekit_manager._initialized:
            await livekit_manager.initialize()
        livekit_api = livekit_manager._get_api_client()
        # Try to list rooms as a health check
        await livekit_api.room.list_rooms(api.ListRoomsRequest())
        livekit_healthy = True
    except Exception as e:
        logger.warning(f"LiveKit health check failed: {e}")

    try:
        clients = await client_service.get_all_clients()
        if scoped_ids is not None:
            allowed = {str(cid) for cid in scoped_ids}
            clients = [c for c in clients if str(getattr(c, 'id', '')) in allowed]

        # Real health checks for each client
        for client in clients[:5]:  # Limit to first 5 for dashboard
            db_healthy = False
            api_healthy = False

            # Check Supabase database connectivity
            supabase_url = None
            if hasattr(client, 'settings') and client.settings:
                if hasattr(client.settings, 'supabase') and client.settings.supabase:
                    supabase_url = getattr(client.settings.supabase, 'url', None)

            if supabase_url:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as http_client:
                        # Check if Supabase REST API is reachable
                        resp = await http_client.get(f"{supabase_url}/rest/v1/", headers={"apikey": "anon"})
                        db_healthy = resp.status_code in (200, 401, 403)  # 401/403 means API is up but needs auth
                        api_healthy = db_healthy
                except Exception as e:
                    logger.debug(f"Supabase health check failed for {client.name}: {e}")

            # Overall health based on checks
            overall_healthy = db_healthy and livekit_healthy

            health_statuses.append({
                "client_id": client.id,
                "client_name": client.name,
                "healthy": overall_healthy,
                "checks": {
                    "api": {"healthy": api_healthy},
                    "database": {"healthy": db_healthy},
                    "livekit": {"healthy": livekit_healthy}
                }
            })
    except Exception as e:
        logger.warning(f"Failed to get health statuses: {e}")

    return templates.TemplateResponse("admin/partials/health.html", {
        "request": request,
        "health_statuses": health_statuses
    })

@router.get("/partials/container/{client_id}/status", response_class=HTMLResponse)
async def container_status_partial(
    request: Request,
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Container status partial for HTMX updates"""
    ensure_client_or_global_access(client_id, admin_user)
    container = await get_container_detail(client_id)
    
    return templates.TemplateResponse("admin/partials/container_status.html", {
        "request": request,
        "container": container
    })

@router.get("/partials/container/{client_id}/metrics", response_class=HTMLResponse)
async def container_metrics_partial(
    request: Request,
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Container metrics partial for HTMX updates"""
    ensure_client_or_global_access(client_id, admin_user)
    redis = await get_redis()
    
    # Get last 24 hours of metrics
    metrics_history = []
    now = datetime.now()
    
    for hours_ago in range(24):
        timestamp = now - timedelta(hours=hours_ago)
        key = f"metrics:{client_id}:{int(timestamp.timestamp())}"
        data = await redis.get(key)
        if data:
            metrics = json.loads(data)
            metrics["timestamp"] = timestamp.isoformat()
            metrics_history.append(metrics)
    
    return templates.TemplateResponse("admin/partials/container_metrics.html", {
        "request": request,
        "metrics_history": metrics_history,
        "client_id": client_id
    })

# Container Actions

@router.post("/containers/{client_id}/restart")
async def restart_container(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Restart container and return updated status"""
    ensure_client_or_global_access(client_id, admin_user)
    orchestrator = ContainerOrchestrator()
    
    # Stop and start container
    await orchestrator.stop_container(client_id)
    await orchestrator.get_or_create_container(
        client_id=client_id,
        client_config={}  # Config would be fetched from DB
    )
    
    # Return partial HTML for HTMX update
    container = await get_container_detail(client_id)
    
    return templates.TemplateResponse("admin/partials/container_status.html", {
        "container": container
    })

@router.post("/containers/{client_id}/stop")
async def stop_container(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Stop container"""
    ensure_client_or_global_access(client_id, admin_user)
    orchestrator = ContainerOrchestrator()
    await orchestrator.stop_container(client_id)
    
    # Return partial HTML for HTMX update
    container = await get_container_detail(client_id)
    
    return templates.TemplateResponse("admin/partials/container_status.html", {
        "container": container
    })

@router.get("/containers/{client_id}/logs")
async def get_container_logs(
    request: Request,
    client_id: str,
    lines: int = 100,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Stream container logs"""
    ensure_client_or_global_access(client_id, admin_user)
    orchestrator = ContainerOrchestrator()
    logs = await orchestrator.get_container_logs(client_id, lines)
    
    return templates.TemplateResponse("admin/partials/logs.html", {
        "request": request,
        "logs": logs,
        "client_id": client_id
    })

# Monitoring Routes

# Agent Preview Routes

@router.get("/agents/{agent_slug}/preview", response_class=HTMLResponse)
async def agent_preview_modal_legacy(
    request: Request,
    agent_slug: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Legacy route - return error since no client_id provided"""
    raise HTTPException(
        status_code=400,
        detail="Client ID is required. Please use /admin/agents/preview/{client_id}/{agent_slug}"
    )

@router.get("/agents/preview/{client_id}/{agent_slug}", response_class=HTMLResponse)
async def agent_preview_modal(
    request: Request,
    client_id: str,
    agent_slug: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Return the agent preview modal that embeds the production embed UI in an iframe"""
    try:
        logger.info(f"Preview (embed) modal requested for client_id={client_id}, agent_slug={agent_slug}")
        ensure_client_or_global_access(client_id, admin_user)

        base_url = request.base_url
        scheme = (
            request.headers.get("x-forwarded-proto")
            or base_url.scheme
            or "https"
        )
        netloc = (
            request.headers.get("x-forwarded-host")
            or base_url.netloc
            or base_url.hostname
            or request.headers.get("host", "")
        )
        if not netloc:
            netloc = "localhost"
        if scheme == "http" and netloc and not netloc.startswith("localhost"):
            scheme = "https"
        api_base = "http://127.0.0.1:8000"

        # Call EnsureClientUser to get client JWT for admin preview
        import httpx
        timeout = httpx.Timeout(20.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Get platform session token for the API call
            platform_token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if not platform_token and admin_user:
                # Try to get from admin_user if available
                platform_token = admin_user.get("access_token", "")

            ensure_response = None
            try:
                ensure_response = await client.post(
                    f"{api_base}/api/v2/admin/ensure-client-user",
                    json={
                        "client_id": client_id,
                        "platform_user_id": admin_user.get("user_id"),
                        "user_email": admin_user.get("email")
                    },
                    headers={"Authorization": f"Bearer {platform_token}"} if platform_token else {}
                )
            except httpx.TimeoutException:
                logger.warning("ensure-client-user request timed out; continuing without client JWT fallback")
            
        client_jwt = None
        client_user_id = None
        client_supabase_tokens = None
        admin_email = admin_user.get("email") if isinstance(admin_user, dict) else None
            
        if ensure_response and ensure_response.status_code == 200:
            ensure_data = ensure_response.json()
            client_jwt = ensure_data.get("client_jwt")
            client_user_id = ensure_data.get("client_user_id")
            logger.info(f"Got client JWT for preview: client_user_id={client_user_id}")
        elif ensure_response is not None:
            logger.warning(f"Failed to get client JWT: {ensure_response.status_code}")

        if client_id != "global" and admin_email:
            try:
                client_supabase_tokens = await generate_client_session_tokens(client_id, admin_email)
                logger.info(
                    "Generated client Supabase tokens for embed preview user %s (client %s)",
                    admin_email,
                    client_id,
                )
            except Exception as exc:
                logger.warning(f"Failed to bootstrap client Supabase session for embed preview: {exc}")
        
        # Respect the original request scheme/host (include port in dev) for the embed iframe
        iframe_src = f"{scheme}://{netloc}/embed/{client_id}/{agent_slug}?theme=dark&source=admin"
        
        def _format_token_json(tokens: Optional[Dict[str, str]]):
            if not tokens:
                return "null", "null"
            return json.dumps(tokens.get("access_token")), json.dumps(tokens.get("refresh_token"))

        client_supabase_access_json, client_supabase_refresh_json = _format_token_json(client_supabase_tokens)

        # If we have a client JWT, pass it to the embed
        jwt_script = ""
        if client_jwt:
            if not client_supabase_tokens:
                client_supabase_tokens = {"access_token": client_jwt, "refresh_token": None}
            client_supabase_access_json, client_supabase_refresh_json = _format_token_json(client_supabase_tokens)
            jwt_script = f"""
                    // Send client JWT for admin preview (shadow user in client Supabase)
                    iframe.contentWindow.postMessage({{ 
                        type: 'supabase-session', 
                        access_token: '{client_jwt}',
                        // No refresh token for admin preview sessions
                        refresh_token: null,
                        is_admin_preview: true,
                        client_user_id: '{client_user_id}',
                        client_supabase_access_token: {client_supabase_access_json},
                        client_supabase_refresh_token: {client_supabase_refresh_json}
                    }}, '*');
            """
        else:
            # Fallback to original behavior if EnsureClientUser fails
            jwt_script = """
                    // Use global Supabase client from admin base to get current session
                    var sb = window.__adminSupabaseClient || null;
                    if (!sb || !sb.auth || !sb.auth.getSession) return;
                    var res = await sb.auth.getSession();
                    var session = (res && res.data && res.data.session) ? res.data.session : null;
                    if (session && session.access_token && session.refresh_token) {
                      iframe.contentWindow.postMessage({ type: 'supabase-session', access_token: session.access_token, refresh_token: session.refresh_token, client_supabase_access_token: %s, client_supabase_refresh_token: %s }, '*');
                    }
            """ % (client_supabase_access_json, client_supabase_refresh_json)
        
        # Dev banner removed - no longer needed
        dev_banner = ""
        
        modal_html = f"""
        <div class=\"fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4\">
          <div class=\"bg-dark-surface border border-dark-border rounded-lg w-full max-w-4xl h-[90vh] flex flex-col\">
            <div class=\"flex items-center justify-between p-3 border-b border-dark-border\">
              <h3 class=\"text-dark-text text-sm\">Preview Sidekick</h3>
              <button class=\"px-3 py-1 text-sm border border-dark-border rounded\" hx-on:click=\"document.getElementById('modal-container').innerHTML=''\">Close</button>
            </div>
            {dev_banner}
            <div class=\"flex-1\">
              <iframe id=\"embedFrame\" src=\"{iframe_src}\" allow=\"microphone; camera\" referrerpolicy=\"strict-origin-when-cross-origin\" style=\"border:0;width:100%;height:100%\"></iframe>
            </div>
          </div>
          <script>
            (function(){{
              try {{
                var iframe = document.getElementById('embedFrame');
                if (!iframe) return;
                iframe.addEventListener('load', async function() {{
                  try {{
{jwt_script}
                  }} catch (e) {{ console.warn('[preview->embed] token post failed', e); }}
                }});
              }} catch (e) {{ console.warn('[preview modal] init failed', e); }}
            }})();
          </script>
        </div>
        """
        return HTMLResponse(content=modal_html)
    except Exception as e:
        logger.error(f"Error loading embed preview: {e}", exc_info=True)
        return HTMLResponse(
            content=f"""
            <div class=\"fixed inset-0 bg-gray-900 bg-opacity-90 flex items-center justify-center z-50\">
                <div class=\"bg-dark-surface p-6 rounded-lg border border-dark-border max-w-md\">
                    <h3 class=\"text-lg font-medium text-dark-text mb-2\">Error Loading Preview</h3>
                    <p class=\"text-sm text-dark-text-secondary mb-4\">{str(e)}</p>
                    <button hx-on:click=\"document.getElementById('modal-container').innerHTML = ''\" 
                            class=\"btn-primary px-4 py-2 rounded text-sm\">Close</button>
                </div>
            </div>
            """,
            status_code=500
        )


@router.post("/agents/preview/{client_id}/{agent_slug}/send")
async def send_preview_message(
    request: Request,
    client_id: str,
    agent_slug: str,
    message: str = Form(...),
    session_id: str = Form(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Send a message in preview mode and get response"""
    from app.core.dependencies import get_agent_service

    ensure_client_or_global_access(client_id, admin_user)

    # Get agent details
    from app.integrations.supabase_client import supabase_manager
    
    agent = None
    
    # Handle global agents
    if client_id == "global":
        try:
            # Ensure supabase_manager is initialized
            if not supabase_manager._initialized:
                await supabase_manager.initialize()
            result = supabase_manager.auth_client.table('agents').select('*').eq('slug', agent_slug).execute()
            if result.data:
                agent_data = result.data[0]
                # Convert to agent object format
                agent = type('Agent', (), {
                    'id': agent_data.get('id'),
                    'slug': agent_data.get('slug'),
                    'name': agent_data.get('name'),
                    'description': agent_data.get('description', ''),
                    'system_prompt': agent_data.get('system_prompt', ''),
                    'enabled': agent_data.get('enabled', True),
                    'voice_settings': json.loads(agent_data.get('voice_settings')) if isinstance(agent_data.get('voice_settings'), str) and agent_data.get('voice_settings') else agent_data.get('voice_settings') or {
                        'provider': 'openai',
                        'voice_id': 'alloy',
                        'temperature': 0.7
                    },
                    'webhooks': agent_data.get('webhooks', {}),
                    'client_id': 'global'
                })()
        except Exception as e:
            logger.error(f"Failed to get global agent: {e}")
    else:
        # Use normal agent service for client-specific agents
        agent_service = get_agent_service()
        agent = await agent_service.get_agent(client_id, agent_slug)
    
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Get messages from session (stored in memory for preview)
    preview_sessions = getattr(request.app.state, 'preview_sessions', {})
    session_data = preview_sessions.get(session_id, {"messages": [], "conversation_id": None})
    
    # Ensure backward compatibility
    if isinstance(session_data, list):
        # Old format - convert to new format
        session_data = {"messages": session_data, "conversation_id": str(uuid.uuid4())}
    
    messages = session_data["messages"]
    
    # Get or create conversation_id for this session
    conversation_id = session_data.get("conversation_id")
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        session_data["conversation_id"] = conversation_id
    
    # Add user message
    messages.append({"content": message, "is_user": True})
    
    # Generate AI response using the trigger endpoint
    try:
        # Use the trigger endpoint to get a real AI response
        from app.api.v1.trigger import handle_text_trigger, TriggerAgentRequest, TriggerMode
        from app.utils.default_ids import validate_uuid
        
        # Require logged-in user's UUID; no generic or fallback
        user_id = None
        if admin_user:
            user_id = admin_user.get('user_id') or admin_user.get('id')
        # Normalize to string to satisfy Pydantic model typing
        if user_id is not None:
            user_id = str(user_id)
        # Enforce presence and validity
        if not user_id:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not validate_uuid(user_id):
            raise HTTPException(status_code=400, detail="Invalid user_id for authenticated user")
        
        # Create a mock request for the text trigger
        trigger_request = TriggerAgentRequest(
            agent_slug=agent_slug,
            client_id=client_id,
            mode=TriggerMode.TEXT,
            message=message,
            user_id=user_id,
            session_id=session_id,
            conversation_id=conversation_id  # Use the session's conversation_id
        )
        
        # For global agents, we'll use backend API keys
        if client_id == "global":
            # Get API keys from agent_configurations for this agent
            from app.integrations.supabase_client import supabase_manager
            
            # Try to get agent configuration with API keys
            api_keys = {}
            try:
                config_result = supabase_manager.admin_client.table('agent_configurations').select('*').eq('agent_slug', agent_slug).execute()
                if config_result.data:
                    config = config_result.data[0]
                    # Extract API keys from configuration
                    api_keys = {
                        'openai_api_key': config.get('openai_api_key', os.getenv('OPENAI_API_KEY', '')),
                        'groq_api_key': config.get('groq_api_key', ''),
                        'cerebras_api_key': config.get('cerebras_api_key', ''),
                        'deepgram_api_key': config.get('deepgram_api_key', ''),
                        'elevenlabs_api_key': config.get('elevenlabs_api_key', ''),
                        'cartesia_api_key': config.get('cartesia_api_key', '')
                    }
            except Exception as e:
                logger.warning(f"Failed to get agent configuration: {e}")
                # Use environment variables as fallback
                api_keys = {
                    'openai_api_key': os.getenv('OPENAI_API_KEY', ''),
                    'groq_api_key': os.getenv('GROQ_API_KEY', ''),
                    'cerebras_api_key': os.getenv('CEREBRAS_API_KEY', ''),
                }
            
            # Process the message using the agent's configured provider
            voice_settings = getattr(agent, 'voice_settings', None)
            llm_provider = (getattr(voice_settings, 'llm_provider', '') or '').lower()
            llm_model = getattr(voice_settings, 'llm_model', None)

            if not llm_provider:
                raise ValueError("Agent preview requires an LLM provider in voice settings")

            if not llm_model:
                raise ValueError(f"Agent preview requires an LLM model for provider '{llm_provider}'")

            import httpx

            def require_key(key_name: str) -> str:
                key = api_keys.get(key_name)
                if not key:
                    raise ValueError(f"Missing {key_name.replace('_', ' ')} for provider '{llm_provider}'")
                return key

            try:
                if llm_provider == 'openai':
                    openai_key = require_key('openai_api_key')
                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {openai_key}"},
                            json={
                                "model": llm_model,
                                "messages": [
                                    {"role": "system", "content": agent.system_prompt},
                                    {"role": "user", "content": message}
                                ],
                                "temperature": 0.7,
                                "max_tokens": 500
                            },
                            timeout=30.0
                        )
                    if response.status_code != 200:
                        raise RuntimeError(f"OpenAI API error: {response.status_code}")
                    result = response.json()
                    ai_response = result['choices'][0]['message']['content']

                elif llm_provider == 'groq':
                    groq_key = require_key('groq_api_key')
                    model = llm_model
                    if model in ('llama3-70b-8192', 'llama-3.1-70b-versatile'):
                        model = 'llama-3.3-70b-versatile'
                    logger.info(f"Using Groq API for agent {agent.name} with model {model}")
                    request_data = {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": agent.system_prompt or "You are a helpful AI assistant."},
                            {"role": "user", "content": message}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 500
                    }
                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={"Authorization": f"Bearer {groq_key}"},
                            json=request_data,
                            timeout=30.0
                        )
                    if response.status_code != 200:
                        error_detail = response.text[:200]
                        raise RuntimeError(f"Groq API error {response.status_code}: {error_detail}")
                    result = response.json()
                    ai_response = result['choices'][0]['message']['content']

                elif llm_provider == 'cerebras':
                    cerebras_key = require_key('cerebras_api_key')
                    logger.info(f"Using Cerebras API for agent {agent.name} with model {llm_model}")
                    headers = {"Authorization": f"Bearer {cerebras_key}"}
                    request_data = {
                        "model": llm_model,
                        "messages": [
                            {"role": "system", "content": agent.system_prompt or "You are a helpful assistant."},
                            {"role": "user", "content": message}
                        ],
                        "stream": False
                    }
                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            "https://api.cerebras.ai/v1/chat/completions",
                            headers=headers,
                            json=request_data,
                            timeout=30.0
                        )
                    if response.status_code != 200:
                        raise RuntimeError(f"Cerebras API error: {response.status_code}")
                    data = response.json()
                    ai_response = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if not ai_response:
                        raise RuntimeError("Cerebras response did not include assistant content")

                else:
                    raise ValueError(f"Unsupported LLM provider '{llm_provider}' for preview")

                logger.debug(f"System prompt length: {len(agent.system_prompt) if agent.system_prompt else 0}")

            except Exception as e:
                logger.error(f"AI API call failed: {e}")
                raise
                
        else:
            # Get client info for non-global agents
            from app.core.dependencies import get_client_service
            client_service = get_client_service()
            client = await client_service.get_client(client_id)
            
            # Handle the text trigger
            result = await handle_text_trigger(trigger_request, agent, client)
            ai_response = result.get("response", f"I'm {agent.name}. I'm currently in preview mode. In production, I would process your message: '{message}'")
        
    except Exception as e:
        import traceback
        logger.error(f"Preview AI response failed: {e}")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Fallback to a simple preview response with error detail
        ai_response = f"I'm {agent.name}. Error: {str(e)[:100]}"
    
    # Add AI response
    messages.append({"content": ai_response, "is_user": False})
    
    # Store messages and conversation_id in session
    if not hasattr(request.app.state, 'preview_sessions'):
        request.app.state.preview_sessions = {}
    request.app.state.preview_sessions[session_id] = session_data
    
    # Return updated messages
    return templates.TemplateResponse("admin/partials/chat_messages.html", {
        "request": request,
        "messages": messages,
        "is_loading": False
    })


@router.post("/agents/preview/{client_id}/{agent_slug}/stream")
async def stream_preview_message(
    request: Request,
    client_id: str,
    agent_slug: str,
    message: str = Form(...),
    session_id: str = Form(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Stream a message reply in preview mode using SSE-like chunking."""
    from app.core.dependencies import get_agent_service, get_client_service
    from app.shared.llm_factory import get_llm
    from livekit.agents import llm as lk_llm
    import json, asyncio

    ensure_client_or_global_access(client_id, admin_user)

    async def generate():
        try:
            try:
                logger.info("[preview-stream] start")
            except Exception:
                pass
            # Send an initial SSE comment to open the stream quickly
            try:
                yield ":stream-open\n\n"
            except Exception:
                pass
            # Load agent and client
            agent_service = get_agent_service()
            client_service = get_client_service()
            agent = await agent_service.get_agent(client_id, agent_slug)
            if not agent:
                yield f"data: {json.dumps({'error': 'Agent not found'})}\n\n"
                return
            client = await client_service.get_client(client_id)

            # Build context from preview session history
            preview_sessions = getattr(request.app.state, 'preview_sessions', {})
            session_data = preview_sessions.get(session_id, {"messages": [], "conversation_id": None})
            history = session_data.get("messages", [])
            ctx = lk_llm.ChatContext()
            if getattr(agent, 'system_prompt', None):
                ctx.add_message(role="system", content=agent.system_prompt)
            for m in history[-10:]:
                ctx.add_message(role=("user" if m.get("is_user") else "assistant"), content=m.get("content", ""))
            ctx.add_message(role="user", content=message)

            # Provider/model and keys
            vs = getattr(agent, 'voice_settings', None)
            llm_provider = getattr(vs, 'llm_provider', None) or 'openai'
            llm_model = getattr(vs, 'llm_model', None) or 'gpt-4'
            api_keys = (getattr(client, 'settings', None) and getattr(client.settings, 'api_keys', None)) or {}
            api_keys = api_keys.dict() if hasattr(api_keys, 'dict') else {}

            # Init LLM via factory and stream
            llm = get_llm(llm_provider, llm_model, api_keys)
            stream = llm.chat(chat_ctx=ctx)
            full_text = ""
            # Stream token chunks as they arrive
            import re
            async for chunk in stream:
                delta = None
                try:
                    if hasattr(chunk, 'choices') and chunk.choices:
                        part = getattr(chunk.choices[0], 'delta', None) or getattr(chunk.choices[0], 'message', None)
                        if part and hasattr(part, 'content') and part.content:
                            delta = part.content
                    if not delta and hasattr(chunk, 'content') and chunk.content:
                        delta = chunk.content
                    if not delta and hasattr(chunk, 'text') and getattr(chunk, 'text'):
                        delta = getattr(chunk, 'text')
                    if not delta and isinstance(chunk, str):
                        # Some providers can send plain strings; allow those
                        delta = chunk if chunk.strip() else None
                    if not delta:
                        # Regex fallback: extract content='...' fragments from stringified chunk
                        s = str(chunk)
                        matches = re.findall(r"content=\'([^\']*)\'", s)
                        if not matches:
                            matches = re.findall(r'content=\"([^\"]*)\"', s)
                        if matches:
                            delta = ''.join(matches)
                except Exception:
                    delta = None
                if delta:
                    full_text += delta
                    # Log length for diagnostics without leaking content
                    try:
                        logger.info(f"[preview-stream] delta len={len(delta)}")
                    except Exception:
                        pass
                    yield f"data: {json.dumps({'delta': delta})}\n\n"
                    await asyncio.sleep(0)

            # Save assistant message back to preview session
            if not hasattr(request.app.state, 'preview_sessions'):
                request.app.state.preview_sessions = {}
            session_data.setdefault("messages", []).append({
                "message_id": f"asst_{int(asyncio.get_event_loop().time()*1000)}",
                "role": "assistant",
                "content": full_text,
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": {"agent_slug": agent.slug, "model": llm_model}
            })
            request.app.state.preview_sessions[session_id] = session_data
            try:
                logger.info(f"[preview-stream] done len={len(full_text)}")
            except Exception:
                pass
            # Send completion with full_text for fallback rendering on client
            yield f"data: {json.dumps({'done': True, 'full_text': full_text})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    })


@router.get("/agents/preview/{client_id}/{agent_slug}/messages")
async def get_preview_messages(
    request: Request,
    client_id: str,
    agent_slug: str,
    session_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get messages for a preview session"""
    ensure_client_or_global_access(client_id, admin_user)
    preview_sessions = getattr(request.app.state, 'preview_sessions', {})
    session_data = preview_sessions.get(session_id, {"messages": [], "conversation_id": None})
    
    # Handle backward compatibility
    if isinstance(session_data, list):
        messages = session_data
    else:
        messages = session_data.get("messages", [])
    
    return templates.TemplateResponse("admin/partials/chat_messages.html", {
        "request": request,
        "messages": messages,
        "is_loading": False
    })


@router.post("/agents/preview/{client_id}/{agent_slug}/set-mode")
async def set_preview_mode(
    request: Request,
    client_id: str,
    agent_slug: str,
    session_id: str = Form(...),
    mode: str = Form(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Switch between text and voice preview modes"""
    import time
    request_start = time.time()

    from app.core.dependencies import get_agent_service
    from app.integrations.supabase_client import supabase_manager
    import json

    ensure_client_or_global_access(client_id, admin_user)

    logger.info(f"Set preview mode started: mode={mode}, client_id={client_id}, agent_slug={agent_slug}")
    
    agent = None
    
    # Handle global agents
    if client_id == "global":
        # Try Supabase, fall back to default
        try:
            # Ensure supabase_manager is initialized
            if not supabase_manager._initialized:
                await supabase_manager.initialize()
            result = supabase_manager.auth_client.table('agents').select('*').eq('slug', agent_slug).execute()
            if result.data:
                agent_data = result.data[0]
                # Convert to agent object format
                agent = type('Agent', (), {
                        'id': agent_data.get('id'),
                        'slug': agent_data.get('slug'),
                        'name': agent_data.get('name'),
                        'description': agent_data.get('description', ''),
                        'system_prompt': agent_data.get('system_prompt', ''),
                        'enabled': agent_data.get('enabled', True),
                        'voice_settings': json.loads(agent_data.get('voice_settings')) if isinstance(agent_data.get('voice_settings'), str) and agent_data.get('voice_settings') else agent_data.get('voice_settings', {
                            'provider': 'openai',
                            'voice_id': 'alloy',
                            'temperature': 0.7
                        }),
                        'webhooks': agent_data.get('webhooks', {}),
                        'client_id': 'global'
                    })()
        except Exception as e:
            logger.error(f"Failed to get global agent: {e}")
            # Create default agent
            agent = type('Agent', (), {
                'id': f'{agent_slug}-global',
                'slug': agent_slug,
                'name': agent_slug.replace('-', ' ').title(),
                'description': 'Global AI assistant',
                'system_prompt': f'You are {agent_slug.replace("-", " ").title()}, an AI assistant.',
                'enabled': True,
                'voice_settings': {
                    'provider': 'openai',
                    'voice_id': 'alloy',
                    'temperature': 0.7
                },
                'webhooks': {},
                'client_id': 'global'
            })()
    else:
        agent_fetch_start = time.time()
        agent_service = get_agent_service()
        agent = await agent_service.get_agent(client_id, agent_slug)
        logger.info(f"Agent fetch took {time.time() - agent_fetch_start:.2f}s")
    
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    if mode == "voice":
        # Handle voice mode - FAST VERSION for testing
        logger.info("Handling voice mode request - FAST")
        
        # # Skip everything and just return a simple response
        # return templates.TemplateResponse("admin/partials/voice_preview_live.html", {
        #     "request": request,
        #     "room_name": f"preview_{agent_slug}_fast",
        #     "server_url": "wss://litebridge-hw6srhvi.livekit.cloud",
        #     "user_token": "dummy-token-for-testing",
        #     "agent_slug": agent_slug,
        #     "client_id": client_id,
        #     "session_id": session_id
        # })
        
        try:
            import uuid
            from app.api.v1.trigger import TriggerAgentRequest, TriggerMode, trigger_agent
            from app.core.dependencies import get_agent_service
            
            # Generate a unique room name for this preview
            room_name = f"preview_{agent_slug}_{uuid.uuid4().hex[:8]}"
            
            # Create trigger request for voice mode
            trigger_request = TriggerAgentRequest(
                agent_slug=agent_slug,
                client_id=client_id if client_id != "global" else None,  # Let trigger endpoint handle global agents
                mode=TriggerMode.VOICE,
                room_name=room_name,
                # Use the actual user's UUID from the query parameter or a default admin user
                # This ensures the context system can find the user's profile
                user_id=get_user_id_from_request(request.query_params.get('user_id'), admin_user),
                session_id=session_id,
                conversation_id=str(uuid.uuid4())  # Generate proper UUID for database storage
            )
            
            # Call the actual trigger endpoint to ensure proper setup
            agent_service_inst = get_agent_service()
            
            logger.info(f"Calling trigger endpoint for preview room: {room_name}")
            trigger_result = await trigger_agent(trigger_request, agent_service_inst)
            
            # Extract the response data from trigger result
            if trigger_result.success and trigger_result.data:
                livekit_config = trigger_result.data.get("livekit_config", {})
                user_token = livekit_config.get("user_token", "")
                server_url = livekit_config.get("server_url", "")
                
                # Log what we're sending to the template
                logger.info(f"Voice preview from trigger - Room: {room_name}, Server: {server_url}, Token: {user_token[:50] if user_token else 'None'}...")
                
                transcript_context = await _get_transcript_supabase_context(client_id)
                client_supabase_tokens = None
                admin_email = admin_user.get("email") if isinstance(admin_user, dict) else None
                if client_id != "global" and admin_email:
                    try:
                        client_supabase_tokens = await generate_client_session_tokens(client_id, admin_email)
                    except Exception as exc:
                        logger.warning(f"Failed to bootstrap client Supabase session: {exc}")

                # Return voice interface with LiveKit client
                return templates.TemplateResponse("admin/partials/voice_preview_live.html", {
                    "request": request,
                    "room_name": room_name,
                    "server_url": server_url,
                    "user_token": user_token,
                    "agent_slug": agent_slug,
                    "client_id": client_id,
                    "session_id": session_id,
                    **transcript_context,
                    "client_supabase_access_token": (client_supabase_tokens or {}).get("access_token"),
                    "client_supabase_refresh_token": (client_supabase_tokens or {}).get("refresh_token"),
                    "client_supabase_token_type": (client_supabase_tokens or {}).get("token_type"),
                    "client_supabase_expires_in": (client_supabase_tokens or {}).get("expires_in"),
                })
            else:
                error_msg = trigger_result.message if hasattr(trigger_result, 'message') else "Failed to start voice session"
                raise Exception(error_msg)
                
        except Exception as e:
            logger.error(f"Failed to start voice preview: {e}")
            return templates.TemplateResponse("admin/partials/voice_error.html", {
                "request": request,
                "error": str(e),
                "client_id": client_id,
                "agent_slug": agent_slug,
                "session_id": session_id
            })
    else:
        # Return text chat interface (reuse the messages partial with container)
        preview_sessions = getattr(request.app.state, 'preview_sessions', {})
        session_data = preview_sessions.get(session_id, {"messages": [], "conversation_id": None})
        
        # Handle backward compatibility
        if isinstance(session_data, list):
            messages = session_data
        else:
            messages = session_data.get("messages", [])
        
        # Return the full text chat container
        return f"""
        <div class="h-96 flex flex-col">
            <!-- Messages Area -->
            <div id="chatMessages" class="flex-1 overflow-y-auto p-4 space-y-4"
                 hx-get="/admin/agents/preview/{client_id}/{agent_slug}/messages?session_id={session_id}"
                 hx-trigger="load"
                 hx-swap="innerHTML">
                {"".join([f'<div class="flex {"justify-end" if msg["is_user"] else "justify-start"}"><div class="max-w-xs lg:max-w-md px-4 py-2 rounded-lg {"bg-brand-teal text-white" if msg["is_user"] else "bg-dark-elevated text-dark-text border border-dark-border"}">{msg["content"]}</div></div>' for msg in messages]) if messages else '<div class="text-center text-dark-text-secondary text-sm py-8"><p>Start a conversation with your agent</p><p class="text-xs mt-2">Messages are not saved</p></div>'}
            </div>
            
            <!-- Input Area -->
            <div class="border-t border-dark-border p-4">
                <form hx-post="/admin/agents/preview/{client_id}/{agent_slug}/send"
                      hx-target="#chatMessages"
                      hx-swap="innerHTML"
                      hx-on::after-request="this.reset()"
                      class="flex gap-2">
                    <input type="hidden" name="session_id" value="{session_id}">
                    <input type="text" 
                           name="message"
                           placeholder="Type a message..." 
                           class="flex-1 bg-dark-elevated border-dark-border text-dark-text rounded-md px-3 py-2 border focus:ring-brand-teal focus:border-brand-teal"
                           autocomplete="off"
                           required>
                    <button type="submit" 
                            class="btn-primary px-4 py-2 rounded text-sm font-medium transition-all">
                        Send
                    </button>
                </form>
            </div>
        </div>
        """


@router.get("/test-htmx")
async def test_htmx(request: Request):
    """Test HTMX functionality"""
    return templates.TemplateResponse("admin/test_htmx.html", {"request": request})


@router.get("/agents/preview/voice-debug")
async def voice_preview_debug(request: Request):
    """Debug endpoint to test voice preview"""
    # Generate test values
    import uuid
    from app.integrations.livekit_client import livekit_manager
    
    try:
        # Ensure livekit_manager is initialized
        if not livekit_manager._initialized:
            await livekit_manager.initialize()
            
        room_name = f"debug_room_{uuid.uuid4().hex[:8]}"
        
        # Create a test token
        user_token = livekit_manager.create_token(
            identity="debug_user",
            room_name=room_name
        )
        
        transcript_context = await _get_transcript_supabase_context("debug-client")
        return templates.TemplateResponse("admin/partials/voice_preview_live.html", {
            "request": request,
            "room_name": room_name,
            "server_url": livekit_manager.url,
            "user_token": user_token,
            "agent_slug": "debug-agent",
            "client_id": "debug-client",
            "session_id": "debug-session",
            **transcript_context,
        })
    except Exception as e:
        logger.error(f"Voice debug error: {e}")
        return {"error": str(e)}


@router.post("/agents/preview/{client_id}/{agent_slug}/voice-start")
async def start_voice_preview(
    request: Request,
    client_id: str,
    agent_slug: str,
    session_id: str = Form(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Start a voice preview session"""
    import uuid
    from app.api.v1.trigger import TriggerAgentRequest, TriggerMode
    from app.core.dependencies import get_agent_service
    from app.integrations.supabase_client import supabase_manager
    import json

    try:
        ensure_client_or_global_access(client_id, admin_user)

        # Get agent details
        agent = None
        
        # Handle global agents
        if client_id == "global":
            try:
                # Ensure supabase_manager is initialized
                if not supabase_manager._initialized:
                    await supabase_manager.initialize()
                result = supabase_manager.auth_client.table('agents').select('*').eq('slug', agent_slug).execute()
                if result.data and len(result.data) > 0:
                    agent_data = result.data[0]
                    # Convert to agent object format
                    agent = type('Agent', (), {
                        'id': agent_data.get('id'),
                        'slug': agent_data.get('slug'),
                        'name': agent_data.get('name'),
                        'description': agent_data.get('description', ''),
                        'system_prompt': agent_data.get('system_prompt', ''),
                        'enabled': agent_data.get('enabled', True),
                        'voice_settings': json.loads(agent_data.get('voice_settings')) if isinstance(agent_data.get('voice_settings'), str) else agent_data.get('voice_settings') or {
                            'provider': 'openai',
                            'voice_id': 'alloy',
                            'temperature': 0.7
                        },
                        'webhooks': agent_data.get('webhooks', {}),
                        'client_id': 'global'
                    })()
                else:
                    # Fallback for testing
                    agent = type('Agent', (), {
                        'id': f'{agent_slug}-global',
                        'slug': agent_slug,
                        'name': agent_slug.replace('-', ' ').title(),
                        'description': 'Test agent for preview',
                        'system_prompt': 'You are a helpful AI assistant.',
                        'enabled': True,
                        'voice_settings': {
                            'provider': 'openai',
                            'voice_id': 'alloy',
                            'temperature': 0.7
                        },
                        'webhooks': {},
                        'client_id': 'global'
                    })()
            except Exception as e:
                logger.error(f"Failed to get global agent: {e}")
                # Use fallback
                agent = type('Agent', (), {
                    'id': f'{agent_slug}-global',
                    'slug': agent_slug,
                    'name': agent_slug.replace('-', ' ').title(),
                    'description': 'Test agent for preview',
                    'system_prompt': 'You are a helpful AI assistant.',
                    'enabled': True,
                    'voice_settings': {
                        'provider': 'openai',
                        'voice_id': 'alloy',
                        'temperature': 0.7
                    },
                    'webhooks': {},
                    'client_id': 'global'
                })()
        else:
            agent_service = get_agent_service()
            agent = await agent_service.get_agent(client_id, agent_slug)
        
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        # Generate unique room name and conversation ID for this session
        room_name = f"preview_{agent_slug}_{uuid.uuid4().hex[:8]}"
        conversation_id = f"voice_{room_name}_{uuid.uuid4().hex[:8]}"
        
        # Create trigger request - always use the client_id provided
        trigger_request = TriggerAgentRequest(
            agent_slug=agent_slug,
            client_id=client_id,  # Use the actual client ID (Autonomite client)
            mode=TriggerMode.VOICE,
            room_name=room_name,
            # Use the actual user's UUID for proper context loading
            user_id=get_user_id_from_request(None, admin_user),
            session_id=session_id,
            conversation_id=conversation_id
        )
        
        # Always use the standard trigger flow for ALL agents
        # This ensures consistent behavior across all agent types
        
        # Import the trigger function
        from app.api.v1.trigger import trigger_agent
        
        # Get agent service for trigger
        agent_service = get_agent_service()
        
        # Trigger the agent
        logger.info(f"🎯 Triggering agent for room: {room_name}")
        trigger_result = await trigger_agent(trigger_request, agent_service=agent_service)
        
        # Extract connection details from the response data
        # trigger_result is a TriggerAgentResponse object, not a dict
        livekit_config = trigger_result.data.get('livekit_config', {}) if trigger_result.data else {}
        server_url = livekit_config.get('server_url', '')
        user_token = livekit_config.get('user_token', '')
        
        # CRITICAL: Use the room name from the trigger response to ensure consistency
        actual_room_name = trigger_result.data.get('room_name', room_name) if trigger_result.data else room_name
        
        logger.info(f"📍 Room names - Requested: {room_name}, Actual: {actual_room_name}")
        if room_name != actual_room_name:
            logger.warning(f"⚠️ Room name mismatch! Frontend will use: {actual_room_name}")
        
        # Get conversation_id from agent_context (now properly provided by trigger)
        agent_context = trigger_result.data.get('agent_context', {}) if trigger_result.data else {}
        conversation_id = agent_context.get('conversation_id')
        
        logger.info(f"📝 Voice session started with conversation_id: {conversation_id}")
        
        transcript_context = await _get_transcript_supabase_context(client_id)
        client_supabase_tokens = None
        admin_email = admin_user.get("email") if isinstance(admin_user, dict) else None
        if client_id != "global" and admin_email:
            try:
                client_supabase_tokens = await generate_client_session_tokens(client_id, admin_email)
                logger.info(
                    "Generated client Supabase tokens for voice preview user %s (client %s)",
                    admin_email,
                    client_id,
                )
            except Exception as exc:
                logger.warning(f"Failed to bootstrap client Supabase session: {exc}")

        # Return voice interface with LiveKit client and transcript support
        return templates.TemplateResponse("admin/partials/voice_preview_live.html", {
            "request": request,
            "room_name": actual_room_name,  # Use the actual room name from trigger
            "server_url": server_url,
            "user_token": user_token,
            "agent": agent,
            "agent_id": agent.id if hasattr(agent, 'id') else agent_slug,
            "agent_slug": agent_slug,
            "client_id": client_id,
            "session_id": session_id,
            "conversation_id": conversation_id,
            **transcript_context,
            "client_supabase_access_token": (client_supabase_tokens or {}).get("access_token"),
            "client_supabase_refresh_token": (client_supabase_tokens or {}).get("refresh_token"),
            "client_supabase_token_type": (client_supabase_tokens or {}).get("token_type"),
            "client_supabase_expires_in": (client_supabase_tokens or {}).get("expires_in"),
        })
        
    except Exception as e:
        logger.error(f"Failed to start voice preview: {e}", exc_info=True)
        return templates.TemplateResponse("admin/partials/voice_error.html", {
            "request": request,
            "error": str(e),
            "client_id": client_id,
            "agent_slug": agent_slug,
            "session_id": session_id
        })


@router.post("/agents/preview/{client_id}/{agent_slug}/voice-stop")
async def stop_voice_preview(
    request: Request,
    client_id: str,
    agent_slug: str,
    session_id: str = Form(...),
    room_name: str = Form(None),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Stop a voice preview session"""
    ensure_client_or_global_access(client_id, admin_user)

    # Stop the agent if room name provided
    if room_name:
        try:
            from app.services.agent_spawner import agent_spawner
            await agent_spawner.stop_agent_for_room(room_name)
            logger.info(f"Stopped agent for room {room_name}")
        except Exception as e:
            logger.error(f"Error stopping agent: {e}")
    
    # Get agent to display correct voice settings
    from app.core.dependencies import get_agent_service
    from app.integrations.supabase_client import supabase_manager
    import json
    
    agent = None
    
    # Handle global agents
    if client_id == "global":
        try:
            # Ensure supabase_manager is initialized
            if not supabase_manager._initialized:
                await supabase_manager.initialize()
            result = supabase_manager.auth_client.table('agents').select('*').eq('slug', agent_slug).execute()
            if result.data:
                agent_data = result.data[0]
                # Parse voice settings if it's a string
                voice_settings = agent_data.get('voice_settings', {})
                if isinstance(voice_settings, str):
                    voice_settings = json.loads(voice_settings)
                agent = {
                    "name": agent_data.get('name', agent_slug),
                    "slug": agent_slug,
                    "voice_settings": voice_settings
                }
        except Exception as e:
            logger.error(f"Failed to fetch global agent: {e}")
    else:
        # Use agent service for non-global agents
        agent_service = get_agent_service()
        try:
            agent_obj = await agent_service.get_agent(client_id, agent_slug)
            if agent_obj:
                voice_settings = agent_obj.voice_settings
                if isinstance(voice_settings, str):
                    voice_settings = json.loads(voice_settings)
                agent = {
                    "name": agent_obj.name,
                    "slug": agent_slug,
                    "voice_settings": voice_settings
                }
        except Exception as e:
            logger.error(f"Failed to fetch agent: {e}")
    
    # Fallback if agent not found
    if not agent:
        agent = {
            "name": agent_slug,
            "slug": agent_slug,
            "voice_settings": {"provider": "openai", "voice_id": "alloy", "temperature": 0.7}
        }
    
    # Return to the initial voice interface
    return templates.TemplateResponse("admin/partials/voice_chat.html", {
        "request": request,
        "agent": agent,
        "client_id": client_id,
        "session_id": session_id
    })


@router.post("/agents/preview/{client_id}/{agent_slug}/clear")
async def clear_preview_messages(
    request: Request,
    client_id: str,
    agent_slug: str,
    session_id: str = Form(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Clear messages for a preview session"""
    ensure_client_or_global_access(client_id, admin_user)
    if hasattr(request.app.state, 'preview_sessions') and session_id in request.app.state.preview_sessions:
        # Reset with new conversation_id
        request.app.state.preview_sessions[session_id] = {
            "messages": [],
            "conversation_id": str(uuid.uuid4())
        }
    
    return templates.TemplateResponse("admin/partials/chat_messages.html", {
        "request": request,
        "messages": [],
        "is_loading": False
    })


@router.get("/agents/preview/{client_id}/{agent_slug}/trigger-info")
async def get_trigger_info(
    request: Request,
    client_id: str,
    agent_slug: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get trigger endpoint info for testing"""
    ensure_client_or_global_access(client_id, admin_user)
    # Simple info modal
    return f"""
    <div class="fixed bottom-4 right-4 max-w-md p-4 bg-dark-surface rounded-lg shadow-lg border border-dark-border">
        <div class="flex justify-between items-start mb-3">
            <h4 class="text-sm font-medium text-dark-text">Trigger Endpoint Info</h4>
            <button hx-on:click="this.parentElement.parentElement.remove()" 
                    class="text-dark-text-secondary hover:text-dark-text">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                </svg>
            </button>
        </div>
        <div class="space-y-2 text-xs">
            <div class="p-2 bg-dark-elevated rounded border border-dark-border">
                <p class="text-dark-text-secondary mb-1">API Endpoint:</p>
                <p class="font-mono text-dark-text">/api/v1/trigger-agent</p>
            </div>
            <div class="p-2 bg-dark-elevated rounded border border-dark-border">
                <p class="text-dark-text-secondary mb-1">Agent Slug:</p>
                <p class="font-mono text-dark-text">{agent_slug}</p>
            </div>
            <div class="p-2 bg-dark-elevated rounded border border-dark-border">
                <p class="text-dark-text-secondary mb-1">Client ID:</p>
                <p class="font-mono text-dark-text text-xs">{client_id}</p>
            </div>
            <p class="text-dark-text-secondary mt-2">
                Use these values to test the agent via the API or WordPress plugin.
            </p>
        </div>
    </div>
    """


@router.get("/agents/{client_id}/{agent_slug}/embed-code", response_class=HTMLResponse)
async def embed_code_modal(request: Request, client_id: str, agent_slug: str, admin_user: Dict[str, Any] = Depends(get_admin_user)):
    ensure_client_access(client_id, admin_user)
    host = request.base_url.hostname
    iframe = f"""<iframe
src=\"https://{host}/embed/{client_id}/{agent_slug}?theme=dark\"
style=\"border:0;width:100%;max-width:800px;height:640px\"
allow=\"microphone; camera\"
referrerpolicy=\"strict-origin-when-cross-origin\"></iframe>"""
    return HTMLResponse("""
<div class=\"fixed inset-0 z-50 flex items-center justify-center bg-black/65 backdrop-blur-sm\">
  <div class=\"bg-black/70 border border-dark-border rounded-lg p-4 max-w-lg w-full shadow-xl\">
    <h3 class=\"text-lg text-dark-text mb-3\">Copy Embed Code</h3>
    <textarea id=\"embedCode\" class=\"w-full bg-dark-elevated text-dark-text border border-dark-border rounded p-2\" rows=\"6\" readonly>""" + iframe + """</textarea>
    <div class=\"mt-3 flex gap-2\">
      <button class=\"btn-primary px-3 py-2 rounded text-sm\" onclick=\"var b=this; navigator.clipboard.writeText(document.getElementById('embedCode').value).then(function(){ b.disabled=true; var t=b.textContent; b.textContent='Copied!'; setTimeout(function(){ b.textContent=t; b.disabled=false; },1200); });\">Copy</button>
      <button class=\"px-3 py-2 rounded text-sm border border-dark-border text-dark-text hover:bg-dark-elevated\" hx-on:click=\"document.getElementById('modal-container').innerHTML=''\">Close</button>
    </div>
    <p class=\"text-dark-text-secondary text-xs mt-3\">Ensure your site origin is in this Sidekick's allowlist.</p>
  </div>
</div>
""")


@router.get("/monitoring", response_class=HTMLResponse)
async def monitoring_dashboard(
    request: Request,
    client_id: Optional[str] = None,
    period: str = "current",
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """System monitoring dashboard"""
    from app.core.dependencies import get_client_service
    from app.services.usage_tracking import UsageTrackingService

    client_service = get_client_service()
    usage_service = UsageTrackingService()
    await usage_service.initialize()

    # Get clients the user has access to
    scoped_ids = get_scoped_client_ids(admin_user)
    clients = []
    try:
        all_clients = await client_service.get_all_clients()
        if scoped_ids is not None:
            allowed = {str(cid) for cid in scoped_ids}
            clients = [c for c in all_clients if str(getattr(c, 'id', '')) in allowed]
        else:
            clients = all_clients
    except Exception as e:
        logger.warning(f"Failed to fetch clients for monitoring: {e}")

    # Convert clients to dicts for template
    clients_data = []
    for c in clients:
        clients_data.append({
            "id": str(c.id) if hasattr(c, 'id') else str(c.get('id', '')),
            "name": c.name if hasattr(c, 'name') else c.get('name', 'Unknown'),
        })

    # Validate selected client_id
    selected_client_id = None
    if client_id:
        if scoped_ids is None or client_id in scoped_ids:
            selected_client_id = client_id

    # Auto-select primary client for non-superadmin users
    if not selected_client_id and not admin_is_super(admin_user):
        selected_client_id = admin_user.get("primary_client_id")

    # Get sidekicks using the same approach as agents_page
    sidekicks = []
    try:
        if admin_is_super(admin_user):
            all_agents = await get_all_agents()
            if selected_client_id:
                all_agents = [a for a in all_agents if str(a.get('client_id', '')) == selected_client_id]
        else:
            from app.core.dependencies import get_agent_service
            agent_service = get_agent_service()
            visible_client_ids = list(scoped_ids) if scoped_ids else []
            all_agents = []
            client_name_cache = {}

            for cid in visible_client_ids:
                if selected_client_id and str(cid) != selected_client_id:
                    continue
                try:
                    client_agents = await agent_service.get_client_agents(str(cid))
                    if str(cid) not in client_name_cache:
                        try:
                            client = await client_service.get_client(str(cid))
                            client_name_cache[str(cid)] = client.name if hasattr(client, 'name') else client.get('name', 'Unknown')
                        except:
                            client_name_cache[str(cid)] = 'Unknown'

                    for a in client_agents:
                        a_dict = a.dict() if hasattr(a, 'dict') else (a if isinstance(a, dict) else {})
                        a_dict['client_name'] = client_name_cache[str(cid)]
                        a_dict['client_id'] = str(cid)
                        all_agents.append(a_dict)
                except Exception as e:
                    logger.warning(f"Failed to get agents for client {cid}: {e}")

        # Convert to sidekicks format
        for agent in all_agents[:20]:
            sidekicks.append({
                "id": str(agent.get('id', '')),
                "name": agent.get('name', 'Unknown'),
                "slug": agent.get('slug', ''),
                "client_id": str(agent.get('client_id', '')),
                "client_name": agent.get('client_name', 'Unknown'),
                "enabled": agent.get('enabled', False),
                "voice_chat_enabled": agent.get('voice_chat_enabled', True),
                "text_chat_enabled": agent.get('text_chat_enabled', True),
                "agent_image": agent.get('agent_image', ''),
                "status": "idle" if agent.get('enabled', False) else "inactive",
                "status_label": "Idle" if agent.get('enabled', False) else "Inactive",
                "status_color": "brand-teal" if agent.get('enabled', False) else "dark-text-secondary",
            })
    except Exception as e:
        logger.error(f"Failed to load sidekicks for monitoring: {e}")

    # Get aggregated usage data
    usage_data = {}
    try:
        target_clients = [selected_client_id] if selected_client_id else [c['id'] for c in clients_data]

        total_voice_used = 0
        total_voice_limit = 0
        total_text_used = 0
        total_text_limit = 0
        total_embed_used = 0
        total_embed_limit = 0

        for cid in target_clients[:10]:  # Limit to 10 clients for performance
            try:
                agg = await usage_service.get_client_aggregated_usage(cid)
                total_voice_used += agg.voice.used
                total_voice_limit += agg.voice.limit
                total_text_used += agg.text.used
                total_text_limit += agg.text.limit
                total_embed_used += agg.embedding.used
                total_embed_limit += agg.embedding.limit
            except Exception as e:
                logger.warning(f"Failed to get usage for client {cid}: {e}")

        # Calculate percentages
        voice_percent = (total_voice_used / total_voice_limit * 100) if total_voice_limit > 0 else 0
        text_percent = (total_text_used / total_text_limit * 100) if total_text_limit > 0 else 0
        embed_percent = (total_embed_used / total_embed_limit * 100) if total_embed_limit > 0 else 0

        usage_data = {
            "voice": {
                "used": total_voice_used,
                "limit": total_voice_limit,
                "minutes_used": round(total_voice_used / 60, 1),
                "minutes_limit": round(total_voice_limit / 60, 1),
                "percent": round(voice_percent, 1),
                "is_warning": voice_percent >= 80,
                "is_exceeded": voice_percent >= 100,
            },
            "text": {
                "used": total_text_used,
                "limit": total_text_limit,
                "percent": round(text_percent, 1),
                "is_warning": text_percent >= 80,
                "is_exceeded": text_percent >= 100,
            },
            "embedding": {
                "used": total_embed_used,
                "limit": total_embed_limit,
                "percent": round(embed_percent, 1),
                "is_warning": embed_percent >= 80,
                "is_exceeded": embed_percent >= 100,
            },
        }
    except Exception as e:
        logger.warning(f"Failed to get usage data: {e}")
        usage_data = {
            "voice": {"used": 0, "limit": 6000, "minutes_used": 0, "minutes_limit": 100, "percent": 0, "is_warning": False, "is_exceeded": False},
            "text": {"used": 0, "limit": 1000, "percent": 0, "is_warning": False, "is_exceeded": False},
            "embedding": {"used": 0, "limit": 10000, "percent": 0, "is_warning": False, "is_exceeded": False},
        }

    return templates.TemplateResponse("admin/monitoring.html", {
        "request": request,
        "user": admin_user,
        "clients": clients_data,
        "selected_client_id": selected_client_id,
        "period": period,
        "usage_data": usage_data,
        "sidekicks": sidekicks,
    })

@router.get("/monitoring/metrics", response_class=HTMLResponse)
async def metrics_dashboard(
    request: Request,
    time_range: str = "1h",
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Metrics visualization dashboard"""
    redis = await get_redis()
    
    # Parse time range
    hours = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}.get(time_range, 1)
    
    # Aggregate metrics across all containers
    orchestrator = ContainerOrchestrator()
    containers = await orchestrator.list_containers()
    
    metrics_by_time = {}
    
    for container in containers:
        # Get metrics for time range
        for hour in range(hours):
            timestamp = datetime.now() - timedelta(hours=hour)
            key = f"metrics:{container['client_id']}:{int(timestamp.timestamp())}"
            data = await redis.get(key)
            
            if data:
                metrics = json.loads(data)
                time_key = timestamp.strftime("%Y-%m-%d %H:00")
                
                if time_key not in metrics_by_time:
                    metrics_by_time[time_key] = {
                        "cpu": 0,
                        "memory": 0,
                        "sessions": 0,
                        "count": 0
                    }
                
                metrics_by_time[time_key]["cpu"] += metrics.get("cpu_percent", 0)
                metrics_by_time[time_key]["memory"] += metrics.get("memory_mb", 0)
                metrics_by_time[time_key]["sessions"] += metrics.get("active_sessions", 0)
                metrics_by_time[time_key]["count"] += 1
    
    return templates.TemplateResponse("admin/metrics.html", {
        "request": request,
        "metrics_by_time": metrics_by_time,
        "time_range": time_range,
        "user": admin_user
    })

# Export router and utilities
__all__ = ["router", "get_redis"]


# ============================================================================
# Monitoring Dashboard Partials (HTMX)
# ============================================================================

@router.get("/partials/monitoring/usage-cards", response_class=HTMLResponse)
async def monitoring_usage_cards_partial(
    request: Request,
    client_id: Optional[str] = None,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Usage cards partial for HTMX auto-refresh"""
    from app.core.dependencies import get_client_service
    from app.services.usage_tracking import UsageTrackingService

    client_service = get_client_service()
    usage_service = UsageTrackingService()
    await usage_service.initialize()

    scoped_ids = get_scoped_client_ids(admin_user)
    clients_data = []
    try:
        all_clients = await client_service.get_all_clients()
        if scoped_ids is not None:
            allowed = {str(cid) for cid in scoped_ids}
            clients = [c for c in all_clients if str(getattr(c, 'id', '')) in allowed]
        else:
            clients = all_clients
        for c in clients:
            clients_data.append({
                "id": str(c.id) if hasattr(c, 'id') else str(c.get('id', '')),
            })
    except Exception as e:
        logger.warning(f"Failed to fetch clients: {e}")

    # Validate client_id
    selected_client_id = None
    if client_id and (scoped_ids is None or client_id in scoped_ids):
        selected_client_id = client_id

    # Get usage data
    target_clients = [selected_client_id] if selected_client_id else [c['id'] for c in clients_data]
    total_voice_used = 0
    total_voice_limit = 0
    total_text_used = 0
    total_text_limit = 0
    total_embed_used = 0
    total_embed_limit = 0

    for cid in target_clients[:10]:
        try:
            agg = await usage_service.get_client_aggregated_usage(cid)
            total_voice_used += agg.voice.used
            total_voice_limit += agg.voice.limit
            total_text_used += agg.text.used
            total_text_limit += agg.text.limit
            total_embed_used += agg.embedding.used
            total_embed_limit += agg.embedding.limit
        except Exception as e:
            logger.warning(f"Failed to get usage for client {cid}: {e}")

    voice_percent = (total_voice_used / total_voice_limit * 100) if total_voice_limit > 0 else 0
    text_percent = (total_text_used / total_text_limit * 100) if total_text_limit > 0 else 0
    embed_percent = (total_embed_used / total_embed_limit * 100) if total_embed_limit > 0 else 0

    usage_data = {
        "voice": {
            "used": total_voice_used,
            "limit": total_voice_limit,
            "minutes_used": round(total_voice_used / 60, 1),
            "minutes_limit": round(total_voice_limit / 60, 1),
            "percent": round(voice_percent, 1),
            "is_warning": voice_percent >= 80,
            "is_exceeded": voice_percent >= 100,
        },
        "text": {
            "used": total_text_used,
            "limit": total_text_limit,
            "percent": round(text_percent, 1),
            "is_warning": text_percent >= 80,
            "is_exceeded": text_percent >= 100,
        },
        "embedding": {
            "used": total_embed_used,
            "limit": total_embed_limit,
            "percent": round(embed_percent, 1),
            "is_warning": embed_percent >= 80,
            "is_exceeded": embed_percent >= 100,
        },
    }

    return templates.TemplateResponse("admin/partials/monitoring/usage_cards.html", {
        "request": request,
        "usage_data": usage_data,
    })


@router.get("/partials/monitoring/sidekick-grid", response_class=HTMLResponse)
async def monitoring_sidekick_grid_partial(
    request: Request,
    client_id: Optional[str] = None,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Sidekick grid partial for HTMX auto-refresh - with real-time status"""
    from app.integrations.supabase_client import supabase_manager
    from datetime import datetime, timezone, timedelta

    scoped_ids = get_scoped_client_ids(admin_user)
    sidekicks = []

    # Fetch active room data and processing jobs for status indicators
    active_agents = set()  # Agents currently in voice calls
    processing_agents = set()  # Agents processing documents
    recent_active_agents = set()  # Agents with activity in last 5 minutes

    try:
        # Get recent room events (last 5 minutes) to determine active/recent status
        five_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        room_events = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("livekit_events")
            .select("event_type, room_name, metadata, created_at")
            .gte("created_at", five_mins_ago)
            .order("created_at", desc=True)
        )

        if room_events and room_events.data:
            started_rooms = {}
            finished_rooms = set()

            for event in room_events.data:
                metadata = event.get("metadata", {})
                if isinstance(metadata, str):
                    try:
                        import json
                        metadata = json.loads(metadata)
                    except:
                        metadata = {}

                agent_id = metadata.get("agent_id")
                room_name = event.get("room_name", "")
                event_type = event.get("event_type")

                if agent_id:
                    recent_active_agents.add(str(agent_id))
                    if event_type == "room_started" and room_name:
                        started_rooms[room_name] = str(agent_id)
                    elif event_type == "room_finished" and room_name:
                        finished_rooms.add(room_name)

            for room_name, agent_id in started_rooms.items():
                if room_name not in finished_rooms:
                    active_agents.add(agent_id)

        # Check for agents with processing documents
        try:
            doc_jobs = await supabase_manager.execute_query(
                supabase_manager.admin_client.table("documentsense_learning_jobs")
                .select("agent_id, status")
                .in_("status", ["pending", "processing"])
            )
            if doc_jobs and doc_jobs.data:
                for job in doc_jobs.data:
                    if job.get("agent_id"):
                        processing_agents.add(str(job.get("agent_id")))
        except Exception:
            pass  # Table may not exist

    except Exception as e:
        logger.warning(f"Failed to fetch status data: {e}")

    # Use the same approach as agents_page - call get_all_agents() for superadmins
    try:
        if admin_is_super(admin_user):
            # Superadmin - get all agents
            all_agents = await get_all_agents()
            # Filter by client_id if specified
            if client_id:
                all_agents = [a for a in all_agents if str(a.get('client_id', '')) == client_id]
        else:
            # Non-superadmin - get agents from visible clients only
            from app.core.dependencies import get_agent_service, get_client_service
            agent_service = get_agent_service()
            client_service = get_client_service()

            visible_client_ids = list(scoped_ids) if scoped_ids else []
            all_agents = []
            client_name_cache = {}

            for cid in visible_client_ids:
                if client_id and str(cid) != client_id:
                    continue
                try:
                    client_agents = await agent_service.get_client_agents(str(cid))
                    if str(cid) not in client_name_cache:
                        try:
                            client = await client_service.get_client(str(cid))
                            client_name_cache[str(cid)] = client.name if hasattr(client, 'name') else client.get('name', 'Unknown')
                        except:
                            client_name_cache[str(cid)] = 'Unknown'

                    for a in client_agents:
                        a_dict = a.dict() if hasattr(a, 'dict') else (a if isinstance(a, dict) else {})
                        a_dict['client_name'] = client_name_cache[str(cid)]
                        a_dict['client_id'] = str(cid)
                        all_agents.append(a_dict)
                except Exception as e:
                    logger.warning(f"Failed to get agents for client {cid}: {e}")

        # Build sidekicks list with status
        for agent in all_agents[:20]:  # Limit to 20 for monitoring view
            agent_id = str(agent.get('id', ''))

            # Determine status
            if agent_id in active_agents:
                status = "in_conversation"
                status_label = "In Conversation"
                status_color = "brand-teal"
            elif agent_id in processing_agents:
                status = "processing"
                status_label = "Processing Docs"
                status_color = "brand-orange"
            elif agent_id in recent_active_agents:
                status = "recent"
                status_label = "Recently Active"
                status_color = "purple-400"
            elif agent.get('enabled', False):
                status = "idle"
                status_label = "Idle"
                status_color = "brand-teal"
            else:
                status = "inactive"
                status_label = "Inactive"
                status_color = "dark-text-secondary"

            sidekicks.append({
                "id": agent_id,
                "name": agent.get('name', 'Unknown'),
                "slug": agent.get('slug', ''),
                "client_id": str(agent.get('client_id', '')),
                "client_name": agent.get('client_name', 'Unknown'),
                "enabled": agent.get('enabled', False),
                "voice_chat_enabled": agent.get('voice_chat_enabled', True),
                "text_chat_enabled": agent.get('text_chat_enabled', True),
                "agent_image": agent.get('agent_image', ''),
                "status": status,
                "status_label": status_label,
                "status_color": status_color,
            })

    except Exception as e:
        logger.error(f"Failed to load agents for monitoring: {e}")

    return templates.TemplateResponse("admin/partials/monitoring/sidekick_grid.html", {
        "request": request,
        "sidekicks": sidekicks,
    })


@router.get("/partials/monitoring/health-panel", response_class=HTMLResponse)
async def monitoring_health_panel_partial(
    request: Request,
    client_id: Optional[str] = None,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """System health panel partial for HTMX auto-refresh"""
    return templates.TemplateResponse("admin/partials/monitoring/health_panel.html", {
        "request": request,
        "now": datetime.now().isoformat(),
    })


def format_time_ago(timestamp_str: str) -> str:
    """Format a timestamp as a relative time string (e.g., '2m ago', '1h ago')"""
    from datetime import datetime, timezone
    try:
        # Parse ISO format timestamp
        if timestamp_str.endswith('Z'):
            timestamp_str = timestamp_str[:-1] + '+00:00'
        dt = datetime.fromisoformat(timestamp_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - dt

        seconds = int(diff.total_seconds())
        if seconds < 60:
            return "Just now"
        elif seconds < 3600:
            minutes = seconds // 60
            return f"{minutes}m ago"
        elif seconds < 86400:
            hours = seconds // 3600
            return f"{hours}h ago"
        elif seconds < 604800:
            days = seconds // 86400
            return f"{days}d ago"
        else:
            return dt.strftime("%b %d")
    except Exception:
        return "Unknown"


@router.get("/partials/monitoring/activity-timeline", response_class=HTMLResponse)
async def monitoring_activity_timeline_partial(
    request: Request,
    client_id: Optional[str] = None,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Activity timeline partial for HTMX auto-refresh - shows events from livekit_events and activity_log"""
    from app.integrations.supabase_client import supabase_manager
    import json as json_lib

    scoped_ids = get_scoped_client_ids(admin_user)
    recent_events = []

    # Fetch LiveKit events
    try:
        result = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("livekit_events")
            .select("*")
            .order("created_at", desc=True)
            .limit(15)
        )

        if result and result.data:
            for event in result.data:
                metadata = event.get("metadata", {})
                if isinstance(metadata, str):
                    try:
                        metadata = json_lib.loads(metadata)
                    except:
                        metadata = {}

                # Filter by client access if scoped
                event_client_id = metadata.get("client_id")
                if scoped_ids is not None and event_client_id and str(event_client_id) not in scoped_ids:
                    continue
                if client_id and event_client_id and str(event_client_id) != client_id:
                    continue

                event_type = event.get("event_type", "unknown")
                room_name = event.get("room_name", "")
                duration = event.get("duration")
                created_at = event.get("created_at", "")
                agent_name = metadata.get("agent_name", "")

                # Format LiveKit events for display
                if event_type == "room_finished":
                    if duration and duration > 0:
                        minutes = round(duration / 60, 1)
                        title = f"Voice conversation ended"
                        subtitle = f"{agent_name or 'Sidekick'} • {minutes} min"
                        color = "brand-teal"
                        icon = "microphone"
                    else:
                        title = f"Room closed"
                        subtitle = agent_name or room_name or "Unknown"
                        color = "dark-text-secondary"
                        icon = "x-circle"
                elif event_type == "room_started":
                    title = f"Voice conversation started"
                    subtitle = agent_name or room_name or "New session"
                    color = "brand-teal"
                    icon = "microphone"
                elif event_type == "participant_joined":
                    identity = event.get("participant_identity", "")
                    title = f"User joined"
                    subtitle = identity.replace("user_", "") if identity else "Anonymous"
                    color = "brand-orange"
                    icon = "user-plus"
                elif event_type == "participant_left":
                    identity = event.get("participant_identity", "")
                    title = f"User left"
                    subtitle = identity.replace("user_", "") if identity else "Anonymous"
                    color = "dark-text-secondary"
                    icon = "user-minus"
                else:
                    title = event_type.replace("_", " ").title()
                    subtitle = agent_name or room_name or ""
                    color = "dark-text-secondary"
                    icon = "activity"

                recent_events.append({
                    "title": title,
                    "subtitle": subtitle,
                    "color": color,
                    "icon": icon,
                    "created_at": created_at,
                    "time_ago": format_time_ago(created_at) if created_at else "Just now",
                    "event_type": event_type,
                    "source": "livekit",
                })

    except Exception as e:
        logger.debug(f"livekit_events table may not exist yet: {e}")

    # Fetch activity log events
    try:
        activity_result = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("activity_log")
            .select("*")
            .order("created_at", desc=True)
            .limit(15)
        )

        if activity_result and activity_result.data:
            for activity in activity_result.data:
                # Filter by client access if scoped
                activity_client_id = str(activity.get("client_id", ""))
                if scoped_ids is not None and activity_client_id and activity_client_id not in scoped_ids:
                    continue
                if client_id and activity_client_id and activity_client_id != client_id:
                    continue

                activity_type = activity.get("activity_type", "")
                action = activity.get("action", "")
                resource_name = activity.get("resource_name", "")
                resource_type = activity.get("resource_type", "")
                created_at = activity.get("created_at", "")
                details = activity.get("details", {})

                # Format activity events for display
                if activity_type == "sidekick_created":
                    title = "Sidekick created"
                    subtitle = resource_name or "New sidekick"
                    color = "brand-teal"
                    icon = "robot"
                elif activity_type == "sidekick_updated":
                    title = "Sidekick updated"
                    subtitle = resource_name
                    color = "brand-orange"
                    icon = "edit"
                elif activity_type == "sidekick_deleted":
                    title = "Sidekick deleted"
                    subtitle = resource_name
                    color = "brand-salmon"
                    icon = "trash"
                elif activity_type == "ability_run":
                    title = "Ability started"
                    subtitle = resource_name
                    color = "purple-400"
                    icon = "zap"
                elif activity_type == "ability_completed":
                    title = "Ability completed"
                    subtitle = resource_name
                    color = "brand-teal"
                    icon = "zap"
                elif activity_type == "ability_failed":
                    title = "Ability failed"
                    subtitle = resource_name
                    color = "brand-salmon"
                    icon = "x-circle"
                elif activity_type == "document_uploaded":
                    title = "Document uploaded"
                    subtitle = resource_name
                    color = "brand-orange"
                    icon = "file-text"
                elif activity_type == "document_processed":
                    title = "Document processed"
                    subtitle = resource_name
                    color = "brand-teal"
                    icon = "file-text"
                elif activity_type == "conversation_started":
                    title = "Text conversation started"
                    subtitle = resource_name or "New chat"
                    color = "brand-orange"
                    icon = "message-circle"
                elif activity_type == "conversation_ended":
                    title = "Conversation ended"
                    subtitle = resource_name or "Chat ended"
                    color = "dark-text-secondary"
                    icon = "message-circle"
                elif activity_type == "voice_call_started":
                    title = "Voice call started"
                    subtitle = resource_name
                    color = "brand-teal"
                    icon = "microphone"
                elif activity_type == "voice_call_ended":
                    title = "Voice call ended"
                    subtitle = resource_name
                    color = "dark-text-secondary"
                    icon = "microphone"
                else:
                    title = activity_type.replace("_", " ").title()
                    subtitle = resource_name or resource_type or ""
                    color = "dark-text-secondary"
                    icon = "activity"

                recent_events.append({
                    "title": title,
                    "subtitle": subtitle,
                    "color": color,
                    "icon": icon,
                    "created_at": created_at,
                    "time_ago": format_time_ago(created_at) if created_at else "Just now",
                    "event_type": activity_type,
                    "source": "activity_log",
                })

    except Exception as e:
        logger.debug(f"activity_log table may not exist yet: {e}")

    # Sort all events by created_at descending and limit to 20
    recent_events.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    recent_events = recent_events[:20]

    return templates.TemplateResponse("admin/partials/monitoring/activity_timeline.html", {
        "request": request,
        "recent_events": recent_events,
    })


@router.get("/partials/monitoring/conversation-analytics", response_class=HTMLResponse)
async def monitoring_conversation_analytics_partial(
    request: Request,
    client_id: Optional[str] = None,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Conversation analytics partial for HTMX auto-refresh"""
    from app.integrations.supabase_client import supabase_manager
    from app.core.dependencies import get_client_service
    from app.services.usage_tracking import UsageTrackingService
    from datetime import datetime, timezone, timedelta

    scoped_ids = get_scoped_client_ids(admin_user)

    # Build target client list (same as usage cards)
    client_service = get_client_service()
    usage_service = UsageTrackingService()
    await usage_service.initialize()

    target_client_ids = []
    try:
        all_clients = await client_service.get_all_clients()
        if scoped_ids is not None:
            allowed = {str(cid) for cid in scoped_ids}
            clients = [c for c in all_clients if str(getattr(c, 'id', '')) in allowed]
        else:
            clients = all_clients
        for c in clients:
            target_client_ids.append(str(c.id) if hasattr(c, 'id') else str(c.get('id', '')))
    except Exception as e:
        logger.warning(f"Failed to fetch clients for analytics: {e}")

    # If specific client requested, filter to just that one
    if client_id and (scoped_ids is None or client_id in scoped_ids):
        target_client_ids = [client_id]

    # Default stats
    conversation_stats = {
        "total": 0,
        "active": 0,
        "avg_duration": "--",
        "avg_messages": "--",
    }
    channel_stats = {
        "voice_count": 0,
        "voice_percent": 0,
        "text_count": 0,
        "text_percent": 0,
    }

    try:
        # Get completed voice calls (room_finished events) from last 30 days
        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        result = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("livekit_events")
            .select("*")
            .eq("event_type", "room_finished")
            .gte("created_at", thirty_days_ago)
            .order("created_at", desc=True)
        )

        if result and result.data:
            voice_calls = []
            for event in result.data:
                metadata = event.get("metadata", {})
                if isinstance(metadata, str):
                    try:
                        import json
                        metadata = json.loads(metadata)
                    except:
                        metadata = {}

                # Filter by target client IDs
                event_client_id = metadata.get("client_id")
                if event_client_id and str(event_client_id) not in target_client_ids:
                    continue

                room_name = event.get("room_name", "")
                # Only count voice calls (non-text rooms)
                if not room_name.startswith("text-"):
                    duration = event.get("duration", 0) or 0
                    voice_calls.append(duration)

            # Calculate stats
            total_calls = len(voice_calls)
            conversation_stats["total"] = total_calls

            if total_calls > 0:
                total_duration = sum(voice_calls)
                avg_seconds = total_duration / total_calls
                if avg_seconds >= 60:
                    conversation_stats["avg_duration"] = f"{round(avg_seconds / 60, 1)}m"
                else:
                    conversation_stats["avg_duration"] = f"{int(avg_seconds)}s"

            # Channel stats (voice vs text - from room names)
            channel_stats["voice_count"] = total_calls

        # Count text conversations using the same approach as usage cards
        text_total = 0
        for cid in target_client_ids[:10]:  # Limit to prevent slow queries
            try:
                agg = await usage_service.get_client_aggregated_usage(cid)
                text_total += agg.text.used
            except Exception as e:
                logger.debug(f"Failed to get usage for client {cid}: {e}")

        channel_stats["text_count"] = text_total

        # Calculate percentages
        total_interactions = channel_stats["voice_count"] + channel_stats["text_count"]
        if total_interactions > 0:
            channel_stats["voice_percent"] = round((channel_stats["voice_count"] / total_interactions) * 100)
            channel_stats["text_percent"] = round((channel_stats["text_count"] / total_interactions) * 100)

    except Exception as e:
        logger.warning(f"Failed to fetch conversation analytics: {e}")

    return templates.TemplateResponse("admin/partials/monitoring/conversation_analytics.html", {
        "request": request,
        "conversation_stats": conversation_stats,
        "channel_stats": channel_stats,
    })


@router.post("/agents/{client_id}/{agent_slug}/upload-image")
async def upload_agent_image(
    client_id: str,
    agent_slug: str,
    file: UploadFile = File(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Persist an uploaded image to the static assets directory for an agent."""

    ensure_client_or_global_access(client_id, admin_user)

    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    content_type = (file.content_type or "").lower()
    suffix = ""

    if file.filename:
        original_suffix = Path(file.filename).suffix.lower()
        if original_suffix in ALLOWED_AGENT_IMAGE_EXTENSIONS:
            suffix = original_suffix

    if not suffix and content_type in ALLOWED_AGENT_IMAGE_TYPES:
        suffix = ALLOWED_AGENT_IMAGE_TYPES[content_type]

    if not suffix and content_type:
        guessed_suffix = mimetypes.guess_extension(content_type)
        if guessed_suffix in ALLOWED_AGENT_IMAGE_EXTENSIONS:
            suffix = guessed_suffix

    if not suffix:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image type. Please upload PNG, JPG, WEBP, GIF, or SVG files.",
        )

    try:
        contents = await file.read()
    except Exception as exc:
        logger.error("Failed to read uploaded agent image: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read uploaded file") from exc

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    if len(contents) > AGENT_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 5 MB limit")

    AGENT_IMAGE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    safe_slug = re.sub(r"[^a-z0-9_-]", "", agent_slug.lower()) or "agent"
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    unique = uuid.uuid4().hex[:8]
    filename = f"{safe_slug}_{timestamp}_{unique}{suffix}"
    destination = AGENT_IMAGE_STORAGE_DIR / filename

    try:
        with destination.open("wb") as buffer:
            buffer.write(contents)
    except Exception as exc:
        logger.error("Failed to persist agent image '%s': %s", filename, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save uploaded image") from exc

    public_url = f"/static/images/agents/{filename}"
    logger.info(
        "Agent image uploaded",
        extra={
            "client_id": client_id,
            "agent_slug": agent_slug,
            "stored_filename": filename,
            "size_bytes": len(contents),
        },
    )

    return {
        "success": True,
        "url": public_url,
        "filename": filename,
        "content_type": content_type or mimetypes.guess_type(filename)[0],
        "size": len(contents),
    }


# Avatar image storage directory
AVATAR_IMAGE_STORAGE_DIR = Path("/app/static/images/avatars")


@router.post("/api/upload-avatar")
async def upload_avatar_image(
    file: UploadFile = File(...),
    agent_id: str = Form(...),
    client_id: str = Form(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Upload an avatar image for video chat."""

    ensure_client_or_global_access(client_id, admin_user)

    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    content_type = (file.content_type or "").lower()
    suffix = ""

    if file.filename:
        original_suffix = Path(file.filename).suffix.lower()
        if original_suffix in ALLOWED_AGENT_IMAGE_EXTENSIONS:
            suffix = original_suffix

    if not suffix and content_type in ALLOWED_AGENT_IMAGE_TYPES:
        suffix = ALLOWED_AGENT_IMAGE_TYPES[content_type]

    if not suffix:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image type. Please upload PNG, JPG, or WebP files.",
        )

    try:
        contents = await file.read()
    except Exception as exc:
        logger.error("Failed to read uploaded avatar image: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read uploaded file") from exc

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    if len(contents) > AGENT_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 5 MB limit")

    AVATAR_IMAGE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    # Use agent_id for unique filename
    safe_agent_id = re.sub(r"[^a-z0-9_-]", "", agent_id.lower()) or "avatar"
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    unique = uuid.uuid4().hex[:8]
    filename = f"avatar_{safe_agent_id}_{timestamp}_{unique}{suffix}"
    destination = AVATAR_IMAGE_STORAGE_DIR / filename

    try:
        with destination.open("wb") as buffer:
            buffer.write(contents)
    except Exception as exc:
        logger.error("Failed to persist avatar image '%s': %s", filename, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save uploaded image") from exc

    public_url = f"/static/images/avatars/{filename}"
    logger.info(
        "Avatar image uploaded",
        extra={
            "client_id": client_id,
            "agent_id": agent_id,
            "stored_filename": filename,
            "size_bytes": len(contents),
        },
    )

    return {
        "success": True,
        "url": public_url,
        "filename": filename,
        "content_type": content_type or mimetypes.guess_type(filename)[0],
        "size": len(contents),
    }


@router.post("/agents/{client_id}/{agent_slug}/update")
async def admin_update_agent(
    client_id: str,
    agent_slug: str,
    request: Request
):
    """Admin endpoint to update agent using Supabase service"""
    # Enforce authenticated admin; temporarily relax role check to unblock saves
    from app.admin.auth import get_admin_user
    admin_user = await get_admin_user(request)
    ensure_client_or_global_access(client_id, admin_user)
    try:
        # Parse JSON body
        data = await request.json()
        
        # Get agent and client services
        from app.core.dependencies import get_agent_service, get_client_service
        agent_service = get_agent_service()
        client_service = get_client_service()
        
        # Get existing agent
        agent = await agent_service.get_agent(client_id, agent_slug)
        if not agent:
            return {"error": "Agent not found", "status": 404}
        
        # Get client to check API keys
        client = await client_service.get_client(client_id)
        if not client:
            return {"error": "Client not found", "status": 404}
        
        # Validate API keys if voice_settings are provided
        # Skip validation if client uses platform keys (Sidekick Forge Inference)
        uses_platform_keys = getattr(client, 'uses_platform_keys', None)
        if uses_platform_keys is None:
            # Check additional_settings as fallback
            uses_platform_keys = (client.additional_settings or {}).get('uses_platform_keys', False)

        if "voice_settings" in data and not uses_platform_keys:
            voice_settings = data["voice_settings"]
            missing_keys = []

            # Define provider to API key mappings
            llm_provider_keys = {
                "openai": "openai_api_key",
                "groq": "groq_api_key",
                "cerebras": "cerebras_api_key",
                "deepinfra": "deepinfra_api_key",
                "replicate": "replicate_api_key"
            }
            
            stt_provider_keys = {
                "deepgram": "deepgram_api_key",
                "groq": "groq_api_key",
                "openai": "openai_api_key",
                "cartesia": "cartesia_api_key"
            }
            
            tts_provider_keys = {
                "openai": "openai_api_key",
                "elevenlabs": "elevenlabs_api_key",
                "cartesia": "cartesia_api_key",
                "speechify": "speechify_api_key",
                "replicate": "replicate_api_key"
            }
            
            # Check LLM provider
            if "llm_provider" in voice_settings and voice_settings["llm_provider"]:
                llm_provider = voice_settings["llm_provider"]
                if llm_provider in llm_provider_keys:
                    required_key = llm_provider_keys[llm_provider]
                    if not hasattr(client.settings.api_keys, required_key) or not getattr(client.settings.api_keys, required_key):
                        missing_keys.append({
                            "provider_type": "LLM",
                            "provider": llm_provider,
                            "required_key": required_key,
                            "message": f"LLM provider '{llm_provider}' requires {required_key}"
                        })
            
            # Check STT provider
            if "stt_provider" in voice_settings and voice_settings["stt_provider"]:
                stt_provider = voice_settings["stt_provider"]
                if stt_provider in stt_provider_keys:
                    required_key = stt_provider_keys[stt_provider]
                    if not hasattr(client.settings.api_keys, required_key) or not getattr(client.settings.api_keys, required_key):
                        missing_keys.append({
                            "provider_type": "STT",
                            "provider": stt_provider,
                            "required_key": required_key,
                            "message": f"STT provider '{stt_provider}' requires {required_key}"
                        })
            
            # Check TTS provider
            if "tts_provider" in voice_settings and voice_settings["tts_provider"]:
                tts_provider = voice_settings["tts_provider"]
                if tts_provider in tts_provider_keys:
                    required_key = tts_provider_keys[tts_provider]
                    if not hasattr(client.settings.api_keys, required_key) or not getattr(client.settings.api_keys, required_key):
                        missing_keys.append({
                            "provider_type": "TTS",
                            "provider": tts_provider,
                            "required_key": required_key,
                            "message": f"TTS provider '{tts_provider}' requires {required_key}"
                        })
            
            # If missing keys found, return validation error
            if missing_keys:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "Missing API keys for selected providers",
                        "missing_keys": missing_keys,
                        "client_id": client_id,
                        "client_name": client.name
                    }
                )
        
        # Prepare update data
        from app.models.agent import AgentUpdate, VoiceSettings, WebhookSettings, SoundSettings
        from app.models.client import ChannelSettings, TelegramChannelSettings
        
        # Build update object
        update_data = AgentUpdate(
            name=data.get("name", agent.name),
            description=data.get("description", agent.description),
            agent_image=data.get("agent_image", agent.agent_image),
            system_prompt=data.get("system_prompt", agent.system_prompt),
            enabled=data.get("enabled", agent.enabled),
            tools_config=data.get("tools_config", agent.tools_config),
            show_citations=data.get("show_citations", getattr(agent, 'show_citations', True)),
            rag_results_limit=data.get("rag_results_limit", getattr(agent, "rag_results_limit", 5)),
            supertab_enabled=data.get("supertab_enabled", getattr(agent, 'supertab_enabled', False)),
            supertab_voice_enabled=data.get("supertab_voice_enabled", getattr(agent, 'supertab_voice_enabled', getattr(agent, 'supertab_enabled', False))),
            supertab_text_enabled=data.get("supertab_text_enabled", getattr(agent, 'supertab_text_enabled', False)),
            supertab_video_enabled=data.get("supertab_video_enabled", getattr(agent, 'supertab_video_enabled', False)),
            supertab_experience_id=data.get("supertab_experience_id", getattr(agent, 'supertab_experience_id', None)),
            supertab_subscription_experience_id=data.get("supertab_subscription_experience_id", getattr(agent, 'supertab_subscription_experience_id', None)),
            supertab_subscription_price=data.get("supertab_subscription_price", getattr(agent, 'supertab_subscription_price', None)),
            voice_chat_enabled=data.get("voice_chat_enabled", getattr(agent, 'voice_chat_enabled', True)),
            text_chat_enabled=data.get("text_chat_enabled", getattr(agent, 'text_chat_enabled', True)),
            video_chat_enabled=data.get("video_chat_enabled", getattr(agent, 'video_chat_enabled', False)),
        )
        
        # Handle voice settings if provided
        if "voice_settings" in data:
            logger.info(f"AVATAR DEBUG - voice_settings from form: avatar_image_url={data['voice_settings'].get('avatar_image_url')}, avatar_model_type={data['voice_settings'].get('avatar_model_type')}")
            logger.info(f"KENBURNS DEBUG - voice_settings from form: video_provider={data['voice_settings'].get('video_provider')}, kenburns_starting_image={data['voice_settings'].get('kenburns_starting_image')}")
            update_data.voice_settings = VoiceSettings(**data["voice_settings"])
            logger.info(f"AVATAR DEBUG - VoiceSettings object: avatar_image_url={update_data.voice_settings.avatar_image_url}, avatar_model_type={update_data.voice_settings.avatar_model_type}")
            logger.info(f"KENBURNS DEBUG - VoiceSettings object: video_provider={update_data.voice_settings.video_provider}, kenburns_starting_image={update_data.voice_settings.kenburns_starting_image}")

        # Handle sound settings if provided
        if "sound_settings" in data:
            logger.info(f"SOUND DEBUG - sound_settings from form: {data['sound_settings']}")
            update_data.sound_settings = SoundSettings(**data["sound_settings"])

        # Handle webhooks if provided
        if "webhooks" in data:
            update_data.webhooks = WebhookSettings(**data["webhooks"])

        # Handle channels (Telegram) and enforce token requirement for non-global sidekicks
        existing_tools = {}
        try:
            if hasattr(agent, "tools_config"):
                existing_tools = agent.tools_config or {}
            elif isinstance(agent, dict):
                existing_tools = agent.get("tools_config", {}) or {}
        except Exception:
            existing_tools = {}
        tools_config = data.get("tools_config") or existing_tools or {}
        channels_payload = data.get("channels") or {}
        telegram_payload = channels_payload.get("telegram") if isinstance(channels_payload, dict) else None

        if telegram_payload and isinstance(telegram_payload, dict):
            telegram_enabled = bool(telegram_payload.get("enabled"))
            telegram_token = telegram_payload.get("bot_token")

            if client_id != "global" and telegram_enabled and not telegram_token:
                return {
                    "error": "Telegram is enabled but no bot token was provided for this sidekick.",
                    "status": 400,
                }

            # Global sidekicks cannot override platform token
            if client_id == "global":
                telegram_token = None

            telegram_cfg = TelegramChannelSettings(
                enabled=telegram_enabled,
                bot_token=telegram_token,
                webhook_secret=telegram_payload.get("webhook_secret"),
                default_agent_slug=telegram_payload.get("default_agent_slug") or agent_slug,
                reply_mode=telegram_payload.get("reply_mode", "auto"),
                transcribe_voice=bool(telegram_payload.get("transcribe_voice", True)),
            )
            channels_obj = ChannelSettings(telegram=telegram_cfg)
            channels_dict = channels_obj.dict()
            if not isinstance(tools_config, dict):
                tools_config = {}
            tools_config["channels"] = channels_dict
            update_data.channels = channels_obj
            update_data.tools_config = tools_config
        elif tools_config:
            update_data.tools_config = tools_config

        personality_payload = data.get("personality_engine") or data.get("personality") or {}
        if isinstance(personality_payload, dict) and personality_payload:
            _upsert_agent_personality(agent.id, client, personality_payload)
        
        # Update agent
        updated_agent = await agent_service.update_agent(client_id, agent_slug, update_data)
        
        if updated_agent:
            return {"success": True, "message": "Agent updated successfully"}
        else:
            return {"error": "Failed to update agent", "status": 500}
        
    except Exception as e:
        logger.error(f"Error updating agent: {e}")
        return {"error": str(e), "status": 500}


def get_redis_client_admin():
    """Get Redis client for admin operations"""
    import redis
    return redis.Redis(host='localhost', port=6379, decode_responses=True)


@router.get("/user-settings", response_class=HTMLResponse)
async def user_settings(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """User settings page (profile + security + channel handles + billing)."""
    profile = None
    billing_info = None
    try:
        user_id = admin_user.get("id") or admin_user.get("user_id")
        user_email = (admin_user.get("email") or admin_user.get("username") or "").strip().lower()
        if user_id:
            profile = await supabase_manager.get_user_profile(user_id)
        if (not profile) and user_email:
            auth_user = await supabase_manager.find_auth_user_by_email(user_email)
            if auth_user:
                meta = getattr(auth_user, "user_metadata", None) or auth_user.get("user_metadata", {}) or {}
                profile = {
                    "user_id": getattr(auth_user, "id", None) or auth_user.get("id"),
                    "email": getattr(auth_user, "email", user_email) or auth_user.get("email", user_email),
                    "full_name": meta.get("full_name"),
                    "company": meta.get("company"),
                    "phone": meta.get("phone"),
                    "telegram_username": meta.get("telegram_username"),
                }
        # Fallback to cache if still missing
        if (not profile) and user_email and user_email in _profile_cache:
            profile = _profile_cache[user_email]

        # Get billing info from client record
        if user_id:
            try:
                client_result = supabase.table("clients").select(
                    "id, name, tier, stripe_customer_id, stripe_subscription_id, "
                    "subscription_status, subscription_current_period_end, "
                    "subscription_cancel_at_period_end"
                ).eq("owner_user_id", user_id).limit(1).execute()
                if client_result.data:
                    client = client_result.data[0]
                    db_status = client.get("subscription_status")
                    has_subscription_id = bool(client.get("stripe_subscription_id"))

                    # If there's a subscription ID but no status recorded, treat as active
                    # This handles 100% coupon/free subscriptions that may not have status set
                    if has_subscription_id and not db_status:
                        effective_status = "active"
                    else:
                        effective_status = db_status or "none"

                    billing_info = {
                        "client_id": client.get("id"),
                        "client_name": client.get("name"),
                        "tier": client.get("tier"),
                        "tier_name": {
                            "adventurer": "Adventurer",
                            "champion": "Champion",
                            "paragon": "Paragon"
                        }.get(client.get("tier"), client.get("tier", "").title()),
                        "tier_price": {
                            "adventurer": 49,
                            "champion": 199,
                            "paragon": 0
                        }.get(client.get("tier"), 0),
                        "stripe_customer_id": client.get("stripe_customer_id"),
                        "stripe_subscription_id": client.get("stripe_subscription_id"),
                        "subscription_status": effective_status,
                        "subscription_end": client.get("subscription_current_period_end"),
                        "cancel_at_period_end": client.get("subscription_cancel_at_period_end", False),
                    }
            except Exception as be:
                logger.warning(f"Failed to load billing info: {be}")
    except Exception as e:
        logger.warning(f"Failed to load user profile for settings: {e}")

    return templates.TemplateResponse(
        "admin/user_settings.html",
        {
            "request": request,
            "user": admin_user,
            "profile": profile or {},
            "billing": billing_info,
            "telegram_bot_username": os.getenv("TELEGRAM_BOT_USERNAME", ""),
        },
    )


@router.post("/user-settings", response_class=HTMLResponse)
async def update_user_settings(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Handle profile updates."""
    form = await request.form()
    user_id = admin_user.get("id") or admin_user.get("user_id")
    user_email = admin_user.get("email") or admin_user.get("username")

    updates = {
        "full_name": form.get("full_name") or None,
        "email": form.get("email") or user_email,
        "company": form.get("company") or None,
        "phone": form.get("phone") or None,
        "telegram_username": form.get("telegram_username") or None,
        "updated_at": datetime.utcnow().isoformat(),
    }

    # Ensure supabase_manager initialized
    try:
        if not supabase_manager._initialized:
            await supabase_manager.initialize()
    except Exception:
        pass

    try:
        if user_id:
            profile = await supabase_manager.update_user_profile(user_id, updates)
            if not profile:
                auth_user = await supabase_manager.get_auth_user(user_id)
                if auth_user:
                    meta = getattr(auth_user, "user_metadata", None) or auth_user.get("user_metadata", {}) or {}
                    profile = {
                        "user_id": user_id,
                        "email": getattr(auth_user, "email", user_email) or auth_user.get("email", user_email),
                        "full_name": updates.get("full_name") or meta.get("full_name"),
                        "company": updates.get("company") or meta.get("company"),
                        "phone": updates.get("phone") or meta.get("phone"),
                        "telegram_username": updates.get("telegram_username") or meta.get("telegram_username"),
                    }
        else:
            # Try to resolve auth user by email to get id
            auth_user = await supabase_manager.find_auth_user_by_email(user_email)
            if auth_user:
                found_id = getattr(auth_user, "id", None) or auth_user.get("id")
                if found_id:
                    profile = await supabase_manager.update_user_profile(found_id, updates)
                else:
                    profile = await supabase_manager.upsert_profile_by_email(user_email, updates)
            else:
                profile = await supabase_manager.upsert_profile_by_email(user_email, updates)
        if not profile:
            raise ValueError("Profile update returned empty result")
        # Cache fallback so it sticks even if backing store is absent
        if user_email:
            _profile_cache[user_email] = profile
    except Exception as e:
        logger.error(f"Failed to update user profile: {e}", exc_info=True)
        return templates.TemplateResponse(
            "admin/user_settings.html",
            {
                "request": request,
                "user": admin_user,
                "profile": updates,
                "error": f"Failed to update profile: {e}",
            },
        )

    return templates.TemplateResponse(
        "admin/user_settings.html",
        {
            "request": request,
            "user": admin_user,
            "profile": profile or updates,
            "pending_code": None,
            "success": "Profile updated",
        },
    )


@router.post("/user-settings/telegram/start", response_class=HTMLResponse)
async def start_telegram_verification(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Generate a one-time verification code for Telegram linking."""
    import random
    import string

    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    user_id = admin_user.get("id") or admin_user.get("user_id")
    user_email = (admin_user.get("email") or admin_user.get("username") or "").strip().lower()
    key = _pending_key(user_id, user_email)
    _pending_telegram_codes[key] = {
        "code": code,
        "user_id": user_id,
        "email": user_email,
        "created_at": datetime.utcnow().isoformat(),
    }

    profile = None
    try:
        if user_id:
            profile = await supabase_manager.get_user_profile(user_id)
        if not profile and user_email:
            auth_user = await supabase_manager.find_auth_user_by_email(user_email)
            if auth_user:
                meta = getattr(auth_user, "user_metadata", None) or auth_user.get("user_metadata", {}) or {}
                profile = {
                    "user_id": getattr(auth_user, "id", None) or auth_user.get("id"),
                    "email": getattr(auth_user, "email", user_email) or auth_user.get("email", user_email),
                    "full_name": meta.get("full_name"),
                    "company": meta.get("company"),
                    "phone": meta.get("phone"),
                    "telegram_username": meta.get("telegram_username"),
                }
    except Exception:
        profile = profile or {}

    return templates.TemplateResponse(
        "admin/user_settings.html",
        {
            "request": request,
            "user": admin_user,
            "profile": profile or {},
            "pending_code": code,
            "telegram_bot_username": os.getenv("TELEGRAM_VERIFICATION_BOT_USERNAME", os.getenv("TELEGRAM_BOT_USERNAME", "")),
            "success": f"Verification code generated: {code}. Open Telegram and tap the deep link to verify.",
        },
    )


@router.post("/user-settings/billing/portal")
async def billing_portal_redirect(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Create a Stripe Customer Portal session and redirect to it."""
    from app.services.stripe_service import stripe_service
    from fastapi.responses import RedirectResponse

    user_id = admin_user.get("id") or admin_user.get("user_id")

    # Get stripe_customer_id from client record
    try:
        client_result = supabase.table("clients").select(
            "stripe_customer_id"
        ).eq("owner_user_id", user_id).limit(1).execute()

        if not client_result.data or not client_result.data[0].get("stripe_customer_id"):
            # No Stripe customer - redirect back with error
            return RedirectResponse(
                url="/admin/user-settings?billing_error=no_subscription",
                status_code=303
            )

        customer_id = client_result.data[0]["stripe_customer_id"]

        # Create portal session
        domain = os.getenv("DOMAIN_NAME", "https://sidekickforge.com")
        if not domain.startswith("http"):
            domain = f"https://{domain}"

        portal_url = await stripe_service.create_customer_portal_session(
            customer_id=customer_id,
            return_url=f"{domain}/admin/user-settings"
        )

        return RedirectResponse(url=portal_url, status_code=303)

    except Exception as e:
        logger.error(f"Failed to create billing portal session: {e}")
        return RedirectResponse(
            url="/admin/user-settings?billing_error=portal_failed",
            status_code=303
        )


@router.get("/api/upgrade-preview/{target_tier}")
async def get_upgrade_preview(
    target_tier: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Get prorated upgrade pricing preview."""
    import stripe
    from datetime import datetime
    from app.services.stripe_service import stripe_service, TIER_CONFIG

    if target_tier not in ("champion", "paragon"):
        raise HTTPException(status_code=400, detail="Invalid tier")

    user_id = admin_user.get("id") or admin_user.get("user_id")

    try:
        from app.services.client_connection_manager import get_connection_manager
        conn_mgr = get_connection_manager()
        supabase = conn_mgr.platform_client

        # Get client and subscription info
        client_result = supabase.table("clients").select(
            "id, name, tier, stripe_customer_id, stripe_subscription_id, "
            "subscription_current_period_end"
        ).eq("owner_user_id", user_id).limit(1).execute()

        if not client_result.data:
            raise HTTPException(status_code=404, detail="No client found")

        client = client_result.data[0]
        current_tier = client.get("tier", "adventurer")
        subscription_id = client.get("stripe_subscription_id")
        period_end = client.get("subscription_current_period_end")

        if not subscription_id:
            # No subscription - show full price
            target_config = TIER_CONFIG.get(target_tier, {})
            return {
                "current_tier": current_tier,
                "target_tier": target_tier,
                "target_tier_name": target_config.get("name", target_tier.title()),
                "has_subscription": False,
                "amount_due_now": target_config.get("price_cents", 0) / 100,
                "new_monthly_price": target_config.get("price_cents", 0) / 100,
                "proration_date": None,
                "period_end": None,
            }

        # Get proration preview from Stripe
        stripe_service._ensure_initialized()
        new_price_id = stripe_service._get_or_create_price(target_tier)

        subscription = stripe.Subscription.retrieve(subscription_id)
        subscription_item_id = subscription["items"]["data"][0]["id"]

        # Create an invoice preview to see proration
        upcoming_invoice = stripe.Invoice.upcoming(
            customer=client.get("stripe_customer_id"),
            subscription=subscription_id,
            subscription_items=[{
                "id": subscription_item_id,
                "price": new_price_id,
            }],
            subscription_proration_behavior="create_prorations",
        )

        # Calculate amounts
        proration_amount = 0
        for line in upcoming_invoice.lines.data:
            if line.proration:
                proration_amount += line.amount

        target_config = TIER_CONFIG.get(target_tier, {})

        return {
            "current_tier": current_tier,
            "target_tier": target_tier,
            "target_tier_name": target_config.get("name", target_tier.title()),
            "has_subscription": True,
            "amount_due_now": max(0, proration_amount) / 100,  # In dollars
            "new_monthly_price": target_config.get("price_cents", 0) / 100,
            "proration_date": datetime.now().strftime("%B %d, %Y"),
            "period_end": period_end[:10] if period_end else None,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error getting upgrade preview: {e}")
        raise HTTPException(status_code=500, detail="Failed to calculate pricing")
    except Exception as e:
        logger.error(f"Error getting upgrade preview: {e}")
        raise HTTPException(status_code=500, detail="Failed to get pricing info")


@router.post("/api/upgrade")
async def api_upgrade_subscription(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Handle subscription upgrade via API (returns JSON)."""
    import stripe
    from app.services.stripe_service import stripe_service

    data = await request.json()
    target_tier = data.get("target_tier", "").strip()

    if target_tier not in ("champion", "paragon"):
        raise HTTPException(status_code=400, detail="Invalid tier")

    user_id = admin_user.get("id") or admin_user.get("user_id")

    try:
        from app.services.client_connection_manager import get_connection_manager
        conn_mgr = get_connection_manager()
        supabase = conn_mgr.platform_client

        # Get client and subscription info
        client_result = supabase.table("clients").select(
            "id, name, tier, stripe_customer_id, stripe_subscription_id"
        ).eq("owner_user_id", user_id).limit(1).execute()

        if not client_result.data:
            raise HTTPException(status_code=404, detail="No client found")

        client = client_result.data[0]
        subscription_id = client.get("stripe_subscription_id")
        customer_id = client.get("stripe_customer_id")

        if not subscription_id or not customer_id:
            raise HTTPException(status_code=400, detail="No active subscription")

        # Get the new price ID for the target tier
        stripe_service._ensure_initialized()
        new_price_id = stripe_service._get_or_create_price(target_tier)

        # Retrieve current subscription to get item ID
        subscription = stripe.Subscription.retrieve(subscription_id)
        subscription_item_id = subscription["items"]["data"][0]["id"]

        # Update subscription with prorated billing
        updated_subscription = stripe.Subscription.modify(
            subscription_id,
            items=[{
                "id": subscription_item_id,
                "price": new_price_id,
            }],
            proration_behavior="create_prorations",
        )

        # Update client tier in database
        supabase.table("clients").update({
            "tier": target_tier,
        }).eq("id", client["id"]).execute()

        logger.info(f"Upgraded client {client['id']} from {client.get('tier')} to {target_tier}")

        return {
            "success": True,
            "new_tier": target_tier,
            "message": f"Successfully upgraded to {target_tier.title()}!"
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error during upgrade: {e}")
        raise HTTPException(status_code=500, detail=f"Payment error: {str(e)}")
    except Exception as e:
        logger.error(f"Failed to upgrade subscription: {e}")
        raise HTTPException(status_code=500, detail="Upgrade failed")


@router.post("/user-settings/upgrade")
async def upgrade_subscription(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Handle subscription upgrade with prorated billing (form POST)."""
    import stripe
    from app.services.stripe_service import stripe_service, TIER_CONFIG

    form = await request.form()
    target_tier = form.get("target_tier", "").strip()

    if target_tier not in ("champion", "paragon"):
        return RedirectResponse(
            url="/admin/user-settings?billing_error=invalid_tier",
            status_code=303
        )

    user_id = admin_user.get("id") or admin_user.get("user_id")

    try:
        from app.services.client_connection_manager import get_connection_manager
        conn_mgr = get_connection_manager()
        supabase = conn_mgr.platform_client

        # Get client and subscription info
        client_result = supabase.table("clients").select(
            "id, name, tier, stripe_customer_id, stripe_subscription_id"
        ).eq("owner_user_id", user_id).limit(1).execute()

        if not client_result.data:
            return RedirectResponse(
                url="/admin/user-settings?billing_error=no_client",
                status_code=303
            )

        client = client_result.data[0]
        subscription_id = client.get("stripe_subscription_id")
        customer_id = client.get("stripe_customer_id")

        if not subscription_id or not customer_id:
            return RedirectResponse(
                url="/admin/user-settings?billing_error=no_subscription",
                status_code=303
            )

        # Get the new price ID for the target tier
        stripe_service._ensure_initialized()
        new_price_id = stripe_service._get_or_create_price(target_tier)

        # Retrieve current subscription to get item ID
        subscription = stripe.Subscription.retrieve(subscription_id)
        subscription_item_id = subscription["items"]["data"][0]["id"]

        # Update subscription with prorated billing
        updated_subscription = stripe.Subscription.modify(
            subscription_id,
            items=[{
                "id": subscription_item_id,
                "price": new_price_id,
            }],
            proration_behavior="create_prorations",  # Prorated billing
        )

        # Update client tier in database
        supabase.table("clients").update({
            "tier": target_tier,
        }).eq("id", client["id"]).execute()

        logger.info(f"Upgraded client {client['id']} from {client.get('tier')} to {target_tier}")

        return RedirectResponse(
            url="/admin/user-settings?upgrade_success=true",
            status_code=303
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error during upgrade: {e}")
        return RedirectResponse(
            url="/admin/user-settings?billing_error=stripe_error",
            status_code=303
        )
    except Exception as e:
        logger.error(f"Failed to upgrade subscription: {e}")
        return RedirectResponse(
            url="/admin/user-settings?billing_error=upgrade_failed",
            status_code=303
        )


@router.post("/user-settings/downgrade")
async def downgrade_subscription(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Handle subscription downgrade (effective at end of billing period)."""
    import stripe
    from app.services.stripe_service import stripe_service

    form = await request.form()
    target_tier = form.get("target_tier", "").strip()

    if target_tier not in ("adventurer",):
        return RedirectResponse(
            url="/admin/user-settings?billing_error=invalid_tier",
            status_code=303
        )

    user_id = admin_user.get("id") or admin_user.get("user_id")

    try:
        from app.services.client_connection_manager import get_connection_manager
        conn_mgr = get_connection_manager()
        supabase = conn_mgr.platform_client

        # Get client and subscription info
        client_result = supabase.table("clients").select(
            "id, name, tier, stripe_customer_id, stripe_subscription_id"
        ).eq("owner_user_id", user_id).limit(1).execute()

        if not client_result.data:
            return RedirectResponse(
                url="/admin/user-settings?billing_error=no_client",
                status_code=303
            )

        client = client_result.data[0]
        subscription_id = client.get("stripe_subscription_id")

        if not subscription_id:
            return RedirectResponse(
                url="/admin/user-settings?billing_error=no_subscription",
                status_code=303
            )

        # Get the new price ID for the target tier
        stripe_service._ensure_initialized()
        new_price_id = stripe_service._get_or_create_price(target_tier)

        # Retrieve current subscription to get item ID
        subscription = stripe.Subscription.retrieve(subscription_id)
        subscription_item_id = subscription["items"]["data"][0]["id"]

        # Schedule downgrade at end of billing period (no proration)
        stripe.Subscription.modify(
            subscription_id,
            items=[{
                "id": subscription_item_id,
                "price": new_price_id,
            }],
            proration_behavior="none",  # No proration - takes effect at renewal
        )

        # Mark the pending downgrade in database
        supabase.table("clients").update({
            "pending_tier_change": target_tier,
        }).eq("id", client["id"]).execute()

        logger.info(f"Scheduled downgrade for client {client['id']} to {target_tier}")

        return RedirectResponse(
            url="/admin/user-settings?downgrade_scheduled=true",
            status_code=303
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error during downgrade: {e}")
        return RedirectResponse(
            url="/admin/user-settings?billing_error=stripe_error",
            status_code=303
        )
    except Exception as e:
        logger.error(f"Failed to downgrade subscription: {e}")
        return RedirectResponse(
            url="/admin/user-settings?billing_error=downgrade_failed",
            status_code=303
        )


@router.post("/user-settings/cancel-subscription")
async def cancel_subscription(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Cancel subscription at end of billing period."""
    from app.services.stripe_service import stripe_service

    user_id = admin_user.get("id") or admin_user.get("user_id")

    try:
        from app.services.client_connection_manager import get_connection_manager
        conn_mgr = get_connection_manager()
        supabase = conn_mgr.platform_client

        # Get client subscription ID
        client_result = supabase.table("clients").select(
            "id, stripe_subscription_id"
        ).eq("owner_user_id", user_id).limit(1).execute()

        if not client_result.data or not client_result.data[0].get("stripe_subscription_id"):
            return RedirectResponse(
                url="/admin/user-settings?billing_error=no_subscription",
                status_code=303
            )

        subscription_id = client_result.data[0]["stripe_subscription_id"]

        # Cancel at period end
        result = await stripe_service.cancel_subscription(
            subscription_id=subscription_id,
            cancel_immediately=False  # Cancel at end of billing period
        )

        # Update client record
        supabase.table("clients").update({
            "subscription_cancel_at_period_end": True,
        }).eq("id", client_result.data[0]["id"]).execute()

        logger.info(f"Subscription {subscription_id} set to cancel at period end")

        return RedirectResponse(
            url="/admin/user-settings?cancel_scheduled=true",
            status_code=303
        )

    except Exception as e:
        logger.error(f"Failed to cancel subscription: {e}")
        return RedirectResponse(
            url="/admin/user-settings?billing_error=cancel_failed",
            status_code=303
        )


@router.post("/user-settings/reactivate-subscription")
async def reactivate_subscription(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Reactivate a subscription that was set to cancel."""
    from app.services.stripe_service import stripe_service

    user_id = admin_user.get("id") or admin_user.get("user_id")

    try:
        from app.services.client_connection_manager import get_connection_manager
        conn_mgr = get_connection_manager()
        supabase = conn_mgr.platform_client

        # Get client subscription ID
        client_result = supabase.table("clients").select(
            "id, stripe_subscription_id"
        ).eq("owner_user_id", user_id).limit(1).execute()

        if not client_result.data or not client_result.data[0].get("stripe_subscription_id"):
            return RedirectResponse(
                url="/admin/user-settings?billing_error=no_subscription",
                status_code=303
            )

        subscription_id = client_result.data[0]["stripe_subscription_id"]

        # Reactivate subscription
        result = await stripe_service.reactivate_subscription(subscription_id)

        # Update client record
        supabase.table("clients").update({
            "subscription_cancel_at_period_end": False,
        }).eq("id", client_result.data[0]["id"]).execute()

        logger.info(f"Subscription {subscription_id} reactivated")

        return RedirectResponse(
            url="/admin/user-settings?reactivated=true",
            status_code=303
        )

    except Exception as e:
        logger.error(f"Failed to reactivate subscription: {e}")
        return RedirectResponse(
            url="/admin/user-settings?billing_error=reactivate_failed",
            status_code=303
        )


@router.post("/user-settings/change-password")
async def change_password(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Change the user's password."""
    from fastapi.responses import RedirectResponse

    form = await request.form()
    current_password = form.get("current_password", "").strip()
    new_password = form.get("new_password", "").strip()
    confirm_password = form.get("confirm_password", "").strip()

    user_id = admin_user.get("id") or admin_user.get("user_id")
    user_email = admin_user.get("email") or admin_user.get("username")

    # Validation
    if not current_password:
        return RedirectResponse(
            url="/admin/user-settings?password_error=current_required",
            status_code=303
        )

    if not new_password:
        return RedirectResponse(
            url="/admin/user-settings?password_error=new_required",
            status_code=303
        )

    if len(new_password) < 8:
        return RedirectResponse(
            url="/admin/user-settings?password_error=too_short",
            status_code=303
        )

    if new_password != confirm_password:
        return RedirectResponse(
            url="/admin/user-settings?password_error=mismatch",
            status_code=303
        )

    try:
        # Verify current password by attempting to sign in
        try:
            verify_response = supabase.auth.sign_in_with_password({
                "email": user_email,
                "password": current_password
            })
            if not verify_response.user:
                return RedirectResponse(
                    url="/admin/user-settings?password_error=wrong_current",
                    status_code=303
                )
        except Exception as auth_err:
            logger.warning(f"Password verification failed for {user_email}: {auth_err}")
            return RedirectResponse(
                url="/admin/user-settings?password_error=wrong_current",
                status_code=303
            )

        # Update password using admin API
        supabase.auth.admin.update_user_by_id(
            user_id,
            {"password": new_password}
        )

        logger.info(f"Password changed successfully for user {user_email}")
        return RedirectResponse(
            url="/admin/user-settings?password_success=true",
            status_code=303
        )

    except Exception as e:
        logger.error(f"Failed to change password for {user_email}: {e}")
        return RedirectResponse(
            url="/admin/user-settings?password_error=failed",
            status_code=303
        )


@router.get("/clients/{client_id}/edit", response_class=HTMLResponse)
async def edit_client_modal(client_id: str, request: Request, admin_user: Dict[str, Any] = Depends(get_admin_user)):
    from app.core.dependencies import get_client_service
    client_service = get_client_service()
    ensure_client_access(client_id, admin_user)
    client = await client_service.get_client(client_id, auto_sync=False)
    if not client:
        return HTMLResponse("<div class='p-4 text-red-600'>Client not found</div>", status_code=404)

    # Normalize current_api_keys for template access
    current_api_keys = client.settings.api_keys if hasattr(client.settings, 'api_keys') else {}
    def get_key(name):
        try:
            return getattr(current_api_keys, name)
        except Exception:
            return current_api_keys.get(name, '') if isinstance(current_api_keys, dict) else ''

    html = f"""
    <div class=\"fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center\">
      <div class=\"bg-white rounded-lg shadow-lg w-full max-w-2xl\">
        <div class=\"px-6 py-4 border-b\">
          <h3 class=\"text-lg font-semibold\">Edit Client: {client.name}</h3>
        </div>
        <form hx-post=\"/admin/clients/{client_id}/update\" hx-target=\"#modal-container\" hx-swap=\"outerHTML\">
          <div class=\"px-6 py-4 space-y-4\">
            <div>
              <label class=\"block text-sm font-medium text-gray-700 mb-1\">Cerebras API Key</label>
              <input type=\"password\" name=\"cerebras_api_key\" value=\"{get_key('cerebras_api_key') or ''}\" placeholder=\"sk-...\" class=\"w-full px-3 py-2 border rounded-md\">
              <p class=\"text-xs text-gray-500 mt-1\">Used when LLM provider is set to Cerebras.</p>
            </div>
            <!-- Optionally render other keys here as needed -->
          </div>
          <div class=\"px-6 py-4 border-t flex justify-end gap-2\">
            <button type=\"button\" class=\"px-4 py-2 rounded border\" onclick=\"document.getElementById('modal-container').innerHTML='';\">Cancel</button>
            <button type=\"submit\" class=\"px-4 py-2 rounded bg-indigo-600 text-white\">Save</button>
          </div>
        </form>
      </div>
    </div>
    """
    return HTMLResponse(html)

@router.post("/clients/{client_id}/update")
async def admin_update_client(
    client_id: str,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Admin endpoint to update client using Supabase service"""
    try:
        # Parse form data
        form = await request.form()
        
        # Debug: Log form data for API keys
        logger.info(f"Form data received - cartesia_api_key: {form.get('cartesia_api_key')}")
        logger.info(f"Form data received - siliconflow_api_key: {form.get('siliconflow_api_key')}")
        
        # Get client service
        from app.core.dependencies import get_client_service
        client_service = get_client_service()

        ensure_client_access(client_id, admin_user)

        # Get existing client (disable auto-sync to prevent overriding manual changes)
        client = await client_service.get_client(client_id, auto_sync=False)
        if not client:
            return RedirectResponse(
                url="/admin/clients?error=Client+not+found",
                status_code=303
            )
        
        # Prepare update data
        from app.models.client import (
            ClientUpdate,
            ClientSettings,
            SupabaseConfig,
            LiveKitConfig,
            APIKeys,
            EmbeddingSettings,
            RerankSettings,
            ChannelSettings,
            TelegramChannelSettings,
        )
        
        # Get current settings - handle both dict and object formats
        if hasattr(client, 'settings'):
            current_settings = client.settings
            current_supabase = current_settings.supabase if hasattr(current_settings, 'supabase') else None
            current_livekit = current_settings.livekit if hasattr(current_settings, 'livekit') else None
            current_api_keys = current_settings.api_keys if hasattr(current_settings, 'api_keys') else None
            current_embedding = current_settings.embedding if hasattr(current_settings, 'embedding') else None
            current_rerank = current_settings.rerank if hasattr(current_settings, 'rerank') else None
            current_perf_monitoring = current_settings.performance_monitoring if hasattr(current_settings, 'performance_monitoring') else False
            current_license_key = current_settings.license_key if hasattr(current_settings, 'license_key') else None
            current_channels = current_settings.channels if hasattr(current_settings, 'channels') else None
        else:
            # Client is a dict
            current_settings = client.get('settings', {})
            current_supabase = current_settings.get('supabase', {})
            current_livekit = current_settings.get('livekit', {})
            current_api_keys = current_settings.get('api_keys', {})
            current_embedding = current_settings.get('embedding', {})
            current_rerank = current_settings.get('rerank', {})
            current_perf_monitoring = current_settings.get('performance_monitoring', False)
            current_license_key = current_settings.get('license_key')
            current_channels = current_settings.get('channels', {})

        if not current_channels and getattr(client, "additional_settings", None):
            try:
                current_channels = client.additional_settings.get("channels", {})
            except Exception:
                current_channels = {}

        def _get_current_telegram(field: str, default=None):
            """Helper to fetch current telegram channel field from either object or dict."""
            try:
                if hasattr(current_channels, "telegram") and hasattr(current_channels.telegram, field):
                    return getattr(current_channels.telegram, field)
            except Exception:
                pass
            try:
                if isinstance(current_channels, dict):
                    return current_channels.get("telegram", {}).get(field, default)
            except Exception:
                pass
            return default
        
        # Build settings update with proper defaults
        settings_update = ClientSettings(
            supabase=SupabaseConfig(
                url=form.get("supabase_url", current_supabase.url if hasattr(current_supabase, 'url') else current_supabase.get('url', '')),
                anon_key=form.get("supabase_anon_key", current_supabase.anon_key if hasattr(current_supabase, 'anon_key') else current_supabase.get('anon_key', '')),
                service_role_key=form.get("supabase_service_key", current_supabase.service_role_key if hasattr(current_supabase, 'service_role_key') else current_supabase.get('service_role_key', ''))
            ),
            livekit=LiveKitConfig(
                server_url=form.get("livekit_server_url", current_livekit.server_url if hasattr(current_livekit, 'server_url') else current_livekit.get('server_url', '')),
                api_key=form.get("livekit_api_key", current_livekit.api_key if hasattr(current_livekit, 'api_key') else current_livekit.get('api_key', '')),
                api_secret=form.get("livekit_api_secret", current_livekit.api_secret if hasattr(current_livekit, 'api_secret') else current_livekit.get('api_secret', ''))
            ),
            api_keys=APIKeys(
                openai_api_key=form.get("openai_api_key") or (current_api_keys.openai_api_key if hasattr(current_api_keys, 'openai_api_key') else current_api_keys.get('openai_api_key') if isinstance(current_api_keys, dict) else None),
                groq_api_key=form.get("groq_api_key") or (current_api_keys.groq_api_key if hasattr(current_api_keys, 'groq_api_key') else current_api_keys.get('groq_api_key') if isinstance(current_api_keys, dict) else None),
                cerebras_api_key=form.get("cerebras_api_key") or (current_api_keys.cerebras_api_key if hasattr(current_api_keys, 'cerebras_api_key') else current_api_keys.get('cerebras_api_key') if isinstance(current_api_keys, dict) else None),
                deepinfra_api_key=form.get("deepinfra_api_key") or (current_api_keys.deepinfra_api_key if hasattr(current_api_keys, 'deepinfra_api_key') else current_api_keys.get('deepinfra_api_key') if isinstance(current_api_keys, dict) else None),
                replicate_api_key=form.get("replicate_api_key") or (current_api_keys.replicate_api_key if hasattr(current_api_keys, 'replicate_api_key') else current_api_keys.get('replicate_api_key') if isinstance(current_api_keys, dict) else None),
                perplexity_api_key=form.get("perplexity_api_key") or (current_api_keys.perplexity_api_key if hasattr(current_api_keys, 'perplexity_api_key') else current_api_keys.get('perplexity_api_key') if isinstance(current_api_keys, dict) else None),
                deepgram_api_key=form.get("deepgram_api_key") or (current_api_keys.deepgram_api_key if hasattr(current_api_keys, 'deepgram_api_key') else current_api_keys.get('deepgram_api_key') if isinstance(current_api_keys, dict) else None),
                elevenlabs_api_key=form.get("elevenlabs_api_key") or (current_api_keys.elevenlabs_api_key if hasattr(current_api_keys, 'elevenlabs_api_key') else current_api_keys.get('elevenlabs_api_key') if isinstance(current_api_keys, dict) else None),
                cartesia_api_key=form.get("cartesia_api_key") or (current_api_keys.cartesia_api_key if hasattr(current_api_keys, 'cartesia_api_key') else current_api_keys.get('cartesia_api_key') if isinstance(current_api_keys, dict) else None),
                speechify_api_key=form.get("speechify_api_key") or (current_api_keys.speechify_api_key if hasattr(current_api_keys, 'speechify_api_key') else current_api_keys.get('speechify_api_key') if isinstance(current_api_keys, dict) else None),
                novita_api_key=form.get("novita_api_key") or (current_api_keys.novita_api_key if hasattr(current_api_keys, 'novita_api_key') else current_api_keys.get('novita_api_key') if isinstance(current_api_keys, dict) else None),
                cohere_api_key=form.get("cohere_api_key") or (current_api_keys.cohere_api_key if hasattr(current_api_keys, 'cohere_api_key') else current_api_keys.get('cohere_api_key') if isinstance(current_api_keys, dict) else None),
                siliconflow_api_key=form.get("siliconflow_api_key") or (current_api_keys.siliconflow_api_key if hasattr(current_api_keys, 'siliconflow_api_key') else current_api_keys.get('siliconflow_api_key') if isinstance(current_api_keys, dict) else None),
                jina_api_key=form.get("jina_api_key") or (current_api_keys.jina_api_key if hasattr(current_api_keys, 'jina_api_key') else current_api_keys.get('jina_api_key') if isinstance(current_api_keys, dict) else None),
                bithuman_api_secret=form.get("bithuman_api_secret") or (current_api_keys.bithuman_api_secret if hasattr(current_api_keys, 'bithuman_api_secret') else current_api_keys.get('bithuman_api_secret') if isinstance(current_api_keys, dict) else None),
                bey_api_key=form.get("bey_api_key") or (current_api_keys.bey_api_key if hasattr(current_api_keys, 'bey_api_key') else current_api_keys.get('bey_api_key') if isinstance(current_api_keys, dict) else None),
                liveavatar_api_key=form.get("liveavatar_api_key") or (current_api_keys.liveavatar_api_key if hasattr(current_api_keys, 'liveavatar_api_key') else current_api_keys.get('liveavatar_api_key') if isinstance(current_api_keys, dict) else None),
                assemblyai_api_key=form.get("assemblyai_api_key") or (current_api_keys.assemblyai_api_key if hasattr(current_api_keys, 'assemblyai_api_key') else current_api_keys.get('assemblyai_api_key') if isinstance(current_api_keys, dict) else None)
            ),
            embedding=EmbeddingSettings(
                provider=form.get("embedding_provider", current_embedding.provider if hasattr(current_embedding, 'provider') else current_embedding.get('provider', 'openai') if current_embedding else 'openai'),
                document_model=form.get("document_embedding_model", current_embedding.document_model if hasattr(current_embedding, 'document_model') else current_embedding.get('document_model', 'text-embedding-3-small') if current_embedding else 'text-embedding-3-small'),
                conversation_model=form.get("conversation_embedding_model", current_embedding.conversation_model if hasattr(current_embedding, 'conversation_model') else current_embedding.get('conversation_model', 'text-embedding-3-small') if current_embedding else 'text-embedding-3-small'),
                dimension=int(form.get("embedding_dimension")) if form.get("embedding_dimension") and form.get("embedding_dimension").strip() else (current_embedding.dimension if hasattr(current_embedding, 'dimension') else current_embedding.get('dimension') if current_embedding else None)
            ),
            rerank=RerankSettings(
                enabled=form.get("rerank_enabled", "off") == "on",
                provider=form.get("rerank_provider", current_rerank.provider if hasattr(current_rerank, 'provider') else current_rerank.get('provider') if current_rerank else None),
                model=form.get("rerank_model", current_rerank.model if hasattr(current_rerank, 'model') else current_rerank.get('model') if current_rerank else None),
                top_k=int(form.get("rerank_top_k", current_rerank.top_k if hasattr(current_rerank, 'top_k') else current_rerank.get('top_k', 5) if current_rerank else 5)),
                candidates=int(form.get("rerank_candidates", current_rerank.candidates if hasattr(current_rerank, 'candidates') else current_rerank.get('candidates', 20) if current_rerank else 20))
            ),
            channels=ChannelSettings(
                telegram=TelegramChannelSettings(
                    enabled=str(form.get("telegram_enabled", _get_current_telegram("enabled", False))).lower() in {"on", "true", "1"},
                    bot_token=_get_current_telegram("bot_token"),
                    webhook_secret=_get_current_telegram("webhook_secret"),
                    default_agent_slug=_get_current_telegram("default_agent_slug"),
                    reply_mode=_get_current_telegram("reply_mode", "auto"),
                    transcribe_voice=str(form.get("telegram_transcribe_voice", _get_current_telegram("transcribe_voice", True))).lower() in {"on", "true", "1"},
                )
            ),
            performance_monitoring=current_perf_monitoring,
            license_key=current_license_key
        )
        
        # Create update object - handle both dict and object formats
        # Handle checkbox: if multiple values (hidden + checked), take the last one
        active_values = form.getlist("active") if hasattr(form, 'getlist') else ([form.get("active")] if form.get("active") else [])
        active_value = active_values[-1] if active_values else "true"

        # Handle UserSense enabled checkbox
        usersense_enabled_values = form.getlist("usersense_enabled") if hasattr(form, 'getlist') else ([form.get("usersense_enabled")] if form.get("usersense_enabled") else [])
        usersense_enabled_value = usersense_enabled_values[-1] if usersense_enabled_values else None
        usersense_enabled = usersense_enabled_value == "on" if usersense_enabled_value else False

        # Check if UserSense was previously disabled (to trigger initial learning if newly enabled)
        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)
        current_usersense_result = platform_sb.table("clients").select("usersense_enabled").eq("id", client_id).execute()
        was_usersense_enabled = current_usersense_result.data[0].get("usersense_enabled", False) if current_usersense_result.data else False

        # Handle Supertab Client ID (empty string becomes None)
        supertab_client_id = form.get("supertab_client_id", "").strip() or None

        # Handle Firecrawl API key (empty string becomes None)
        firecrawl_api_key = form.get("firecrawl_api_key", "").strip() or None

        # Handle uses_platform_keys checkbox (BYOK setting)
        # The checkbox is "Bring Your Own Keys" - when checked, uses_platform_keys should be False
        # Hidden input sends "true", checkbox sends "false" when checked
        uses_platform_keys_values = form.getlist("uses_platform_keys") if hasattr(form, 'getlist') else ([form.get("uses_platform_keys")] if form.get("uses_platform_keys") else [])
        uses_platform_keys_value = uses_platform_keys_values[-1] if uses_platform_keys_values else "true"
        uses_platform_keys = str(uses_platform_keys_value).lower() == "true"

        update_data = ClientUpdate(
            name=form.get("name", client.name if hasattr(client, 'name') else client.get('name', '')),
            domain=form.get("domain", client.domain if hasattr(client, 'domain') else client.get('domain', '')),
            description=form.get("description", client.description if hasattr(client, 'description') else client.get('description', '')),
            settings=settings_update,
            active=str(active_value).lower() == "true",
            usersense_enabled=usersense_enabled,
            uses_platform_keys=uses_platform_keys,
            supertab_client_id=supertab_client_id,
            firecrawl_api_key=firecrawl_api_key
        )
        
        # Debug: Log the API keys and embedding settings being updated
        logger.info(f"About to update client with API keys: cartesia={update_data.settings.api_keys.cartesia_api_key}, siliconflow={update_data.settings.api_keys.siliconflow_api_key}")
        logger.info(f"Embedding settings: provider={update_data.settings.embedding.provider}, dimension={update_data.settings.embedding.dimension}, form_value='{form.get('embedding_dimension')}'")
        logger.info(f"BITHUMAN DEBUG - form value: '{form.get('bithuman_api_secret')}', in update_data: '{update_data.settings.api_keys.bithuman_api_secret}'")

        # Update client
        updated_client = await client_service.update_client(client_id, update_data)
        
        # Debug: Log the API keys after update
        logger.info(f"After update - cartesia={updated_client.settings.api_keys.cartesia_api_key if updated_client.settings.api_keys else 'None'}, siliconflow={updated_client.settings.api_keys.siliconflow_api_key if updated_client.settings.api_keys else 'None'}")
        
        # Sync API keys from platform to client database
        from app.services.platform_to_client_sync import PlatformToClientSync
        sync_success = await PlatformToClientSync.sync_after_update(client_id, client_service)
        if sync_success:
            logger.info(f"✅ Synced API keys from platform to client database")
        else:
            logger.warning(f"⚠️ Failed to sync API keys to client database")
        
        # Check if we should sync LiveKit credentials to backend for specific clients
        # This was previously checking for a default client ID which no longer exists
        if False:  # Disabled - no default client ID
            from app.services.backend_livekit_sync import BackendLiveKitSync
            sync_success = await BackendLiveKitSync.sync_credentials()
            if sync_success:
                logger.info("✅ LiveKit credentials synced to backend")
            else:
                logger.warning("⚠️ Failed to sync LiveKit credentials to backend")

        # If UserSense was just enabled (wasn't enabled before), trigger initial learning
        if usersense_enabled and not was_usersense_enabled:
            logger.info(f"🧠 UserSense enabled for client {client_id} - triggering initial learning")
            try:
                # Queue initial learning job for all users of this client
                result = platform_sb.rpc('queue_client_initial_learning', {
                    'p_client_id': client_id
                }).execute()
                if result.data:
                    job_id = result.data
                    logger.info(f"✅ Initial learning job queued: {job_id}")
                else:
                    logger.warning("⚠️ Failed to queue initial learning job (no job_id returned)")
            except Exception as learn_error:
                logger.error(f"⚠️ Failed to queue initial learning: {learn_error}")
                # Don't fail the whole update, just log the error

        # Redirect back to client detail with success
        success_message = "Client+updated+successfully"
        if usersense_enabled and not was_usersense_enabled:
            success_message = "Client+updated+successfully.+UserSense+initial+learning+started."
        return RedirectResponse(
            url=f"/admin/clients/{client_id}?message={success_message}",
            status_code=303
        )
        
    except Exception as e:
        logger.error(f"Error updating client: {e}")
        return RedirectResponse(
            url=f"/admin/clients/{client_id}?error=Failed+to+update+client:+{str(e)}",
            status_code=303
        )


# UserSense Learning Status Endpoints
@router.get("/clients/{client_id}/usersense-learning-status")
async def get_usersense_learning_status(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get UserSense learning status for a client"""
    try:
        ensure_client_access(client_id, admin_user)

        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)

        result = platform_sb.rpc('get_client_learning_status', {
            'p_client_id': client_id
        }).execute()

        if result.data:
            return {
                "success": True,
                "status": result.data
            }
        return {
            "success": True,
            "status": {
                "pending": 0,
                "in_progress": [],
                "completed": 0,
                "failed": 0,
                "has_active_jobs": False
            }
        }
    except Exception as e:
        logger.error(f"Failed to get learning status for client {client_id}: {e}")
        return {
            "success": False,
            "error": str(e),
            "status": None
        }


@router.delete("/clients/{client_id}/user-overviews")
async def delete_client_user_overviews(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Delete all user overviews for a client when UserSense is disabled"""
    try:
        ensure_client_access(client_id, admin_user)

        from app.utils.supabase_credentials import SupabaseCredentialManager

        # Get client's Supabase credentials
        client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_url, client_key)

        # Delete all user overviews for this client
        result = client_sb.table("user_overviews").delete().eq("client_id", client_id).execute()

        deleted_count = len(result.data) if result.data else 0
        logger.info(f"Deleted {deleted_count} user overviews for client {client_id}")

        # Also delete any pending learning jobs from the platform database
        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)

        platform_sb.table("usersense_learning_jobs").delete().eq("client_id", client_id).execute()

        return {
            "success": True,
            "deleted_count": deleted_count,
            "message": f"Deleted {deleted_count} user overview(s)"
        }

    except Exception as e:
        logger.error(f"Failed to delete user overviews for client {client_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/clients/{client_id}/user-overviews/preview")
async def admin_preview_user_overviews(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Admin preview of user overviews for a client - returns JSON for modal display"""
    try:
        ensure_client_access(client_id, admin_user)

        from app.utils.supabase_credentials import SupabaseCredentialManager

        # Get admin's user_id for highlighting their own profile
        admin_user_id = admin_user.get("user_id")

        # Get client info
        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)

        client_result = platform_sb.table("clients").select("name").eq("id", client_id).execute()
        client_name = client_result.data[0]["name"] if client_result.data else "Unknown Client"

        # Get client's Supabase credentials
        client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_url, client_key)

        # Fetch all user overviews for this client (for admin preview)
        result = client_sb.table("user_overviews").select(
            "user_id", "overview", "sidekick_insights", "learning_status", "updated_at"
        ).eq("client_id", client_id).order("updated_at", desc=True).limit(50).execute()

        user_overviews = result.data if result.data else []

        # Resolve admin's client user ID from the mapping table
        # Admin users have different IDs in platform vs client databases
        admin_client_user_id = admin_user_id  # Default to platform ID
        if admin_user_id:
            try:
                mapping_result = platform_sb.table("platform_client_user_mappings").select(
                    "client_user_id"
                ).eq("platform_user_id", admin_user_id).eq("client_id", client_id).maybe_single().execute()

                if mapping_result.data and mapping_result.data.get("client_user_id"):
                    admin_client_user_id = mapping_result.data["client_user_id"]
                    logger.debug(f"Resolved admin user mapping: {admin_user_id[:8]}... -> {admin_client_user_id[:8]}...")
            except Exception as mapping_err:
                logger.debug(f"Could not look up admin user mapping: {mapping_err}")

        # Sort to put admin's own profile first (if it exists)
        # Use the mapped client user ID to match against user_overviews
        if admin_client_user_id:
            admin_overviews = [uo for uo in user_overviews if uo.get("user_id") == admin_client_user_id]
            other_overviews = [uo for uo in user_overviews if uo.get("user_id") != admin_client_user_id]
            user_overviews = admin_overviews + other_overviews

        return {
            "success": True,
            "client_name": client_name,
            "user_overviews": user_overviews,
            "total_count": len(user_overviews),
            "admin_user_id": admin_client_user_id  # Return the mapped client user ID for highlighting
        }

    except Exception as e:
        logger.error(f"Failed to preview user overviews for client {client_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# DocumentSense Status and Control Endpoints
@router.get("/clients/{client_id}/documentsense-status")
async def get_documentsense_status(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get DocumentSense extraction status for a client"""
    try:
        ensure_client_access(client_id, admin_user)

        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)

        # Get job status from platform DB
        result = platform_sb.rpc('get_client_documentsense_status', {
            'p_client_id': client_id
        }).execute()

        status = result.data if result.data else {
            "pending": 0,
            "in_progress": [],
            "completed": 0,
            "failed": 0,
            "has_active_jobs": False
        }

        # Also get actual indexed document count from tenant DB
        # This ensures we show "View Documents" even if jobs table was cleared
        try:
            from app.utils.supabase_credentials import SupabaseCredentialManager
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            # Count documents with intelligence (table may not exist yet if schema sync hasn't run)
            intel_result = client_sb.table("document_intelligence").select("id", count="exact").eq("client_id", client_id).execute()
            indexed_count = intel_result.count if intel_result.count else 0
            logger.info(f"[DocumentSense] Client {client_id}: tenant indexed_count={indexed_count}, platform completed={status.get('completed', 0)}")

            # Use the higher of completed jobs or indexed documents
            # This handles cases where jobs were cleared but intelligence exists
            if indexed_count > status.get("completed", 0):
                status["completed"] = indexed_count
                status["indexed_documents"] = indexed_count
                logger.info(f"[DocumentSense] Updated status.completed to {indexed_count}")
        except Exception as tenant_err:
            # Table might not exist yet - that's OK, schema sync will create it
            if "does not exist" in str(tenant_err):
                logger.debug(f"document_intelligence table not yet created for client {client_id}")
            else:
                logger.warning(f"[DocumentSense] Could not check tenant document_intelligence: {tenant_err}")

        return {
            "success": True,
            "status": status
        }

    except Exception as e:
        logger.error(f"Failed to get DocumentSense status for client {client_id}: {e}")
        return {
            "success": False,
            "error": str(e),
            "status": None
        }


@router.post("/clients/{client_id}/documentsense/enable")
async def enable_documentsense_for_client(
    client_id: str,
    agent_id: str = Form(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Enable DocumentSense for a client and trigger batch extraction"""
    try:
        ensure_client_access(client_id, admin_user)

        from app.utils.supabase_credentials import SupabaseCredentialManager

        # Get client's Supabase credentials
        client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_url, client_key)

        # Check if DocumentSense tool exists in client's tools table
        tool_result = client_sb.table("tools").select("id").eq("slug", "documentsense").limit(1).execute()

        documentsense_tool_id = None
        if tool_result.data:
            documentsense_tool_id = tool_result.data[0]["id"]
        else:
            # Create the DocumentSense tool entry
            new_tool = client_sb.table("tools").insert({
                "slug": "documentsense",
                "name": "DocumentSense",
                "description": "Query extracted intelligence about specific documents",
                "type": "documentsense",
                "enabled": True
            }).execute()
            if new_tool.data:
                documentsense_tool_id = new_tool.data[0]["id"]

        if documentsense_tool_id:
            # Assign tool to agent (if not already assigned)
            existing_assignment = client_sb.table("agent_tools").select("id").eq(
                "agent_id", agent_id
            ).eq("tool_id", documentsense_tool_id).limit(1).execute()

            if not existing_assignment.data:
                client_sb.table("agent_tools").insert({
                    "agent_id": agent_id,
                    "tool_id": documentsense_tool_id,
                    "enabled": True
                }).execute()
                logger.info(f"Assigned DocumentSense tool to agent {agent_id}")

        # Queue batch extraction jobs
        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)

        result = platform_sb.rpc('queue_client_documentsense_extraction', {
            'p_client_id': client_id,
            'p_document_ids': None  # Process all documents
        }).execute()

        jobs_created = result.data.get('jobs_created', 0) if result.data else 0

        return {
            "success": True,
            "message": f"DocumentSense enabled. Queued {jobs_created} extraction job(s).",
            "jobs_created": jobs_created,
            "tool_assigned_to": agent_id
        }

    except Exception as e:
        logger.error(f"Failed to enable DocumentSense for client {client_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.delete("/clients/{client_id}/document-intelligence")
async def delete_client_document_intelligence(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Delete all document intelligence for a client when DocumentSense is disabled"""
    try:
        ensure_client_access(client_id, admin_user)

        from app.utils.supabase_credentials import SupabaseCredentialManager

        # Get client's Supabase credentials
        client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_url, client_key)

        # Delete all document intelligence for this client
        result = client_sb.table("document_intelligence").delete().eq("client_id", client_id).execute()

        deleted_count = len(result.data) if result.data else 0
        logger.info(f"Deleted {deleted_count} document intelligence records for client {client_id}")

        # Also delete any pending extraction jobs from the platform database
        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)

        platform_sb.table("documentsense_learning_jobs").delete().eq("client_id", client_id).execute()

        return {
            "success": True,
            "deleted_count": deleted_count,
            "message": f"Deleted {deleted_count} document intelligence record(s)"
        }

    except Exception as e:
        logger.error(f"Failed to delete document intelligence for client {client_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/clients/{client_id}/document-intelligence/preview")
async def admin_preview_document_intelligence(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Admin preview of document intelligence for a client - returns JSON for modal display"""
    try:
        ensure_client_access(client_id, admin_user)

        from app.utils.supabase_credentials import SupabaseCredentialManager

        # Get client info
        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)

        client_result = platform_sb.table("clients").select("name").eq("id", client_id).execute()
        client_name = client_result.data[0]["name"] if client_result.data else "Unknown Client"

        # Get client's Supabase credentials
        client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_url, client_key)

        # Fetch all document intelligence for this client
        # Table may not exist yet if schema sync hasn't run
        try:
            result = client_sb.table("document_intelligence").select(
                "document_id", "document_title", "intelligence", "extraction_model", "extraction_timestamp", "updated_at"
            ).eq("client_id", client_id).order("updated_at", desc=True).limit(50).execute()
            documents = result.data if result.data else []
        except Exception as table_err:
            if "does not exist" in str(table_err):
                logger.info(f"document_intelligence table not yet created for client {client_id} - schema sync needed")
                documents = []
            else:
                raise table_err

        # Format for display - include full intelligence for modal display
        formatted_docs = []
        for doc in documents:
            intel = doc.get("intelligence", {})
            formatted_docs.append({
                "document_id": doc.get("document_id"),
                "document_title": doc.get("document_title", "Untitled"),
                "intelligence": intel,  # Full intelligence object for modal display
                "extraction_model": doc.get("extraction_model"),
                "updated_at": doc.get("updated_at")
            })

        return {
            "success": True,
            "client_name": client_name,
            "documents": formatted_docs,
            "total_count": len(formatted_docs)
        }

    except Exception as e:
        logger.error(f"Failed to preview document intelligence for client {client_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.post("/clients/{client_id}/schema-sync")
async def trigger_client_schema_sync(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Trigger schema sync for a client to ensure all tables exist"""
    try:
        ensure_client_access(client_id, admin_user)

        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)

        # Get client's Supabase credentials
        client_result = platform_sb.table("clients").select(
            "supabase_url", "supabase_service_role_key"
        ).eq("id", client_id).single().execute()

        if not client_result.data:
            return {"success": False, "error": "Client not found"}

        client_supabase_url = client_result.data.get("supabase_url")
        client_service_key = client_result.data.get("supabase_service_role_key")

        if not client_supabase_url or not client_service_key:
            return {"success": False, "error": "Client Supabase credentials not configured"}

        # Run schema sync - use the Supabase Management API access token
        from app.services.schema_sync import apply_schema, project_ref_from_url
        project_ref = project_ref_from_url(client_supabase_url)
        # The Management API requires SUPABASE_ACCESS_TOKEN, not the client's service role key
        access_token = app_settings.supabase_access_token
        if not access_token:
            return {"success": False, "error": "SUPABASE_ACCESS_TOKEN not configured on platform"}
        results = apply_schema(project_ref, access_token, include_indexes=False)

        # Summarize results
        successful = [r[0] for r in results if r[1]]
        failed = [(r[0], r[2]) for r in results if not r[1]]

        logger.info(f"Schema sync for client {client_id}: {len(successful)} successful, {len(failed)} failed")

        return {
            "success": len(failed) == 0,
            "message": f"Schema sync completed: {len(successful)} tables/functions synced",
            "successful": successful,
            "failed": failed
        }

    except Exception as e:
        logger.error(f"Failed to run schema sync for client {client_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.post("/clients/{client_id}/provision")
async def trigger_client_provisioning(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """
    Trigger provisioning for a client that is missing Supabase credentials.

    This is useful when:
    - A Champion/Paragon tier client was created but provisioning failed
    - The original provisioning job was never created
    - Manual re-provisioning is needed after a failure
    """
    try:
        ensure_client_access(client_id, admin_user)

        from supabase import create_client as create_supabase_client
        from app.config import settings as app_settings
        platform_sb = create_supabase_client(app_settings.supabase_url, app_settings.supabase_service_role_key)

        # Get client info
        client_result = platform_sb.table("clients").select(
            "id, name, tier, hosting_type, provisioning_status, supabase_url, supabase_service_role_key"
        ).eq("id", client_id).single().execute()

        if not client_result.data:
            return {"success": False, "error": "Client not found"}

        client = client_result.data
        tier = client.get("tier", "adventurer")
        current_status = client.get("provisioning_status")
        has_supabase = bool(client.get("supabase_url") and client.get("supabase_service_role_key"))

        # Check if provisioning is needed
        if has_supabase and current_status == "ready":
            return {
                "success": True,
                "message": "Client already fully provisioned",
                "provisioning_status": current_status
            }

        # Check if already in progress
        if current_status in ("creating_project", "schema_syncing", "configuring_shared"):
            return {
                "success": True,
                "message": f"Provisioning already in progress (status: {current_status})",
                "provisioning_status": current_status
            }

        # Trigger provisioning
        from app.services.onboarding.provisioning_worker import provision_client_by_tier
        await provision_client_by_tier(client_id, tier, platform_sb)

        logger.info(f"✅ Triggered provisioning for client {client_id} (tier: {tier})")

        return {
            "success": True,
            "message": f"Provisioning queued for {tier} tier client",
            "provisioning_status": "queued",
            "tier": tier
        }

    except Exception as e:
        logger.error(f"Failed to trigger provisioning for client {client_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# WordPress Sites Management Endpoints
@router.get("/clients/{client_id}/wordpress-sites")
async def get_client_wordpress_sites(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get WordPress sites for a specific client"""
    try:
        ensure_client_access(client_id, admin_user)

        # Initialize WordPress service
        wp_service = get_wordpress_service()
        sites = wp_service.list_sites(client_id=client_id)
        
        return {
            "success": True,
            "sites": [site.dict() for site in sites]
        }
    except Exception as e:
        logger.error(f"Failed to get WordPress sites for client {client_id}: {e}")
        return {
            "success": False,
            "error": str(e),
            "sites": []
        }


@router.post("/clients/{client_id}/wordpress-sites")
async def create_wordpress_site(
    client_id: str,
    domain: str = Form(...),
    site_name: str = Form(...),
    admin_email: str = Form(...),
    allowed_origins: Optional[str] = Form(None),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Create a new WordPress site for a client"""
    try:
        ensure_client_access(client_id, admin_user)

        # Initialize WordPress service  
        wp_service = get_wordpress_service()
        
        # Create site data
        metadata: Dict[str, Any] = {}
        if allowed_origins:
            parsed = [o.strip() for o in allowed_origins.split(',') if o.strip()]
            metadata["allowed_origins"] = parsed

        site_data = WordPressSiteCreate(
            domain=domain,
            site_name=site_name,
            admin_email=admin_email,
            client_id=client_id,
            metadata=metadata
        )
        
        # Create the site
        site = wp_service.create_site(site_data)
        
        return RedirectResponse(
            url=f"/admin/clients/{client_id}?message=WordPress+credentials+created",
            status_code=303
        )
        
    except Exception as e:
        logger.error(f"Failed to create WordPress site: {e}")
        return RedirectResponse(
            url=f"/admin/clients/{client_id}?error=Failed+to+create+WordPress+site:+{str(e)}",
            status_code=303
        )


@router.post("/wordpress-sites/{site_id}/regenerate-keys")
async def regenerate_wordpress_keys(
    site_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Regenerate API keys for a WordPress site"""
    try:
        wp_service = get_wordpress_service()
        
        # Get existing site
        site = wp_service.get_site(site_id)
        if not site:
            return {"success": False, "error": "Site not found"}

        ensure_client_access(str(site.client_id), admin_user)

        updated_site = wp_service.regenerate_api_keys(site_id)
        if not updated_site:
            raise ValueError("Failed to regenerate keys")

        return RedirectResponse(
            url=f"/admin/clients/{site.client_id}?message=WordPress+shared+secret+regenerated",
            status_code=303
        )

    except Exception as e:
        logger.error(f"Failed to regenerate keys for site {site_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.delete("/wordpress-sites/{site_id}")
async def delete_wordpress_site(
    site_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Delete a WordPress site"""
    try:
        wp_service = get_wordpress_service()

        site = wp_service.get_site(site_id)
        if not site:
            return {
                "success": False,
                "error": "Site not found"
            }

        ensure_client_access(str(site.client_id), admin_user)
        
        # Delete the site
        success = wp_service.delete_site(site_id)
        
        if success:
            return {
                "success": True,
                "message": "WordPress site deleted successfully"
            }
        else:
            return {
                "success": False,
                "error": "Failed to delete site"
            }
            
    except Exception as e:
        logger.error(f"Failed to delete site {site_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# Knowledge Base Admin Endpoints
@router.get("/knowledge-base/documents")
async def get_knowledge_base_documents(
    client_id: str,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get documents for Knowledge Base admin interface"""
    try:
        # Validate client_id
        if not client_id or client_id == 'null' or client_id == 'undefined':
            logger.warning(f"Invalid client_id received: {client_id}")
            return JSONResponse(
                status_code=400,
                content={"error": "Client ID is required. Please select a client from the dropdown."}
            )

        ensure_client_access(client_id, admin_user)

        from app.services.document_processor import document_processor

        # Get documents for the specified client
        offset = (page - 1) * page_size
        documents, total_count, total_size, all_filenames = await document_processor.get_documents(
            user_id=None,  # Admin access doesn't need user_id
            client_id=client_id,
            status=status,
            limit=page_size,
            offset=offset,
            with_count=True,
        )

        return {
            "documents": documents,
            "total_count": total_count,
            "total_size": total_size,
            "page": page,
            "page_size": page_size,
            "all_file_names": all_filenames,
        }

    except Exception as e:
        logger.error(f"Failed to get documents for client {client_id}: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to load documents", "details": str(e)},
        )


@router.get("/knowledge-base/document-stats")
async def get_knowledge_base_document_stats(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Return aggregate document counts by status for the given client."""
    try:
        ensure_client_access(client_id, admin_user)

        from app.services.document_processor import document_processor
        supabase, _ = await document_processor._get_client_context(client_id)
        if not supabase:
            raise HTTPException(status_code=500, detail="Supabase connection unavailable")

        counts = {}
        for status in ["processing", "ready", "error"]:
            counts[status] = supabase.table('documents').select('id', count='exact').eq('status', status).execute().count or 0

        # Track documents that are marked ready but still missing embeddings
        pending_embeddings = supabase.table('documents')\
            .select('id', count='exact')\
            .eq('status', 'ready')\
            .is_('embeddings', 'null')\
            .execute()\
            .count or 0

        total = supabase.table('documents').select('id', count='exact').execute().count or 0
        return {
            "processing": counts.get("processing", 0),
            "ready": counts.get("ready", 0),
            "error": counts.get("error", 0),
            "total": total,
            "pending_embeddings": pending_embeddings,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get document stats for client {client_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load document stats: {e}")


@router.get("/api/clients")
async def get_admin_clients(
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get all clients for admin interface"""
    try:
        from app.core.dependencies import get_client_service
        client_service = get_client_service()
        clients = await client_service.get_all_clients()

        scoped_ids = get_scoped_client_ids(admin_user)
        if scoped_ids is not None:
            scoped_id_strings = {str(cid) for cid in scoped_ids}
            clients = [c for c in clients if str(getattr(c, 'id', '')) in scoped_id_strings]

        # Convert to list of dicts for JSON response
        client_list = []
        for client in clients:
            client_dict = {
                "id": client.id,
                "name": client.name,
                "domain": client.domain,
                "active": client.active
            }
            client_list.append(client_dict)
        
        return client_list
    except Exception as e:
        logger.error(f"Failed to get clients: {e}")
        return []


@router.get("/knowledge-base/agents")
async def get_knowledge_base_agents(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get agents for Knowledge Base admin interface"""
    try:
        ensure_client_access(client_id, admin_user)

        from app.core.dependencies import get_agent_service

        # Get agent service
        agent_service = get_agent_service()
        
        # Get all agents for the client
        agents = await agent_service.get_client_agents(client_id)
        
        # Convert to list format expected by frontend
        agent_list = []
        for agent in agents:
            agent_list.append({
                "id": agent.id,
                "name": agent.name,
                "agent_name": agent.name,  # For compatibility
                "slug": agent.slug,
                "agent_slug": agent.slug,  # For compatibility
                "enabled": agent.enabled
            })
        
        return agent_list
        
    except Exception as e:
        logger.error(f"Failed to get agents for client {client_id}: {e}")
        return []


@router.post("/knowledge-base/upload")
async def upload_knowledge_base_document(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Upload document to knowledge base using document processor"""
    try:
        # Set max content length to configured limit
        request._max_content_length = DOCUMENT_MAX_UPLOAD_BYTES
        
        # Parse form data
        form = await request.form()
        file = form.get("file")
        title = form.get("title", "")
        description = form.get("description", "")
        client_id = form.get("client_id")
        agent_ids = form.get("agent_ids", "")
        
        if not file:
            return {"success": False, "message": "No file provided"}
        
        if not client_id:
            return {"success": False, "message": "No client ID provided"}

        ensure_client_access(client_id, admin_user)

        # Get file content
        content = await file.read()
        filename = file.filename
        
        # Save file temporarily
        import os
        import tempfile
        import uuid
        
        # Create a unique temporary file
        temp_dir = tempfile.mkdtemp()
        temp_file_path = os.path.join(temp_dir, filename)
        
        try:
            # Save file temporarily
            with open(temp_file_path, 'wb') as f:
                f.write(content)
            
            # Determine agent access
            agent_id_list = None
            if agent_ids != "all" and agent_ids:
                agent_id_list = [id.strip() for id in agent_ids.split(',') if id.strip()]
            
            # Use document processor to handle the file
            from app.services.document_processor import document_processor
            
            result = await document_processor.process_uploaded_file(
                file_path=temp_file_path,
                title=title or filename,
                description=description,
                user_id=admin_user.get("user_id"),
                agent_ids=agent_id_list,
                client_id=client_id
            )
            
            if result['success']:
                logger.info(f"Document uploaded and processing started: {result['document_id']}")
                return {
                    "success": True, 
                    "message": "Document uploaded and processing started",
                    "document_id": result['document_id']
                }
            else:
                logger.error(f"Document processing failed: {result.get('error')}")
                # Clean up temp file on error
                try:
                    os.remove(temp_file_path)
                    os.rmdir(temp_dir)
                except:
                    pass
                return {"success": False, "message": result.get('error', 'Failed to process document')}
                
        except Exception as e:
            logger.error(f"Failed to process document: {e}")
            # Clean up temp file on error
            try:
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                if os.path.exists(temp_dir):
                    os.rmdir(temp_dir)
            except:
                pass
            return {"success": False, "message": f"Failed to process document: {str(e)}"}
        
    except Exception as e:
        logger.error(f"Document upload failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "message": f"Upload failed: {str(e)}"}


@router.post("/knowledge-base/upload-url")
async def upload_knowledge_base_url(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """
    Scrape a website URL and add it to the knowledge base.

    Supports both single URL scraping and multi-page crawling via Firecrawl API.
    """
    try:
        # Parse JSON body
        body = await request.json()
        url = body.get("url", "").strip()
        title = body.get("title", "").strip()
        description = body.get("description", "")
        client_id = body.get("client_id")
        agent_ids = body.get("agent_ids", "")
        crawl = body.get("crawl", False)  # Whether to crawl multiple pages
        crawl_limit = body.get("crawl_limit", 20)  # Max pages to crawl
        exclude_paths = body.get("exclude_paths", "")  # Paths to exclude
        include_paths = body.get("include_paths", "")  # Paths to include

        if not url:
            return {"success": False, "message": "URL is required"}

        if not client_id:
            return {"success": False, "message": "Client ID is required"}

        ensure_client_access(client_id, admin_user)

        # Validate and get Firecrawl API key for this client
        from app.services.firecrawl_scraper import FirecrawlScraper, FirecrawlError, get_firecrawl_scraper

        scraper = await get_firecrawl_scraper(client_id)
        if not scraper:
            return {
                "success": False,
                "message": "Firecrawl API key not configured for this client. Please add it in client settings."
            }

        # Validate embedding provider is configured
        from app.core.dependencies import get_client_service
        client_service = get_client_service()
        client = await client_service.get_client(client_id)
        if client:
            embedding_settings = None
            api_keys = None
            uses_platform_keys = False

            # Check if client uses platform keys (Adventurer tier pattern)
            if hasattr(client, 'uses_platform_keys'):
                uses_platform_keys = client.uses_platform_keys
            elif hasattr(client, 'additional_settings') and client.additional_settings:
                uses_platform_keys = client.additional_settings.get('uses_platform_keys', False)

            # Handle both object and dict formats
            if hasattr(client, 'settings') and client.settings:
                settings = client.settings
                if hasattr(settings, 'embedding'):
                    embedding_settings = settings.embedding
                elif isinstance(settings, dict):
                    embedding_settings = settings.get('embedding', {})
                if hasattr(settings, 'api_keys'):
                    api_keys = settings.api_keys
                elif isinstance(settings, dict):
                    api_keys = settings.get('api_keys', {})

            # Determine the embedding provider
            provider = None
            if embedding_settings:
                if hasattr(embedding_settings, 'provider'):
                    provider = embedding_settings.provider
                elif isinstance(embedding_settings, dict):
                    provider = embedding_settings.get('provider')

            # For platform-key clients (Adventurer tier), default to siliconflow
            # which uses platform-level API keys managed by the system
            if not provider:
                if uses_platform_keys:
                    provider = 'siliconflow'
                    logger.info(f"Using platform siliconflow embeddings for client {client_id}")
                else:
                    provider = 'openai'

            # Check if the corresponding API key is set (skip for platform-key clients)
            if not uses_platform_keys:
                provider_key_map = {
                    'openai': 'openai_api_key',
                    'novita': 'novita_api_key',
                    'deepinfra': 'deepinfra_api_key',
                    'siliconflow': 'siliconflow_api_key',
                }

                required_key = provider_key_map.get(provider)
                if required_key and api_keys:
                    key_value = None
                    if hasattr(api_keys, required_key):
                        key_value = getattr(api_keys, required_key)
                    elif isinstance(api_keys, dict):
                        key_value = api_keys.get(required_key)

                    if not key_value:
                        return {
                            "success": False,
                            "message": f"Embedding provider '{provider}' is configured but its API key ({required_key}) is not set. Please add the API key in client settings before scraping."
                        }

        # Validate URL
        try:
            validated_url = FirecrawlScraper.validate_url(url)
        except ValueError as e:
            return {"success": False, "message": str(e)}

        # Determine agent access
        agent_id_list = None
        if agent_ids and agent_ids != "all":
            if isinstance(agent_ids, str):
                agent_id_list = [id.strip() for id in agent_ids.split(',') if id.strip()]
            elif isinstance(agent_ids, list):
                agent_id_list = agent_ids

        try:
            # Scrape the URL(s)
            # Parse path filters (comma-separated strings to lists)
            include_paths_list = None
            exclude_paths_list = None
            if include_paths:
                include_paths_list = [p.strip() for p in include_paths.split(',') if p.strip()]
            if exclude_paths:
                exclude_paths_list = [p.strip() for p in exclude_paths.split(',') if p.strip()]

            logger.info(f"Starting web scrape for URL: {validated_url} (crawl={crawl}, limit={crawl_limit}, exclude={exclude_paths_list}, include={include_paths_list})")

            pages = await scraper.scrape_and_extract(
                url=validated_url,
                crawl=crawl,
                crawl_limit=min(crawl_limit, 100),  # Cap at 100 pages
                include_paths=include_paths_list,
                exclude_paths=exclude_paths_list,
            )

            if not pages:
                return {"success": False, "message": "No content could be extracted from the URL"}

            # Process each scraped page
            from app.services.document_processor import document_processor

            results = []
            is_multi_page = len(pages) > 1

            for page in pages:
                page_url = page.get("url", validated_url)

                # For multi-page crawls, use URL path as title for uniqueness
                # For single page, user-provided title takes precedence
                if is_multi_page:
                    # Extract meaningful title from URL path
                    from urllib.parse import urlparse, unquote
                    parsed = urlparse(page_url)
                    path = unquote(parsed.path.strip('/'))
                    if path:
                        # Convert path to readable title: /blog/my-post -> blog/my-post
                        page_title = path
                    else:
                        # Root URL - use domain
                        page_title = parsed.netloc
                else:
                    # Single page: user title > page title > URL
                    page_title = title or page.get("title") or page_url
                page_content = page.get("content", "")
                page_metadata = page.get("metadata", {})

                if not page_content.strip():
                    logger.warning(f"Skipping empty page: {page_url}")
                    continue

                result = await document_processor.process_web_content(
                    content=page_content,
                    title=page_title,
                    source_url=page_url,
                    description=description,
                    user_id=admin_user.get("user_id"),
                    agent_ids=agent_id_list,
                    client_id=client_id,
                    metadata=page_metadata,
                )

                results.append({
                    "url": page_url,
                    "title": page_title,
                    "success": result.get("success", False),
                    "document_id": result.get("document_id"),
                    "status": result.get("status"),
                    "message": result.get("message") or result.get("error"),
                    "duplicate": result.get("duplicate", False),
                })

            # Close the scraper
            await scraper.close()

            # Summary
            successful = sum(1 for r in results if r["success"])
            failed = len(results) - successful

            if successful == 0 and failed > 0:
                return {
                    "success": False,
                    "message": "Failed to process any pages",
                    "results": results
                }

            return {
                "success": True,
                "message": f"Successfully queued {successful} page(s) for processing" + (f" ({failed} failed)" if failed else ""),
                "total_pages": len(results),
                "successful": successful,
                "failed": failed,
                "results": results
            }

        except FirecrawlError as e:
            logger.error(f"Firecrawl error scraping {url}: {e}")
            await scraper.close()
            return {"success": False, "message": f"Scraping failed: {str(e)}"}

    except Exception as e:
        logger.error(f"URL upload failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "message": f"Failed to scrape URL: {str(e)}"}


@router.delete("/knowledge-base/documents/{document_id}")
async def delete_knowledge_base_document(
    document_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Delete a document from knowledge base"""
    try:
        from app.services.document_processor import document_processor
        scoped_ids = get_scoped_client_ids(admin_user)
        target_client_id = None if scoped_ids is None else await resolve_document_client(document_id, admin_user)

        # Delete the document using document processor
        success = await document_processor.delete_document(
            document_id=document_id,
            user_id=admin_user.get("user_id"),
            client_id=target_client_id,
        )

        if success:
            return {"success": True, "message": "Document deleted successfully"}
        else:
            return {"success": False, "message": "Failed to delete document"}
            
    except Exception as e:
        logger.error(f"Failed to delete document {document_id}: {e}")
        return {"success": False, "message": str(e)}


@router.post("/knowledge-base/documents/{document_id}/reprocess")
async def reprocess_knowledge_base_document(
    document_id: str,
    client_id: Optional[str] = Query(None),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Reprocess a document"""
    try:
        scoped_ids = get_scoped_client_ids(admin_user)
        target_client_id = client_id

        # Resolve client if not provided
        if not target_client_id:
            target_client_id = await resolve_document_client(document_id, admin_user)

        # Enforce scope
        if scoped_ids is not None:
            ensure_client_access(target_client_id, admin_user)
        if not target_client_id:
            raise HTTPException(status_code=400, detail="client_id is required to reprocess this document")

        from app.services.document_processor import document_processor
        import asyncio

        supabase_client, _ = await document_processor._get_client_context(target_client_id)
        if not supabase_client:
            raise HTTPException(status_code=500, detail="Supabase connection unavailable for client")

        # Try to fetch document to find file_path
        doc = supabase_client.table('documents').select('metadata').eq('id', document_id).single().execute().data
        metadata = doc.get('metadata', {}) if doc else {}
        file_path = metadata.get('file_path')

        # Mark as processing to give feedback immediately
        supabase_client.table('documents').update({'status': 'processing', 'processing_status': 'processing'}).eq('id', document_id).execute()

        if file_path and os.path.exists(file_path):
            asyncio.create_task(
                document_processor._process_document_async(
                    document_id=document_id,
                    file_path=file_path,
                    agent_ids=[],
                    client_id=target_client_id
                )
            )
        else:
            # Fall back to rebuilding from chunks
            asyncio.create_task(document_processor.reprocess_from_chunks(document_id, client_id=target_client_id, supabase=supabase_client))

        return {"success": True, "message": "Document reprocessing started"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reprocess document {document_id}: {e}")
        return {"success": False, "message": str(e)}


@router.post("/knowledge-base/reprocess-missing-embeddings")
async def reprocess_missing_embeddings(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Reprocess all documents that are missing embeddings for a client"""
    try:
        data = await request.json()
        client_id = data.get("client_id")

        if not client_id:
            raise HTTPException(status_code=400, detail="client_id is required")

        scoped_ids = get_scoped_client_ids(admin_user)
        if scoped_ids is not None:
            ensure_client_access(client_id, admin_user)

        from app.services.document_processor import document_processor
        import asyncio

        # Get client context
        supabase_client, _ = await document_processor._get_client_context(client_id)

        # Find all documents missing embeddings (status=ready but embeddings is null)
        result = supabase_client.table("documents").select("id, title, status").eq("status", "ready").is_("embeddings", "null").execute()

        documents_to_process = result.data if result.data else []

        if not documents_to_process:
            return {"success": True, "message": "No documents need reprocessing", "count": 0}

        # Queue reprocessing for each document
        async def reprocess_batch():
            processed = 0
            for doc in documents_to_process:
                try:
                    # Check if document has content in raw_content or file_path
                    doc_detail = supabase_client.table("documents").select("raw_content, file_path, metadata").eq("id", doc["id"]).single().execute()
                    if doc_detail.data:
                        raw_content = doc_detail.data.get("raw_content")
                        if raw_content:
                            await document_processor.reprocess_document(doc["id"], raw_content, client_id=client_id, supabase=supabase_client)
                            processed += 1
                        else:
                            # Try from chunks
                            await document_processor.reprocess_from_chunks(doc["id"], client_id=client_id, supabase=supabase_client)
                            processed += 1
                except Exception as e:
                    logger.error(f"Failed to reprocess document {doc['id']}: {e}")
            logger.info(f"Bulk reprocess completed: {processed}/{len(documents_to_process)} documents")

        # Start reprocessing in background
        asyncio.create_task(reprocess_batch())

        return {
            "success": True,
            "message": f"Started reprocessing {len(documents_to_process)} documents missing embeddings",
            "count": len(documents_to_process)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start bulk reprocess: {e}")
        return {"success": False, "message": str(e)}


@router.put("/knowledge-base/documents/{document_id}/access")
async def update_document_access(
    document_id: str,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Update document access permissions"""
    try:
        scoped_ids = get_scoped_client_ids(admin_user)
        if scoped_ids is not None:
            await resolve_document_client(document_id, admin_user)

        data = await request.json()
        agent_access = data.get("agent_access", "specific")
        agent_ids = data.get("agent_ids", [])

        # For now, return success
        # TODO: Implement actual access update in Supabase
        return {"success": True, "message": "Document access updated successfully"}
    except Exception as e:
        logger.error(f"Failed to update document access: {e}")
        return {"success": False, "message": str(e)}


# =========================================================================
# Usage Tracking API Endpoints
# =========================================================================

@router.get("/api/usage/{client_id}/{agent_id}")
async def get_agent_usage(
    client_id: str,
    agent_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get usage statistics for a specific agent/sidekick"""
    try:
        ensure_client_access(client_id, admin_user)

        from app.services.usage_tracking import usage_tracking_service

        await usage_tracking_service.initialize()
        quotas = await usage_tracking_service.get_all_agent_quotas(client_id, agent_id)

        return {
            "success": True,
            "agent_id": agent_id,
            "client_id": client_id,
            "voice": {
                "used_seconds": quotas["voice"].used,
                "limit_seconds": quotas["voice"].limit,
                "used_minutes": round(quotas["voice"].used / 60, 1),
                "limit_minutes": round(quotas["voice"].limit / 60, 1) if quotas["voice"].limit > 0 else 0,
                "percent_used": quotas["voice"].percent_used,
                "is_exceeded": quotas["voice"].is_exceeded,
                "is_warning": quotas["voice"].is_warning,
            },
            "text": {
                "used": quotas["text"].used,
                "limit": quotas["text"].limit,
                "percent_used": quotas["text"].percent_used,
                "is_exceeded": quotas["text"].is_exceeded,
                "is_warning": quotas["text"].is_warning,
            },
            "embedding": {
                "used": quotas["embedding"].used,
                "limit": quotas["embedding"].limit,
                "percent_used": quotas["embedding"].percent_used,
                "is_exceeded": quotas["embedding"].is_exceeded,
                "is_warning": quotas["embedding"].is_warning,
            },
        }
    except Exception as e:
        logger.error(f"Failed to get agent usage: {e}")
        return {"success": False, "message": str(e)}


@router.get("/api/usage/{client_id}")
async def get_client_usage(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get usage statistics for all agents belonging to a client"""
    try:
        ensure_client_access(client_id, admin_user)

        from app.services.usage_tracking import usage_tracking_service
        from app.services.client_connection_manager import get_connection_manager

        await usage_tracking_service.initialize()

        # Get client's Supabase to fetch agent names
        client_supabase = None
        try:
            conn_manager = get_connection_manager()
            client_config = await conn_manager.get_client_config(client_id)
            if client_config and client_config.get("supabase_url") and client_config.get("supabase_service_role_key"):
                from supabase import create_client
                client_supabase = create_client(
                    client_config["supabase_url"],
                    client_config["supabase_service_role_key"]
                )
        except Exception as e:
            logger.warning(f"Could not get client Supabase for usage display: {e}")

        agent_usage_records = await usage_tracking_service.get_all_agents_usage(client_id, client_supabase)

        # Convert to JSON-friendly format
        agents = []
        for record in agent_usage_records:
            agents.append({
                "agent_id": record.agent_id,
                "agent_name": record.agent_name,
                "agent_slug": record.agent_slug,
                "voice": {
                    "used_seconds": record.voice.used if record.voice else 0,
                    "limit_seconds": record.voice.limit if record.voice else 0,
                    "used_minutes": round(record.voice.used / 60, 1) if record.voice else 0,
                    "limit_minutes": round(record.voice.limit / 60, 1) if record.voice and record.voice.limit > 0 else 0,
                    "percent_used": record.voice.percent_used if record.voice else 0,
                    "is_exceeded": record.voice.is_exceeded if record.voice else False,
                    "is_warning": record.voice.is_warning if record.voice else False,
                },
                "text": {
                    "used": record.text.used if record.text else 0,
                    "limit": record.text.limit if record.text else 0,
                    "percent_used": record.text.percent_used if record.text else 0,
                    "is_exceeded": record.text.is_exceeded if record.text else False,
                    "is_warning": record.text.is_warning if record.text else False,
                },
                "embedding": {
                    "used": record.embedding.used if record.embedding else 0,
                    "limit": record.embedding.limit if record.embedding else 0,
                    "percent_used": record.embedding.percent_used if record.embedding else 0,
                    "is_exceeded": record.embedding.is_exceeded if record.embedding else False,
                    "is_warning": record.embedding.is_warning if record.embedding else False,
                },
            })

        return {
            "success": True,
            "client_id": client_id,
            "agents": agents,
        }
    except Exception as e:
        logger.error(f"Failed to get client usage: {e}")
        return {"success": False, "message": str(e)}


@router.get("/api/usage/debug/{client_id}")
async def debug_usage_data(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """
    Debug endpoint to view raw usage data in the database.
    Shows both agent_usage and client_usage table contents.
    """
    if not admin_is_super(admin_user):
        raise HTTPException(status_code=403, detail="Superadmin access required")

    try:
        from app.integrations.supabase_client import supabase_manager
        from datetime import date

        # Get current period start (first of the month)
        today = date.today()
        period_start = date(today.year, today.month, 1).isoformat()

        # Query agent_usage table
        agent_usage_result = supabase_manager.admin_client.table("agent_usage")\
            .select("*")\
            .eq("client_id", client_id)\
            .execute()

        # Query client_usage table
        client_usage_result = supabase_manager.admin_client.table("client_usage")\
            .select("*")\
            .eq("client_id", client_id)\
            .execute()

        # Check if the RPC function exists by trying to call it
        rpc_result = None
        rpc_error = None
        try:
            rpc_response = supabase_manager.admin_client.rpc(
                'get_client_aggregated_usage',
                {'p_client_id': client_id, 'p_period_start': period_start}
            ).execute()
            rpc_result = rpc_response.data
        except Exception as e:
            rpc_error = str(e)

        return {
            "success": True,
            "client_id": client_id,
            "current_period_start": period_start,
            "agent_usage_records": agent_usage_result.data or [],
            "agent_usage_count": len(agent_usage_result.data or []),
            "client_usage_records": client_usage_result.data or [],
            "client_usage_count": len(client_usage_result.data or []),
            "rpc_function_result": rpc_result,
            "rpc_function_error": rpc_error,
        }
    except Exception as e:
        logger.error(f"Failed to get debug usage data: {e}")
        return {"success": False, "message": str(e)}


@router.get("/api/debug/transcripts/{client_id}")
async def debug_transcripts(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """
    Debug endpoint to diagnose transcript storage issues.
    Checks platform client credentials and recent transcripts in client database.
    """
    if not admin_is_super(admin_user):
        raise HTTPException(status_code=403, detail="Superadmin access required")

    try:
        from app.core.dependencies import get_platform_client_service
        from app.integrations.supabase_client import supabase_manager

        platform_service = get_platform_client_service()
        platform_client = await platform_service.get_client(client_id)

        result = {
            "success": True,
            "client_id": client_id,
            "platform_client_found": platform_client is not None,
        }

        if not platform_client:
            result["error"] = "Client not found in platform database"
            return result

        # Check Supabase credentials
        supabase_url = getattr(platform_client, "supabase_project_url", None) or getattr(platform_client, "supabase_url", None)
        supabase_service_key = getattr(platform_client, "supabase_service_role_key", None)

        result["credentials"] = {
            "supabase_url_present": bool(supabase_url),
            "supabase_url_value": supabase_url[:50] + "..." if supabase_url and len(supabase_url) > 50 else supabase_url,
            "supabase_service_role_key_present": bool(supabase_service_key),
            "supabase_service_role_key_length": len(supabase_service_key) if supabase_service_key else 0,
        }

        # If credentials are present, try to connect and check transcripts
        if supabase_url and supabase_service_key:
            try:
                from supabase import create_client
                client_supabase = create_client(supabase_url, supabase_service_key)

                # Check recent transcripts
                transcripts_result = client_supabase.table("conversation_transcripts")\
                    .select("id, conversation_id, role, content, created_at, source")\
                    .order("created_at", desc=True)\
                    .limit(10)\
                    .execute()

                result["client_database"] = {
                    "connection_successful": True,
                    "recent_transcripts_count": len(transcripts_result.data or []),
                    "recent_transcripts": [
                        {
                            "id": t.get("id"),
                            "conversation_id": t.get("conversation_id"),
                            "role": t.get("role"),
                            "content_preview": (t.get("content") or "")[:100] + "..." if len(t.get("content") or "") > 100 else t.get("content"),
                            "source": t.get("source"),
                            "created_at": t.get("created_at"),
                        }
                        for t in (transcripts_result.data or [])
                    ],
                }

                # Check recent conversations
                conversations_result = client_supabase.table("conversations")\
                    .select("id, agent_id, user_id, channel, created_at")\
                    .order("created_at", desc=True)\
                    .limit(5)\
                    .execute()

                result["client_database"]["recent_conversations"] = [
                    {
                        "id": c.get("id"),
                        "agent_id": c.get("agent_id"),
                        "channel": c.get("channel"),
                        "created_at": c.get("created_at"),
                    }
                    for c in (conversations_result.data or [])
                ]

            except Exception as db_err:
                result["client_database"] = {
                    "connection_successful": False,
                    "error": str(db_err),
                }
        else:
            result["client_database"] = {
                "connection_successful": False,
                "error": "Missing Supabase credentials - transcripts cannot be stored",
            }

        # Check recent livekit_events for this client to see voice activity
        try:
            livekit_events = supabase_manager.admin_client.table("livekit_events")\
                .select("id, event_type, room_name, metadata, created_at")\
                .order("created_at", desc=True)\
                .limit(10)\
                .execute()

            # Filter for this client's events
            client_events = []
            for event in (livekit_events.data or []):
                metadata = event.get("metadata") or {}
                if isinstance(metadata, str):
                    try:
                        import json
                        metadata = json.loads(metadata)
                    except:
                        metadata = {}
                if metadata.get("client_id") == client_id:
                    client_events.append({
                        "event_type": event.get("event_type"),
                        "room_name": event.get("room_name"),
                        "created_at": event.get("created_at"),
                        "has_conversation_id": bool(metadata.get("conversation_id")),
                    })

            result["recent_livekit_events"] = client_events[:5]

        except Exception as events_err:
            result["recent_livekit_events_error"] = str(events_err)

        return result

    except Exception as e:
        logger.error(f"Failed to debug transcripts: {e}")
        return {"success": False, "message": str(e)}


@router.get("/debug/usage-tracking/{client_id}")
async def debug_usage_tracking(
    request: Request,
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """
    Debug endpoint to diagnose usage tracking issues.
    Shows recent livekit_events, agent_usage records, and RPC function status.
    """
    from app.integrations.supabase_client import supabase_manager
    from app.services.usage_tracking import usage_tracking_service
    from datetime import date
    import json

    result = {
        "client_id": client_id,
        "timestamp": datetime.now().isoformat(),
        "period_start": date(date.today().year, date.today().month, 1).isoformat(),
        "checks": {},
        "issues_found": [],
        "recommendations": [],
    }

    # 1. Check recent room_finished events from livekit_events
    try:
        livekit_events = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("livekit_events")
            .select("id, event_type, room_name, duration, metadata, created_at")
            .eq("event_type", "room_finished")
            .order("created_at", desc=True)
            .limit(20)
        )

        room_finished_events = []
        client_events = []

        for event in (livekit_events.data or []):
            metadata = event.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except:
                    metadata = {}

            event_data = {
                "room_name": event.get("room_name"),
                "duration": event.get("duration"),
                "duration_type": type(event.get("duration")).__name__,
                "has_client_id": bool(metadata.get("client_id")),
                "has_agent_id": bool(metadata.get("agent_id")),
                "client_id": metadata.get("client_id"),
                "agent_id": metadata.get("agent_id"),
                "created_at": event.get("created_at"),
                "is_text_room": event.get("room_name", "").startswith("text-"),
            }
            room_finished_events.append(event_data)

            if str(metadata.get("client_id")) == str(client_id):
                client_events.append(event_data)

        result["checks"]["livekit_events"] = {
            "total_room_finished_events": len(room_finished_events),
            "client_specific_events": len(client_events),
            "recent_events": client_events[:10] if client_events else room_finished_events[:5],
        }

        # Analyze events for issues
        zero_duration_count = sum(1 for e in client_events if (e.get("duration") or 0) == 0 and not e.get("is_text_room"))
        missing_metadata_count = sum(1 for e in client_events if not e.get("has_client_id") or not e.get("has_agent_id"))

        if len(room_finished_events) == 0:
            result["issues_found"].append("No room_finished events found - LiveKit webhook may not be configured")
            result["recommendations"].append("Verify LiveKit webhook URL is set to: {your-server}/webhooks/livekit/events")
        elif len(client_events) == 0:
            result["issues_found"].append(f"No room_finished events found for client {client_id}")
            result["recommendations"].append("Ensure room metadata includes client_id when creating rooms")
        else:
            if zero_duration_count > 0:
                result["issues_found"].append(f"{zero_duration_count} voice room(s) have duration=0 - usage NOT tracked")
                result["recommendations"].append("Check if calls are ending properly before room timeout")
            if missing_metadata_count > 0:
                result["issues_found"].append(f"{missing_metadata_count} event(s) missing client_id or agent_id in metadata")
                result["recommendations"].append("Ensure room metadata includes both client_id and agent_id")

    except Exception as e:
        result["checks"]["livekit_events"] = {"error": str(e)}
        result["issues_found"].append(f"Failed to query livekit_events: {e}")

    # 2. Check agent_usage table directly
    try:
        period_start = date(date.today().year, date.today().month, 1)
        agent_usage = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("agent_usage")
            .select("*")
            .eq("client_id", client_id)
            .eq("period_start", period_start.isoformat())
        )

        usage_records = []
        total_voice_seconds = 0
        total_text_messages = 0

        for record in (agent_usage.data or []):
            voice_secs = record.get("voice_seconds_used", 0) or 0
            text_msgs = record.get("text_messages_used", 0) or 0
            total_voice_seconds += voice_secs
            total_text_messages += text_msgs
            usage_records.append({
                "agent_id": record.get("agent_id"),
                "voice_seconds_used": voice_secs,
                "voice_minutes_used": round(voice_secs / 60, 2),
                "text_messages_used": text_msgs,
                "updated_at": record.get("updated_at"),
            })

        result["checks"]["agent_usage_table"] = {
            "records_found": len(usage_records),
            "total_voice_seconds": total_voice_seconds,
            "total_voice_minutes": round(total_voice_seconds / 60, 2),
            "total_text_messages": total_text_messages,
            "records": usage_records,
        }

        if len(usage_records) == 0:
            result["issues_found"].append("No agent_usage records found for this client/period")
            result["recommendations"].append("Usage records are created when increment functions are called successfully")

    except Exception as e:
        result["checks"]["agent_usage_table"] = {"error": str(e)}
        result["issues_found"].append(f"Failed to query agent_usage: {e}")

    # 3. Check RPC function
    try:
        await usage_tracking_service.initialize()
        period_start = date(date.today().year, date.today().month, 1)

        rpc_result = supabase_manager.admin_client.rpc(
            'get_client_aggregated_usage',
            {'p_client_id': client_id, 'p_period_start': period_start.isoformat()}
        ).execute()

        if rpc_result.data and len(rpc_result.data) > 0:
            rpc_data = rpc_result.data[0]
            result["checks"]["rpc_function"] = {
                "status": "working",
                "total_voice_seconds": rpc_data.get("total_voice_seconds", 0),
                "total_voice_minutes": round((rpc_data.get("total_voice_seconds", 0) or 0) / 60, 2),
                "total_text_messages": rpc_data.get("total_text_messages", 0),
                "agent_count": rpc_data.get("agent_count", 0),
            }
        else:
            result["checks"]["rpc_function"] = {
                "status": "no_data",
                "message": "RPC returned empty result"
            }

    except Exception as e:
        result["checks"]["rpc_function"] = {"status": "error", "error": str(e)}
        result["issues_found"].append(f"RPC function error: {e}")
        result["recommendations"].append("Ensure migration 20250129_add_client_aggregated_usage_rpc.sql has been applied")

    # 4. Summary
    result["summary"] = {
        "issues_count": len(result["issues_found"]),
        "has_webhook_events": result["checks"].get("livekit_events", {}).get("total_room_finished_events", 0) > 0,
        "has_usage_records": result["checks"].get("agent_usage_table", {}).get("records_found", 0) > 0,
        "rpc_working": result["checks"].get("rpc_function", {}).get("status") == "working",
    }

    return result


# ============================================================================
# Client Stats API for Clients List Page
# ============================================================================

@router.get("/api/clients/{client_id}/agents")
async def get_client_agents_list(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """
    Get list of agents for a client.
    Used by the clients list page to show agent count.
    """
    try:
        ensure_client_access(client_id, admin_user)

        from app.core.dependencies import get_agent_service
        agent_service = get_agent_service()

        agents = await agent_service.get_client_agents(client_id)

        # Return simple list for counting
        return [
            {
                "id": agent.id,
                "name": agent.name,
                "slug": agent.slug,
                "enabled": agent.enabled
            }
            for agent in agents
        ]

    except Exception as e:
        logger.error(f"Failed to get agents for client {client_id}: {e}")
        return []


@router.get("/api/clients/{client_id}/usage")
async def get_client_aggregated_usage(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """
    Get aggregated usage statistics for a client (all agents combined).
    Used by the clients list page to show usage progress bars.
    """
    try:
        ensure_client_access(client_id, admin_user)

        from app.services.usage_tracking import usage_tracking_service
        from app.services.client_connection_manager import get_connection_manager

        await usage_tracking_service.initialize()

        # Get client's Supabase connection for agent names
        client_supabase = None
        try:
            conn_manager = get_connection_manager()
            client_config = await conn_manager.get_client_config(client_id)
            if client_config and client_config.get("supabase_url") and client_config.get("supabase_service_role_key"):
                from supabase import create_client
                client_supabase = create_client(
                    client_config["supabase_url"],
                    client_config["supabase_service_role_key"]
                )
        except Exception as e:
            logger.debug(f"Could not get client Supabase for usage lookup: {e}")

        # Get per-agent usage records
        agent_usage_records = await usage_tracking_service.get_all_agents_usage(client_id, client_supabase)

        # Aggregate totals across all agents
        total_voice_used = 0
        total_voice_limit = 0
        total_text_used = 0
        total_text_limit = 0
        total_embedding_used = 0
        total_embedding_limit = 0

        for record in agent_usage_records:
            if record.voice:
                total_voice_used += record.voice.used or 0
                total_voice_limit += record.voice.limit or 0
            if record.text:
                total_text_used += record.text.used or 0
                total_text_limit += record.text.limit or 0
            if record.embedding:
                total_embedding_used += record.embedding.used or 0
                total_embedding_limit += record.embedding.limit or 0

        # Calculate percentages
        voice_percent = round((total_voice_used / total_voice_limit * 100), 1) if total_voice_limit > 0 else 0
        text_percent = round((total_text_used / total_text_limit * 100), 1) if total_text_limit > 0 else 0
        embedding_percent = round((total_embedding_used / total_embedding_limit * 100), 1) if total_embedding_limit > 0 else 0

        return {
            "client_id": client_id,
            "agent_count": len(agent_usage_records),
            "voice": {
                "used": total_voice_used,
                "limit": total_voice_limit,
                "percent_used": voice_percent,
                "is_exceeded": total_voice_limit > 0 and total_voice_used >= total_voice_limit,
                "is_warning": total_voice_limit > 0 and voice_percent >= 80,
            },
            "text": {
                "used": total_text_used,
                "limit": total_text_limit,
                "percent_used": text_percent,
                "is_exceeded": total_text_limit > 0 and total_text_used >= total_text_limit,
                "is_warning": total_text_limit > 0 and text_percent >= 80,
            },
            "embedding": {
                "used": total_embedding_used,
                "limit": total_embedding_limit,
                "percent_used": embedding_percent,
                "is_exceeded": total_embedding_limit > 0 and total_embedding_used >= total_embedding_limit,
                "is_warning": total_embedding_limit > 0 and embedding_percent >= 80,
            },
        }

    except Exception as e:
        logger.error(f"Failed to get aggregated usage for client {client_id}: {e}")
        # Return zeros instead of error to not break UI
        return {
            "client_id": client_id,
            "agent_count": 0,
            "voice": {"used": 0, "limit": 0, "percent_used": 0, "is_exceeded": False, "is_warning": False},
            "text": {"used": 0, "limit": 0, "percent_used": 0, "is_exceeded": False, "is_warning": False},
            "embedding": {"used": 0, "limit": 0, "percent_used": 0, "is_exceeded": False, "is_warning": False},
        }


# ── Documentation pages (not linked from sidebar) ──────────────────────

@router.get("/docs", response_class=HTMLResponse)
async def docs_index(request: Request):
    """Docs landing – redirects to Getting Started."""
    return RedirectResponse(url="/admin/docs/getting-started", status_code=302)

@router.get("/docs/getting-started", response_class=HTMLResponse)
async def docs_getting_started(request: Request):
    return templates.TemplateResponse("admin/docs/getting_started.html", {"request": request})

@router.get("/docs/embedding", response_class=HTMLResponse)
async def docs_embedding(request: Request):
    return templates.TemplateResponse("admin/docs/embedding.html", {"request": request})

@router.get("/docs/managing", response_class=HTMLResponse)
async def docs_managing(request: Request):
    return templates.TemplateResponse("admin/docs/managing.html", {"request": request})

@router.get("/docs/abilities", response_class=HTMLResponse)
async def docs_abilities(request: Request):
    return templates.TemplateResponse("admin/docs/abilities.html", {"request": request})

@router.get("/docs/knowledge-base", response_class=HTMLResponse)
async def docs_knowledge_base(request: Request):
    return templates.TemplateResponse("admin/docs/knowledge_base_guide.html", {"request": request})

@router.get("/docs/wordpress", response_class=HTMLResponse)
async def docs_wordpress(request: Request):
    return templates.TemplateResponse("admin/docs/wordpress.html", {"request": request})

@router.get("/docs/monitoring", response_class=HTMLResponse)
async def docs_monitoring(request: Request):
    return templates.TemplateResponse("admin/docs/monitoring_guide.html", {"request": request})

@router.get("/docs/billing", response_class=HTMLResponse)
async def docs_billing(request: Request):
    return templates.TemplateResponse("admin/docs/billing.html", {"request": request})

@router.get("/docs/troubleshooting", response_class=HTMLResponse)
async def docs_troubleshooting(request: Request):
    return templates.TemplateResponse("admin/docs/troubleshooting.html", {"request": request})
