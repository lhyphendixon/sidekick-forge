from fastapi import APIRouter, Request, Depends, Form, HTTPException, File, UploadFile, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Any, List, Optional, Set, Tuple
import redis.asyncio as aioredis
import asyncio
import redis
import base64
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
    logger.debug(f"ensure_client_access: client_id={client_id!r}, scoped_ids={scoped_ids}")
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
            supabase = await document_processor._get_client_supabase_client(cid)
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
from app.admin.auth import get_admin_user

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
    return templates.TemplateResponse(
        "admin/reset-password.html",
        {
            "request": request,
            "supabase_url": settings.supabase_url,
            "supabase_anon_key": settings.supabase_anon_key,
        },
    )

@router.post("/login")
async def login(request: Request):
    """Handle login form submission"""
    # This will be handled by the frontend JavaScript
    return {"status": "handled_by_frontend"}

@router.post("/logout")
async def logout(request: Request):
    """Admin logout"""
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("admin_token")
    return response

@router.get("/auth/check")
async def check_auth(request: Request):
    """Check if user is authenticated"""
    try:
        user = await get_admin_user(request)
        return {"authenticated": True, "user": user}
    except HTTPException:
        return {"authenticated": False}

# Users management page
@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, user: Dict[str, Any] = Depends(get_admin_user)):
    """Minimal Users page showing recent users and platform permissions."""
    await supabase_manager.initialize()
    import httpx
    headers = {
        'apikey': os.getenv('SUPABASE_SERVICE_ROLE_KEY', ''),
        'Authorization': f"Bearer {os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')}",
    }
    users: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{os.getenv('SUPABASE_URL')}/auth/v1/admin/users", headers=headers, params={"per_page": 25})
            if r.status_code == 200:
                data = r.json()
                users = data.get('users', [])
    except Exception:
        users = []

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
        for record in users:
            metadata = record.get('user_metadata') or {}
            admin_ids = extract_assignment_ids(metadata, ['admin_client_ids'])
            subscriber_ids = extract_assignment_ids(metadata, ['subscriber_client_ids'])
            combined = {str(cid) for cid in admin_ids + subscriber_ids}
            if combined & allowed_ids:
                filtered_users.append(record)
        users = filtered_users
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
    # Pagination variables for template (currently showing all users without pagination)
    total_users = len(enriched)
    page = 1
    per_page = total_users if total_users > 0 else 1
    total_pages = 1
    search = ""
    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "user": user,
        "users": enriched,
        "clients": clients_ctx,
        "supabase_url": settings.supabase_url,
        "supabase_anon_key": settings.supabase_anon_key,
        "total_users": total_users,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "search": search,
    })

@router.post("/users/create")
async def users_create(request: Request, admin: Dict[str, Any] = Depends(get_admin_user)):
    """Create a new user via Supabase Admin API, then assign platform role membership."""
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
        payload = {"email": email, "email_confirm": True, "user_metadata": initial_user_metadata}
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{supabase_url}/auth/v1/admin/users", headers=headers, json=payload)
        if r.status_code not in (200, 201):
            # Handle "already exists" by looking up existing user and continuing
            try:
                if r.status_code in (400, 409):
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
        else:
            user = r.json()
            user_id = user.get('id')

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
        await supabase_manager.initialize()
        admin_client = supabase_manager.admin_client

        scoped_ids = get_scoped_client_ids(admin)
        allowed_ids: Optional[Set[str]] = None if scoped_ids is None else {str(cid) for cid in scoped_ids}

        # Resolve role ids
        def get_role_id(role_key: str) -> Optional[str]:
            try:
                row = (
                    admin_client.table("roles")
                    .select("id,key")
                    .eq("key", role_key)
                    .single()
                    .execute()
                    .data
                )
                return row.get("id") if row else None
            except Exception:
                return None

        super_admin_role_id = get_role_id("super_admin")
        admin_role_id = get_role_id("admin")
        subscriber_role_id = get_role_id("subscriber")

        # Check platform super_admin
        if super_admin_role_id:
            try:
                pr = (
                    admin_client.table("platform_role_memberships")
                    .select("role_id")
                    .eq("user_id", user_id)
                    .eq("role_id", super_admin_role_id)
                    .execute()
                    .data
                )
                if pr:
                    return {"role_key": "super_admin", "client_ids": []}
            except Exception:
                pass

        # Check tenant memberships for admin/subscriber
        def get_client_ids_for_role(role_id: Optional[str]) -> List[str]:
            if not role_id:
                return []
            try:
                rows = (
                    admin_client.table("tenant_memberships")
                    .select("client_id")
                    .eq("user_id", user_id)
                    .eq("role_id", role_id)
                    .execute()
                    .data
                )
                return [r.get("client_id") for r in rows if r.get("client_id")]
            except Exception:
                return []

        admin_clients = get_client_ids_for_role(admin_role_id)
        if admin_clients:
            if allowed_ids is not None:
                admin_clients = [cid for cid in admin_clients if str(cid) in allowed_ids]
            if admin_clients:
                return {"role_key": "admin", "client_ids": admin_clients}

        subscriber_clients = get_client_ids_for_role(subscriber_role_id)
        if subscriber_clients:
            if allowed_ids is not None:
                subscriber_clients = [cid for cid in subscriber_clients if str(cid) in allowed_ids]
            if subscriber_clients:
                return {"role_key": "subscriber", "client_ids": subscriber_clients}

        # Fallback: read from Supabase Auth user_metadata if RBAC tables not configured
        try:
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
        except Exception:
            pass

        # Default if no assignments found anywhere
        return {"role_key": "subscriber", "client_ids": []}
    except HTTPException:
        raise
    except Exception as e:
        return HTMLResponse(status_code=500, content="Failed to fetch user assignments")

@router.post("/users/update")
async def update_user_roles(request: Request, admin: Dict[str, Any] = Depends(get_admin_user)):
    """Update a user's role assignments (platform super_admin or tenant roles)."""
    try:
        data = await request.json()
        user_id = (data.get("user_id") or "").strip()
        role_key = (data.get("role_key") or "").strip()
        client_ids_raw = data.get("client_ids") or []
        if isinstance(client_ids_raw, str):
            client_ids_raw = [client_ids_raw]
        client_ids = [str(cid) for cid in client_ids_raw if cid]

        if not user_id or role_key not in ("super_admin", "admin", "subscriber"):
            raise HTTPException(status_code=400, detail="Invalid payload")
        if role_key in ("admin","subscriber") and not client_ids:
            raise HTTPException(status_code=400, detail="At least one client_id is required for this role")

        scoped_ids = get_scoped_client_ids(admin)
        if scoped_ids is not None:
            allowed_ids = {str(cid) for cid in scoped_ids}
            if role_key == "super_admin":
                raise HTTPException(status_code=403, detail="Insufficient permissions to assign super admin role")
            invalid_ids = [cid for cid in client_ids if cid not in allowed_ids]
            if invalid_ids:
                raise HTTPException(status_code=403, detail="One or more client IDs are not accessible to this admin")

        await supabase_manager.initialize()
        admin_client = supabase_manager.admin_client

        # Helpers
        def seed_core_roles(client):
            try:
                core = [
                    {"key": "super_admin", "scope": "platform", "description": "Platform-wide administrator"},
                    {"key": "admin", "scope": "tenant", "description": "Tenant administrator"},
                    {"key": "subscriber", "scope": "tenant", "description": "Use-only role"},
                ]
                for r in core:
                    client.table("roles").upsert(r, on_conflict="key").execute()
            except Exception:
                pass

        def get_role_id(role_key_local: str) -> Optional[str]:
            try:
                row = (
                    admin_client.table("roles").select("id,key").eq("key", role_key_local).single().execute().data
                )
                if not row:
                    seed_core_roles(admin_client)
                    row = (
                        admin_client.table("roles").select("id,key").eq("key", role_key_local).single().execute().data
                    )
                return row.get("id") if row else None
            except Exception:
                return None

        # Update assignments using RBAC tables if available; otherwise fallback to Auth user_metadata
        try:
            if role_key == "super_admin":
                sa_id = get_role_id("super_admin")
                if sa_id:
                    admin_client.table("platform_role_memberships").upsert({
                        "user_id": user_id,
                        "role_id": sa_id,
                    }).execute()
                    return HTMLResponse(status_code=200, content="Updated")
                # Fallback to metadata
                raise RuntimeError("RBAC roles not present")
            else:
                # For tenant roles, reset existing admin/subscriber memberships and apply new ones
                admin_id = get_role_id("admin")
                sub_id = get_role_id("subscriber")
                target_role_id = admin_id if role_key == "admin" else sub_id
                if target_role_id:
                    try:
                        if admin_id:
                            admin_client.table("tenant_memberships").delete().eq("user_id", user_id).eq("role_id", admin_id).execute()
                    except Exception:
                        pass
                    try:
                        if sub_id:
                            admin_client.table("tenant_memberships").delete().eq("user_id", user_id).eq("role_id", sub_id).execute()
                    except Exception:
                        pass
                    for cid in client_ids:
                        admin_client.table("tenant_memberships").upsert({
                            "user_id": user_id,
                            "client_id": cid,
                            "role_id": target_role_id,
                            "status": "active",
                        }).execute()
                    return HTMLResponse(status_code=200, content="Updated")
                # Fallback to metadata
                raise RuntimeError("RBAC roles not present")
        except Exception:
            # Fallback: store assignments in Supabase Auth user_metadata
            from app.config import settings
            import httpx
            headers = {
                'apikey': settings.supabase_service_role_key,
                'Authorization': f'Bearer {settings.supabase_service_role_key}',
                'Content-Type': 'application/json',
            }
            if role_key == 'super_admin':
                meta_update = {
                    'user_metadata': {
                        'platform_role': 'super_admin',
                        'tenant_assignments': None
                    }
                }
            else:
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
                r = await client.patch(
                    f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                    headers=headers,
                    json=meta_update
                )
                if r.status_code not in (200, 201):
                    # Try PUT as a fallback
                    r = await client.put(
                        f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                        headers=headers,
                        json=meta_update
                    )
            if r.status_code in (200, 201):
                return HTMLResponse(status_code=200, content="Updated")
            else:
                return HTMLResponse(status_code=500, content="Failed to update user")
    except HTTPException:
        raise
    except Exception as e:
        return HTMLResponse(status_code=500, content="Failed to update user")

@router.post("/users/set-password")
async def set_user_password(request: Request, admin: Dict[str, Any] = Depends(get_admin_user)):
    """Set a Supabase Auth password for a user (admin operation)."""
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
    # Non-superadmin users go straight to their sidekicks page
    if admin_user.get("role") != "superadmin":
        return RedirectResponse(url="/admin/agents", status_code=302)

    # Detect mobile devices and redirect to Sidekicks page
    user_agent = request.headers.get("user-agent", "").lower()
    is_mobile = any(mobile_keyword in user_agent for mobile_keyword in [
        "mobile", "android", "iphone", "ipad", "ipod", "blackberry",
        "windows phone", "opera mini", "iemobile"
    ])

    if is_mobile:
        return RedirectResponse(url="/admin/agents", status_code=302)

    summary = await get_system_summary(admin_user)

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "summary": summary,
        "user": admin_user
    })

