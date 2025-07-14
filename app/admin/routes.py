from fastapi.responses import RedirectResponse
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Any, List, Optional
import redis.asyncio as aioredis
import redis
import json
import logging
import os
from datetime import datetime, timedelta

# These would be actual imports in the FastAPI app
# from app.dependencies.admin_auth import get_admin_user
# from app.services.container_orchestrator import get_orchestrator
# from app.services.supabase_service import get_all_clients

# Import from the app services
from app.services.container_manager import container_manager

logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(prefix="/admin", tags=["admin"])

# Initialize template engine
templates = Jinja2Templates(directory="/opt/autonomite-saas/app/templates")

# Redis connection
redis_client = None

async def get_redis():
    """Get Redis client"""
    global redis_client
    if redis_client is None:
        redis_client = await aioredis.from_url("redis://localhost:6379")
    return redis_client

async def get_admin_user(request: Request) -> Dict[str, Any]:
    """Placeholder for admin authentication"""
    # In production, this would validate admin JWT or session
    return {"username": "admin", "role": "superadmin"}

async def get_system_summary() -> Dict[str, Any]:
    """Get system-wide summary statistics"""
    redis = await get_redis()
    
    # Get all containers - for now return mock data
    containers = []
    
    # Calculate stats
    active_containers = len([c for c in containers if c.get("status") == "running"])
    total_clients = len(containers)
    
    # Get system metrics from Redis
    total_cpu = 0
    total_memory = 0
    total_sessions = 0
    
    for container in containers:
        metrics_key = f"metrics:current:{container['client_id']}"
        metrics_data = await redis.get(metrics_key)
        if metrics_data:
            metrics = json.loads(metrics_data)
            total_cpu += metrics.get("cpu_percent", 0)
            total_memory += metrics.get("memory_mb", 0)
            total_sessions += metrics.get("active_sessions", 0)
    
    return {
        "total_clients": total_clients,
        "active_containers": active_containers,
        "stopped_containers": total_clients - active_containers,
        "total_sessions": total_sessions,
        "avg_cpu": round(total_cpu / max(active_containers, 1), 1),
        "total_memory_gb": round(total_memory / 1024, 2),
        "timestamp": datetime.now().isoformat()
    }

async def get_all_clients_with_containers() -> List[Dict[str, Any]]:
    """Get all clients with their container status"""
    orchestrator = ContainerOrchestrator()
    redis = await get_redis()
    
    # Get containers
    containers = await orchestrator.list_containers()
    
    # Enhance with current metrics
    for container in containers:
        metrics_key = f"metrics:current:{container['client_id']}"
        metrics_data = await redis.get(metrics_key)
        if metrics_data:
            metrics = json.loads(metrics_data)
            container["cpu_usage"] = metrics.get("cpu_percent", 0)
            container["memory_usage"] = metrics.get("memory_mb", 0)
            container["active_sessions"] = metrics.get("active_sessions", 0)
    
    return containers

async def get_container_detail(client_id: str) -> Dict[str, Any]:
    """Get detailed container information"""
    orchestrator = ContainerOrchestrator()
    redis = await get_redis()
    
    # Get container info
    container_info = await orchestrator.get_container_info(client_id)
    if not container_info:
        raise HTTPException(status_code=404, detail="Container not found")
    
    # Get current metrics
    metrics_key = f"metrics:current:{client_id}"
    metrics_data = await redis.get(metrics_key)
    if metrics_data:
        metrics = json.loads(metrics_data)
        container_info.update(metrics)
    
    # Get health status
    health_data = await orchestrator.get_container_health(client_id)
    if health_data:
        container_info["health"] = health_data
    
    return container_info

