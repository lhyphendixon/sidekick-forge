"""
Multi-tenant Admin Dashboard Routes
"""
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Any, List, Optional
import logging
import os
from datetime import datetime
from urllib.parse import quote
from uuid import UUID

# Import multi-tenant services
from app.services.client_service_multitenant import ClientService
from app.services.agent_service_multitenant import AgentService
from app.services.client_connection_manager import get_connection_manager, ClientConfigurationError
from app.services.wordpress_site_service_supabase import WordPressSiteService
from app.models.wordpress_site import WordPressSiteCreate
from app.config import settings

logger = logging.getLogger(__name__)

# Templates
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir)

# Services
client_service = ClientService()
agent_service = AgentService()
wordpress_service = WordPressSiteService(settings.supabase_url, settings.supabase_service_role_key)

router = APIRouter(prefix="/admin", tags=["admin"])


# Simple admin auth for development
async def get_admin_user(request: Request):
    """Simple admin authentication check"""
    # In production, implement proper authentication
    return {"username": "admin"}


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    admin_user: dict = Depends(get_admin_user)
):
    """Multi-tenant admin dashboard"""
    try:
        # Get all clients from platform database
        clients = await client_service.get_clients()
        
        # Get statistics
        total_agents = 0
        for client in clients:
            try:
                agents = await agent_service.get_agents(UUID(client.id))
                total_agents += len(agents)
            except Exception as e:
                logger.error(f"Error getting agents for client {client.id}: {e}")
        
        context = {
            "request": request,
            "admin_user": admin_user,
            "stats": {
                "total_clients": len(clients),
                "total_agents": total_agents,
                "active_clients": len([c for c in clients if getattr(c, 'active', True)])
            }
        }
        
        return templates.TemplateResponse("dashboard_multitenant.html", context)
        
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/clients", response_class=HTMLResponse)
async def list_clients(
    request: Request,
    admin_user: dict = Depends(get_admin_user)
):
    """List all platform clients"""
    try:
        clients = await client_service.get_clients()
        
        # Enhance client data with agent counts
        client_data = []
        for client in clients:
            try:
                agents = await agent_service.get_agents(UUID(client.id))
                client_dict = client.dict()
                client_dict['agent_count'] = len(agents)
                # Convert datetime to string for template
                if 'created_at' in client_dict and client_dict['created_at']:
                    client_dict['created_at'] = str(client_dict['created_at'])
                if 'updated_at' in client_dict and client_dict['updated_at']:
                    client_dict['updated_at'] = str(client_dict['updated_at'])
                client_data.append(client_dict)
            except Exception as e:
                logger.error(f"Error getting agents for client {client.id}: {e}")
                client_dict = client.dict()
                client_dict['agent_count'] = 0
                # Convert datetime to string for template
                if 'created_at' in client_dict and client_dict['created_at']:
                    client_dict['created_at'] = str(client_dict['created_at'])
                if 'updated_at' in client_dict and client_dict['updated_at']:
                    client_dict['updated_at'] = str(client_dict['updated_at'])
                client_data.append(client_dict)
        
        context = {
            "request": request,
            "admin_user": admin_user,
            "user": admin_user,
            "clients": client_data
        }
        
        return templates.TemplateResponse("clients.html", context)
        
    except Exception as e:
        logger.error(f"Client list error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(
    request: Request,
    client_id: str,
    admin_user: dict = Depends(get_admin_user)
):
    """Client detail page with agents"""
    try:
        # Get client details
        client = await client_service.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        
        # Get client's agents
        agents = await agent_service.get_agents(UUID(client_id))
        
        # Get API keys (masked)
        connection_manager = get_connection_manager()
        api_keys = connection_manager.get_client_api_keys(UUID(client_id))
        
        # Mask sensitive values
        masked_keys = {}
        for key, value in api_keys.items():
            if value and isinstance(value, str) and len(value) > 10:
                masked_keys[key] = f"{value[:4]}...{value[-4:]}"
            else:
                masked_keys[key] = "Not configured" if not value else value
        
        # Load WordPress sites for this client
        wordpress_sites: List[Dict[str, Any]] = []
        wordpress_error: Optional[str] = None
        try:
            sites = wordpress_service.list_sites(client_id=client_id)
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

        context = {
            "request": request,
            "admin_user": admin_user,
            "user": admin_user,
            "client": client,
            "agents": agents,
            "api_keys": masked_keys,
            "wordpress_sites": wordpress_sites,
            "wordpress_error": wordpress_error,
            "wordpress_api_endpoint": wordpress_api_endpoint,
            "wordpress_domain": settings.domain_name,
        }
        
        return templates.TemplateResponse("client_detail.html", context)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Client detail error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clients/{client_id}/wordpress-sites", response_class=RedirectResponse)