@router.get("/clients", response_class=HTMLResponse)
async def clients_list(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Client management page"""
    # Adventurer users don't have multi-client access; send them to sidekicks
    if admin_user.get("is_adventurer_only"):
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

        # Fetch top-level DB columns not in the Pydantic model (stored as direct columns, not in settings)
        try:
            platform_sb = client_service.supabase
            extra_cols = platform_sb.table("clients").select(
                "descript_api_key, firecrawl_api_key, semrush_api_key, ahrefs_api_key, uses_platform_keys"
            ).eq("id", client_id).maybe_single().execute()
            if extra_cols.data:
                for col_name in ("descript_api_key", "firecrawl_api_key", "semrush_api_key", "ahrefs_api_key"):
                    if extra_cols.data.get(col_name):
                        client_dict[col_name] = extra_cols.data[col_name]
                # uses_platform_keys must always be set (even if False)
                if "uses_platform_keys" in extra_cols.data:
                    client_dict["uses_platform_keys"] = extra_cols.data["uses_platform_keys"]
        except Exception as e:
            logger.warning(f"Could not fetch extra API key columns for client {client_id}: {e}")

        # Get agents for this client
        agents = []
        try:
            agents = await agent_service.get_client_agents(client_id)
        except Exception as agent_err:
            logger.warning(f"Unable to load agents for client {client_id}: {agent_err}")

        # Masked API keys from connection manager
        masked_keys: Dict[str, Any] = {}
        try:
            connection_manager = get_connection_manager()
            api_keys = connection_manager.get_client_api_keys(uuid.UUID(client_id))
            for key, value in api_keys.items():
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

        wordpress_api_endpoint = f"https://{settings.domain_name}/api/v1/wordpress-sites/auth/validate"

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
    
    return templates.TemplateResponse("admin/agents.html", {
        "request": request,
        "agents": agents,
        "clients": clients,
        "user": admin_user,
        "can_create_sidekick": admin_user.get("can_create_sidekick", False),
        "provisioning_in_progress": False,
    })


@router.get("/wizard", response_class=HTMLResponse)
async def wizard_page(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Serve the wizard as a full page (for sidebar navigation)."""
    return templates.TemplateResponse("admin/wizard/wizard_page.html", {
        "request": request,
        "user": admin_user,
    })


@router.get("/wizard/modal", response_class=HTMLResponse)
async def wizard_modal(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Serve the wizard modal as an HTMX partial."""
    return templates.TemplateResponse("admin/wizard/wizard_modal.html", {
        "request": request,
        "user": admin_user,
    })


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
                "webhooks": agent.get("webhooks", {}),
                "tools_config": agent.get("tools_config", {}),
                "show_citations": agent.get("show_citations", True),
                "rag_results_limit": agent.get("rag_results_limit", 5),
                # Chat mode toggles
                "voice_chat_enabled": agent.get("voice_chat_enabled", True),
                "text_chat_enabled": agent.get("text_chat_enabled", True),
                "video_chat_enabled": agent.get("video_chat_enabled", False),
                "sound_settings": agent.get("sound_settings", {}),
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
                "webhooks": agent.webhooks,
                "tools_config": agent.tools_config or {},
                "show_citations": getattr(agent, 'show_citations', True),
                "rag_results_limit": getattr(agent, "rag_results_limit", 5),
                # Chat mode toggles
                "voice_chat_enabled": getattr(agent, "voice_chat_enabled", True),
                "text_chat_enabled": getattr(agent, "text_chat_enabled", True),
                "video_chat_enabled": getattr(agent, "video_chat_enabled", False),
                "sound_settings": getattr(agent, "sound_settings", {}),
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
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Knowledge Base management page"""
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
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Abilities (Tools) management page"""
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
        return JSONResponse([tool.dict() for tool in tools])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/asana/status")
async def admin_asana_status(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    service = get_asana_oauth_service()
    record = service.get_connection(client_id)
    if not record:
        return {"connected": False}

    extra = record.get("extra") or {}
    return {
        "connected": True,
        "updated_at": record.get("updated_at"),
        "expires_at": record.get("expires_at"),
        "user_gid": extra.get("gid"),
        "user_name": extra.get("name") or extra.get("email"),
        "workspaces": extra.get("workspaces"),
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


# ============================================================================
# Evernote OAuth Endpoints
# ============================================================================

def get_evernote_oauth_service():
    from app.services.evernote_oauth_service import EvernoteOAuthService
    from app.core.dependencies import get_client_service

    return EvernoteOAuthService(get_client_service())


@router.get("/api/clients/{client_id}/evernote/status")
async def admin_evernote_status(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    service = get_evernote_oauth_service()
    record = service.get_connection(client_id)
    if not record:
        return {"connected": False}
    return {
        "connected": True,
        "updated_at": record.get("updated_at"),
        "expires_at": record.get("expires_at"),
    }


@router.delete("/api/clients/{client_id}/evernote/disconnect")
async def admin_evernote_disconnect(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    service = get_evernote_oauth_service()
    service.disconnect(client_id)
    return {"success": True}


@router.get("/api/evernote/oauth/start")
async def admin_evernote_oauth_start(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="Unable to determine admin user ID for OAuth state.")

    service = get_evernote_oauth_service()
    try:
        authorization_url = service.build_authorization_url(client_id, user_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"authorization_url": authorization_url}


@router.get("/oauth/evernote/callback")
async def admin_evernote_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    from app.services.evernote_oauth_service import EvernoteOAuthError

    service = get_evernote_oauth_service()

    if error:
        return HTMLResponse(
            f"<p>Evernote returned an error: {error}</p>",
            status_code=400,
        )

    if not state:
        return HTMLResponse("<p>Missing state parameter.</p>", status_code=400)

    try:
        state_data = service.parse_state(state)
    except EvernoteOAuthError as exc:
        return HTMLResponse(f"<p>{exc}</p>", status_code=400)

    client_id = state_data.get("client_id")
    if not client_id:
        return HTMLResponse("<p>Invalid state payload: missing client reference.</p>", status_code=400)

    if not code:
        return HTMLResponse("<p>Missing authorization code.</p>", status_code=400)

    try:
        await service.exchange_code(client_id, code)
    except EvernoteOAuthError as exc:
        return HTMLResponse(f"<p>Failed to complete Evernote OAuth: {exc}</p>", status_code=400)

    success_markup = (
        "<script>"
        "if(window.opener){window.opener.postMessage('evernote-connected','*');}"
        "window.close();"
        "</script>"
        "<p>Evernote connected successfully. You can close this window.</p>"
    )
    return HTMLResponse(success_markup)


# ============================================================================
# Trello Auth Endpoints
# ============================================================================
# Trello uses API Key + User Token auth via a redirect-based authorize flow.
# The token is returned via the return_url as a URL fragment (#token=...).
# We serve a small HTML page that extracts the token from the fragment and
# POSTs it to our backend.

def get_trello_auth_service():
    from app.services.trello_auth_service import TrelloAuthService
    from app.core.dependencies import get_client_service

    return TrelloAuthService(get_client_service())


@router.get("/api/clients/{client_id}/trello/status")
async def admin_trello_status(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    service = get_trello_auth_service()
    record = service.get_connection(client_id)
    if not record:
        return {"connected": False}
    return {
        "connected": True,
        "member_name": record.get("member_name"),
        "updated_at": record.get("updated_at"),
    }


@router.delete("/api/clients/{client_id}/trello/disconnect")
async def admin_trello_disconnect(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    service = get_trello_auth_service()
    service.disconnect(client_id)
    return {"success": True}


@router.get("/api/trello/oauth/start")
async def admin_trello_oauth_start(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="Unable to determine admin user ID.")

    service = get_trello_auth_service()
    try:
        authorization_url = service.build_authorization_url(client_id, user_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"authorization_url": authorization_url}


@router.get("/oauth/trello/callback")
async def admin_trello_oauth_callback(request: Request):
    """Serve a small HTML page that extracts the token from the URL fragment."""
    # Trello puts the token in the fragment (#token=...), which the browser
    # doesn't send to the server. So we serve a page with JS that reads the
    # fragment and POSTs it to our token-save endpoint.
    html = """<!DOCTYPE html>
<html><head><title>Connecting Trello...</title></head>
<body>
<p id="status">Completing Trello connection...</p>
<script>
(function(){
  var hash = window.location.hash.substring(1);
  var params = new URLSearchParams(hash);
  var token = params.get('token');
  var qs = new URLSearchParams(window.location.search);
  var state = qs.get('state');
  if(!token){
    document.getElementById('status').textContent = 'No token received from Trello.';
    return;
  }
  fetch('/admin/api/trello/oauth/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    credentials: 'include',
    body: JSON.stringify({token: token, state: state})
  }).then(function(resp){
    if(resp.ok){
      if(window.opener){ window.opener.postMessage('trello-connected','*'); }
      document.getElementById('status').textContent = 'Trello connected! You can close this window.';
      window.close();
    } else {
      resp.text().then(function(t){ document.getElementById('status').textContent = 'Error: ' + t; });
    }
  }).catch(function(err){
    document.getElementById('status').textContent = 'Error: ' + err.message;
  });
})();
</script>
</body></html>"""
    return HTMLResponse(html)


@router.post("/api/trello/oauth/save")
async def admin_trello_oauth_save(
    request: Request,
):
    """Receive the Trello token from the callback page JS."""
    from app.services.trello_auth_service import TrelloAuthError

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    token = body.get("token", "").strip()
    state = body.get("state", "").strip()

    if not token:
        raise HTTPException(status_code=400, detail="Missing token.")
    if not state:
        raise HTTPException(status_code=400, detail="Missing state.")

    service = get_trello_auth_service()
    try:
        state_data = service.parse_state(state)
    except TrelloAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    client_id = state_data.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="Invalid state payload.")

    # Optionally verify the token by fetching member info
    member_name = None
    try:
        from app.services.trello_service import TrelloClient
        tc = TrelloClient(service.api_key, token)
        me = await tc.get_me()
        member_name = me.get("fullName") or me.get("username")
    except Exception as exc:
        logger.warning("Could not verify Trello token: %s", exc)

    try:
        service.store_token(client_id, token, member_name=member_name)
    except TrelloAuthError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"success": True, "member_name": member_name}


# ============================================================================
# Notion Auth Endpoints
# ============================================================================
# Notion uses standard OAuth 2.0 Authorization Code Grant.
# The callback receives a `code` query param which is exchanged server-side
# for an access token (tokens never expire).

def get_notion_auth_service():
    from app.services.notion_auth_service import NotionAuthService
    from app.core.dependencies import get_client_service

    return NotionAuthService(get_client_service())


@router.get("/api/clients/{client_id}/notion/status")
async def admin_notion_status(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    service = get_notion_auth_service()
    record = service.get_connection(client_id)
    if not record:
        return {"connected": False}
    return {
        "connected": True,
        "workspace_name": record.get("workspace_name"),
        "updated_at": record.get("updated_at"),
    }


@router.delete("/api/clients/{client_id}/notion/disconnect")
async def admin_notion_disconnect(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    service = get_notion_auth_service()
    service.disconnect(client_id)
    return {"success": True}


@router.get("/api/notion/oauth/start")
async def admin_notion_oauth_start(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    ensure_client_access(client_id, admin_user)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="Unable to determine admin user ID.")

    service = get_notion_auth_service()
    try:
        authorization_url = service.build_authorization_url(client_id, user_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"authorization_url": authorization_url}


@router.get("/oauth/notion/callback")
async def admin_notion_oauth_callback(
    request: Request,
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    """Handle the Notion OAuth callback (Authorization Code Grant)."""
    from app.services.notion_auth_service import NotionAuthError

    if error:
        html = f"""<!DOCTYPE html>
<html><head><title>Notion Connection Failed</title></head>
<body><p>Notion authorization failed: {error}</p>
<script>setTimeout(function(){{ window.close(); }}, 3000);</script>
</body></html>"""
        return HTMLResponse(html)

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter.")

    service = get_notion_auth_service()

    try:
        state_data = service.parse_state(state)
    except NotionAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    client_id = state_data.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="Invalid state payload.")

    try:
        token_data = await service.exchange_code(code)
    except NotionAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    access_token = token_data.get("access_token", "")
    workspace_name = token_data.get("workspace_name")
    workspace_id = token_data.get("workspace_id")
    bot_id = token_data.get("bot_id")

    if not access_token:
        raise HTTPException(status_code=400, detail="No access token received from Notion.")

    try:
        service.store_connection(
            client_id,
            access_token,
            workspace_name=workspace_name,
            workspace_id=workspace_id,
            bot_id=bot_id,
        )
    except NotionAuthError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    html = """<!DOCTYPE html>
<html><head><title>Notion Connected</title></head>
<body>
<p id="status">Notion connected! You can close this window.</p>
<script>
if(window.opener){ window.opener.postMessage('notion-connected','*'); }
setTimeout(function(){ window.close(); }, 2000);
</script>
</body></html>"""
    return HTMLResponse(html)


# ============================================================================
# HelpScout OAuth Endpoints
# ============================================================================
# NOTE: HelpScout credentials and tokens are stored in the PLATFORM database
# in the client_helpscout_connections table (which has oauth_client_id and
# oauth_client_secret columns for credentials, plus token fields).

HELPSCOUT_AUTH_URL = "https://secure.helpscout.net/authentication/authorizeClientApplication"
HELPSCOUT_TOKEN_URL = "https://api.helpscout.net/v2/oauth2/token"


def _get_platform_supabase():
    """Get the platform Supabase client."""
    from app.integrations.supabase_client import supabase_manager
    return supabase_manager.admin_client


@router.post("/api/helpscout/credentials")
async def admin_save_helpscout_credentials(
    request: Request,
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Save HelpScout OAuth app credentials for a client."""
    ensure_client_access(client_id, admin_user)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    oauth_client_id = body.get("oauth_client_id", "").strip()
    oauth_client_secret = body.get("oauth_client_secret", "").strip()

    if not oauth_client_id or not oauth_client_secret:
        raise HTTPException(status_code=400, detail="Both oauth_client_id and oauth_client_secret are required")

    platform_sb = _get_platform_supabase()

    # Check if record exists
    try:
        existing = platform_sb.table("client_helpscout_connections").select("client_id").eq("client_id", client_id).limit(1).execute()

        if existing.data:
            # Update existing record with new credentials
            platform_sb.table("client_helpscout_connections").update({
                "oauth_client_id": oauth_client_id,
                "oauth_client_secret": oauth_client_secret,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("client_id", client_id).execute()
        else:
            # Insert new record - access_token needs a placeholder since it has NOT NULL constraint
            platform_sb.table("client_helpscout_connections").insert({
                "client_id": client_id,
                "oauth_client_id": oauth_client_id,
                "oauth_client_secret": oauth_client_secret,
                "access_token": "",  # Placeholder - will be replaced after OAuth
                "updated_at": datetime.utcnow().isoformat(),
            }).execute()
    except Exception as exc:
        logger.error(f"Failed to store HelpScout credentials: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to store credentials: {exc}")

    return {"success": True}


@router.get("/api/helpscout/connection")
async def admin_helpscout_status(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Check HelpScout connection status for a client."""
    ensure_client_access(client_id, admin_user)

    platform_sb = _get_platform_supabase()

    has_tokens = False
    has_credentials = False
    connection_info = {}

    try:
        result = platform_sb.table("client_helpscout_connections").select("*").eq("client_id", client_id).limit(1).execute()
        if result.data:
            row = result.data[0]
            has_tokens = bool(row.get("access_token"))
            has_credentials = bool(row.get("oauth_client_id"))
            connection_info = {
                "updated_at": row.get("updated_at"),
                "expires_at": row.get("expires_at"),
            }
    except Exception as exc:
        logger.warning(f"Failed to check HelpScout status: {exc}")

    return {
        "connected": has_tokens,
        "has_credentials": has_credentials,
        **connection_info,
    }


@router.delete("/api/helpscout/connection")
async def admin_disconnect_helpscout(
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Disconnect HelpScout for a client (clears tokens but keeps credentials)."""
    ensure_client_access(client_id, admin_user)

    platform_sb = _get_platform_supabase()

    try:
        # Clear tokens but keep oauth credentials
        platform_sb.table("client_helpscout_connections").update({
            "access_token": None,
            "refresh_token": None,
            "token_type": None,
            "expires_at": None,
            "extra": None,
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("client_id", client_id).execute()
    except Exception as exc:
        logger.warning(f"Failed to disconnect HelpScout: {exc}")

    return {"success": True}


@router.get("/api/helpscout/oauth/start")
async def admin_helpscout_oauth_start(
    request: Request,
    client_id: str = Query(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Start HelpScout OAuth flow."""
    ensure_client_access(client_id, admin_user)

    platform_sb = _get_platform_supabase()

    # Get stored OAuth credentials from platform DB
    oauth_client_id = None

    try:
        result = platform_sb.table("client_helpscout_connections").select("oauth_client_id").eq("client_id", client_id).limit(1).execute()
        if result.data:
            oauth_client_id = result.data[0].get("oauth_client_id")
    except Exception as exc:
        logger.warning(f"Failed to get HelpScout credentials: {exc}")

    if not oauth_client_id:
        raise HTTPException(status_code=400, detail="HelpScout OAuth credentials not configured. Please enter your App ID and Secret first.")

    # Build state parameter
    import secrets
    import hashlib
    import hmac
    import time

    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")
    nonce = secrets.token_hex(8)
    timestamp = str(int(time.time()))
    raw = f"{client_id}:{user_id}:{timestamp}:{nonce}"
    signature = hmac.new(settings.secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()
    state = base64.urlsafe_b64encode(f"{raw}:{signature}".encode()).decode()

    # Build redirect URI
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    redirect_uri = f"{scheme}://{host}/admin/oauth/helpscout/callback"

    # Build authorization URL
    from urllib.parse import urlencode
    params = {
        "client_id": oauth_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    authorization_url = f"{HELPSCOUT_AUTH_URL}?{urlencode(params)}"

    return {"authorization_url": authorization_url}


@router.get("/oauth/helpscout/callback")
async def admin_helpscout_oauth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Handle HelpScout OAuth callback."""
    import hashlib
    import hmac
    import time
    import httpx

    if error:
        return HTMLResponse(f"<p>HelpScout returned an error: {error}</p>", status_code=400)

    if not state:
        return HTMLResponse("<p>Missing state parameter.</p>", status_code=400)

    # Parse and validate state
    try:
        decoded = base64.urlsafe_b64decode(state.encode()).decode()
        parts = decoded.split(":")
        if len(parts) != 5:
            raise ValueError("Invalid state format")
        client_id, user_id, timestamp_str, nonce, signature = parts

        raw = f"{client_id}:{user_id}:{timestamp_str}:{nonce}"
        expected_sig = hmac.new(settings.secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, signature):
            raise ValueError("State signature mismatch")

        if time.time() - int(timestamp_str) > 900:
            raise ValueError("State expired")

    except Exception as exc:
        return HTMLResponse(f"<p>Invalid state parameter: {exc}</p>", status_code=400)

    if not code:
        return HTMLResponse("<p>Missing authorization code.</p>", status_code=400)

    platform_sb = _get_platform_supabase()

    # Get stored OAuth credentials from platform DB
    oauth_client_id = None
    oauth_client_secret = None

    try:
        result = platform_sb.table("client_helpscout_connections").select("oauth_client_id, oauth_client_secret").eq("client_id", client_id).limit(1).execute()
        if result.data:
            oauth_client_id = result.data[0].get("oauth_client_id")
            oauth_client_secret = result.data[0].get("oauth_client_secret")
    except Exception as exc:
        return HTMLResponse(f"<p>Failed to retrieve OAuth credentials: {exc}</p>", status_code=500)

    if not oauth_client_id or not oauth_client_secret:
        return HTMLResponse("<p>HelpScout OAuth credentials not found.</p>", status_code=400)

    # Build redirect URI (must match the one used in authorization)
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    redirect_uri = f"{scheme}://{host}/admin/oauth/helpscout/callback"

    # Exchange code for tokens
    token_payload = {
        "grant_type": "authorization_code",
        "client_id": oauth_client_id,
        "client_secret": oauth_client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as http_client:
            response = await http_client.post(HELPSCOUT_TOKEN_URL, data=token_payload)

        if response.status_code >= 400:
            error_detail = response.text
            try:
                error_json = response.json()
                error_detail = error_json.get("error_description") or error_json.get("error") or error_detail
            except Exception:
                pass
            return HTMLResponse(f"<p>Failed to exchange code: {error_detail}</p>", status_code=400)

        tokens = response.json()
    except Exception as exc:
        return HTMLResponse(f"<p>Failed to exchange authorization code: {exc}</p>", status_code=500)

    # Store tokens
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in")

    expires_at = None
    if isinstance(expires_in, (int, float)):
        expires_at = (datetime.utcnow() + timedelta(seconds=int(expires_in))).isoformat()

    # Update tokens in platform DB (keep existing oauth credentials)
    token_update = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": tokens.get("token_type"),
        "expires_at": expires_at,
        "extra": tokens,
        "updated_at": datetime.utcnow().isoformat(),
    }

    try:
        platform_sb.table("client_helpscout_connections").update(token_update).eq("client_id", client_id).execute()
    except Exception as exc:
        return HTMLResponse(f"<p>Failed to store HelpScout tokens: {exc}</p>", status_code=500)

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
    return JSONResponse({"success": True})



@router.get("/api/usage/{client_id}/{agent_id}")
async def admin_get_agent_usage(
    client_id: str,
    agent_id: str,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Return current-period usage for a single agent in the shape the
    agent_detail.html JS expects (voice/text/embedding blocks plus the new
    LiveKit per-model counters)."""
    from app.services.usage_tracking import usage_tracking_service

    ensure_client_access(client_id, admin_user)

    try:
        await usage_tracking_service.initialize()
        record = await usage_tracking_service.get_agent_usage_for_period(client_id, agent_id)
    except Exception as exc:
        logger.error("Failed to fetch agent usage for %s/%s: %s", client_id, agent_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch usage") from exc

    def _quota_block(used: int, limit: int) -> Dict[str, Any]:
        used = int(used or 0)
        limit = int(limit or 0)
        percent = (used / limit * 100) if limit > 0 else 0.0
        return {
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used) if limit > 0 else 0,
            "percent_used": round(percent, 1),
            "is_exceeded": limit > 0 and used >= limit,
            "is_warning": limit > 0 and percent >= 80,
        }

    voice_used = int(record.get("voice_seconds_used", 0) or 0)
    voice_limit = int(record.get("voice_seconds_limit", 0) or 0)
    voice_percent = (voice_used / voice_limit * 100) if voice_limit > 0 else 0.0

    voice_block = {
        "used_seconds": voice_used,
        "limit_seconds": voice_limit,
        "used_minutes": round(voice_used / 60, 1),
        "limit_minutes": round(voice_limit / 60, 1),
        "percent_used": round(voice_percent, 1),
        "is_exceeded": voice_limit > 0 and voice_used >= voice_limit,
        "is_warning": voice_limit > 0 and voice_percent >= 80,
    }

    return JSONResponse({
        "success": True,
        "period_start": record.get("period_start"),
        "session_count": int(record.get("session_count", 0) or 0),
        "last_session_at": record.get("last_session_at"),
        "voice": voice_block,
        "text": _quota_block(
            record.get("text_messages_used", 0),
            record.get("text_messages_limit", 0),
        ),
        "embedding": _quota_block(
            record.get("embedding_chunks_used", 0),
            record.get("embedding_chunks_limit", 0),
        ),
        "models": {
            "llm_input_tokens": int(record.get("llm_input_tokens", 0) or 0),
            "llm_output_tokens": int(record.get("llm_output_tokens", 0) or 0),
            "llm_cached_input_tokens": int(record.get("llm_cached_input_tokens", 0) or 0),
            "tts_characters": int(record.get("tts_characters", 0) or 0),
            "tts_audio_seconds": float(record.get("tts_audio_seconds", 0) or 0),
            "stt_audio_seconds": float(record.get("stt_audio_seconds", 0) or 0),
        },
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
    logger.info(f"🔍 agent_detail START: client={client_id}, agent={agent_slug}")
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

            # Debug: Log raw agent data from service
            if agent:
                if isinstance(agent, dict):
                    raw_vs = agent.get("voice_settings", {})
                    logger.info(f"🔍 RAW agent voice_settings type: {type(raw_vs)}")
                    if isinstance(raw_vs, dict):
                        logger.info(f"🔍 RAW avatar_provider: {raw_vs.get('avatar_provider')}")
                        logger.info(f"🔍 RAW video_provider: {raw_vs.get('video_provider')}")
                    else:
                        logger.info(f"🔍 RAW voice_settings (not dict): {raw_vs}")

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
                "webhooks": agent.get("webhooks", {}),
                "tools_config": agent.get("tools_config", {}),
                "show_citations": agent.get("show_citations", True),
                "rag_results_limit": agent.get("rag_results_limit", 5),
                # Chat mode toggles
                "voice_chat_enabled": agent.get("voice_chat_enabled", True),
                "text_chat_enabled": agent.get("text_chat_enabled", True),
                "video_chat_enabled": agent.get("video_chat_enabled", False),
                "sound_settings": agent.get("sound_settings", {}),
                "email_address": agent.get("email_address") or "",
                "client_id": client_id,
                "client_name": client.get("name", "Unknown") if isinstance(client, dict) else (getattr(client, 'name', 'Unknown') if client else "Unknown")
            }
            # Debug: Log sound_settings being passed to template (dict format)
            logger.info(f"🔊 [agent_detail-dict] sound_settings for template: {agent_data.get('sound_settings')}")
        else:
            # Object format - original service
            # Convert voice_settings Pydantic model to dict for template compatibility
            vs = agent.voice_settings
            if hasattr(vs, 'dict'):
                voice_settings_dict = vs.dict()
            elif hasattr(vs, 'model_dump'):
                voice_settings_dict = vs.model_dump()
            elif isinstance(vs, dict):
                voice_settings_dict = vs
            else:
                voice_settings_dict = {}

            # Same for webhooks
            wh = agent.webhooks
            if hasattr(wh, 'dict'):
                webhooks_dict = wh.dict()
            elif hasattr(wh, 'model_dump'):
                webhooks_dict = wh.model_dump()
            elif isinstance(wh, dict):
                webhooks_dict = wh
            else:
                webhooks_dict = {}

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
                "voice_settings": voice_settings_dict,
                "webhooks": webhooks_dict,
                "tools_config": agent.tools_config or {},
                "show_citations": getattr(agent, 'show_citations', True),
                "rag_results_limit": getattr(agent, "rag_results_limit", 5),
                # Chat mode toggles
                "voice_chat_enabled": getattr(agent, "voice_chat_enabled", True),
                "text_chat_enabled": getattr(agent, "text_chat_enabled", True),
                "video_chat_enabled": getattr(agent, "video_chat_enabled", False),
                "sound_settings": getattr(agent, "sound_settings", {}),
                "email_address": getattr(agent, "email_address", "") or "",
                "client_id": client_id,
                "client_name": client.name if client else "Unknown"
            }

        # Debug: Log sound_settings being passed to template
        logger.info(f"🔊 [agent_detail] sound_settings for template: {agent_data.get('sound_settings')}")
        logger.info(f"[admin] Rendering agent_detail for {client_id}/{agent_slug}")

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

            # Debug: Log voice_settings before cleaning
            vs_debug = agent_data.get("voice_settings", {})
            logger.info(f"🔍 DEBUG agent_data['voice_settings'] type: {type(vs_debug)}")
            if isinstance(vs_debug, dict):
                logger.info(f"🔍 DEBUG voice_settings keys: {list(vs_debug.keys())}")
                logger.info(f"🔍 DEBUG avatar_provider: {vs_debug.get('avatar_provider')}")
                logger.info(f"🔍 DEBUG video_provider: {vs_debug.get('video_provider')}")
            else:
                logger.info(f"🔍 DEBUG voice_settings value: {vs_debug}")

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

            # Debug: Log sound_settings after cleaning
            logger.info(f"🔊 [agent_detail] agent_data.sound_settings: {agent_data.get('sound_settings')}")
            logger.info(f"🔊 [agent_detail] cleaned_agent_data.sound_settings: {cleaned_agent_data.get('sound_settings')}")

            # Debug: Log cleaned voice_settings
            vs_cleaned = cleaned_agent_data.get("voice_settings", {})
            logger.info(f"🔍 DEBUG cleaned voice_settings type: {type(vs_cleaned)}")
            if isinstance(vs_cleaned, dict):
                logger.info(f"🔍 DEBUG cleaned avatar_provider: {vs_cleaned.get('avatar_provider')}")
                logger.info(f"🔍 DEBUG cleaned video_provider: {vs_cleaned.get('video_provider')}")
            
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

            template_data = {
                "request": request,
                "agent": cleaned_agent_data,  # Use cleaned data
                "client": client,
                "user": admin_user,
                "disable_stats_poll": True,
                "latest_config": latest_config,
                "latest_config_json": latest_config_json,
                "has_config_updates": bool(agent_config) if agent_config else False
            }

            # Final debug before rendering template
            final_vs = cleaned_agent_data.get("voice_settings", {})
            logger.info(f"✅ FINAL template_data['agent']['voice_settings']: {final_vs}")
            logger.info(f"✅ FINAL avatar_provider: {final_vs.get('avatar_provider') if isinstance(final_vs, dict) else 'NOT_DICT'}")
            logger.info(f"✅ FINAL video_provider: {final_vs.get('video_provider') if isinstance(final_vs, dict) else 'NOT_DICT'}")

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
            inworld_voice_id = voice_settings.get('voice_id', 'Ashley') if tts_provider == 'inworld' else provider_config.get('inworld_voice_id', 'Ashley')
            inworld_model = voice_settings.get('model', 'inworld-tts-1.5-max') if tts_provider == 'inworld' else 'inworld-tts-1.5-max'
            
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
                                            <option value="inworld">Inworld</option>
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

                                <!-- Inworld TTS Settings -->
                                <div id="tts-inworld" class="provider-section">
                                    <h3 class="text-lg font-semibold text-white mb-3">Inworld TTS Settings</h3>
                                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Voice</label>
                                            <input type="text" name="inworld_voice_id" value="{inworld_voice_id}" placeholder="Ashley" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                            <p class="text-xs text-gray-400 mt-1">Inworld voice name (e.g. Ashley, Hades, Edward).</p>
                                        </div>
                                        <div>
                                            <label class="block text-sm font-medium text-gray-300 mb-2">Model</label>
                                            <select name="inworld_model" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                                <option value="inworld-tts-1" {"selected" if inworld_model == "inworld-tts-1" else ""}>inworld-tts-1</option>
                                                <option value="inworld-tts-1-max" {"selected" if inworld_model == "inworld-tts-1-max" else ""}>inworld-tts-1-max</option>
                                                <option value="inworld-tts-1.5-mini" {"selected" if inworld_model == "inworld-tts-1.5-mini" else ""}>inworld-tts-1.5-mini</option>
                                                <option value="inworld-tts-1.5-max" {"selected" if inworld_model == "inworld-tts-1.5-max" else ""}>inworld-tts-1.5-max</option>
                                            </select>
                                            <p class="text-xs text-gray-400 mt-1">Default: inworld-tts-1.5-max</p>
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
                            {{ value: 'llama3.1-8b', label: 'Llama 3.1 8B (Cerebras)' }},
                            {{ value: 'llama-3.3-70b', label: 'Llama 3.3 70B (Cerebras)' }},
                            {{ value: 'llama-4-scout-17b-16e-instruct', label: 'Llama 4 Scout 17B Instruct (Cerebras)' }},
                            {{ value: 'llama-4-maverick-17b-128e-instruct', label: 'Llama 4 Maverick 17B Instruct (preview, Cerebras)' }},
                            {{ value: 'qwen-3-32b', label: 'Qwen 3 32B (Cerebras)' }},
                            {{ value: 'qwen-3-235b-a22b-instruct-2507', label: 'Qwen 3 235B Instruct (preview, Cerebras)' }},
                            {{ value: 'qwen-3-235b-a22b-thinking-2507', label: 'Qwen 3 235B Thinking (preview, Cerebras)' }},
                            {{ value: 'qwen-3-coder-480b', label: 'Qwen 3 Coder 480B (preview, Cerebras)' }},
                            {{ value: 'gpt-oss-120b', label: 'GPT-OSS 120B (preview, Cerebras)' }}
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
            template_data = {
                "request": request,
                "agent": cleaned_agent_data,
                "client": client,
                "user": admin_user,
                "latest_config": latest_config,
                "latest_config_json": latest_config_json,
                "has_config_updates": bool(agent_config) if agent_config else False
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
    """System health partial for HTMX updates"""
    # Get clients and create mock health status
    from app.core.dependencies import get_client_service
    client_service = get_client_service()
    scoped_ids = get_scoped_client_ids(admin_user)

    health_statuses = []
    try:
        clients = await client_service.get_all_clients()
        if scoped_ids is not None:
            allowed = {str(cid) for cid in scoped_ids}
            clients = [c for c in clients if str(getattr(c, 'id', '')) in allowed]

        # Create health status for each client (mocked for now)
        for client in clients[:5]:  # Limit to first 5 for dashboard
            health_statuses.append({
                "client_id": client.id,
                "client_name": client.name,
                "healthy": client.active,  # Use active status as health indicator
                "checks": {
                    "api": {"healthy": True},
                    "database": {"healthy": True},
                    "livekit": {"healthy": client.settings.livekit is not None if client.settings else False}
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
    """Return the agent preview modal that embeds the production embed UI in an iframe.

    Phase 2: This handler returns the modal HTML IMMEDIATELY without waiting for
    Supabase session token generation. The modal's inline script fetches tokens
    via /admin/agents/preview/{client_id}/{agent_slug}/session-tokens in the
    background and posts them to the iframe via postMessage when ready.
    """
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

        # Respect the original request scheme/host (include port in dev) for the embed iframe
        iframe_src = f"{scheme}://{netloc}/embed/{client_id}/{agent_slug}?theme=dark&source=admin"
        token_url = f"/admin/agents/preview/{client_id}/{agent_slug}/session-tokens"

        modal_html = f"""
        <div class=\"fixed inset-0 bg-black/80 flex items-center justify-center z-50\">
          <div class=\"bg-dark-surface border border-dark-border rounded-lg w-full max-w-4xl h-[90vh] flex flex-col\">
            <div class=\"flex items-center justify-between p-3 border-b border-dark-border\">
              <h3 class=\"text-dark-text text-sm\">Preview Sidekick</h3>
              <button class=\"px-3 py-1 text-sm border border-dark-border rounded\" hx-on:click=\"document.getElementById('modal-container').innerHTML=''\">Close</button>
            </div>
            <div class=\"flex-1\">
              <iframe id=\"embedFrame\" src=\"{iframe_src}\" allow=\"microphone; camera\" referrerpolicy=\"strict-origin-when-cross-origin\" style=\"border:0;width:100%;height:100%\"></iframe>
            </div>
          </div>
          <script>
            (function(){{
              try {{
                var iframe = document.getElementById('embedFrame');
                if (!iframe) return;

                // Phase 2: Fetch session tokens in parallel with iframe load,
                // then postMessage as soon as both are ready (whichever finishes last).
                var iframeReady = false;
                var pendingTokens = null;

                function postTokens() {{
                  if (!iframeReady || !pendingTokens) return;
                  try {{
                    iframe.contentWindow.postMessage(pendingTokens, '*');
                  }} catch (e) {{ console.warn('[preview->embed] token post failed', e); }}
                }}

                iframe.addEventListener('load', function() {{
                  iframeReady = true;
                  postTokens();
                }});

                fetch('{token_url}', {{ credentials: 'include' }})
                  .then(function(res) {{ return res.json(); }})
                  .then(function(data) {{
                    if (!data || data.error) {{
                      console.warn('[preview->embed] token fetch failed', data && data.error);
                      // Fallback: use parent's admin Supabase session
                      var sb = window.__adminSupabaseClient || null;
                      if (sb && sb.auth && sb.auth.getSession) {{
                        sb.auth.getSession().then(function(res) {{
                          var session = res && res.data && res.data.session;
                          if (session && session.access_token && session.refresh_token) {{
                            pendingTokens = {{ type: 'supabase-session', access_token: session.access_token, refresh_token: session.refresh_token }};
                            postTokens();
                          }}
                        }});
                      }}
                      return;
                    }}
                    pendingTokens = {{
                      type: 'supabase-session',
                      access_token: data.client_jwt,
                      refresh_token: null,
                      is_admin_preview: true,
                      client_user_id: data.client_user_id,
                      client_supabase_access_token: data.client_supabase_access_token,
                      client_supabase_refresh_token: data.client_supabase_refresh_token
                    }};
                    postTokens();
                  }})
                  .catch(function(err) {{ console.warn('[preview->embed] token fetch error', err); }});
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


@router.get("/agents/preview/{client_id}/{agent_slug}/session-tokens")
async def agent_preview_session_tokens(
    request: Request,
    client_id: str,
    agent_slug: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Phase 2: Generate client session tokens for the embed preview iframe.

    Called from the preview modal's inline script in parallel with the iframe
    load, so the modal HTML can be returned immediately without waiting for
    Supabase token generation.
    """
    try:
        ensure_client_or_global_access(client_id, admin_user)

        api_base = "http://127.0.0.1:8000"
        client_jwt = None
        client_user_id = None
        client_supabase_tokens = None
        admin_email = admin_user.get("email") if isinstance(admin_user, dict) else None

        # Call EnsureClientUser to get client JWT
        import httpx
        timeout = httpx.Timeout(20.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            platform_token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if not platform_token and admin_user:
                platform_token = admin_user.get("access_token", "")

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
                if ensure_response.status_code == 200:
                    ensure_data = ensure_response.json()
                    client_jwt = ensure_data.get("client_jwt")
                    client_user_id = ensure_data.get("client_user_id")
            except httpx.TimeoutException:
                logger.warning("ensure-client-user timed out in session-tokens")

        if client_id != "global" and admin_email:
            try:
                client_supabase_tokens = await generate_client_session_tokens(client_id, admin_email)
            except Exception as exc:
                logger.warning(f"generate_client_session_tokens failed: {exc}")

        return JSONResponse({
            "client_jwt": client_jwt,
            "client_user_id": client_user_id,
            "client_supabase_access_token": (client_supabase_tokens or {}).get("access_token") if client_supabase_tokens else None,
            "client_supabase_refresh_token": (client_supabase_tokens or {}).get("refresh_token") if client_supabase_tokens else None,
        })
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"agent_preview_session_tokens failed: {exc}", exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


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
<div class=\"fixed inset-0 z-50 flex items-center justify-center bg-black/50\">
  <div class=\"bg-dark-surface border border-dark-border rounded-lg p-4 max-w-lg w-full\">
    <h3 class=\"text-lg text-dark-text mb-3\">Copy Embed Code</h3>
    <textarea id=\"embedCode\" class=\"w-full bg-dark-elevated text-dark-text border border-dark-border rounded p-2\" rows=\"6\" readonly>""" + iframe + """</textarea>
    <div class=\"mt-3 flex gap-2\">
      <button class=\"btn-primary px-3 py-2 rounded text-sm\" onclick=\"var b=this; navigator.clipboard.writeText(document.getElementById('embedCode').value).then(function(){ b.disabled=true; var t=b.textContent; b.textContent='Copied!'; setTimeout(function(){ b.textContent=t; b.disabled=false; },1200); });\">Copy</button>
      <button class=\"px-3 py-2 rounded text-sm border border-dark-border\" hx-on:click=\"document.getElementById('modal-container').innerHTML=''\">Close</button>
    </div>
    <p class=\"text-dark-text-secondary text-xs mt-3\">Ensure your site origin is in this Sidekick's allowlist.</p>
  </div>
</div>
""")


@router.get("/monitoring", response_class=HTMLResponse)
async def monitoring_dashboard(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """System monitoring dashboard"""
    return templates.TemplateResponse("admin/monitoring.html", {
        "request": request,
        "user": admin_user
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


@router.post("/agents/{client_id}/{agent_slug}/upload-imx")
async def get_imx_upload_credentials(
    client_id: str,
    agent_slug: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """Return Supabase TUS upload credentials so the browser can do a
    resumable upload directly to Supabase Storage, bypassing both the
    Cloudflare proxy limit and the Supabase 50 MB standard upload cap."""

    ensure_client_or_global_access(client_id, admin_user)

    from app.core.dependencies import get_client_service
    client_service = get_client_service()
    client_sb = await client_service.get_client_supabase_client(client_id, auto_sync=False)
    if not client_sb:
        raise HTTPException(status_code=500, detail="Could not connect to client Supabase project")

    bucket_name = "avatars"
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    storage_file = f"imx/{client_id}/{agent_slug}_{timestamp}_{unique_id}.imx"

    try:
        # Ensure bucket exists with a 500 MB file size limit for IMX models
        bucket_opts = {"public": False, "file_size_limit": "500MB"}
        try:
            client_sb.storage.create_bucket(bucket_name, options=bucket_opts)
        except Exception:
            # Bucket already exists — update its file size limit
            try:
                client_sb.storage.update_bucket(bucket_name, options=bucket_opts)
            except Exception:
                pass
    except Exception:
        pass

    storage_path = f"supabase://{bucket_name}/{storage_file}"
    logger.info("Created TUS upload credentials for %s/%s -> %s", client_id, agent_slug, storage_path)

    return {
        "success": True,
        "tus_endpoint": f"{client_sb.supabase_url}/storage/v1/upload/resumable",
        "auth_token": client_sb.supabase_key,
        "bucket_name": bucket_name,
        "object_name": storage_file,
        "storage_path": storage_path,
    }


@router.post("/api/upload-avatar")
async def upload_avatar_image(
    request: Request,
    file: UploadFile = File(...),
    agent_id: str = Form(None),
    client_id: str = Form(None),
    upload_type: str = Form("avatar"),
    admin_user: Dict[str, Any] = Depends(get_admin_user),
):
    """
    Upload an avatar or starting image to Supabase Storage.
    Used for Ken Burns starting images, agent avatars, etc.
    Returns a signed URL that can be stored in agent settings.
    """
    if client_id:
        ensure_client_or_global_access(client_id, admin_user)

    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    try:
        contents = await file.read()
    except Exception as exc:
        logger.error("Failed to read uploaded avatar: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read uploaded file") from exc

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # Max 10MB for avatars/starting images
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image exceeds 10 MB limit")

    # Determine file extension
    suffix = ".png"
    if file.filename:
        original_suffix = Path(file.filename).suffix.lower()
        if original_suffix in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
            suffix = original_suffix
    elif "jpeg" in content_type or "jpg" in content_type:
        suffix = ".jpg"
    elif "webp" in content_type:
        suffix = ".webp"
    elif "gif" in content_type:
        suffix = ".gif"

    # Generate unique filename
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    filename = f"{upload_type}_{timestamp}_{unique_id}{suffix}"

    # Try to upload to Supabase Storage if client_id is provided
    if client_id:
        try:
            from app.core.dependencies import get_client_service
            client_service = get_client_service()
            client_sb = await client_service.get_client_supabase_client(client_id, auto_sync=False)

            if client_sb:
                bucket_name = "avatars"
                storage_path = f"{upload_type}/{client_id}/{filename}"

                # Try to create bucket if needed
                try:
                    client_sb.storage.create_bucket(bucket_name, options={"public": True})
                except Exception:
                    pass  # Bucket likely already exists

                # Upload file
                result = client_sb.storage.from_(bucket_name).upload(
                    path=storage_path,
                    file=contents,
                    file_options={"content-type": content_type or "image/png"}
                )

                # Get public URL
                public_url = client_sb.storage.from_(bucket_name).get_public_url(storage_path)

                logger.info(f"Avatar uploaded to Supabase Storage: {public_url}")
                return {
                    "success": True,
                    "url": public_url,
                    "filename": filename,
                    "storage": "supabase"
                }
        except Exception as e:
            logger.warning(f"Supabase storage upload failed, falling back to local: {e}")

    # Fallback: store locally in static/images/avatars
    AVATAR_STORAGE_DIR = Path("/app/app/static/images/avatars")
    AVATAR_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    destination = AVATAR_STORAGE_DIR / filename
    try:
        with destination.open("wb") as buffer:
            buffer.write(contents)
    except Exception as exc:
        logger.error("Failed to persist avatar '%s': %s", filename, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save uploaded image") from exc

    public_url = f"/static/images/avatars/{filename}"
    logger.info(f"Avatar uploaded to local storage: {public_url}")

    return {
        "success": True,
        "url": public_url,
        "filename": filename,
        "storage": "local"
    }


@router.post("/agents/{client_id}/{agent_slug}/toggle-email")
async def admin_toggle_email(
    client_id: str,
    agent_slug: str,
    request: Request,
):
    """Toggle email channel on/off for a sidekick.

    Expects JSON: {"enabled": true/false}
    When enabling, auto-reserves an @sidekickforge.com address.
    When disabling, releases the address with a 48-hour hold.
    """
    from app.admin.auth import get_admin_user
    admin_user = await get_admin_user(request)
    ensure_client_or_global_access(client_id, admin_user)

    try:
        data = await request.json()
        enabled = bool(data.get("enabled"))

        from app.core.dependencies import get_agent_service, get_client_service
        from app.models.client import EmailChannelSettings, ChannelSettings, TelegramChannelSettings
        from app.models.agent import AgentUpdate
        from app.services.email_address_service import email_address_service

        agent_service = get_agent_service()
        agent = await agent_service.get_agent(client_id, agent_slug)
        if not agent:
            return JSONResponse(status_code=404, content={"error": "Agent not found"})

        current_email = getattr(agent, "email_address", None)
        new_email = current_email

        if enabled and not current_email:
            # Reserve a new email address
            suggested = await email_address_service.suggest_address(agent_slug)
            reserved = await email_address_service.reserve(suggested, client_id, agent_slug)
            if not reserved:
                return JSONResponse(
                    status_code=409,
                    content={"error": f"Could not reserve {suggested}. Try a different slug."},
                )
            new_email = suggested
            logger.info(f"Reserved email {suggested} for {client_id}/{agent_slug}")

        elif not enabled and current_email:
            # Release the email address (48-hour hold)
            await email_address_service.release(current_email)
            new_email = None
            logger.info(f"Released email {current_email} for {client_id}/{agent_slug}")

        # Build updated channels with email toggle
        existing_tools = agent.tools_config or {}
        existing_channels = existing_tools.get("channels", {}) or {}

        # Preserve existing telegram config
        tg_raw = existing_channels.get("telegram", {})
        try:
            tg_cfg = TelegramChannelSettings(**tg_raw) if tg_raw else TelegramChannelSettings()
        except Exception:
            tg_cfg = TelegramChannelSettings()

        email_cfg = EmailChannelSettings(enabled=enabled)
        channels_obj = ChannelSettings(email=email_cfg, telegram=tg_cfg)

        tools_config = dict(existing_tools)
        tools_config["channels"] = channels_obj.dict()

        update_data = AgentUpdate(
            tools_config=tools_config,
            email_address=new_email,
        )
        update_data.channels = channels_obj

        updated = await agent_service.update_agent(client_id, agent_slug, update_data)

        if not updated:
            return JSONResponse(status_code=500, content={"error": "Failed to update agent"})

        return {
            "success": True,
            "enabled": enabled,
            "email_address": new_email or "",
        }

    except Exception as e:
        logger.error(f"Error toggling email for {client_id}/{agent_slug}: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


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
        logger.info(f"🔧 Agent update request: client={client_id}, agent={agent_slug}")
        logger.info(f"🔧 Update data keys: {list(data.keys())}")
        if "video_chat_enabled" in data:
            logger.info(f"🔧 video_chat_enabled={data.get('video_chat_enabled')}")
        if "voice_settings" in data:
            vs = data.get("voice_settings", {})
            logger.info(f"🔧 voice_settings.cartesia_emotions_enabled={vs.get('cartesia_emotions_enabled')}")
        
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
        
        # Check if client uses platform-provided keys (Sidekick Forge Inference)
        # Query directly from database to get the authoritative value
        # Default to True - if not explicitly set to False, use platform keys
        uses_platform_keys = True
        try:
            platform_sb = client_service.supabase
            client_data = platform_sb.table("clients").select(
                "uses_platform_keys"
            ).eq("id", client_id).single().execute()

            if client_data.data:
                # Only use BYOK (validate keys) if explicitly set to False
                # None or True = use platform keys (Sidekick Forge Inference) = skip validation
                if client_data.data.get("uses_platform_keys") is False:
                    uses_platform_keys = False
        except Exception as e:
            logger.warning(f"Could not check uses_platform_keys for client {client_id}: {e}")
            uses_platform_keys = True  # Safe default

        # Validate API keys if voice_settings are provided AND client is NOT using platform keys
        # When using Sidekick Forge Inference, platform provides all necessary keys
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
                "inworld": "inworld_api_key",
                "fish_audio": "fish_audio_api_key",
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
        from app.models.agent import AgentUpdate, VoiceSettings, WebhookSettings
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
            # Chat mode toggles
            voice_chat_enabled=data.get("voice_chat_enabled", getattr(agent, "voice_chat_enabled", True)),
            text_chat_enabled=data.get("text_chat_enabled", getattr(agent, "text_chat_enabled", True)),
            video_chat_enabled=data.get("video_chat_enabled", getattr(agent, "video_chat_enabled", False)),
        )
        
        # Handle voice settings if provided
        if "voice_settings" in data:
            logger.info(f"🔧 Incoming voice_settings keys: {list(data['voice_settings'].keys())}")
            logger.info(f"🔧 avatar_provider from request: {data['voice_settings'].get('avatar_provider')}")
            logger.info(f"🔧 video_provider from request: {data['voice_settings'].get('video_provider')}")
            try:
                update_data.voice_settings = VoiceSettings(**data["voice_settings"])
                logger.info(f"🔧 VoiceSettings created successfully, avatar_provider: {update_data.voice_settings.avatar_provider}")
            except Exception as vs_error:
                logger.error(f"🔧 VoiceSettings creation failed: {vs_error}")
                # Fall back to storing raw dict
                update_data.voice_settings = data["voice_settings"]
        
        # Handle webhooks if provided
        if "webhooks" in data:
            update_data.webhooks = WebhookSettings(**data["webhooks"])

        # Handle channels (Email + Telegram) and enforce token requirement for non-global sidekicks
        from app.models.client import EmailChannelSettings
        from app.services.email_address_service import email_address_service

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

        # --- Email channel ---
        email_payload = channels_payload.get("email") if isinstance(channels_payload, dict) else None
        email_cfg = EmailChannelSettings()
        if email_payload and isinstance(email_payload, dict):
            email_enabled = bool(email_payload.get("enabled"))
            email_cfg = EmailChannelSettings(enabled=email_enabled)

            # Auto-assign email address when enabled and not yet assigned
            current_email = None
            if hasattr(agent, "email_address"):
                current_email = agent.email_address
            elif isinstance(agent, dict):
                current_email = agent.get("email_address")

            if email_enabled and not current_email:
                suggested = await email_address_service.suggest_address(agent_slug)
                reserved = await email_address_service.reserve(suggested, client_id, agent_slug)
                if reserved:
                    update_data.email_address = suggested
                    logger.info(f"Assigned email address {suggested} to {agent_slug}")
                else:
                    logger.warning(f"Could not reserve email address for {agent_slug}")

            # Release email address when disabled
            if not email_enabled and current_email:
                await email_address_service.release(current_email)
                update_data.email_address = None
                logger.info(f"Released email address {current_email} for {agent_slug}")

        # --- Telegram channel ---
        telegram_payload = channels_payload.get("telegram") if isinstance(channels_payload, dict) else None
        telegram_cfg = TelegramChannelSettings()

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

        # --- Build combined channels object ---
        if email_payload or telegram_payload:
            channels_obj = ChannelSettings(email=email_cfg, telegram=telegram_cfg)
            channels_dict = channels_obj.dict()
            if not isinstance(tools_config, dict):
                tools_config = {}
            tools_config["channels"] = channels_dict
            update_data.channels = channels_obj
            update_data.tools_config = tools_config
        elif tools_config:
            update_data.tools_config = tools_config
        
        # Handle sound_settings if provided - these are stored directly in the agents table
        sound_settings = data.get("sound_settings")
        if sound_settings:
            logger.info(f"🔊 Processing sound_settings: {sound_settings}")

        # Update agent with regular fields first
        updated_agent = await agent_service.update_agent(client_id, agent_slug, update_data)

        # Update sound_settings directly in the database (separate from AgentUpdate model)
        if sound_settings and updated_agent:
            try:
                client_sb = await client_service.get_client_supabase_client(client_id)
                if client_sb:
                    logger.info(f"🔊 Updating sound_settings in DB for {agent_slug}: {sound_settings}")
                    result = client_sb.table("agents").update({
                        "sound_settings": sound_settings
                    }).eq("slug", agent_slug).execute()
                    logger.info(f"🔊 Updated sound_settings result: {result.data if result else 'no result'}")
                else:
                    logger.warning(f"🔊 No Supabase client available for client {client_id}")
            except Exception as sound_err:
                logger.error(f"🔊 Failed to update sound_settings: {sound_err}", exc_info=True)
                # Don't fail the whole update if sound_settings fails

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
    """User settings page (profile + security + channel handles)."""
    profile = None
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
    except Exception as e:
        logger.warning(f"Failed to load user profile for settings: {e}")

    return templates.TemplateResponse(
        "admin/user_settings.html",
        {
            "request": request,
            "user": admin_user,
            "profile": profile or {},
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
                inworld_api_key=form.get("inworld_api_key") or (current_api_keys.inworld_api_key if hasattr(current_api_keys, 'inworld_api_key') else current_api_keys.get('inworld_api_key') if isinstance(current_api_keys, dict) else None),
                fish_audio_api_key=form.get("fish_audio_api_key") or (current_api_keys.fish_audio_api_key if hasattr(current_api_keys, 'fish_audio_api_key') else current_api_keys.get('fish_audio_api_key') if isinstance(current_api_keys, dict) else None),
                bithuman_api_secret=form.get("bithuman_api_secret") or (current_api_keys.bithuman_api_secret if hasattr(current_api_keys, 'bithuman_api_secret') else current_api_keys.get('bithuman_api_secret') if isinstance(current_api_keys, dict) else None),
                bey_api_key=form.get("bey_api_key") or (current_api_keys.bey_api_key if hasattr(current_api_keys, 'bey_api_key') else current_api_keys.get('bey_api_key') if isinstance(current_api_keys, dict) else None),
                liveavatar_api_key=form.get("liveavatar_api_key") or (current_api_keys.liveavatar_api_key if hasattr(current_api_keys, 'liveavatar_api_key') else current_api_keys.get('liveavatar_api_key') if isinstance(current_api_keys, dict) else None),
                novita_api_key=form.get("novita_api_key") or (current_api_keys.novita_api_key if hasattr(current_api_keys, 'novita_api_key') else current_api_keys.get('novita_api_key') if isinstance(current_api_keys, dict) else None),
                cohere_api_key=form.get("cohere_api_key") or (current_api_keys.cohere_api_key if hasattr(current_api_keys, 'cohere_api_key') else current_api_keys.get('cohere_api_key') if isinstance(current_api_keys, dict) else None),
                siliconflow_api_key=form.get("siliconflow_api_key") or (current_api_keys.siliconflow_api_key if hasattr(current_api_keys, 'siliconflow_api_key') else current_api_keys.get('siliconflow_api_key') if isinstance(current_api_keys, dict) else None),
                jina_api_key=form.get("jina_api_key") or (current_api_keys.jina_api_key if hasattr(current_api_keys, 'jina_api_key') else current_api_keys.get('jina_api_key') if isinstance(current_api_keys, dict) else None),
                descript_api_key=form.get("descript_api_key") or (current_api_keys.descript_api_key if hasattr(current_api_keys, 'descript_api_key') else current_api_keys.get('descript_api_key') if isinstance(current_api_keys, dict) else None),
                semrush_api_key=form.get("semrush_api_key") or (current_api_keys.semrush_api_key if hasattr(current_api_keys, 'semrush_api_key') else current_api_keys.get('semrush_api_key') if isinstance(current_api_keys, dict) else None),
                ahrefs_api_key=form.get("ahrefs_api_key") or (current_api_keys.ahrefs_api_key if hasattr(current_api_keys, 'ahrefs_api_key') else current_api_keys.get('ahrefs_api_key') if isinstance(current_api_keys, dict) else None)
            ),
            # Embedding defaults: platform canonical (siliconflow Qwen3-Embedding-4B
            # at 1024 dim). MUST stay in sync with EmbeddingSettings field
            # defaults, AIProcessor.DEFAULT_*, provisioning_worker, and the
            # trigger fallbacks. Mixing models produces incompatible vector
            # spaces and silent RAG failures.
            embedding=EmbeddingSettings(
                provider=form.get("embedding_provider", current_embedding.provider if hasattr(current_embedding, 'provider') else current_embedding.get('provider', 'siliconflow') if current_embedding else 'siliconflow'),
                document_model=form.get("document_embedding_model", current_embedding.document_model if hasattr(current_embedding, 'document_model') else current_embedding.get('document_model', 'Qwen/Qwen3-Embedding-4B') if current_embedding else 'Qwen/Qwen3-Embedding-4B'),
                conversation_model=form.get("conversation_embedding_model", current_embedding.conversation_model if hasattr(current_embedding, 'conversation_model') else current_embedding.get('conversation_model', 'Qwen/Qwen3-Embedding-4B') if current_embedding else 'Qwen/Qwen3-Embedding-4B'),
                dimension=int(form.get("embedding_dimension")) if form.get("embedding_dimension") and form.get("embedding_dimension").strip() else (current_embedding.dimension if hasattr(current_embedding, 'dimension') else current_embedding.get('dimension') if current_embedding else 1024)
            ),
            rerank=RerankSettings(
                enabled=form.get("rerank_enabled", "off") == "on",
                provider=form.get("rerank_provider", current_rerank.provider if hasattr(current_rerank, 'provider') else current_rerank.get('provider', 'siliconflow') if current_rerank else 'siliconflow'),
                model=form.get("rerank_model", current_rerank.model if hasattr(current_rerank, 'model') else current_rerank.get('model', 'BAAI/bge-reranker-base') if current_rerank else 'BAAI/bge-reranker-base'),
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
        
        update_data = ClientUpdate(
            name=form.get("name", client.name if hasattr(client, 'name') else client.get('name', '')),
            domain=form.get("domain", client.domain if hasattr(client, 'domain') else client.get('domain', '')),
            description=form.get("description", client.description if hasattr(client, 'description') else client.get('description', '')),
            settings=settings_update,
            active=str(active_value).lower() == "true"
        )
        
        # Debug: Log the API keys and embedding settings being updated
        logger.info(f"About to update client with API keys: cartesia={update_data.settings.api_keys.cartesia_api_key}, siliconflow={update_data.settings.api_keys.siliconflow_api_key}")
        logger.info(f"Embedding settings: provider={update_data.settings.embedding.provider}, dimension={update_data.settings.embedding.dimension}, form_value='{form.get('embedding_dimension')}'")
        
        # Update client
        updated_client = await client_service.update_client(client_id, update_data)

        # Persist top-level DB columns not covered by the Pydantic model
        direct_col_updates = {}
        uses_platform_keys_val = form.get("uses_platform_keys")
        if uses_platform_keys_val is not None:
            direct_col_updates["uses_platform_keys"] = uses_platform_keys_val.lower() == "true"
        descript_key_val = form.get("descript_api_key")
        if descript_key_val is not None:
            direct_col_updates["descript_api_key"] = descript_key_val or None
        firecrawl_key_val = form.get("firecrawl_api_key")
        if firecrawl_key_val is not None:
            direct_col_updates["firecrawl_api_key"] = firecrawl_key_val or None
        semrush_key_val = form.get("semrush_api_key")
        if semrush_key_val is not None:
            direct_col_updates["semrush_api_key"] = semrush_key_val or None
        ahrefs_key_val = form.get("ahrefs_api_key")
        if ahrefs_key_val is not None:
            direct_col_updates["ahrefs_api_key"] = ahrefs_key_val or None
        if direct_col_updates:
            try:
                client_service.supabase.table("clients").update(direct_col_updates).eq("id", client_id).execute()
                logger.info(f"Updated direct columns for client {client_id}: {list(direct_col_updates.keys())}")
            except Exception as dc_err:
                logger.warning(f"Failed to update direct columns for client {client_id}: {dc_err}")

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
        
        # Redirect back to client detail with success
        return RedirectResponse(
            url=f"/admin/clients/{client_id}?message=Client+updated+successfully",
            status_code=303
        )
        
    except Exception as e:
        logger.error(f"Error updating client: {e}")
        return RedirectResponse(
            url=f"/admin/clients/{client_id}?error=Failed+to+update+client:+{str(e)}",
            status_code=303
        )

# UserSense Learning Status Endpoint
@router.get("/clients/{client_id}/usersense-learning-status")
async def get_usersense_learning_status(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get UserSense learning status for a client (stub endpoint)"""
    try:
        ensure_client_access(client_id, admin_user)
        # Currently UserSense learning is not implemented,
        # return idle status to prevent console errors
        return {
            "success": True,
            "status": {
                "state": "idle",
                "progress": 0,
                "message": "UserSense learning not active"
            }
        }
    except Exception as e:
        logger.error(f"Failed to get UserSense learning status for client {client_id}: {e}")
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


@router.post("/clients/{client_id}/wordpress-sites/{site_id}/regenerate")
async def regenerate_wordpress_keys_v2(
    client_id: str,
    site_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Regenerate API keys for a WordPress site (new URL pattern)"""
    # Delegate to the main regenerate function
    return await regenerate_wordpress_keys(site_id, admin_user)


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


# ── Documentation routes ──────────────────────────────────────────────────────

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


# ---------------------------------------------------------------------------
# Lore — Personal Context MCP
# ---------------------------------------------------------------------------
import httpx as _httpx

_LORE_MCP_BASE = os.getenv("LORE_MCP_URL", "http://lore-mcp:8082")


def _lore_internal_headers() -> Dict[str, str]:
    """Headers that identify this process as an internal caller of the Lore MCP.
    The X-Lore-Internal header carries the platform service role key."""
    return {"X-Lore-Internal": os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")}

_LORE_CATEGORIES = [
    {"key": "identity", "label": "Identity", "description": "Name, role, org, philosophy, personal context", "icon": "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z", "color": "#01a4a6"},
    {"key": "roles_and_responsibilities", "label": "Roles & Responsibilities", "description": "Day-to-day work, outputs, decisions, who you serve", "icon": "M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z", "color": "#fc7244"},
    {"key": "current_projects", "label": "Current Projects", "description": "Active workstreams, status, priority, KPIs", "icon": "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4", "color": "#a78bfa"},
    {"key": "team_and_relationships", "label": "Team & Relationships", "description": "Key people, roles, what each relationship requires", "icon": "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z", "color": "#f472b6"},
    {"key": "tools_and_systems", "label": "Tools & Systems", "description": "Stack, architecture patterns, constraints", "icon": "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z", "color": "#fbbf24"},
    {"key": "communication_style", "label": "Communication Style", "description": "Tone, formatting, editing preferences", "icon": "M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z", "color": "#34d399"},
    {"key": "goals_and_priorities", "label": "Goals & Priorities", "description": "Week / quarter / year / career targets", "icon": "M13 7h8m0 0v8m0-8l-8 8-4-4-6 6", "color": "#60a5fa"},
    {"key": "preferences_and_constraints", "label": "Preferences & Constraints", "description": "Always/never rules, hard constraints", "icon": "M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z", "color": "#f87171"},
    {"key": "domain_knowledge", "label": "Domain Knowledge", "description": "Expertise areas, frameworks, what NOT to explain", "icon": "M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z", "color": "#c084fc"},
    {"key": "decision_log", "label": "Decision Log", "description": "Past decisions and reasoning", "icon": "M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z", "color": "#fb923c"},
]


async def _resolve_lore_llm(admin_user: dict):
    """Resolve an LLM provider for Lore processing. Returns (provider, openai_api_key)."""
    from app.services.content_catalyst_service import (
        AnthropicProvider, OpenAIProvider, GroqProvider, CerebrasProvider,
    )
    llm_provider = None
    openai_api_key = None

    # 1. Platform API keys (Cerebras preferred)
    try:
        from dotenv import load_dotenv
        load_dotenv()
        from supabase import create_client as _create_sb
        _sb = _create_sb(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
        _pk = _sb.table("platform_api_keys").select("key_value").eq("key_name", "cerebras_api_key").eq("is_active", True).maybe_single().execute()
        if _pk.data and _pk.data.get("key_value"):
            llm_provider = CerebrasProvider(_pk.data["key_value"], model="zai-glm-4.7")
            logger.info("Lore: using platform Cerebras API key (GLM 4.7)")
    except Exception as exc:
        logger.warning(f"Failed to load platform Cerebras key: {exc}")

    # 2. Client-level keys
    if not llm_provider:
        try:
            client_ids = admin_user.get("visible_client_ids", [])
            primary_client_id = admin_user.get("primary_client_id") or (client_ids[0] if client_ids else None)
            if primary_client_id:
                from app.core.dependencies import get_client_service
                client_service = get_client_service()
                client_obj = await client_service.get_client(primary_client_id)
                if client_obj and client_obj.settings and client_obj.settings.api_keys:
                    keys = client_obj.settings.api_keys
                    cerebras_key = getattr(keys, "cerebras_api_key", None)
                    openai_key = getattr(keys, "openai_api_key", None)
                    anthropic_key = getattr(keys, "anthropic_api_key", None)
                    groq_key = getattr(keys, "groq_api_key", None)
                    if cerebras_key:
                        llm_provider = CerebrasProvider(cerebras_key, model="zai-glm-4.7")
                    elif anthropic_key:
                        llm_provider = AnthropicProvider(anthropic_key)
                    elif openai_key:
                        llm_provider = OpenAIProvider(openai_key)
                        openai_api_key = openai_key
                    elif groq_key:
                        llm_provider = GroqProvider(groq_key)
                    if openai_key:
                        openai_api_key = openai_key
        except Exception as exc:
            logger.warning(f"Failed to resolve LLM provider from client: {exc}")

    # 3. Env vars
    if not llm_provider:
        for env_key, ProviderCls, model in [
            ("CEREBRAS_API_KEY", CerebrasProvider, "zai-glm-4.7"),
            ("ANTHROPIC_API_KEY", AnthropicProvider, None),
            ("OPENAI_API_KEY", OpenAIProvider, None),
            ("GROQ_API_KEY", GroqProvider, None),
        ]:
            val = os.getenv(env_key)
            if val:
                llm_provider = ProviderCls(val) if not model else ProviderCls(val, model=model)
                if env_key == "OPENAI_API_KEY":
                    openai_api_key = val
                break

    return llm_provider, openai_api_key


async def _resolve_lore_target(admin_user: dict) -> Tuple[str, Dict[str, str]]:
    """Resolve the current user's Lore target (user_id + target_url/target_key).

    Returns a tuple: (user_id, params_dict) where params_dict contains
    the query params to pass to the Lore MCP admin API calls.

    The user's Lore lives in their home client's Supabase instance:
    - Champion/Paragon: their dedicated instance (target_url + target_key)
    - Adventurer/platform: the platform Supabase (no overrides needed)
    - Superadmin with no home client: defaults to Leandrew Dixon dedicated
    """
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")

    # Prefer explicit primary_client_id; fall back to visible_client_ids
    client_ids = admin_user.get("visible_client_ids", [])
    primary_client_id = admin_user.get("primary_client_id") or (client_ids[0] if client_ids else None)

    # Superadmin fallback: use the Leandrew Dixon client so superadmins have
    # a home for their own Lore while testing
    if not primary_client_id and admin_user.get("is_super_admin"):
        try:
            from app.integrations.supabase_client import supabase_manager
            result = (
                supabase_manager.admin_client
                .table("clients")
                .select("id")
                .eq("name", "Leandrew Dixon")
                .maybe_single()
                .execute()
            )
            if result and result.data:
                primary_client_id = result.data["id"]
        except Exception as exc:
            logger.warning(f"Failed to resolve superadmin home client: {exc}")

    params: Dict[str, str] = {"user_id": user_id}
    if primary_client_id:
        try:
            from app.utils.supabase_credentials import SupabaseCredentialManager
            target_url, _anon, target_key = await SupabaseCredentialManager.get_client_supabase_credentials(primary_client_id)
            # Only override if the client has its own dedicated instance
            # (different from the platform default)
            platform_url = os.getenv("SUPABASE_URL", "")
            if target_url and target_key and target_url != platform_url:
                params["target_url"] = target_url
                params["target_key"] = target_key
        except Exception as exc:
            logger.warning(f"Failed to resolve Lore target for client {primary_client_id}: {exc}")

    return user_id, params


@router.get("/lore", response_class=HTMLResponse)
async def lore_page(request: Request):
    """Lore personal context editor page."""
    admin_user = await get_admin_user(request)
    user_id, target_params = await _resolve_lore_target(admin_user)

    has_content_map = {}
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/categories",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                for item in resp.json():
                    has_content_map[item["key"]] = item["has_content"]
    except Exception:
        pass

    categories = [{**cat, "has_content": has_content_map.get(cat["key"], False)} for cat in _LORE_CATEGORIES]

    active_category = "identity"
    active_content = ""
    active_label = _LORE_CATEGORIES[0]["label"]
    active_description = _LORE_CATEGORIES[0]["description"]

    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/category/{active_category}",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                active_content = resp.json().get("content", "")
    except Exception:
        pass

    return templates.TemplateResponse("admin/lore.html", {
        "request": request,
        "user": admin_user,
        "categories": categories,
        "active_category": active_category,
        "active_content": active_content,
        "active_label": active_label,
        "active_description": active_description,
        "disable_stats_poll": True,
    })


@router.get("/lore/v2", response_class=HTMLResponse)
async def lore_page_v2(request: Request):
    """Prototype v2 layout for the Lore page — side-by-side comparison route.
    Renders the same data as /admin/lore but through the tab-split template."""
    admin_user = await get_admin_user(request)
    user_id, target_params = await _resolve_lore_target(admin_user)

    has_content_map: Dict[str, bool] = {}
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/categories",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                for item in resp.json():
                    has_content_map[item["key"]] = item["has_content"]
    except Exception:
        pass

    categories = [{**cat, "has_content": has_content_map.get(cat["key"], False)} for cat in _LORE_CATEGORIES]

    active_category = "identity"
    active_content = ""
    active_label = _LORE_CATEGORIES[0]["label"]
    active_description = _LORE_CATEGORIES[0]["description"]

    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/category/{active_category}",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                active_content = resp.json().get("content", "")
    except Exception:
        pass

    return templates.TemplateResponse("admin/lore_v2.html", {
        "request": request,
        "user": admin_user,
        "categories": categories,
        "active_category": active_category,
        "active_content": active_content,
        "active_label": active_label,
        "active_description": active_description,
        "disable_stats_poll": True,
    })


@router.get("/lore/api/status")
async def lore_api_status(request: Request):
    """Check Lore MCP connectivity."""
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_LORE_MCP_BASE}/healthz")
            if resp.status_code == 200:
                return {"status": "ok"}
            return {"status": "error", "detail": f"HTTP {resp.status_code}"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@router.get("/lore/api/category/{category}")
async def lore_api_get_category(category: str, request: Request):
    """Proxy read from Lore MCP for a specific category."""
    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/category/{category}",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})


@router.put("/lore/api/category/{category}")
async def lore_api_update_category(category: str, request: Request):
    """Proxy write to Lore MCP for a specific category."""
    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    body = await request.json()
    content = body.get("content", "")
    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{_LORE_MCP_BASE}/admin-api/category/{category}",
                params=target_params,
                headers=_lore_internal_headers(),
                json={"content": content},
            )
            if resp.status_code != 200:
                return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
            result = resp.json()
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})

    # Fire-and-forget LLM regrade for this category
    _schedule_depth_grade(admin_user, [category])
    return result


# ---------------------------------------------------------------------------
# Lore MCP API keys — list / create / revoke
# ---------------------------------------------------------------------------

@router.get("/lore/api/keys")
async def lore_api_keys_list(request: Request):
    """List the current user's Lore MCP access keys (without the raw token)."""
    admin_user = await get_admin_user(request)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")
    try:
        from app.integrations.supabase_client import supabase_manager
        rows = (
            supabase_manager.admin_client
            .table("lore_api_keys")
            .select("id,name,prefix,created_at,last_used_at,revoked_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return {"keys": rows.data or []}
    except Exception as exc:
        logger.error(f"lore_api_keys_list failed: {exc}")
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.post("/lore/api/keys")
async def lore_api_keys_create(request: Request):
    """Generate a new Lore MCP access key. Returns the raw token ONCE."""
    admin_user = await get_admin_user(request)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")
    body = await request.json()
    name = (body.get("name") or "Unnamed key").strip()[:64]

    import hashlib as _hashlib
    import secrets as _secrets
    raw = f"slf_lore_{_secrets.token_urlsafe(32)}"
    key_hash = _hashlib.sha256(raw.encode("utf-8")).hexdigest()
    prefix = raw[: len("slf_lore_") + 4]

    try:
        from app.integrations.supabase_client import supabase_manager
        row = (
            supabase_manager.admin_client
            .table("lore_api_keys")
            .insert({
                "user_id": user_id,
                "name": name,
                "key_hash": key_hash,
                "prefix": prefix,
            })
            .execute()
        )
        inserted = row.data[0] if row.data else {}
        return {
            "id": inserted.get("id"),
            "name": name,
            "prefix": prefix,
            "raw_token": raw,  # Shown to user ONCE
            "created_at": inserted.get("created_at"),
        }
    except Exception as exc:
        logger.error(f"lore_api_keys_create failed: {exc}")
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.delete("/lore/api/keys/{key_id}")
async def lore_api_keys_revoke(key_id: str, request: Request):
    """Revoke an API key (soft delete — sets revoked_at)."""
    admin_user = await get_admin_user(request)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")
    try:
        from app.integrations.supabase_client import supabase_manager
        from datetime import datetime, timezone
        supabase_manager.admin_client.table("lore_api_keys").update(
            {"revoked_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", key_id).eq("user_id", user_id).execute()
        return {"status": "revoked", "id": key_id}
    except Exception as exc:
        logger.error(f"lore_api_keys_revoke failed: {exc}")
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# Lore external endpoint — user-registered self-host URL + Bearer token.
# When an enabled row exists in lore_external_endpoints, the lore_mcp
# runtime proxies all reads/writes for that user to their self-host server
# instead of touching the platform Supabase tables.
# ---------------------------------------------------------------------------

def _mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "•" * len(token)
    return f"{token[:4]}{'•' * (len(token) - 8)}{token[-4:]}"


@router.get("/lore/api/external-endpoint")
async def lore_api_external_endpoint_get(request: Request):
    """Return the user's self-host endpoint config (token masked)."""
    admin_user = await get_admin_user(request)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")
    try:
        row = (
            supabase_manager.admin_client
            .table("lore_external_endpoints")
            .select("base_url,auth_token,enabled,last_tested_at,last_tested_status,updated_at")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"lore_api_external_endpoint_get failed: {exc}")
        return JSONResponse(status_code=500, content={"error": str(exc)})

    if not row or not row.data:
        return {"configured": False}
    data = row.data
    return {
        "configured": True,
        "base_url": data.get("base_url"),
        "token_masked": _mask_token(data.get("auth_token") or ""),
        "enabled": data.get("enabled", True),
        "last_tested_at": data.get("last_tested_at"),
        "last_tested_status": data.get("last_tested_status"),
        "updated_at": data.get("updated_at"),
    }


@router.post("/lore/api/external-endpoint")
async def lore_api_external_endpoint_save(request: Request):
    """Create or update the user's self-host endpoint. Body:
        { base_url: "...", auth_token: "...", enabled: true }
    Runs a connectivity test BEFORE committing — if the test fails, nothing
    is written and the error is returned so the user knows their server
    isn't reachable or their token is wrong."""
    admin_user = await get_admin_user(request)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")
    body = await request.json()
    base_url = (body.get("base_url") or "").strip().rstrip("/")
    auth_token = (body.get("auth_token") or "").strip()
    enabled = bool(body.get("enabled", True))

    if not base_url or not auth_token:
        return JSONResponse(status_code=400, content={"error": "base_url and auth_token are required"})
    if not (base_url.startswith("https://") or base_url.startswith("http://")):
        return JSONResponse(status_code=400, content={"error": "base_url must start with http:// or https://"})

    # Pre-flight test — refuse to save an unreachable endpoint
    ok, detail = await _test_remote_endpoint(base_url, auth_token)
    if not ok:
        return JSONResponse(status_code=400, content={"error": f"Connection test failed: {detail}"})

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        supabase_manager.admin_client.table("lore_external_endpoints").upsert(
            {
                "user_id": user_id,
                "base_url": base_url,
                "auth_token": auth_token,
                "enabled": enabled,
                "updated_at": now_iso,
                "last_tested_at": now_iso,
                "last_tested_status": "ok",
            },
            on_conflict="user_id",
        ).execute()
    except Exception as exc:
        logger.error(f"lore_api_external_endpoint_save failed: {exc}")
        return JSONResponse(status_code=500, content={"error": str(exc)})

    # Flush the lore_mcp cache so the new endpoint is picked up immediately
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{_LORE_MCP_BASE}/admin-api/external-endpoint/invalidate",
                params={"user_id": user_id},
                headers=_lore_internal_headers(),
            )
    except Exception:
        pass

    return {"status": "ok", "enabled": enabled, "base_url": base_url, "token_masked": _mask_token(auth_token)}


@router.delete("/lore/api/external-endpoint")
async def lore_api_external_endpoint_delete(request: Request):
    """Remove the user's self-host endpoint — platform reverts to Supabase storage."""
    admin_user = await get_admin_user(request)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")
    try:
        supabase_manager.admin_client.table("lore_external_endpoints").delete().eq("user_id", user_id).execute()
    except Exception as exc:
        logger.error(f"lore_api_external_endpoint_delete failed: {exc}")
        return JSONResponse(status_code=500, content={"error": str(exc)})
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{_LORE_MCP_BASE}/admin-api/external-endpoint/invalidate",
                params={"user_id": user_id},
                headers=_lore_internal_headers(),
            )
    except Exception:
        pass
    return {"status": "removed"}


@router.post("/lore/api/external-endpoint/test")
async def lore_api_external_endpoint_test(request: Request):
    """Run a connectivity test without committing. Body same as save."""
    _admin_user = await get_admin_user(request)
    body = await request.json()
    base_url = (body.get("base_url") or "").strip().rstrip("/")
    auth_token = (body.get("auth_token") or "").strip()
    if not base_url or not auth_token:
        return JSONResponse(status_code=400, content={"error": "base_url and auth_token are required"})
    ok, detail = await _test_remote_endpoint(base_url, auth_token)
    return {"ok": ok, "detail": detail}


async def _test_remote_endpoint(base_url: str, auth_token: str) -> Tuple[bool, str]:
    """Hits /healthz and /admin-api/categories on the user's self-host server.
    Returns (ok, detail) — detail is "ok" on success or an error description."""
    headers = {"Authorization": f"Bearer {auth_token}"}
    try:
        async with _httpx.AsyncClient(timeout=8.0) as client:
            hz = await client.get(f"{base_url}/healthz")
            if hz.status_code != 200:
                return False, f"healthz returned HTTP {hz.status_code}"
            cats = await client.get(f"{base_url}/admin-api/categories", headers=headers)
            if cats.status_code == 401:
                return False, "auth_token rejected (HTTP 401)"
            if cats.status_code != 200:
                return False, f"admin-api/categories returned HTTP {cats.status_code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


# ---------------------------------------------------------------------------
# Lore export — markdown, JSON, self-host ZIP. All three proxy through to
# the lore-mcp container which owns the Supabase access. The main app just
# authenticates the user and forwards the binary response as an attachment.
# ---------------------------------------------------------------------------

@router.get("/lore/api/export/{fmt}")
async def lore_api_export(fmt: str, request: Request):
    from fastapi.responses import StreamingResponse
    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    if fmt not in ("markdown", "json", "self-host"):
        return JSONResponse(status_code=400, content={"error": f"Unknown format '{fmt}'"})

    try:
        async with _httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/export/{fmt}",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code != 200:
                return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
            # Forward filename + content-type from the upstream response
            content_type = resp.headers.get("content-type", "application/octet-stream")
            disposition = resp.headers.get("content-disposition", f'attachment; filename="lore-export.{ "json" if fmt == "json" else "zip" }"')
            return StreamingResponse(
                iter([resp.content]),
                media_type=content_type,
                headers={"Content-Disposition": disposition},
            )
    except Exception as exc:
        logger.error(f"lore export proxy failed: {exc}")
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})


# ---------------------------------------------------------------------------
# Lore OAuth 2.1 shim — /authorize + /consent
#
# Flow:
#   1. Claude.ai redirects the user to /admin/lore/oauth/authorize?...
#   2. We require login. If not logged in, bounce to /admin/login?next=<this url>.
#   3. We look up the registered client from lore_oauth_clients and render a
#      consent page showing the client_name + requested scope, with a Deny/
#      Approve form.
#   4. On Approve POST, we generate a random authorization_code, store it in
#      lore_oauth_authorization_codes with the stashed PKCE challenge, and
#      redirect the user back to the client's redirect_uri with ?code=&state=.
# ---------------------------------------------------------------------------

def _lore_oauth_error_redirect(redirect_uri: str, state: Optional[str], error: str, description: str) -> RedirectResponse:
    from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
    parsed = urlparse(redirect_uri)
    params = dict(parse_qsl(parsed.query))
    params["error"] = error
    params["error_description"] = description
    if state:
        params["state"] = state
    new_url = urlunparse(parsed._replace(query=urlencode(params)))
    return RedirectResponse(url=new_url, status_code=303)


@router.get("/lore/oauth/authorize", response_class=HTMLResponse)
async def lore_oauth_authorize(
    request: Request,
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    response_type: str = Query("code"),
    scope: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query("S256"),
):
    """Consent page for Lore MCP OAuth. User must be logged in to Sidekick
    Forge; the approved code is scoped to their own user_id only."""
    from urllib.parse import urlencode
    try:
        admin_user = await get_admin_user(request)
    except HTTPException:
        # Not logged in — bounce to login with the full authorize URL as next
        next_qs = urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": response_type,
            "scope": scope or "",
            "state": state or "",
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
        })
        from urllib.parse import quote
        next_url = f"/admin/lore/oauth/authorize?{next_qs}"
        return RedirectResponse(
            url=f"/admin/login?next={quote(next_url, safe='')}",
            status_code=303,
        )

    if response_type != "code":
        return _lore_oauth_error_redirect(redirect_uri, state, "unsupported_response_type", "Only 'code' is supported")
    if code_challenge_method != "S256":
        return _lore_oauth_error_redirect(redirect_uri, state, "invalid_request", "Only S256 PKCE is supported")

    # Validate client + redirect_uri
    try:
        row = (
            supabase_manager.admin_client
            .table("lore_oauth_clients")
            .select("client_id,client_name,redirect_uris,scope")
            .eq("client_id", client_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"lore oauth authorize: client lookup failed: {exc}")
        return HTMLResponse("<h1>OAuth error</h1><p>Client lookup failed.</p>", status_code=500)

    if not row or not row.data:
        return HTMLResponse("<h1>OAuth error</h1><p>Unknown client_id.</p>", status_code=400)
    oauth_client = row.data

    if redirect_uri not in (oauth_client.get("redirect_uris") or []):
        return HTMLResponse("<h1>OAuth error</h1><p>redirect_uri does not match any registered URI for this client.</p>", status_code=400)

    effective_scope = scope or oauth_client.get("scope") or "lore:read lore:write"

    return templates.TemplateResponse("admin/lore_consent.html", {
        "request": request,
        "user": admin_user,
        "client_name": oauth_client.get("client_name") or "MCP Client",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state or "",
        "scope": effective_scope,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
    })


@router.post("/lore/oauth/consent")
async def lore_oauth_consent(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    scope: str = Form(...),
    state: str = Form(""),
    code_challenge: str = Form(...),
    code_challenge_method: str = Form("S256"),
    decision: str = Form(...),
):
    """Handles the Approve/Deny decision. Writes an authorization_code to
    lore_oauth_authorization_codes and redirects back to the client."""
    from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
    import secrets as _secrets
    from datetime import datetime, timezone, timedelta

    admin_user = await get_admin_user(request)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required")

    if decision != "approve":
        return _lore_oauth_error_redirect(redirect_uri, state, "access_denied", "User denied access")

    # Verify client + redirect_uri still match (defense in depth)
    try:
        row = (
            supabase_manager.admin_client
            .table("lore_oauth_clients")
            .select("redirect_uris")
            .eq("client_id", client_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"lore oauth consent: client lookup failed: {exc}")
        return HTMLResponse("<h1>OAuth error</h1>", status_code=500)
    if not row or not row.data or redirect_uri not in (row.data.get("redirect_uris") or []):
        return HTMLResponse("<h1>OAuth error</h1><p>Invalid client or redirect_uri.</p>", status_code=400)

    code = _secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

    try:
        supabase_manager.admin_client.table("lore_oauth_authorization_codes").insert({
            "code": code,
            "client_id": client_id,
            "user_id": user_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "expires_at": expires_at,
        }).execute()
    except Exception as exc:
        logger.error(f"lore oauth consent: failed to write auth code: {exc}")
        return _lore_oauth_error_redirect(redirect_uri, state, "server_error", "Failed to issue code")

    parsed = urlparse(redirect_uri)
    params = dict(parse_qsl(parsed.query))
    params["code"] = code
    if state:
        params["state"] = state
    new_url = urlunparse(parsed._replace(query=urlencode(params)))
    return RedirectResponse(url=new_url, status_code=303)


async def _background_grade_nodes(admin_user: Dict[str, Any], node_keys: List[str]) -> None:
    """Run LLM grading for a set of nodes in the background. Never raises —
    grading failures are logged and the cached scores simply stay stale.
    """
    from app.services.lore_depth_grader import grade_nodes_parallel
    try:
        llm_provider, _openai_key = await _resolve_lore_llm(admin_user)
        if not llm_provider:
            logger.info("Lore depth grade: no LLM provider available — skipping")
            return
        _uid, target_params = await _resolve_lore_target(admin_user)
        await grade_nodes_parallel(
            _LORE_MCP_BASE,
            _lore_internal_headers(),
            target_params,
            node_keys,
            llm_provider=llm_provider,
            concurrency=4,
        )
    except Exception as exc:
        logger.warning(f"Lore depth background grading failed: {exc}")


def _schedule_depth_grade(admin_user: Dict[str, Any], node_keys: List[str]) -> None:
    """Fire-and-forget wrapper around the background grader."""
    if not node_keys:
        return
    try:
        asyncio.create_task(_background_grade_nodes(admin_user, list(node_keys)))
    except Exception as exc:
        logger.warning(f"Failed to schedule depth grade: {exc}")


@router.get("/lore/api/depth-score")
async def lore_api_depth_score(request: Request):
    """Proxy depth score from Lore MCP.

    Fetches the current (possibly heuristic) breakdown, then fires an async
    background task to LLM-grade any nodes whose cached grade is stale or
    missing. The next page load will serve the fresh grades.
    """
    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/depth-score",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code != 200:
                return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
            data = resp.json()
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})

    stale = data.get("stale_nodes") or []
    if stale:
        _schedule_depth_grade(admin_user, stale)
    return data


@router.post("/lore/api/depth-score/regrade")
async def lore_api_depth_score_regrade(request: Request):
    """Force a re-grade of every node (or a subset via body). Runs
    synchronously so the caller gets the new totals immediately."""
    from app.services.lore_depth_grader import grade_nodes_parallel
    admin_user = await get_admin_user(request)

    llm_provider, _openai_key = await _resolve_lore_llm(admin_user)
    if not llm_provider:
        return JSONResponse(status_code=400, content={
            "error": "No LLM API key available. Configure a Cerebras, Anthropic, OpenAI, or Groq key to grade Lore."
        })

    try:
        body = await request.json()
    except Exception:
        body = {}
    nodes = body.get("nodes") if isinstance(body, dict) else None
    if not nodes:
        nodes = [
            "identity", "roles_and_responsibilities", "current_projects",
            "team_and_relationships", "tools_and_systems", "communication_style",
            "goals_and_priorities", "preferences_and_constraints",
            "domain_knowledge", "decision_log",
            "birth_chart", "human_design", "mbti", "big5",
        ]

    _uid, target_params = await _resolve_lore_target(admin_user)
    try:
        await grade_nodes_parallel(
            _LORE_MCP_BASE,
            _lore_internal_headers(),
            target_params,
            nodes,
            llm_provider=llm_provider,
            concurrency=4,
        )
    except Exception as exc:
        logger.exception("Depth regrade failed")
        return JSONResponse(status_code=502, content={"error": f"Regrade failed: {exc}"})

    # Fetch the refreshed breakdown
    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/depth-score",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})


# In-memory import job storage (single-tenant; upgrade to DB for multi-tenant)
_import_jobs: Dict[str, Dict[str, Any]] = {}
# Chunked upload sessions: upload_id -> {"path": str, "total_bytes": int}
_upload_sessions: Dict[str, Dict[str, Any]] = {}

import tempfile as _tempfile


@router.post("/lore/api/import/init")
async def lore_api_import_init(request: Request):
    """Initialize a chunked upload session. Returns an upload_id."""
    admin_user = await get_admin_user(request)
    body = await request.json()
    filename = body.get("filename", "")
    file_size = body.get("file_size", 0)

    if not filename.lower().endswith(".zip"):
        return JSONResponse(status_code=400, content={"error": "Please upload a ZIP file."})
    if file_size > 2 * 1024 * 1024 * 1024:
        return JSONResponse(status_code=400, content={"error": "File too large (max 2GB)."})

    upload_id = str(uuid.uuid4())
    tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp.close()
    _upload_sessions[upload_id] = {"path": tmp.name, "total_bytes": 0, "expected_size": file_size}
    return {"upload_id": upload_id}


@router.post("/lore/api/import/chunk/{upload_id}")
async def lore_api_import_chunk(upload_id: str, request: Request, file: UploadFile = File(...)):
    """Append a chunk to an in-progress upload session."""
    admin_user = await get_admin_user(request)
    session = _upload_sessions.get(upload_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Upload session not found."})

    chunk_data = await file.read()
    with open(session["path"], "ab") as f:
        f.write(chunk_data)
    session["total_bytes"] += len(chunk_data)

    return {"upload_id": upload_id, "bytes_received": session["total_bytes"]}


@router.post("/lore/api/import/finalize/{upload_id}")
async def lore_api_import_finalize(upload_id: str, request: Request):
    """Finalize the upload and start the import pipeline."""
    admin_user = await get_admin_user(request)
    session = _upload_sessions.pop(upload_id, None)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Upload session not found."})

    tmp_path = session["path"]
    try:
        with open(tmp_path, "rb") as f:
            zip_bytes = f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    llm_provider, openai_api_key = await _resolve_lore_llm(admin_user)
    if not llm_provider:
        return JSONResponse(status_code=400, content={
            "error": "No LLM API key available. Configure a Cerebras, Anthropic, OpenAI, or Groq key."
        })

    # Resolve Lore target (user_id + target_url/target_key)
    lore_user_id, lore_params = await _resolve_lore_target(admin_user)
    _lore_target_url = lore_params.get("target_url")
    _lore_target_key = lore_params.get("target_key")

    from app.services.lore_import_service import run_import_pipeline

    job_id = str(uuid.uuid4())
    _import_jobs[job_id] = {
        "status": "running",
        "step": "starting",
        "detail": "Initializing...",
        "_lore_user_id": lore_user_id,
        "_lore_target_url": _lore_target_url,
        "_lore_target_key": _lore_target_key,
    }

    async def progress_callback(step: str, detail: str):
        _import_jobs[job_id]["step"] = step
        _import_jobs[job_id]["detail"] = detail

    async def run_job():
        try:
            result = await run_import_pipeline(
                zip_bytes=zip_bytes,
                llm_provider=llm_provider,
                openai_api_key=openai_api_key,
                progress_callback=progress_callback,
                user_id=lore_user_id,
                target_url=_lore_target_url,
                target_key=_lore_target_key,
            )
            _import_jobs[job_id]["status"] = "complete"
            _import_jobs[job_id]["result"] = result
        except Exception as exc:
            logger.error(f"Import job {job_id} failed: {exc}", exc_info=True)
            _import_jobs[job_id]["status"] = "error"
            _import_jobs[job_id]["detail"] = str(exc)

    asyncio.create_task(run_job())
    return {"job_id": job_id, "status": "running"}


@router.get("/lore/api/import/{job_id}")
async def lore_api_import_status(job_id: str, request: Request):
    """Check import job status and get results."""
    admin_user = await get_admin_user(request)
    job = _import_jobs.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found."})
    return job


@router.post("/lore/api/import/{job_id}/apply")
async def lore_api_import_apply(job_id: str, request: Request):
    """Apply approved import proposals to the Lore."""
    admin_user = await get_admin_user(request)
    job = _import_jobs.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found."})
    if job.get("status") != "complete":
        return JSONResponse(status_code=400, content={"error": "Job is not complete yet."})

    body = await request.json()
    selected_categories = body.get("categories", [])

    proposals = job.get("result", {}).get("proposals", {})
    to_apply = {
        cat: prop for cat, prop in proposals.items()
        if cat in selected_categories
    }

    if not to_apply:
        return JSONResponse(status_code=400, content={"error": "No categories selected."})

    # Retrieve the Lore target stored on the job (set when the import was started)
    lore_user_id = job.get("_lore_user_id", "")
    lore_target_url = job.get("_lore_target_url")
    lore_target_key = job.get("_lore_target_key")

    # Fall back to resolving from current admin user if missing (shouldn't happen)
    if not lore_user_id:
        lore_user_id, _params = await _resolve_lore_target(admin_user)
        lore_target_url = _params.get("target_url")
        lore_target_key = _params.get("target_key")

    from app.services.lore_import_service import apply_proposals
    results = await apply_proposals(
        to_apply,
        user_id=lore_user_id,
        target_url=lore_target_url,
        target_key=lore_target_key,
    )
    return {"status": "applied", "results": results}


@router.post("/lore/api/add-content")
async def lore_api_add_content(request: Request, file: UploadFile = File(...)):
    """Process a single text or audio file and extract Lore insights."""
    admin_user = await get_admin_user(request)

    llm_provider, openai_api_key = await _resolve_lore_llm(admin_user)
    if not llm_provider:
        return JSONResponse(status_code=400, content={
            "error": "No LLM API key available. Configure a Cerebras, Anthropic, OpenAI, or Groq key."
        })

    filename = (file.filename or "").lower()
    file_bytes = await file.read()

    audio_extensions = {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".mp4", ".flac"}
    text_extensions = {".txt", ".md", ".csv", ".srt", ".json", ".jsonl"}
    suffix = os.path.splitext(filename)[1]

    texts = []
    source_label = "file"

    if suffix in audio_extensions:
        if not openai_api_key:
            return JSONResponse(status_code=400, content={
                "error": "Audio transcription requires an OpenAI API key."
            })
        source_label = "audio"
        import httpx as _hx
        try:
            async with _hx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {openai_api_key}"},
                    files={"file": (file.filename, file_bytes, f"audio/{suffix.lstrip('.')}")},
                    data={"model": "whisper-1"},
                )
                if resp.status_code == 200:
                    text = resp.json().get("text", "").strip()
                    if text:
                        texts.append(text)
                else:
                    return JSONResponse(status_code=502, content={
                        "error": f"Whisper transcription failed: HTTP {resp.status_code}"
                    })
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": f"Transcription failed: {exc}"})

    elif suffix in text_extensions:
        source_label = "text"
        try:
            content = file_bytes.decode("utf-8", errors="replace")
            if suffix in {".json", ".jsonl"}:
                for line in content.strip().splitlines():
                    try:
                        data = json.loads(line)
                        if isinstance(data, str):
                            texts.append(data)
                        elif isinstance(data, dict):
                            for val in data.values():
                                if isinstance(val, str) and len(val) > 20:
                                    texts.append(val)
                    except json.JSONDecodeError:
                        pass
                if not texts:
                    texts.append(content)
            else:
                texts.append(content)
        except Exception:
            texts.append(file_bytes.decode("latin-1", errors="replace"))

    elif suffix == ".pdf":
        source_label = "document"
        try:
            import PyPDF2
            import io as _io
            reader = PyPDF2.PdfReader(_io.BytesIO(file_bytes))
            for page in reader.pages:
                text = page.extract_text()
                if text and text.strip():
                    texts.append(text.strip())
        except Exception as exc:
            return JSONResponse(status_code=400, content={"error": f"Failed to read PDF: {exc}"})

    elif suffix in {".doc", ".docx"}:
        source_label = "document"
        try:
            import docx
            import io as _io
            doc = docx.Document(_io.BytesIO(file_bytes))
            for para in doc.paragraphs:
                if para.text.strip():
                    texts.append(para.text.strip())
        except Exception as exc:
            return JSONResponse(status_code=400, content={"error": f"Failed to read document: {exc}"})

    else:
        return JSONResponse(status_code=400, content={
            "error": f"Unsupported file type '{suffix}'. Supported: audio (.mp3, .wav, .m4a, .ogg, .webm, .flac), text (.txt, .md, .csv, .srt), documents (.pdf, .docx), or data (.json, .jsonl)."
        })

    if not texts:
        return JSONResponse(status_code=400, content={"error": "No content could be extracted from the file."})

    lore_user_id, lore_params = await _resolve_lore_target(admin_user)
    _lore_target_url = lore_params.get("target_url")
    _lore_target_key = lore_params.get("target_key")

    from app.services.lore_import_service import run_text_pipeline

    job_id = str(uuid.uuid4())
    _import_jobs[job_id] = {
        "status": "running",
        "step": "starting",
        "detail": "Initializing...",
        "_lore_user_id": lore_user_id,
        "_lore_target_url": _lore_target_url,
        "_lore_target_key": _lore_target_key,
    }

    async def progress_callback(step: str, detail: str):
        _import_jobs[job_id]["step"] = step
        _import_jobs[job_id]["detail"] = detail

    async def run_job():
        try:
            result = await run_text_pipeline(
                texts=texts,
                source_label=source_label,
                llm_provider=llm_provider,
                progress_callback=progress_callback,
                user_id=lore_user_id,
                target_url=_lore_target_url,
                target_key=_lore_target_key,
            )
            _import_jobs[job_id]["status"] = "complete"
            _import_jobs[job_id]["result"] = result
        except Exception as exc:
            logger.error(f"Add-content job {job_id} failed: {exc}", exc_info=True)
            _import_jobs[job_id]["status"] = "error"
            _import_jobs[job_id]["detail"] = str(exc)

    asyncio.create_task(run_job())
    return {"job_id": job_id, "status": "running"}


# ---------------------------------------------------------------------------
# Lore Social OAuth — LinkedIn, Twitter/X, Facebook
# ---------------------------------------------------------------------------

@router.get("/lore/api/social/authorize/{provider}")
async def lore_social_authorize(provider: str, request: Request):
    """Start OAuth flow for a social provider. Returns authorization URL."""
    admin_user = await get_admin_user(request)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")
    from app.services.lore_social_oauth_service import (
        linkedin_authorize_url, twitter_authorize_url, facebook_authorize_url,
    )
    try:
        if provider == "linkedin":
            url = linkedin_authorize_url(user_id)
        elif provider == "twitter":
            url = twitter_authorize_url(user_id)
        elif provider == "facebook":
            url = facebook_authorize_url(user_id)
        else:
            return JSONResponse(status_code=400, content={"error": f"Unknown provider: {provider}"})
        return {"authorization_url": url}
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.get("/oauth/lore/{provider}/callback")
async def lore_social_callback(provider: str, request: Request,
                                code: Optional[str] = Query(None),
                                state: Optional[str] = Query(None),
                                error: Optional[str] = Query(None)):
    """OAuth callback — exchanges code for token, fetches profile, starts Lore pipeline."""
    if error:
        return HTMLResponse(
            f"<html><body style='background:#000;color:#f56453;font-family:sans-serif;padding:40px;'>"
            f"<h2>Authorization Failed</h2><p>{error}</p>"
            f"<script>setTimeout(()=>window.close(),3000)</script></body></html>",
            status_code=400,
        )

    from app.services.lore_social_oauth_service import (
        decode_state,
        linkedin_exchange_code, linkedin_fetch_profile,
        twitter_exchange_code, twitter_fetch_profile,
        facebook_exchange_code, facebook_fetch_profile,
    )
    from app.services.lore_import_service import run_text_pipeline

    try:
        state_data = decode_state(state)
    except ValueError as exc:
        return HTMLResponse(
            f"<html><body style='background:#000;color:#f56453;font-family:sans-serif;padding:40px;'>"
            f"<h2>Invalid State</h2><p>{exc}</p></body></html>",
            status_code=400,
        )

    try:
        if provider == "linkedin":
            token_bundle = await linkedin_exchange_code(code)
            texts = await linkedin_fetch_profile(token_bundle.access_token)
        elif provider == "twitter":
            token_bundle = await twitter_exchange_code(code, state)
            texts = await twitter_fetch_profile(token_bundle.access_token)
        elif provider == "facebook":
            token_bundle = await facebook_exchange_code(code)
            texts = await facebook_fetch_profile(token_bundle.access_token)
        else:
            return HTMLResponse("<p>Unknown provider</p>", status_code=400)
    except Exception as exc:
        logger.error(f"Lore OAuth {provider} failed: {exc}", exc_info=True)
        return HTMLResponse(
            f"<html><body style='background:#000;color:#f56453;font-family:sans-serif;padding:40px;'>"
            f"<h2>Connection Failed</h2><p>{exc}</p>"
            f"<script>setTimeout(()=>window.close(),5000)</script></body></html>",
            status_code=502,
        )

    if not texts:
        return HTMLResponse(
            "<html><body style='background:#000;color:#fbbf24;font-family:sans-serif;padding:40px;'>"
            "<h2>Connected</h2><p>No content found to import. The window will close shortly.</p>"
            "<script>if(window.opener){window.opener.postMessage({type:'lore-social-done',provider:'"
            + provider + "',job_id:null},'*');}setTimeout(()=>window.close(),3000)</script></body></html>",
        )

    # Resolve LLM and Lore target from state
    admin_user_id = state_data.get("admin_user_id", "")
    admin_user_stub = {"user_id": admin_user_id, "is_super_admin": True}
    llm_provider, _ = await _resolve_lore_llm(admin_user_stub)
    lore_user_id, lore_params = await _resolve_lore_target(admin_user_stub)
    _lore_target_url = lore_params.get("target_url")
    _lore_target_key = lore_params.get("target_key")

    if not llm_provider:
        return HTMLResponse(
            "<html><body style='background:#000;color:#f56453;font-family:sans-serif;padding:40px;'>"
            "<h2>No LLM Key</h2><p>Cannot process — no LLM API key configured.</p>"
            "<script>setTimeout(()=>window.close(),5000)</script></body></html>",
            status_code=400,
        )

    job_id = str(uuid.uuid4())
    _import_jobs[job_id] = {
        "status": "running",
        "step": "starting",
        "detail": f"Processing {provider} data...",
        "_lore_user_id": lore_user_id,
        "_lore_target_url": _lore_target_url,
        "_lore_target_key": _lore_target_key,
    }

    async def progress_callback(step: str, detail: str):
        _import_jobs[job_id]["step"] = step
        _import_jobs[job_id]["detail"] = detail

    async def run_job():
        try:
            result = await run_text_pipeline(
                texts=texts,
                source_label=provider,
                llm_provider=llm_provider,
                progress_callback=progress_callback,
                user_id=lore_user_id,
                target_url=_lore_target_url,
                target_key=_lore_target_key,
            )
            _import_jobs[job_id]["status"] = "complete"
            _import_jobs[job_id]["result"] = result
        except Exception as exc:
            logger.error(f"Lore social import {provider} failed: {exc}", exc_info=True)
            _import_jobs[job_id]["status"] = "error"
            _import_jobs[job_id]["detail"] = str(exc)

    asyncio.create_task(run_job())

    return HTMLResponse(
        "<html><body style='background:#000;color:#01a4a6;font-family:sans-serif;padding:40px;'>"
        f"<h2>Connected to {provider.title()}</h2>"
        "<p>Processing your data... this window will close shortly.</p>"
        "<script>if(window.opener){window.opener.postMessage({type:'lore-social-done',provider:'"
        + provider + "',job_id:'" + job_id + "'},'*');}setTimeout(()=>window.close(),2000)</script></body></html>",
    )


@router.post("/lore/api/social/twitter-scrape")
async def lore_social_twitter_scrape(request: Request):
    """Scrape public tweets from an X/Twitter username and run Lore extraction."""
    admin_user = await get_admin_user(request)
    body = await request.json()
    username = (body.get("username") or "").strip().lstrip("@")
    if not username:
        return JSONResponse(status_code=400, content={"error": "Please provide a username."})
    if not all(c.isalnum() or c == "_" for c in username):
        return JSONResponse(status_code=400, content={"error": "Invalid username."})

    llm_provider, _ = await _resolve_lore_llm(admin_user)
    if not llm_provider:
        return JSONResponse(status_code=400, content={
            "error": "No LLM API key available."
        })

    lore_user_id, lore_params = await _resolve_lore_target(admin_user)
    _lore_target_url = lore_params.get("target_url")
    _lore_target_key = lore_params.get("target_key")

    job_id = str(uuid.uuid4())
    _import_jobs[job_id] = {
        "status": "running",
        "step": "starting",
        "detail": f"Fetching @{username} posts...",
        "_lore_user_id": lore_user_id,
        "_lore_target_url": _lore_target_url,
        "_lore_target_key": _lore_target_key,
    }

    async def progress_callback(step: str, detail: str):
        _import_jobs[job_id]["step"] = step
        _import_jobs[job_id]["detail"] = detail

    async def run_job():
        try:
            await progress_callback("parsing", f"Fetching posts from @{username}...")

            texts = []

            # Use Twitter API v2 with bearer token if configured
            bearer = os.getenv("TWITTER_BEARER_TOKEN", "")
            if bearer:
                try:
                    async with _httpx.AsyncClient(timeout=15.0) as client:
                        # Look up user ID
                        resp = await client.get(
                            f"https://api.twitter.com/2/users/by/username/{username}",
                            params={"user.fields": "name,description,location"},
                            headers={"Authorization": f"Bearer {bearer}"},
                        )
                        if resp.status_code == 200:
                            user_data = resp.json().get("data", {})
                            user_id = user_data.get("id")
                            name = user_data.get("name", "")
                            bio = user_data.get("description", "")
                            loc = user_data.get("location", "")
                            if name:
                                texts.append(f"X profile: {name} (@{username}). Bio: {bio}. Location: {loc}.")

                            if user_id:
                                resp = await client.get(
                                    f"https://api.twitter.com/2/users/{user_id}/tweets",
                                    params={"max_results": 100, "tweet.fields": "text,created_at"},
                                    headers={"Authorization": f"Bearer {bearer}"},
                                )
                                if resp.status_code == 200:
                                    for tweet in resp.json().get("data", []):
                                        text = tweet.get("text", "")
                                        if text and not text.startswith("RT @"):
                                            texts.append(f"Tweet from @{username}: {text}")
                        elif resp.status_code == 404:
                            raise ValueError(f"User @{username} not found on X.")
                        else:
                            logger.warning(f"Twitter API returned {resp.status_code} for @{username}")
                except ValueError:
                    raise
                except Exception as exc:
                    logger.warning(f"Twitter API failed for @{username}: {exc}")

            if not texts:
                raise ValueError(
                    f"Could not fetch posts for @{username}. "
                    f"Please ensure TWITTER_BEARER_TOKEN is set in the environment, "
                    f"or use the 'Document / Text' option to paste tweets directly."
                )

            logger.info(f"Twitter scrape: fetched {len(texts)} text blocks for @{username}")

            from app.services.lore_import_service import run_text_pipeline
            result = await run_text_pipeline(
                texts=texts,
                source_label=f"x/@{username}",
                llm_provider=llm_provider,
                progress_callback=progress_callback,
                user_id=lore_user_id,
                target_url=_lore_target_url,
                target_key=_lore_target_key,
            )
            _import_jobs[job_id]["status"] = "complete"
            _import_jobs[job_id]["result"] = result
        except Exception as exc:
            logger.error(f"Twitter scrape job failed: {exc}", exc_info=True)
            _import_jobs[job_id]["status"] = "error"
            _import_jobs[job_id]["detail"] = str(exc)

    asyncio.create_task(run_job())
    return {"job_id": job_id, "status": "running"}


@router.get("/lore/api/social/status")
async def lore_social_status(request: Request):
    """Check which social providers are configured."""
    astrology_ready = bool(os.getenv("ASTROLOGY_API_IO_KEY"))
    return {
        "linkedin": bool(os.getenv("LINKEDIN_OAUTH_CLIENT_ID")),
        "twitter": bool(os.getenv("TWITTER_BEARER_TOKEN")),
        "facebook": bool(os.getenv("FACEBOOK_OAUTH_CLIENT_ID")),
        "astrology": astrology_ready,
        "human_design": astrology_ready,
    }


# ---------------------------------------------------------------------------
# Lore Personality — Myers-Briggs + Big Five
# ---------------------------------------------------------------------------

@router.get("/lore/api/personality")
async def lore_api_personality_get(request: Request):
    """Return the user's current MBTI + Big5. Either half can be None."""
    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/personality",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})


@router.post("/lore/api/personality")
async def lore_api_personality_save(request: Request):
    """Manual save — body: {mbti: {type, summary}, big5: {openness, ..., summary}}.
    Either half can be omitted; unspecified halves are left alone on the row."""
    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    body = await request.json()

    # Inject source='manual' unless caller explicitly overrides (e.g. analyze endpoint)
    if isinstance(body.get("mbti"), dict) and not body["mbti"].get("source"):
        body["mbti"]["source"] = "manual"
    if isinstance(body.get("big5"), dict) and not body["big5"].get("source"):
        body["big5"]["source"] = "manual"

    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{_LORE_MCP_BASE}/admin-api/personality",
                params=target_params,
                headers=_lore_internal_headers(),
                json=body,
            )
            if resp.status_code != 200:
                return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
            result = resp.json()
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})

    # Background LLM regrade for whichever halves were touched
    touched = []
    if isinstance(body.get("mbti"), dict):
        touched.append("mbti")
    if isinstance(body.get("big5"), dict):
        touched.append("big5")
    _schedule_depth_grade(admin_user, touched)
    return result


@router.post("/lore/api/personality/analyze")
async def lore_api_personality_analyze(request: Request):
    """AI Analysis — pulls all lore categories, runs the configured LLM against
    them, and persists the inferred MBTI or Big5 (body: {kind: 'mbti'|'big5'})."""
    from app.services.personality_service import (
        PersonalityAnalysisError,
        analyze_big5_from_lore,
        analyze_mbti_from_lore,
    )

    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    body = await request.json()
    kind = (body.get("kind") or "").strip().lower()
    if kind not in ("mbti", "big5"):
        return JSONResponse(status_code=400, content={"error": "kind must be 'mbti' or 'big5'"})

    llm_provider, _openai_key = await _resolve_lore_llm(admin_user)
    if not llm_provider:
        return JSONResponse(status_code=400, content={
            "error": "No LLM API key available. Configure Cerebras, Anthropic, OpenAI, or Groq before running AI analysis."
        })

    # Pull all 10 category files in parallel
    categories: Dict[str, str] = {}
    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            cats_resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/categories",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if cats_resp.status_code != 200:
                return JSONResponse(status_code=502, content={"error": f"category list failed: {cats_resp.text}"})
            cat_keys = [item["key"] for item in cats_resp.json() if item.get("key")]

            async def fetch_one(k: str):
                r = await client.get(
                    f"{_LORE_MCP_BASE}/admin-api/category/{k}",
                    params=target_params,
                    headers=_lore_internal_headers(),
                )
                return k, (r.json().get("content") if r.status_code == 200 else "") or ""

            results = await asyncio.gather(*[fetch_one(k) for k in cat_keys])
            for k, content in results:
                categories[k] = content
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Failed to load lore: {exc}"})

    try:
        if kind == "mbti":
            result = await analyze_mbti_from_lore(categories, llm_provider=llm_provider)
            put_body = {
                "mbti": {
                    "type": result["mbti_type"],
                    "summary": result["mbti_summary"],
                    "source": "ai_analysis",
                },
                "analysis_model": result["analysis_model"],
            }
        else:
            result = await analyze_big5_from_lore(categories, llm_provider=llm_provider)
            put_body = {
                "big5": {
                    "openness":          result["big5_openness"],
                    "conscientiousness": result["big5_conscientiousness"],
                    "extraversion":      result["big5_extraversion"],
                    "agreeableness":     result["big5_agreeableness"],
                    "neuroticism":       result["big5_neuroticism"],
                    "summary":           result["big5_summary"],
                    "source":            "ai_analysis",
                },
                "analysis_model": result["analysis_model"],
            }
    except PersonalityAnalysisError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:
        logger.exception("Personality analysis failed")
        return JSONResponse(status_code=500, content={"error": f"Analysis failed: {exc}"})

    # Persist
    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            put_resp = await client.put(
                f"{_LORE_MCP_BASE}/admin-api/personality",
                params=target_params,
                headers=_lore_internal_headers(),
                json=put_body,
            )
            if put_resp.status_code != 200:
                return JSONResponse(status_code=put_resp.status_code, content={"error": put_resp.text})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Failed to persist: {exc}"})

    _schedule_depth_grade(admin_user, [kind])
    return {"status": "ok", "kind": kind, **put_body, "model": result.get("analysis_model")}


# ---------------------------------------------------------------------------
# Lore Astrology — birth chart + Human Design (via astrology-api.io)
# ---------------------------------------------------------------------------

@router.get("/lore/api/me/profile")
async def lore_api_me_profile(request: Request):
    """Return the current user's profile — full name, email, avatar URL."""
    admin_user = await get_admin_user(request)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")
    profile_full_name = admin_user.get("full_name") or admin_user.get("email") or ""
    avatar_url = None
    if user_id and user_id != "dev-admin":
        try:
            from app.integrations.supabase_client import supabase_manager
            resp = (
                supabase_manager.admin_client
                .table("profiles")
                .select("full_name,email,metadata")
                .eq("user_id", user_id)
                .execute()
            )
            rows = resp.data or []
            if rows:
                row = rows[0]
                profile_full_name = row.get("full_name") or profile_full_name
                meta = row.get("metadata") or {}
                if isinstance(meta, dict):
                    avatar_url = meta.get("avatar_url")
        except Exception as exc:
            logger.warning(f"lore_api_me_profile failed: {exc}")
    return {
        "user_id": user_id,
        "full_name": profile_full_name,
        "email": admin_user.get("email"),
        "avatar_url": avatar_url,
    }


@router.post("/lore/api/me/avatar")
async def lore_api_me_avatar_upload(
    request: Request,
    file: UploadFile = File(...),
):
    """Upload a profile avatar. Saved locally under static/images/avatars/profile/
    and the URL is persisted on `profiles.metadata.avatar_url`."""
    admin_user = await get_admin_user(request)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")
    if not user_id or user_id == "dev-admin":
        return JSONResponse(status_code=400, content={"error": "No user context"})

    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        return JSONResponse(status_code=400, content={"error": "File must be an image"})

    contents = await file.read()
    if not contents:
        return JSONResponse(status_code=400, content={"error": "Empty file"})
    if len(contents) > 5 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"error": "Image exceeds 5 MB limit"})

    # Extension
    suffix = ".png"
    if file.filename:
        original = Path(file.filename).suffix.lower()
        if original in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
            suffix = original
    elif "jpeg" in content_type or "jpg" in content_type:
        suffix = ".jpg"
    elif "webp" in content_type:
        suffix = ".webp"
    elif "gif" in content_type:
        suffix = ".gif"

    # Save locally, one file per user. Cache-bust via a mtime query string on the URL.
    avatar_dir = Path("/app/app/static/images/avatars/profile")
    avatar_dir.mkdir(parents=True, exist_ok=True)

    # Remove any prior avatar file for this user (any extension) so we don't leak
    for prior in avatar_dir.glob(f"{user_id}.*"):
        try:
            prior.unlink()
        except Exception:
            pass

    destination = avatar_dir / f"{user_id}{suffix}"
    try:
        destination.write_bytes(contents)
    except Exception as exc:
        logger.error(f"Failed to save profile avatar: {exc}")
        return JSONResponse(status_code=500, content={"error": "Failed to save image"})

    # Cache-bust so the browser doesn't serve stale versions after a re-upload
    import time as _time
    public_url = f"/static/images/avatars/profile/{user_id}{suffix}?v={int(_time.time())}"

    # Persist on profiles.metadata.avatar_url. `profiles.user_id` has no
    # unique constraint, so we can't use an ON CONFLICT upsert — do an
    # explicit select-then-insert-or-update instead.
    try:
        from app.integrations.supabase_client import supabase_manager
        sb = supabase_manager.admin_client
        existing = (
            sb.table("profiles")
            .select("id,metadata,full_name,email")
            .eq("user_id", user_id)
            .execute()
        )
        rows = (existing.data if existing else None) or []
        row = rows[0] if rows else None

        meta: Dict[str, Any] = {}
        if row and isinstance(row.get("metadata"), dict):
            meta = row["metadata"]
        meta["avatar_url"] = public_url

        if row:
            sb.table("profiles").update(
                {"metadata": meta}
            ).eq("id", row["id"]).execute()
        else:
            sb.table("profiles").insert({
                "user_id": user_id,
                "full_name": admin_user.get("full_name") or "",
                "email": admin_user.get("email") or "",
                "metadata": meta,
            }).execute()
    except Exception as exc:
        logger.warning(f"Failed to persist avatar URL on profile: {exc}", exc_info=True)
        return JSONResponse(status_code=500, content={
            "error": f"Saved image but failed to persist URL: {exc}",
        })

    return {"status": "ok", "avatar_url": public_url}


@router.get("/lore/api/mcp-visibility")
async def lore_api_mcp_visibility_get(request: Request):
    """Return the per-node visibility flags for the current user."""
    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/mcp-visibility",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})


@router.put("/lore/api/mcp-visibility")
async def lore_api_mcp_visibility_put(request: Request):
    """Update one or more node visibility flags. Body: `{node: bool, ...}`."""
    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    body = await request.json()
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.put(
                f"{_LORE_MCP_BASE}/admin-api/mcp-visibility",
                params=target_params,
                headers=_lore_internal_headers(),
                json=body,
            )
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})


@router.get("/lore/api/astrology/city-search")
async def lore_api_astrology_city_search(request: Request, q: str = ""):
    """Proxy city autosuggest for the birth-place field. Returns a small list
    of `{label, city, country_code}` tuples sourced from Nominatim/OSM, so the
    values we send to astrology-api.io match its expected `city` +
    `country_code` shape."""
    await get_admin_user(request)
    from app.services.astrology_service import search_cities
    try:
        results = await search_cities(q)
    except Exception as exc:
        logger.warning(f"City search failed: {exc}")
        results = []
    return {"results": results}


@router.get("/lore/api/astrology")
async def lore_api_astrology_get(request: Request):
    """Return the summary fields for the left-sidebar astrology cards."""
    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/astrology",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})


@router.get("/lore/api/astrology/birth-chart")
async def lore_api_astrology_birth_chart(request: Request):
    """Full birth chart JSON for the 'View chart' modal."""
    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/astrology/full",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code != 200:
                return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
            data = resp.json()
            return {
                "full_name": data.get("full_name"),
                "birth_date": data.get("birth_date"),
                "birth_time": data.get("birth_time"),
                "birth_place": data.get("birth_place"),
                "sun_sign": data.get("sun_sign"),
                "chart_json": data.get("chart_json"),
                "birth_chart_analysis": data.get("birth_chart_analysis"),
                "updated_at": data.get("updated_at"),
            }
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})


@router.get("/lore/api/astrology/human-design")
async def lore_api_astrology_human_design(request: Request):
    """Full Human Design JSON + LLM analysis for the 'View report' modal."""
    admin_user = await get_admin_user(request)
    _uid, target_params = await _resolve_lore_target(admin_user)
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/astrology/full",
                params=target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code != 200:
                return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
            data = resp.json()
            return {
                "hd_type": data.get("hd_type"),
                "hd_strategy": data.get("hd_strategy"),
                "hd_authority": data.get("hd_authority"),
                "hd_profile": data.get("hd_profile"),
                "human_design_json": data.get("human_design_json"),
                "human_design_analysis": data.get("human_design_analysis"),
                "analysis_model": data.get("analysis_model"),
                "updated_at": data.get("updated_at"),
            }
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})


@router.post("/lore/api/astrology/connect")
async def lore_api_astrology_connect(request: Request):
    """Fetch birth chart + Human Design from astrology-api.io, run LLM analysis,
    and persist the result via the Lore MCP. Returns the summary fields for
    immediate sidebar render."""
    from datetime import date as _date, time as _time
    from app.services.astrology_service import (
        AstrologyAPIError,
        analyze_birth_chart,
        analyze_human_design,
        build_subject,
        extract_hd_summary,
        extract_sun_sign,
        fetch_birth_chart_and_hd,
    )

    admin_user = await get_admin_user(request)

    llm_provider, _openai_api_key = await _resolve_lore_llm(admin_user)
    if not llm_provider:
        return JSONResponse(status_code=400, content={
            "error": "No LLM API key available. Configure a Cerebras, Anthropic, OpenAI, or Groq key before connecting Human Design."
        })

    body = await request.json()
    full_name = (body.get("full_name") or "").strip()
    birth_date_str = (body.get("birth_date") or "").strip()
    birth_time_str = (body.get("birth_time") or "").strip()
    birth_place = (body.get("birth_place") or "").strip()
    resolved_city = (body.get("city") or "").strip() or None
    resolved_country = (body.get("country_code") or "").strip().upper() or None

    if not birth_date_str or not birth_time_str or not birth_place:
        return JSONResponse(status_code=400, content={
            "error": "birth_date, birth_time, and birth_place are required."
        })
    if not resolved_city or not resolved_country:
        return JSONResponse(status_code=400, content={
            "error": "Please pick a birth place from the suggestions list so we can resolve city + country.",
        })

    try:
        birth_date = _date.fromisoformat(birth_date_str)
        hh, mm = birth_time_str.split(":")[:2]
        birth_time = _time(int(hh), int(mm))
    except Exception as exc:
        return JSONResponse(status_code=400, content={
            "error": f"Invalid birth_date or birth_time: {exc}"
        })

    subject = build_subject(
        full_name,
        birth_date,
        birth_time,
        birth_place,
        city=resolved_city,
        country_code=resolved_country,
    )

    try:
        chart_json, hd_json = await fetch_birth_chart_and_hd(subject)
    except AstrologyAPIError as exc:
        logger.error(f"Astrology API error: {exc}")
        return JSONResponse(status_code=502, content={"error": str(exc)})
    except Exception as exc:
        logger.exception("Unexpected error calling astrology-api.io")
        return JSONResponse(status_code=502, content={"error": f"astrology-api.io error: {exc}"})

    sun_sign = extract_sun_sign(chart_json)
    hd_summary = extract_hd_summary(hd_json)

    subject_name_for_llm = full_name or (admin_user.get("full_name") or admin_user.get("email") or "").strip() or None

    try:
        chart_analysis, _chart_model = await analyze_birth_chart(
            chart_json,
            llm_provider=llm_provider,
            subject_name=subject_name_for_llm,
        )
    except AstrologyAPIError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    except Exception as exc:
        logger.exception("Birth chart LLM analysis failed")
        return JSONResponse(status_code=502, content={"error": f"LLM analysis failed: {exc}"})

    try:
        hd_analysis, analysis_model = await analyze_human_design(
            hd_json,
            llm_provider=llm_provider,
            subject_name=subject_name_for_llm,
        )
    except AstrologyAPIError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    except Exception as exc:
        logger.exception("Human Design LLM analysis failed")
        return JSONResponse(status_code=502, content={"error": f"LLM analysis failed: {exc}"})

    payload = {
        "full_name": full_name or None,
        "birth_date": birth_date_str,
        "birth_time": birth_time_str if len(birth_time_str.split(":")) == 3 else f"{birth_time_str}:00",
        "birth_place": birth_place,
        "city": resolved_city,
        "country_code": resolved_country,
        "sun_sign": sun_sign,
        "hd_type": hd_summary.get("type"),
        "hd_strategy": hd_summary.get("strategy"),
        "hd_authority": hd_summary.get("authority"),
        "hd_profile": hd_summary.get("profile"),
        "chart_json": chart_json,
        "human_design_json": hd_json,
        "birth_chart_analysis": chart_analysis,
        "human_design_analysis": hd_analysis,
        "analysis_model": analysis_model,
    }

    _uid, target_params = await _resolve_lore_target(admin_user)
    try:
        async with _httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.put(
                f"{_LORE_MCP_BASE}/admin-api/astrology",
                params=target_params,
                headers=_lore_internal_headers(),
                json=payload,
            )
            if resp.status_code != 200:
                return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": f"Lore MCP unreachable: {exc}"})

    _schedule_depth_grade(admin_user, ["birth_chart", "human_design"])

    return {
        "status": "ok",
        "sun_sign": sun_sign,
        "hd_type": hd_summary.get("type"),
        "hd_strategy": hd_summary.get("strategy"),
        "hd_authority": hd_summary.get("authority"),
        "hd_profile": hd_summary.get("profile"),
    }


# ---------------------------------------------------------------------------
# Lore Voice Interview
# ---------------------------------------------------------------------------

@router.post("/lore/api/interview/start")
async def lore_interview_start(request: Request):
    """Start a Lore voice interview session. Returns LiveKit room credentials."""
    admin_user = await get_admin_user(request)
    user_id = str(admin_user.get("user_id") or admin_user.get("id") or "")

    # Resolve Lore target (user's home Supabase) — same resolver used everywhere else
    lore_user_id, lore_target_params = await _resolve_lore_target(admin_user)

    # Resolve client context for the LiveKit room (separate from Lore target)
    client_ids = admin_user.get("visible_client_ids", [])
    primary_client_id = admin_user.get("primary_client_id") or (client_ids[0] if client_ids else None)

    if not primary_client_id and admin_user.get("is_super_admin"):
        try:
            from app.integrations.supabase_client import supabase_manager
            result = (
                supabase_manager.admin_client
                .table("clients")
                .select("id")
                .eq("name", "Leandrew Dixon")
                .maybe_single()
                .execute()
            )
            if result and result.data:
                primary_client_id = result.data["id"]
        except Exception as exc:
            logger.warning(f"Failed to resolve superadmin home client: {exc}")

    if not primary_client_id:
        return JSONResponse(status_code=400, content={"error": "No client context available."})

    # Fetch depth score from the user's Lore target to decide interview scope
    target_categories = []
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_LORE_MCP_BASE}/admin-api/depth-score",
                params=lore_target_params,
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                for layer in resp.json().get("layers", []):
                    if layer["level"] in ("not_captured", "emerging", "growing"):
                        target_categories.append(layer["key"])
    except Exception:
        pass

    if not target_categories:
        return JSONResponse(status_code=200, content={
            "status": "no_gaps",
            "message": "All Lore categories have strong coverage. No interview needed.",
        })

    # Create a wizard session for tracking progress
    from app.services.wizard_session_service import WizardSessionService
    session_service = WizardSessionService()
    session = await session_service.create_session(user_id, primary_client_id)
    session_id = session["id"]

    # Get client credentials for LiveKit room
    from app.utils.supabase_credentials import SupabaseCredentialManager
    client_url, client_anon, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(primary_client_id)

    # Load Farah's live settings — same function the Sidekick Creation Wizard uses.
    # This gets her voice/sound settings AND the provider API keys she needs
    # (Deepgram, Cartesia, Cerebras, etc.) from the Autonomite client record.
    from app.api.v1.wizard import _load_farah_live_settings
    voice_settings, sound_settings, farah_provider_keys = await _load_farah_live_settings()

    # Merge client API keys with Farah's provider keys (Farah's keys take precedence
    # for providers she uses — the client's own keys are for their sidekick, not Farah)
    api_keys = {}
    try:
        from app.core.dependencies import get_client_service
        client_service = get_client_service()
        client_obj = await client_service.get_client(primary_client_id)
        if client_obj and client_obj.settings and client_obj.settings.api_keys:
            keys = client_obj.settings.api_keys
            for k in ["cerebras_api_key", "openai_api_key", "anthropic_api_key", "groq_api_key",
                       "cartesia_api_key", "deepgram_api_key", "elevenlabs_api_key", "siliconflow_api_key"]:
                val = getattr(keys, k, None)
                if val:
                    api_keys[k] = val
    except Exception:
        pass
    # Farah's provider keys override — she needs her own keys for whatever providers she's configured with
    api_keys = {k: v for k, v in {**api_keys, **farah_provider_keys}.items() if v}

    # Create LiveKit room
    from app.config import settings as app_settings
    from livekit import api as lk_api

    room_name = f"lore-interview-{session_id[:8]}"
    conversation_id = str(uuid.uuid4())

    room_metadata = {
        "type": "wizard_guide",
        "wizard_config": {
            "session_id": session_id,
            "wizard_type": "lore_interview",
            "current_step": 1,
            "form_data": {},
            "target_categories": target_categories,
            "lore": {
                "user_id": lore_user_id,
                "target_url": lore_target_params.get("target_url"),
                "target_key": lore_target_params.get("target_key"),
            },
        },
        "client_id": primary_client_id,
        "user_id": user_id,
        "agent_slug": "lore-interviewer",
        "agent_name": "Lore Interviewer",
        "conversation_id": conversation_id,
        "system_prompt": "",
        "voice_settings": voice_settings,
        "sound_settings": sound_settings,
        "api_keys": api_keys,
        "supabase_url": client_url,
        "supabase_anon_key": client_anon,
        "supabase_service_role_key": client_key,
        "platform_supabase_url": app_settings.supabase_url,
        "platform_supabase_service_role_key": app_settings.supabase_service_role_key,
    }

    lk_url = app_settings.livekit_url
    lk_key = app_settings.livekit_api_key
    lk_secret = app_settings.livekit_api_secret

    lk = lk_api.LiveKitAPI(lk_url, lk_key, lk_secret)
    try:
        await lk.room.create_room(lk_api.CreateRoomRequest(
            name=room_name,
            metadata=json.dumps(room_metadata),
        ))
    except Exception as exc:
        logger.warning(f"Room create (may already exist): {exc}")

    # Dispatch agent
    agent_name = os.getenv("AGENT_NAME", os.getenv("LIVEKIT_AGENT_NAME", "sidekick-agent"))
    try:
        await lk.agent_dispatch.create_dispatch(
            lk_api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name=agent_name,
                metadata=json.dumps(room_metadata),
            )
        )
    except Exception as exc:
        logger.warning(f"Agent dispatch: {exc}")

    # Generate participant token
    token = lk_api.AccessToken(lk_key, lk_secret)
    token.with_identity(f"user-{user_id[:8]}")
    token.with_name("Lore Interview User")
    token.with_grants(lk_api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
    ))

    await lk.aclose()

    return {
        "status": "started",
        "session_id": session_id,
        "room_name": room_name,
        "token": token.to_jwt(),
        "ws_url": lk_url,
        "target_categories": target_categories,
    }