async def get_all_clients_with_containers() -> List[Dict[str, Any]]:
    """Get all clients with their container information"""
    try:
        # Try project-based discovery first (if access token is available)
        access_token = os.getenv("SUPABASE_ACCESS_TOKEN")
        if access_token:
            try:
                from app.services.supabase_project_service import SupabaseProjectService
                project_service = SupabaseProjectService(access_token)
                
                # Get all projects as clients
                clients = await project_service.get_all_projects()
                
                # Add agent count to each client
                for client in clients:
                    try:
                        agents = await project_service.get_project_agents(client["id"])
                        client["agent_count"] = len(agents)
                    except Exception as e:
                        logger.warning(f"Failed to get agent count for {client['name']}: {e}")
                        client["agent_count"] = 0
                
                return clients
            except Exception as e:
                logger.warning(f"Project-based discovery failed: {e}")
                # Fall back to original method
        
        # Original method - use the existing client service
        from app.core.dependencies import get_client_service
        client_service = get_client_service()
        
        # Get all clients using original method
        clients = await client_service.get_all_clients()
        
        # Convert to dict format for templates
        clients_data = []
        for client in clients:
            client_dict = {
                "id": client.id,
                "name": client.name,
                "domain": client.domain,
                "status": "active" if client.active else "inactive",
                "created_at": client.created_at.isoformat() if client.created_at else None,
                "container_status": "unknown",
                "agent_count": 0,
                "settings": {
                    "supabase": getattr(client, 'supabase_url', None) is not None,
                    "status": "connected" if getattr(client, 'supabase_url', None) else "disconnected"
                }
            }
            clients_data.append(client_dict)
        
        return clients_data
    except Exception as e:
        logger.error(f"Error fetching clients: {e}")
        return []

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
                            "client_id": agent.get("client_id"),
                            "client_name": agent.get("client_name", "Unknown"),
                            "status": "active" if agent.get("active", agent.get("enabled", True)) else "inactive",
                            "active": agent.get("active", agent.get("enabled", True)),
                            "enabled": agent.get("enabled", True),
                            "created_at": agent.get("created_at", ""),
                            "updated_at": agent.get("updated_at", ""),
                            "system_prompt": agent.get("system_prompt", "")[:100] + "..." if agent.get("system_prompt") and len(agent.get("system_prompt", "")) > 100 else agent.get("system_prompt", ""),
                            "voice_settings": agent.get("voice_settings", {}),
                            "webhooks": agent.get("webhooks", {})
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
        
        for client in clients:
            try:
                # Get agents for this client
                client_agents = await agent_service.get_client_agents(client.id)
                
                # Convert to template format
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
                        "webhooks": getattr(agent, 'webhooks', {})
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
    clients = await get_all_clients_with_containers()
    
    return templates.TemplateResponse("admin/clients.html", {
        "request": request,
        "clients": clients,
        "user": admin_user
    })

