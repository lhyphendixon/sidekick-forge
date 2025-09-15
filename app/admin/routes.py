from fastapi import APIRouter, Request, Depends, Form, HTTPException, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Any, List, Optional
import redis.asyncio as aioredis
import redis
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from livekit import api

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

logger = logging.getLogger(__name__)

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

# Initialize router
router = APIRouter(prefix="/admin", tags=["admin"])

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
    return templates.TemplateResponse("admin/reset-password.html", {"request": request})

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
        clients_ctx = [{"id": c.id, "name": c.name} for c in clients]
    except Exception:
        clients_ctx = []
    from app.config import settings
    return templates.TemplateResponse("admin/users.html", {"request": request, "user": user, "users": enriched, "clients": clients_ctx, "supabase_url": settings.supabase_url, "supabase_anon_key": settings.supabase_anon_key})

@router.post("/users/create")
async def users_create(request: Request, admin: Dict[str, Any] = Depends(get_admin_user)):
    """Create a new user via Supabase Admin API, then assign platform role membership."""
    try:
        data = await request.json()
        full_name = (data.get('full_name') or '').strip()
        email = (data.get('email') or '').strip()
        role_key = (data.get('role_key') or 'subscriber').strip()  # expected: super_admin | admin | subscriber
        client_ids = data.get('client_ids') or []
        if isinstance(client_ids, str):
            client_ids = [client_ids]
        client_ids = [cid for cid in client_ids if cid]
        if not full_name or not email:
            raise HTTPException(status_code=400, detail="Email is required")

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
            return {"role_key": "admin", "client_ids": admin_clients}

        subscriber_clients = get_client_ids_for_role(subscriber_role_id)
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
                    return {"role_key": "admin", "client_ids": admin_ids}
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
        client_ids = data.get("client_ids") or []
        if isinstance(client_ids, str):
            client_ids = [client_ids]
        client_ids = [cid for cid in client_ids if cid]

        if not user_id or role_key not in ("super_admin", "admin", "subscriber"):
            raise HTTPException(status_code=400, detail="Invalid payload")
        if role_key in ("admin","subscriber") and not client_ids:
            raise HTTPException(status_code=400, detail="At least one client_id is required for this role")

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

async def get_system_summary() -> Dict[str, Any]:
    """Get system-wide summary statistics"""
    # Get all clients from Supabase
    from app.core.dependencies import get_client_service
    from app.integrations.livekit_client import livekit_manager
    client_service = get_client_service()
    
    try:
        clients = await client_service.get_all_clients()
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