async def create_client_wordpress_site(
    client_id: str,
    domain: str = Form(...),
    site_name: str = Form(...),
    admin_email: str = Form(...)
):
    """Create a WordPress site registration for the client"""
    try:
        site_data = WordPressSiteCreate(
            domain=domain,
            site_name=site_name,
            admin_email=admin_email,
            client_id=client_id
        )
        wordpress_service.create_site(site_data)
        message = quote("WordPress site added successfully")
        return RedirectResponse(url=f"/admin/clients/{client_id}?message={message}", status_code=303)
    except Exception as e:
        logger.error(f"Failed to create WordPress site for client {client_id}: {e}")
        error = quote(f"Failed to create WordPress site: {e}")
        return RedirectResponse(url=f"/admin/clients/{client_id}?error={error}", status_code=303)


@router.post("/clients/{client_id}/wordpress-sites/{site_id}/regenerate", response_class=RedirectResponse)
async def regenerate_client_wordpress_keys(
    client_id: str,
    site_id: str
):
    """Regenerate the API credentials for a WordPress site"""
    try:
        updated_site = wordpress_service.regenerate_api_keys(site_id)
        if not updated_site:
            raise ValueError("Site not found")
        message = quote("WordPress API keys regenerated")
        return RedirectResponse(url=f"/admin/clients/{client_id}?message={message}", status_code=303)
    except Exception as e:
        logger.error(f"Failed to regenerate keys for site {site_id}: {e}")
        error = quote(f"Failed to regenerate keys: {e}")
        return RedirectResponse(url=f"/admin/clients/{client_id}?error={error}", status_code=303)


@router.get("/agents", response_class=HTMLResponse)
async def list_all_agents(
    request: Request,
    admin_user: dict = Depends(get_admin_user)
):
    """List all agents across all clients"""
    try:
        clients = await client_service.get_clients()
        
        # Collect all agents with client info
        all_agents = []
        for client in clients:
            try:
                agents = await agent_service.get_agents(UUID(client.id))
                for agent in agents:
                    agent_dict = agent.dict()
                    agent_dict['client_name'] = client.name
                    agent_dict['client_id'] = client.id
                    all_agents.append(agent_dict)
            except Exception as e:
                logger.error(f"Error getting agents for client {client.id}: {e}")
        
        context = {
            "request": request,
            "admin_user": admin_user,
            "agents": all_agents
        }
        
        return templates.TemplateResponse("agents.html", context)
        
    except Exception as e:
        logger.error(f"Agent list error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/{client_id}/{agent_slug}", response_class=HTMLResponse)
async def agent_detail(
    request: Request,
    client_id: str,
    agent_slug: str,
    admin_user: dict = Depends(get_admin_user)
):
    """Agent detail page"""
    try:
        # Get client info
        client = await client_service.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        
        # Get agent details
        agent = await agent_service.get_agent(UUID(client_id), agent_slug)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        context = {
            "request": request,
            "admin_user": admin_user,
            "client": client,
            "agent": agent
        }
        
        return templates.TemplateResponse("agent_detail.html", context)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Agent detail error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents/{client_id}/{agent_slug}/toggle", response_class=RedirectResponse)
async def toggle_agent(
    request: Request,
    client_id: str,
    agent_slug: str,
    admin_user: dict = Depends(get_admin_user)
):
    """Toggle agent enabled/disabled status"""
    try:
        # Get current agent
        agent = await agent_service.get_agent(UUID(client_id), agent_slug)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        # Toggle enabled status
        from app.models.agent import AgentUpdate
        update = AgentUpdate(enabled=not agent.enabled)
        
        await agent_service.update_agent(UUID(client_id), agent_slug, update)
        
        return RedirectResponse(url=f"/admin/agents/{client_id}/{agent_slug}", status_code=303)
        
    except Exception as e:
        logger.error(f"Agent toggle error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page (placeholder)"""
    context = {"request": request, "error": None}
    return templates.TemplateResponse("login.html", context)


@router.post("/login", response_class=RedirectResponse)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """Handle login (placeholder)"""
    # In production, implement proper authentication
    if username == "admin" and password == "admin":
        return RedirectResponse(url="/admin", status_code=303)
    else:
        context = {"request": request, "error": "Invalid credentials"}
        return templates.TemplateResponse("login.html", context)