@router.get("/agents", response_class=HTMLResponse)
async def agents_page(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Agent management page"""
    # Get agents from all clients
    agents = await get_all_agents()
    
    return templates.TemplateResponse("admin/agents.html", {
        "request": request,
        "agents": agents,
        "user": admin_user
    })

@router.get("/agents/{client_id}/{agent_slug}", response_class=HTMLResponse)
async def agent_detail(
    request: Request,
    client_id: str,
    agent_slug: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Agent detail and configuration page"""
    try:
        # Check if this is a project ID or UUID format
        is_uuid_format = len(client_id) == 36 and '-' in client_id and client_id.count('-') == 4
        agent = None
        client = None
        
        if is_uuid_format:
            # Use original agent service for UUID clients
            try:
                from app.core.dependencies import get_client_service, get_agent_service
                client_service = get_client_service()
                agent_service = get_agent_service()
                
                # Get agent details
                agent = await agent_service.get_agent(client_id, agent_slug)
                
                # Get client info
                client = await client_service.get_client(client_id)
            except Exception as e:
                logger.warning(f"Original agent service failed for {agent_slug} in client {client_id}: {e}")
        else:
            # This appears to be a project ID - show a placeholder until project access is fixed
            agent = {
                "id": f"agent-{agent_slug}",
                "slug": agent_slug,
                "name": f"Agent {agent_slug}",
                "description": "Project access token required to load details",
                "system_prompt": "Access required",
                "active": True,
                "enabled": True,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "voice_settings": {},
                "webhooks": {},
                "tools_config": {}
            }
            client = {
                "id": client_id,
                "name": f"Project {client_id}",
                "domain": f"{client_id}.local"
            }
        
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {agent_slug} not found")
        
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
                "agent_image": agent.get("agent_image", ""),
                "system_prompt": agent.get("system_prompt", ""),
                "active": agent.get("active", agent.get("enabled", True)),
                "enabled": agent.get("enabled", True),
                "created_at": agent.get("created_at", ""),
                "updated_at": agent.get("updated_at", ""),
                "voice_settings": agent.get("voice_settings", {}),
                "webhooks": agent.get("webhooks", {}),
                "tools_config": agent.get("tools_config", {}),
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
                "client_id": client_id,
                "client_name": client.name if client else "Unknown"
            }
        
        # Provide default configuration for template compatibility
        latest_config = {
            "last_updated": "",
            "enabled": True,
            "system_prompt": "",
            "provider_type": "livekit",
            "llm_provider": "groq",
            "llm_model": "llama-3.1-8b-instant",
            "temperature": 0.7,
            "stt_provider": "deepgram",
            "stt_model": "nova-2",
            "tts_provider": "openai",
            "openai_voice": "alloy",
            "elevenlabs_voice_id": "",
            "cartesia_voice_id": "a0e99841-438c-4a64-b679-ae501e7d6091",
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
            
            # For now, return a simple HTML response for project-based agents to bypass template issues
            if isinstance(agent, dict):
                from fastapi.responses import HTMLResponse
                simple_html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Agent {agent_data['name']}</title>
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
            return templates.TemplateResponse("admin/agent_detail.html", {
                "request": request,
                "agent": agent_data,
                "client": client,
                "user": admin_user,
                "latest_config": latest_config,
                "latest_config_json": latest_config_json,
                "has_config_updates": bool(agent_config) if agent_config else False
            })
        except Exception as template_error:
            logger.error(f"Template rendering error: {template_error}")
            # Return proper JSON response
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=500,
                content={"error": "Failed to load agent details", "details": str(template_error)}
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching agent {agent_slug}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load agent details")

@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(
    request: Request,
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Client detail and configuration page"""
    try:
        # Check if this is a project ID or UUID format
        is_uuid_format = len(client_id) == 36 and '-' in client_id and client_id.count('-') == 4
        client = None
        
        if is_uuid_format:
            # Use original client service for UUID clients
            try:
                from app.core.dependencies import get_client_service
                client_service = get_client_service()
                client = await client_service.get_client(client_id)
            except Exception as e:
                logger.warning(f"Original client service failed for {client_id}: {e}")
                client = None
        else:
            # This appears to be a project ID - show a placeholder until project access is fixed
            client = {
                "id": client_id,
                "name": f"Project {client_id}",
                "domain": f"{client_id}.local",
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z",
                "settings": {
                    "supabase": {
                        "url": f"https://{client_id}.supabase.co",
                        "anon_key": "Project access token required",
                        "service_role_key": "Project access token required"
                    },
                    "livekit": {
                        "server_url": "",
                        "api_key": "",
                        "api_secret": ""
                    },
                    "api_keys": {
                        "openai_api_key": "",
                        "groq_api_key": "",
                        "deepinfra_api_key": "",
                        "replicate_api_key": "",
                        "deepgram_api_key": "",
                        "elevenlabs_api_key": "",
                        "cartesia_api_key": "",
                        "speechify_api_key": "",
                        "novita_api_key": "",
                        "cohere_api_key": "",
                        "siliconflow_api_key": "",
                        "jina_api_key": ""
                    },
                    "status": "access_required"
                }
            }
        
        if not client:
            raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
        
        # Handle both dict and object format clients
        logger.info(f"Client type: {type(client)}, Client data: {client}")
        
        try:
            # Convert to dict format for template - handle dict or object format
            if isinstance(client, dict):
                client_data = {
                    "id": client.get("id", ""),
                    "name": client.get("name", ""),
                    "domain": client.get("domain", ""),
                    "status": "active" if client.get("active", False) else "inactive",
                    "created_at": client.get("created_at", ""),
                    "settings": {
                        "supabase": {
                            "url": client.get("supabase_url", ""),
                            "anon_key": client.get("supabase_anon_key", ""),
                            "service_role_key": client.get("supabase_service_key", "")
                        },
                        "livekit": {
                            "server_url": client.get("livekit_url", ""),
                            "api_key": client.get("livekit_api_key", ""),
                            "api_secret": client.get("livekit_api_secret", "")
                        },
                        "api_keys": {
                            "openai_api_key": "",
                            "groq_api_key": "",
                            "deepinfra_api_key": "",
                            "replicate_api_key": "",
                            "deepgram_api_key": "",
                            "elevenlabs_api_key": "",
                            "cartesia_api_key": "",
                            "speechify_api_key": "",
                            "novita_api_key": "",
                            "cohere_api_key": "",
                            "siliconflow_api_key": "",
                            "jina_api_key": ""
                        },
                        "status": "connected" if client.get("supabase_url") else "disconnected"
                    }
                }
            else:
                # Object format - create template-compatible structure
                settings = getattr(client, 'settings', None)
                client_data = {
                    "id": client.id,
                    "name": client.name,
                    "domain": client.domain,
                    "status": "active" if client.active else "inactive",
                    "created_at": client.created_at.isoformat() if hasattr(client.created_at, 'isoformat') else str(client.created_at),
                    "settings": {
                        "supabase": {
                            "url": settings.supabase.url if settings and hasattr(settings, 'supabase') else '',
                            "anon_key": settings.supabase.anon_key if settings and hasattr(settings, 'supabase') else '',
                            "service_role_key": settings.supabase.service_role_key if settings and hasattr(settings, 'supabase') else ''
                        },
                        "livekit": {
                            "server_url": settings.livekit.server_url if settings and hasattr(settings, 'livekit') else '',
                            "api_key": settings.livekit.api_key if settings and hasattr(settings, 'livekit') else '',
                            "api_secret": settings.livekit.api_secret if settings and hasattr(settings, 'livekit') else ''
                        },
                        "api_keys": {
                            "openai_api_key": getattr(settings.api_keys, 'openai_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else '',
                            "groq_api_key": getattr(settings.api_keys, 'groq_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else '',
                            "deepinfra_api_key": getattr(settings.api_keys, 'deepinfra_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else '',
                            "replicate_api_key": getattr(settings.api_keys, 'replicate_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else '',
                            "deepgram_api_key": getattr(settings.api_keys, 'deepgram_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else '',
                            "elevenlabs_api_key": getattr(settings.api_keys, 'elevenlabs_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else '',
                            "cartesia_api_key": getattr(settings.api_keys, 'cartesia_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else '',
                            "speechify_api_key": getattr(settings.api_keys, 'speechify_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else '',
                            "novita_api_key": getattr(settings.api_keys, 'novita_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else '',
                            "cohere_api_key": getattr(settings.api_keys, 'cohere_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else '',
                            "siliconflow_api_key": getattr(settings.api_keys, 'siliconflow_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else '',
                            "jina_api_key": getattr(settings.api_keys, 'jina_api_key', '') if settings and hasattr(settings, 'api_keys') and settings.api_keys else ''
                        },
                        "status": "connected" if settings else "disconnected"
                    }
                }
            
            logger.info(f"Successfully processed client data: {client_data}")
            
            return templates.TemplateResponse("admin/client_detail.html", {
                "request": request,
                "client": client_data,
                "user": admin_user
            })
        except Exception as template_error:
            logger.error(f"Error processing client data: {template_error}")
            raise template_error
            
    except Exception as e:
        logger.error(f"Error fetching client {client_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load client details")

@router.get("/containers/{client_id}", response_class=HTMLResponse)
async def container_detail(
    request: Request,
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Container detail view with live updates"""
    container = await get_container_detail(client_id)
    
    return templates.TemplateResponse("admin/container_detail.html", {
        "request": request,
        "container": container,
        "user": admin_user
    })

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
    orchestrator = ContainerOrchestrator()
    
    # Get health status for all containers
    health_statuses = []
    containers = await orchestrator.list_containers()
    
    for container in containers[:5]:  # Limit to first 5 for dashboard
        health = await orchestrator.get_container_health(container["client_id"])
        if health:
            health_statuses.append({
                "client_id": container["client_id"],
                "client_name": container.get("client_name", container["client_id"]),
                "healthy": health.get("healthy", False),
                "checks": health.get("checks", {})
            })
    
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
    try:
        # Parse JSON body
        data = await request.json()
        
        # Get agent service
        from app.core.dependencies import get_agent_service
        agent_service = get_agent_service()
        
        # Get existing agent
        agent = await agent_service.get_agent(client_id, agent_slug)
        if not agent:
            return {"error": "Agent not found", "status": 404}
        
        # Prepare update data
        from app.models.agent import AgentUpdate, VoiceSettings, WebhookSettings
        
        # Build update object
        update_data = AgentUpdate(
            name=data.get("name", agent.name),
            description=data.get("description", agent.description),
            agent_image=data.get("agent_image", agent.agent_image),
            system_prompt=data.get("system_prompt", agent.system_prompt),
            enabled=data.get("enabled", agent.enabled),
            tools_config=data.get("tools_config", agent.tools_config)
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

@router.post("/clients/{client_id}/update")
async def admin_update_client(
    client_id: str,
    request: Request
):
    """Admin endpoint to update client using Supabase service"""
    try:
        # Parse form data
        form = await request.form()
        
        # Get client service
        from app.core.dependencies import get_client_service
        client_service = get_client_service()
        
        # Get existing client
        client = await client_service.get_client(client_id)
        if not client:
            return RedirectResponse(
                url="/admin/clients?error=Client+not+found",
                status_code=303
            )
        
        # Prepare update data
        from app.models.client import ClientUpdate, ClientSettings, SupabaseConfig, LiveKitConfig, APIKeys
        
        # Build settings update
        settings_update = ClientSettings(
            supabase=SupabaseConfig(
                url=form.get("supabase_url", client.settings.supabase.url),
                anon_key=form.get("supabase_anon_key", client.settings.supabase.anon_key),
                service_role_key=form.get("supabase_service_key", client.settings.supabase.service_role_key)
            ),
            livekit=LiveKitConfig(
                server_url=form.get("livekit_server_url", client.settings.livekit.server_url),
                api_key=form.get("livekit_api_key", client.settings.livekit.api_key),
                api_secret=form.get("livekit_api_secret", client.settings.livekit.api_secret)
            ),
            api_keys=client.settings.api_keys,  # Keep existing API keys
            embedding=client.settings.embedding,  # Keep existing embedding settings
            rerank=client.settings.rerank,  # Keep existing rerank settings
            performance_monitoring=client.settings.performance_monitoring,
            license_key=client.settings.license_key
        )
        
        # Create update object
        update_data = ClientUpdate(
            name=form.get("name", client.name),
            domain=form.get("domain", client.domain),
            description=form.get("description", client.description),
            settings=settings_update,
            active=form.get("active", "true").lower() == "true"
        )
        
        # Update client
        updated_client = await client_service.update_client(client_id, update_data)
        
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