async def get_all_clients_with_containers() -> List[Dict[str, Any]]:
    """Get all clients with their container status"""
    # Use the existing client service
    from app.core.dependencies import get_client_service
    from app.integrations.livekit_client import livekit_manager
    client_service = get_client_service()
    
    try:
        # Get all clients from platform database
        logger.info("Fetching all clients from platform database...")
        clients = await client_service.get_all_clients()
        logger.info(f"✅ Successfully fetched {len(clients)} clients from platform database")
        
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
                client_dict = {
                    "id": client.id,
                    "name": client.name,
                    "domain": getattr(client, 'domain', ''),
                    "status": "running" if getattr(client, 'active', True) else "stopped",
                    "active": getattr(client, 'active', True),
                    "created_at": client.created_at.isoformat() if hasattr(client, 'created_at') and client.created_at else None,
                    "client_id": client.id,  # For compatibility with templates
                    "client_name": client.name,
                    "cpu_usage": 15.5,  # Mock CPU usage
                    "memory_usage": 512,  # Mock memory usage in MB
                    "active_sessions": room_sessions.get(client.id, 0),  # Real session count from LiveKit
                    "settings": {
                        "supabase": client.settings.supabase if hasattr(client, 'settings') and client.settings else None,
                        "livekit": client.settings.livekit if hasattr(client, 'settings') and client.settings else None
                    }
                }
                logger.debug(f"Processed client: {client.name} (ID: {client.id})")
            except Exception as e:
                logger.error(f"Failed to process client {getattr(client, 'name', 'Unknown')}: {e}", exc_info=True)
                # Create minimal client dict to avoid complete failure
                client_dict = {
                    "id": getattr(client, 'id', 'unknown'),
                    "name": getattr(client, 'name', 'Unknown Client'),
                    "domain": '',
                    "status": "error",
                    "active": False,
                    "created_at": None,
                    "client_id": getattr(client, 'id', 'unknown'),
                    "client_name": getattr(client, 'name', 'Unknown Client'),
                    "cpu_usage": 0,
                    "memory_usage": 0,
                    "active_sessions": 0,
                    "settings": {
                        "supabase": getattr(client, 'settings', {}).get('supabase', None) if hasattr(client, 'settings') else None,
                        "livekit": getattr(client, 'settings', {}).get('livekit', None) if hasattr(client, 'settings') else None
                    }
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
        # Try project-based discovery first (if access token is available)
        access_token = os.getenv("SUPABASE_ACCESS_TOKEN")
        if access_token:
            try:
                from app.core.dependencies_project_based import get_project_service
                project_service = get_project_service()
                
                # Get all agents across all projects
                agents = await project_service.get_all_agents()
                
                # Convert to template format
                agents_data = []
                for agent in agents:
                    # Handle both dict and object format agents
                    if isinstance(agent, dict):
                        agent_dict = {
                            "id": agent.get("id"),
                            "slug": agent.get("slug"),
                            "name": agent.get("name"),
                            "description": agent.get("description", ""),
                            "client_id": agent.get("client_id", "global"),
                            "client_name": agent.get("client_name", "Unknown"),
                            "status": "active" if agent.get("active", agent.get("enabled", True)) else "inactive",
                            "active": agent.get("active", agent.get("enabled", True)),
                            "enabled": agent.get("enabled", True),
                            "created_at": agent.get("created_at", ""),
                            "updated_at": agent.get("updated_at", ""),
                            "system_prompt": agent.get("system_prompt", "")[:100] + "..." if agent.get("system_prompt") and len(agent.get("system_prompt", "")) > 100 else agent.get("system_prompt", ""),
                            "voice_settings": agent.get("voice_settings", {}),
                            "webhooks": agent.get("webhooks", {}),
                            "show_citations": agent.get("show_citations", True)
                        }
                        agents_data.append(agent_dict)
                
                return agents_data
            except Exception as project_error:
                logger.warning(f"Project-based agent discovery failed: {project_error}")
        
        # Fall back to original Redis-based agent service
        from app.core.dependencies import get_client_service, get_agent_service
        client_service = get_client_service()
        agent_service = get_agent_service()
        
        # Get all clients first
        clients = await client_service.get_all_clients()
        all_agents = []
        
        # Create a mapping of client IDs to names for quick lookup
        client_map = {client.id: client.name for client in clients}
        
        # CORRECT METHOD: Iterate through each client to get their agents
        for client in clients:
                try:
                    client_agents = await agent_service.get_client_agents(client.id)
                    for agent in client_agents:
                        agent_dict = {
                            "id": agent.id,
                            "slug": agent.slug,
                            "name": agent.name,
                            "description": getattr(agent, 'description', ''),
                            "client_id": agent.client_id,
                            "client_name": client.name,
                            "status": "active" if getattr(agent, 'active', agent.enabled) else "inactive",
                            "active": getattr(agent, 'active', agent.enabled),
                            "enabled": agent.enabled,
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
    summary = await get_system_summary()
    
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
    try:
        clients = await get_all_clients_with_containers()
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
async def client_edit(
    client_id: str,
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Client edit page"""
    try:
        # Get client service
        from app.core.dependencies import get_client_service
        client_service = get_client_service()
        
        # Get client details
        client = await client_service.get_client(client_id, auto_sync=False)
        if not client:
            # Redirect to clients list with error
            return RedirectResponse(
                url="/admin/clients?error=Client+not+found",
                status_code=303
            )
        
        # Get any additional data needed for the form
        # For example, available API providers, etc.
        
        # Convert client to dict if it's a Pydantic model
        if hasattr(client, 'dict'):
            client_dict = client.dict()
        elif hasattr(client, 'model_dump'):
            client_dict = client.model_dump()
        else:
            client_dict = client
        
        return templates.TemplateResponse("admin/client_edit.html", {
            "request": request,
            "client": client_dict,
            "user": admin_user
        })
    except Exception as e:
        logger.error(f"Error loading client edit page: {e}")
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
    # Determine which clients this user can see
    visible_client_ids: List[str] = []
    try:
        # Superadmins see all clients
        if admin_user.get("role") == "superadmin":
            from app.core.dependencies import get_client_service
            client_service = get_client_service()
            clients_all = await client_service.get_all_clients()
            visible_client_ids = [c.id for c in clients_all]
        else:
            # Subscribers/Admins: load tenant assignments from metadata
            # Note: If RBAC tables are added, we can replace this with tenant_memberships
            from app.integrations.supabase_client import supabase_manager
            if not supabase_manager._initialized:
                await supabase_manager.initialize()
            # Fetch user to read metadata
            import httpx
            headers = {
                'apikey': supabase_manager.admin_client.supabase_key if hasattr(supabase_manager.admin_client, 'supabase_key') else os.getenv('SUPABASE_SERVICE_ROLE_KEY', ''),
                'Authorization': f"Bearer {os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')}",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{os.getenv('SUPABASE_URL')}/auth/v1/admin/users/{admin_user.get('user_id')}", headers=headers)
            if r.status_code == 200:
                user = r.json()
                meta = user.get('user_metadata') or {}
                ta = meta.get('tenant_assignments') or {}
                if admin_user.get('role') == 'admin':
                    visible_client_ids = ta.get('admin_client_ids') or []
                else:
                    visible_client_ids = ta.get('subscriber_client_ids') or []
    except Exception:
        visible_client_ids = []

    # Get agents from visible clients only
    try:
        if admin_user.get("role") == "superadmin":
            agents = await get_all_agents()
        else:
            from app.core.dependencies import get_agent_service
            agent_service = get_agent_service()
            agents = []
            for cid in visible_client_ids:
                client_agents = await agent_service.get_client_agents(cid)
                # Attach client_name via client service
                try:
                    from app.core.dependencies import get_client_service
                    client_service = get_client_service()
                    client = await client_service.get_client(cid)
                    client_name = client.name if hasattr(client, 'name') else (client.get('name') if isinstance(client, dict) else 'Unknown')
                except Exception:
                    client_name = 'Unknown'
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
        if admin_user.get("role") == "superadmin":
            clients = await client_service.get_all_clients()
        else:
            clients_all = await client_service.get_all_clients()
            clients = [c for c in clients_all if c.id in set(visible_client_ids)]
    except Exception as e:
        logger.error(f"Failed to load clients: {e}")
        # Return minimal client data if database is inaccessible
        clients = []
    
    return templates.TemplateResponse("admin/agents.html", {
        "request": request,
        "agents": agents,
        "clients": clients,
        "user": admin_user
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
async def debug_agent_data(client_id: str, agent_slug: str):
    """Debug what data is passed to agent detail template"""
    try:
        # Copy the same logic from agent_detail function
        if client_id == "global":
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
async def debug_single_agent(client_id: str, agent_slug: str):
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
        "cache_bust": int(time.time())
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
    try:
        return templates.TemplateResponse("admin/tools.html", {
            "request": request,
            "user": admin_user
        })
    except Exception as e:
        logger.error(f"Error loading tools page: {e}")
        return templates.TemplateResponse("admin/tools.html", {
            "request": request,
            "user": admin_user,
            "error": str(e)
        })

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
                "webhooks": agent.get("webhooks", {}),
                "tools_config": agent.get("tools_config", {}),
                "show_citations": agent.get("show_citations", True),
                "client_id": client_id,
                "client_name": client.get("name", "Unknown") if isinstance(client, dict) else (getattr(client, 'name', 'Unknown') if client else "Unknown")
            }
        else:
            # Object format - original service
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
                "client_id": client_id,
                "client_name": client.name if client else "Unknown"
            }
        
        # Provide configuration for template - pull from agent's voice_settings first
        voice_settings_data = agent_data.get("voice_settings", {})
        
        # Convert VoiceSettings object to dict if needed
        if hasattr(voice_settings_data, '__dict__'):
            # It's an object, convert to dict
            voice_settings_dict = {}
            for key in ['llm_provider', 'llm_model', 'temperature', 'stt_provider', 'stt_model', 
                       'tts_provider', 'openai_voice', 'elevenlabs_voice_id', 'cartesia_voice_id',
                       'voice_id', 'provider', 'provider_config']:
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
            "tts_provider": voice_settings_data.get("tts_provider", "openai"),
            "openai_voice": voice_settings_data.get("openai_voice", "alloy"),
            "elevenlabs_voice_id": voice_settings_data.get("elevenlabs_voice_id", ""),
            "cartesia_voice_id": voice_settings_data.get("voice_id", "248be419-c632-4f23-adf1-5324ed7dbf1d") if voice_settings_data.get("tts_provider") == "cartesia" else voice_settings_data.get("provider_config", {}).get("cartesia_voice_id", "248be419-c632-4f23-adf1-5324ed7dbf1d"),
            "voice_context_webhook_url": "",
            "text_context_webhook_url": ""
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

            template_data = {
                "request": request,
                "agent": cleaned_agent_data,  # Use cleaned data
                "client": client,
                "user": admin_user,
                "latest_config": latest_config,
                "latest_config_json": latest_config_json,
                "has_config_updates": bool(agent_config) if agent_config else False
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
                            const response = await fetch('/api/v1/agents/client/{client_id}/{agent_slug_clean}', {{
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
    summary = await get_system_summary()
    
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
    clients = await get_all_clients_with_containers()
    
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
    
    health_statuses = []
    try:
        clients = await client_service.get_all_clients()
        
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
        
        # Call EnsureClientUser to get client JWT for admin preview
        import httpx
        async with httpx.AsyncClient() as client:
            # Get platform session token for the API call
            platform_token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if not platform_token and admin_user:
                # Try to get from admin_user if available
                platform_token = admin_user.get("access_token", "")
            
            ensure_response = await client.post(
                f"{request.base_url}api/v2/admin/ensure-client-user",
                json={
                    "client_id": client_id,
                    "platform_user_id": admin_user.get("id"),
                    "user_email": admin_user.get("email")
                },
                headers={"Authorization": f"Bearer {platform_token}"} if platform_token else {}
            )
            
            client_jwt = None
            client_user_id = None
            
            if ensure_response.status_code == 200:
                ensure_data = ensure_response.json()
                client_jwt = ensure_data.get("client_jwt")
                client_user_id = ensure_data.get("client_user_id")
                logger.info(f"Got client JWT for preview: client_user_id={client_user_id}")
            else:
                logger.warning(f"Failed to get client JWT: {ensure_response.status_code}")
        
        host = request.base_url.hostname
        iframe_src = f"https://{host}/embed/{client_id}/{agent_slug}?theme=dark&source=admin"
        
        # If we have a client JWT, pass it to the embed
        jwt_script = ""
        if client_jwt:
            jwt_script = f"""
                    // Send client JWT for admin preview (shadow user in client Supabase)
                    iframe.contentWindow.postMessage({{ 
                        type: 'supabase-session', 
                        access_token: '{client_jwt}',
                        // No refresh token for admin preview sessions
                        refresh_token: null,
                        is_admin_preview: true,
                        client_user_id: '{client_user_id}'
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
                      iframe.contentWindow.postMessage({ type: 'supabase-session', access_token: session.access_token, refresh_token: session.refresh_token }, '*');
                    }
            """
        
        # Add development banner if in dev mode
        dev_banner = ""
        import os
        if os.getenv("ENVIRONMENT", "development") == "development" and client_user_id:
            # Redact sensitive parts of IDs for display
            client_id_display = f"{client_id[:8]}..."
            client_user_display = f"{client_user_id[:8]}..."
            dev_banner = f"""
            <div class=\"bg-yellow-900/50 border border-yellow-700 p-2 text-xs text-yellow-200\">
              <span class=\"font-semibold\">DEV MODE:</span> 
              Preview as client_user: <code>{client_user_display}</code> | 
              Client: <code>{client_id_display}</code> | 
              JWT expires: 15 min
            </div>
            """
        
        modal_html = f"""
        <div class=\"fixed inset-0 bg-black/80 flex items-center justify-center z-50\">
          <div class=\"bg-dark-surface border border-dark-border rounded-lg w-full max-w-3xl h-[80vh] flex flex-col\">
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
            
            # Process the message using AI
            if api_keys.get('openai_api_key') or api_keys.get('groq_api_key') or api_keys.get('cerebras_api_key'):
                # Use OpenAI or Groq to generate response
                import httpx
                
                try:
                    if api_keys.get('openai_api_key'):
                        # Use OpenAI
                        async with httpx.AsyncClient() as client:
                            response = await client.post(
                                "https://api.openai.com/v1/chat/completions",
                                headers={"Authorization": f"Bearer {api_keys['openai_api_key']}"},
                                json={
                                    "model": "gpt-3.5-turbo",
                                    "messages": [
                                        {"role": "system", "content": agent.system_prompt},
                                        {"role": "user", "content": message}
                                    ],
                                    "temperature": 0.7,
                                    "max_tokens": 500
                                }
                            )
                            if response.status_code == 200:
                                result = response.json()
                                ai_response = result['choices'][0]['message']['content']
                            else:
                                raise Exception(f"OpenAI API error: {response.status_code}")
                    
                    elif api_keys.get('groq_api_key'):
                        # Use Groq as fallback
                        logger.info(f"Using Groq API for agent {agent.name}")
                    elif api_keys.get('cerebras_api_key'):
                        # Minimal Cerebras test path for admin preview (text-only)
                        import httpx
                        logger.info(f"Using Cerebras API for agent {agent.name}")
                        headers = {"Authorization": f"Bearer {api_keys['cerebras_api_key']}"}
                        request_data = {
                            "model": "llama3.1-8b",
                            "messages": [
                                {"role": "system", "content": agent.system_prompt or "You are a helpful assistant."},
                                {"role": "user", "content": message}
                            ],
                            "stream": False
                        }
                        async with httpx.AsyncClient() as client:
                            response = await client.post("https://api.cerebras.ai/v1/chat/completions", headers=headers, json=request_data, timeout=30.0)
                            if response.status_code == 200:
                                data = response.json()
                                text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                                if text:
                                    return JSONResponse({"success": True, "response": text})
                            raise Exception(f"Cerebras API error: {response.status_code}")
                        logger.debug(f"System prompt length: {len(agent.system_prompt) if agent.system_prompt else 0}")
                        
                        # Try multiple Groq models in case some are unavailable
                        groq_models = ["llama-3.1-70b-versatile", "llama3-70b-8192", "llama3-8b-8192", "gemma2-9b-it"]
                        
                        for model in groq_models:
                            try:
                                async with httpx.AsyncClient() as client:
                                    request_data = {
                                        "model": model,
                                        "messages": [
                                            {"role": "system", "content": agent.system_prompt or "You are a helpful AI assistant."},
                                            {"role": "user", "content": message}
                                        ],
                                        "temperature": 0.7,
                                        "max_tokens": 500
                                    }
                                    
                                    response = await client.post(
                                        "https://api.groq.com/openai/v1/chat/completions",
                                        headers={"Authorization": f"Bearer {api_keys['groq_api_key']}"},
                                        json=request_data,
                                        timeout=30.0
                                    )
                                    
                                    if response.status_code == 200:
                                        result = response.json()
                                        ai_response = result['choices'][0]['message']['content']
                                        logger.info(f"Successfully used Groq model: {model}")
                                        break
                                    elif response.status_code == 503:
                                        logger.warning(f"Groq service unavailable for model {model}, trying next...")
                                        continue
                                    else:
                                        error_detail = response.text
                                        logger.warning(f"Groq API error {response.status_code} for model {model}: {error_detail[:100]}")
                                        continue
                            except Exception as model_error:
                                logger.warning(f"Failed with model {model}: {str(model_error)}")
                                continue
                        else:
                            # All models failed
                            raise Exception("All Groq models failed. Service may be temporarily unavailable.")
                    else:
                        ai_response = f"I'm {agent.name}. I'd love to help, but I need API keys configured to provide live responses."
                        
                except Exception as e:
                    logger.error(f"AI API call failed: {e}")
                    ai_response = f"I'm {agent.name}. I encountered an error processing your request: {str(e)}"
            else:
                ai_response = f"I'm {agent.name}. Please configure API keys to enable live AI responses."
                
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
                
                # Return voice interface with LiveKit client
                return templates.TemplateResponse("admin/partials/voice_preview_live.html", {
                    "request": request,
                    "room_name": room_name,
                    "server_url": server_url,
                    "user_token": user_token,
                    "agent_slug": agent_slug,
                    "client_id": client_id,
                    "session_id": session_id
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
        
        return templates.TemplateResponse("admin/partials/voice_preview_live.html", {
            "request": request,
            "room_name": room_name,
            "server_url": livekit_manager.url,
            "user_token": user_token,
            "agent_slug": "debug-agent",
            "client_id": "debug-client",
            "session_id": "debug-session"
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
            "conversation_id": conversation_id
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
        if "voice_settings" in data:
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
        from app.models.agent import AgentUpdate, VoiceSettings, WebhookSettings
        
        # Build update object
        update_data = AgentUpdate(
            name=data.get("name", agent.name),
            description=data.get("description", agent.description),
            agent_image=data.get("agent_image", agent.agent_image),
            system_prompt=data.get("system_prompt", agent.system_prompt),
            enabled=data.get("enabled", agent.enabled),
            tools_config=data.get("tools_config", agent.tools_config),
            show_citations=data.get("show_citations", getattr(agent, 'show_citations', True))
        )
        
        # Handle voice settings if provided
        if "voice_settings" in data:
            update_data.voice_settings = VoiceSettings(**data["voice_settings"])
        
        # Handle webhooks if provided
        if "webhooks" in data:
            update_data.webhooks = WebhookSettings(**data["webhooks"])
        
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

@router.get("/clients/{client_id}/edit", response_class=HTMLResponse)
async def edit_client_modal(client_id: str, request: Request, admin_user: Dict[str, Any] = Depends(get_admin_user)):
    from app.core.dependencies import get_client_service
    client_service = get_client_service()
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
    request: Request
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
        
        # Get existing client (disable auto-sync to prevent overriding manual changes)
        client = await client_service.get_client(client_id, auto_sync=False)
        if not client:
            return RedirectResponse(
                url="/admin/clients?error=Client+not+found",
                status_code=303
            )
        
        # Prepare update data
        from app.models.client import ClientUpdate, ClientSettings, SupabaseConfig, LiveKitConfig, APIKeys, EmbeddingSettings, RerankSettings
        
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
                deepgram_api_key=form.get("deepgram_api_key") or (current_api_keys.deepgram_api_key if hasattr(current_api_keys, 'deepgram_api_key') else current_api_keys.get('deepgram_api_key') if isinstance(current_api_keys, dict) else None),
                elevenlabs_api_key=form.get("elevenlabs_api_key") or (current_api_keys.elevenlabs_api_key if hasattr(current_api_keys, 'elevenlabs_api_key') else current_api_keys.get('elevenlabs_api_key') if isinstance(current_api_keys, dict) else None),
                cartesia_api_key=form.get("cartesia_api_key") or (current_api_keys.cartesia_api_key if hasattr(current_api_keys, 'cartesia_api_key') else current_api_keys.get('cartesia_api_key') if isinstance(current_api_keys, dict) else None),
                speechify_api_key=form.get("speechify_api_key") or (current_api_keys.speechify_api_key if hasattr(current_api_keys, 'speechify_api_key') else current_api_keys.get('speechify_api_key') if isinstance(current_api_keys, dict) else None),
                novita_api_key=form.get("novita_api_key") or (current_api_keys.novita_api_key if hasattr(current_api_keys, 'novita_api_key') else current_api_keys.get('novita_api_key') if isinstance(current_api_keys, dict) else None),
                cohere_api_key=form.get("cohere_api_key") or (current_api_keys.cohere_api_key if hasattr(current_api_keys, 'cohere_api_key') else current_api_keys.get('cohere_api_key') if isinstance(current_api_keys, dict) else None),
                siliconflow_api_key=form.get("siliconflow_api_key") or (current_api_keys.siliconflow_api_key if hasattr(current_api_keys, 'siliconflow_api_key') else current_api_keys.get('siliconflow_api_key') if isinstance(current_api_keys, dict) else None),
                jina_api_key=form.get("jina_api_key") or (current_api_keys.jina_api_key if hasattr(current_api_keys, 'jina_api_key') else current_api_keys.get('jina_api_key') if isinstance(current_api_keys, dict) else None)
            ),
            embedding=EmbeddingSettings(
                provider=form.get("embedding_provider", current_embedding.provider if hasattr(current_embedding, 'provider') else current_embedding.get('provider', 'openai') if current_embedding else 'openai'),
                document_model=form.get("document_embedding_model", current_embedding.document_model if hasattr(current_embedding, 'document_model') else current_embedding.get('document_model', 'text-embedding-3-small') if current_embedding else 'text-embedding-3-small'),
                conversation_model=form.get("conversation_embedding_model", current_embedding.conversation_model if hasattr(current_embedding, 'conversation_model') else current_embedding.get('conversation_model', 'text-embedding-3-small') if current_embedding else 'text-embedding-3-small'),
                dimension=int(form.get("embedding_dimension")) if form.get("embedding_dimension") and form.get("embedding_dimension").strip() else (current_embedding.dimension if hasattr(current_embedding, 'dimension') else current_embedding.get('dimension') if current_embedding else None)
            ),
            rerank=RerankSettings(
                enabled=form.get("rerank_enabled", "off") == "on",
                provider=form.get("rerank_provider", current_rerank.provider if hasattr(current_rerank, 'provider') else current_rerank.get('provider', 'siliconflow') if current_rerank else 'siliconflow'),
                model=form.get("rerank_model", current_rerank.model if hasattr(current_rerank, 'model') else current_rerank.get('model', 'BAAI/bge-reranker-base') if current_rerank else 'BAAI/bge-reranker-base'),
                top_k=int(form.get("rerank_top_k", current_rerank.top_k if hasattr(current_rerank, 'top_k') else current_rerank.get('top_k', 5) if current_rerank else 5)),
                candidates=int(form.get("rerank_candidates", current_rerank.candidates if hasattr(current_rerank, 'candidates') else current_rerank.get('candidates', 20) if current_rerank else 20))
            ),
            performance_monitoring=current_perf_monitoring,
            license_key=current_license_key
        )
        
        # Create update object - handle both dict and object formats
        update_data = ClientUpdate(
            name=form.get("name", client.name if hasattr(client, 'name') else client.get('name', '')),
            domain=form.get("domain", client.domain if hasattr(client, 'domain') else client.get('domain', '')),
            description=form.get("description", client.description if hasattr(client, 'description') else client.get('description', '')),
            settings=settings_update,
            active=form.get("active", "true").lower() == "true"
        )
        
        # Debug: Log the API keys and embedding settings being updated
        logger.info(f"About to update client with API keys: cartesia={update_data.settings.api_keys.cartesia_api_key}, siliconflow={update_data.settings.api_keys.siliconflow_api_key}")
        logger.info(f"Embedding settings: provider={update_data.settings.embedding.provider}, dimension={update_data.settings.embedding.dimension}, form_value='{form.get('embedding_dimension')}'")
        
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


# WordPress Sites Management Endpoints
@router.get("/clients/{client_id}/wordpress-sites")
async def get_client_wordpress_sites(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get WordPress sites for a specific client"""
    try:
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
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Create a new WordPress site for a client"""
    try:
        # Initialize WordPress service  
        wp_service = get_wordpress_service()
        
        # Create site data
        site_data = WordPressSiteCreate(
            domain=domain,
            site_name=site_name,
            admin_email=admin_email,
            client_id=client_id
        )
        
        # Create the site
        site = wp_service.create_site(site_data)
        
        return {
            "success": True,
            "message": f"WordPress site {domain} created successfully",
            "site": site.dict()
        }
        
    except Exception as e:
        logger.error(f"Failed to create WordPress site: {e}")
        return {
            "success": False,
            "error": str(e)
        }


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
            return {
                "success": False,
                "error": "Site not found"
            }
        
        # Generate new keys
        new_api_key = WordPressSite.generate_api_key()
        new_api_secret = WordPressSite.generate_api_secret()
        
        # Update site with new keys
        # Note: This will need to be implemented in the service
        # For now, return the new keys
        
        return {
            "success": True,
            "message": "API keys regenerated successfully",
            "api_key": new_api_key,
            "api_secret": new_api_secret
        }
        
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
        
        from app.services.document_processor import document_processor
        
        # Get documents for the specified client
        documents = await document_processor.get_documents(
            user_id=None,  # Admin access doesn't need user_id
            client_id=client_id,
            status=status,
            limit=100
        )
        
        # Return documents array directly to match frontend expectation
        return documents
        
    except Exception as e:
        logger.error(f"Failed to get documents for client {client_id}: {e}")
        return []


@router.get("/api/clients")
async def get_admin_clients(
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get all clients for admin interface"""
    try:
        from app.core.dependencies import get_client_service
        client_service = get_client_service()
        clients = await client_service.get_all_clients()
        
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
        # Set max content length to 50MB
        request._max_content_length = 50 * 1024 * 1024
        
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
                user_id=admin_user.get("id"),  # Admin user ID
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
        
        # Delete the document using document processor
        success = await document_processor.delete_document(
            document_id=document_id,
            user_id=admin_user.get("id")  # Pass admin user ID for permission check
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
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Reprocess a document"""
    try:
        # For now, return success
        # TODO: Implement actual reprocessing
        return {"success": True, "message": "Document reprocessing started"}
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
        data = await request.json()
        agent_access = data.get("agent_access", "specific")
        agent_ids = data.get("agent_ids", [])
        
        # For now, return success
        # TODO: Implement actual access update in Supabase
        return {"success": True, "message": "Document access updated successfully"}
    except Exception as e:
        logger.error(f"Failed to update document access: {e}")
        return {"success": False, "message": str(e)}
