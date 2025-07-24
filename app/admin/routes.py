from fastapi.responses import RedirectResponse
from fastapi import APIRouter, Request, Depends, Form, HTTPException, File, UploadFile, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Any, List, Optional
import redis.asyncio as aioredis
import redis
import json
import logging
import os
from datetime import datetime, timedelta
from livekit import api

# These would be actual imports in the FastAPI app
# from app.dependencies.admin_auth import get_admin_user
# from app.services.container_orchestrator import get_orchestrator
# from app.services.supabase_service import get_all_clients

# Import from the app services
from app.services.container_manager import container_manager
from app.services.wordpress_site_service_supabase import WordPressSiteService
from app.models.wordpress_site import WordPressSite, WordPressSiteCreate, WordPressSiteUpdate

logger = logging.getLogger(__name__)

def get_wordpress_service() -> WordPressSiteService:
    """Get WordPress site service with Supabase credentials"""
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    return WordPressSiteService(supabase_url, supabase_key)

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

# Import proper admin authentication
from app.admin.auth import get_admin_user

# Import debug routes
from app.admin import debug_routes

# Login/Logout Routes
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Admin login page"""
    return templates.TemplateResponse("admin/login.html", {"request": request})

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
        clients = []
        total_clients = 0
    
    # Check actual container status
    active_containers = 0
    stopped_containers = 0
    
    try:
        # Check container status for each client
        for client in clients:
            containers = await container_manager.list_client_containers(client.id)
            for container in containers:
                if container.get("status") == "running":
                    active_containers += 1
                else:
                    stopped_containers += 1
                    
        # If no containers found, assume one per client for demo purposes
        if active_containers == 0 and stopped_containers == 0 and total_clients > 0:
            active_containers = total_clients
            
    except Exception as e:
        logger.warning(f"Failed to get container status: {e}")
        # Fallback to assuming one container per client
        active_containers = total_clients
    
    # Get active sessions from LiveKit
    total_sessions = 0
    try:
        # Initialize LiveKit if needed
        if not livekit_manager._initialized:
            await livekit_manager.initialize()
        
        # Get all rooms from LiveKit
        room_service = api.RoomServiceClient(
            livekit_manager.url,
            livekit_manager.api_key,
            livekit_manager.api_secret
        )
        
        rooms = await room_service.list_rooms(api.ListRoomsRequest())
        
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
        # Get all clients
        clients = await client_service.get_all_clients()
        
        # Get LiveKit room data for session counting
        room_sessions = {}
        try:
            if not livekit_manager._initialized:
                await livekit_manager.initialize()
            
            room_service = api.RoomServiceClient(
                livekit_manager.url,
                livekit_manager.api_key,
                livekit_manager.api_secret
            )
            
            rooms = await room_service.list_rooms(api.ListRoomsRequest())
            
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
            client_dict = {
                "id": client.id,
                "name": client.name,
                "domain": client.domain,
                "status": "running" if client.active else "stopped",  # Assume active = running container
                "active": client.active,
                "created_at": client.created_at.isoformat() if client.created_at else None,
                "client_id": client.id,  # For compatibility with templates
                "client_name": client.name,
                "cpu_usage": 15.5,  # Mock CPU usage
                "memory_usage": 512,  # Mock memory usage in MB
                "active_sessions": room_sessions.get(client.id, 0),  # Real session count from LiveKit
                "settings": {
                    "supabase": client.settings.supabase if client.settings else None,
                    "livekit": client.settings.livekit if client.settings else None
                }
            }
            clients_data.append(client_dict)
        
        return clients_data
    except Exception as e:
        logger.error(f"Error fetching clients: {e}")
        return []

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
                            "client_id": "global" if agent.get("client_id") == "yuowazxcxwhczywurmmw" else agent.get("client_id"),
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
        
        # Create a mapping of client IDs to names for quick lookup
        client_map = {client.id: client.name for client in clients}
        
        # Get agents from the main Supabase (faster than querying each client)
        try:
            from app.integrations.supabase_client import supabase_manager
            result = supabase_manager.admin_client.table('agents').select('*').execute()
            
            for agent_data in result.data:
                # Agents in main table are global agents (no client association)
                # We'll use a special identifier for these
                client_id = "global"  # Special identifier for global agents
                    
                agent_dict = {
                    "id": agent_data.get("id"),
                    "slug": agent_data.get("slug"),
                    "name": agent_data.get("name"),
                    "description": agent_data.get("description", ""),
                    "client_id": client_id,
                    "client_name": "Global Agent",
                    "status": "active" if agent_data.get("enabled", True) else "inactive",
                    "active": agent_data.get("enabled", True),
                    "enabled": agent_data.get("enabled", True),
                    "created_at": agent_data.get("created_at", ""),
                    "updated_at": agent_data.get("updated_at", ""),
                    "system_prompt": agent_data.get("system_prompt", ""),
                    "voice_settings": agent_data.get("voice_settings", {
                        "provider": "livekit",
                        "voice_id": "alloy",
                        "temperature": 0.7
                    }),
                    "webhooks": agent_data.get("webhooks", {})
                }
                all_agents.append(agent_dict)
                
        except Exception as e:
            logger.warning(f"Fast agent fetch failed, falling back to slow method: {e}")
            # Fallback to the slower method if needed
            for client in clients[:5]:  # Limit to 5 clients to prevent timeout
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
    
    # Get all clients for the filter dropdown
    from app.core.dependencies import get_client_service
    client_service = get_client_service()
    clients = await client_service.get_all_clients()
    
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

@router.get("/agents/{client_id}/{agent_slug}", response_class=HTMLResponse)
async def agent_detail(
    request: Request,
    client_id: str,
    agent_slug: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Agent detail and configuration page"""
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
            return templates.TemplateResponse("admin/agent_detail.html", {
                "request": request,
                "agent": cleaned_agent_data,  # Use cleaned data
                "client": client,
                "user": admin_user,
                "latest_config": latest_config,
                "latest_config_json": latest_config_json,
                "has_config_updates": bool(agent_config) if agent_config else False
            })
        except Exception as template_error:
            logger.error(f"Template rendering error: {template_error}")
            logger.error(f"Error type: {type(template_error)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            # Return working configuration page bypassing template issues
            from fastapi.responses import HTMLResponse
            import json
            
            # Parse voice settings if it's a string
            voice_settings = agent.get('voice_settings', {})
            if isinstance(voice_settings, str):
                try:
                    voice_settings = json.loads(voice_settings)
                except:
                    voice_settings = {}
            
            # Extract specific settings with defaults
            tts_provider = voice_settings.get('provider', 'openai')
            llm_provider = voice_settings.get('llm_provider', 'groq')
            llm_model = voice_settings.get('llm_model', 'llama3-70b-8192')
            stt_provider = voice_settings.get('stt_provider', 'deepgram')
            temperature = voice_settings.get('temperature', 0.7)
            
            # Agent status
            is_enabled = agent.get('enabled', True)
            enabled_checked = 'checked' if is_enabled else ''
            
            # Provider-specific voice settings
            openai_voice = voice_settings.get('voice_id', 'alloy') if tts_provider == 'openai' else 'alloy'
            cartesia_voice_id = voice_settings.get('voice_id', '') if tts_provider == 'cartesia' else ''
            cartesia_model = voice_settings.get('model', 'sonic-english') if tts_provider == 'cartesia' else 'sonic-english'
            elevenlabs_voice_id = voice_settings.get('voice_id', '') if tts_provider == 'elevenlabs' else ''
            speechify_voice_id = voice_settings.get('voice_id', 'jack') if tts_provider == 'speechify' else 'jack'
            
            # Escape any problematic characters
            agent_name = str(agent.get('name', agent_slug)).replace('"', '&quot;')
            agent_slug_clean = str(agent.get('slug', 'N/A')).replace('"', '&quot;')
            system_prompt = str(agent.get('system_prompt', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
            agent_description = str(agent.get('description', '')).replace('"', '&quot;')
            agent_image_url = str(agent.get('agent_image', '')).replace('"', '&quot;')
            
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
                                        <input type="url" name="agent_image" value="{agent_image_url}" placeholder="https://example.com/image.jpg" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
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
                                            <option value="deepinfra">DeepInfra</option>
                                        </select>
                                    </div>
                                    <div>
                                        <label class="block text-sm font-medium text-gray-300 mb-2">Model</label>
                                        <select name="llm_model" id="llm-model" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
                                            <option value="gpt-4o">GPT-4o (OpenAI)</option>
                                            <option value="gpt-4o-mini">GPT-4o Mini (OpenAI)</option>
                                            <option value="llama3-70b-8192" selected>Llama 3 70B (Groq)</option>
                                            <option value="mixtral-8x7b-32768">Mixtral 8x7B (Groq)</option>
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
                                            <option value="openai" selected>OpenAI</option>
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
                                            <input type="text" name="elevenlabs_voice_id" placeholder="pNInz6obpgDQGcFmaJgB" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
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
                                            <input type="text" name="cartesia_voice_id" value="{cartesia_voice_id}" placeholder="a0e99841-438c-4a64-b679-ae501e7d6091" class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:ring-blue-500 focus:border-blue-500">
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
                                            hx-get="/admin/agents/preview/global/{agent_slug_clean}" 
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
                            agent_image: configData.agent_image || null,
                            system_prompt: configData.system_prompt,
                            enabled: configData.enabled === 'on',
                            voice_settings: voiceSettings
                        }};
                        
                        try {{
                            // Use the existing API endpoint
                            const response = await fetch('/api/v1/agents/client/global/{agent_slug_clean}', {{
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
                                alert(`Error saving configuration: ${{error.detail || 'Unknown error'}}`);
                            }}
                        }} catch (error) {{
                            alert(`Error saving configuration: ${{error.message}}`);
                        }} finally {{
                            submitBtn.textContent = originalText;
                            submitBtn.disabled = false;
                        }}
                    }}

                    // Initialize - Set current values
                    document.getElementById('tts-provider').value = '{tts_provider}';
                    document.getElementById('llm-provider').value = '{llm_provider}';
                    document.getElementById('llm-model').value = '{llm_model}';
                    document.getElementById('stt-provider').value = '{stt_provider}';
                    document.getElementById('temperature-range').value = '{temperature}';
                    document.getElementById('temperature-value').textContent = '{temperature}';
                    toggleTTSProviderSettings();
                </script>
            </body>
            </html>
            '''
            return HTMLResponse(content=working_html)
    
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
                if client:
                    logger.info(f"Loaded client {client_id}: name={client.name}, description={client.description}")
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
                        "embedding": {
                            "provider": "openai",
                            "document_model": "text-embedding-3-small",
                            "conversation_model": "text-embedding-3-small"
                        },
                        "rerank": {
                            "enabled": False,
                            "provider": "siliconflow",
                            "model": "BAAI/bge-reranker-base",
                            "top_k": 3,
                            "candidates": 20
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
                        "embedding": {
                            "provider": getattr(settings.embedding, 'provider', 'openai') if settings and hasattr(settings, 'embedding') and settings.embedding else 'openai',
                            "document_model": getattr(settings.embedding, 'document_model', 'text-embedding-3-small') if settings and hasattr(settings, 'embedding') and settings.embedding else 'text-embedding-3-small',
                            "conversation_model": getattr(settings.embedding, 'conversation_model', 'text-embedding-3-small') if settings and hasattr(settings, 'embedding') and settings.embedding else 'text-embedding-3-small',
                            "dimension": getattr(settings.embedding, 'dimension', None) if settings and hasattr(settings, 'embedding') and settings.embedding else None
                        },
                        "rerank": {
                            "enabled": getattr(settings.rerank, 'enabled', False) if settings and hasattr(settings, 'rerank') and settings.rerank else False,
                            "provider": getattr(settings.rerank, 'provider', 'siliconflow') if settings and hasattr(settings, 'rerank') and settings.rerank else 'siliconflow',
                            "model": getattr(settings.rerank, 'model', 'BAAI/bge-reranker-base') if settings and hasattr(settings, 'rerank') and settings.rerank else 'BAAI/bge-reranker-base',
                            "top_k": getattr(settings.rerank, 'top_k', 3) if settings and hasattr(settings, 'rerank') and settings.rerank else 3,
                            "candidates": getattr(settings.rerank, 'candidates', 20) if settings and hasattr(settings, 'rerank') and settings.rerank else 20
                        },
                        "status": "connected" if settings else "disconnected"
                    }
                }
            
            logger.info(f"Successfully processed client data: {client_data}")
            
            # Log the specific data being passed to template
            logger.info(f"Passing to template - client type: {type(client_data)}")
            logger.info(f"Client settings type: {type(client_data.get('settings', {}))}")
            logger.info(f"Client embedding data: {client_data.get('settings', {}).get('embedding', 'NOT FOUND')}")
            
            try:
                return templates.TemplateResponse("admin/client_detail.html", {
                    "request": request,
                    "client": client_data,
                    "user": admin_user
                })
            except Exception as render_error:
                logger.error(f"Template rendering error: {render_error}")
                logger.error(f"Error type: {type(render_error)}")
                logger.error(f"Error args: {render_error.args}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                raise render_error
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

@router.get("/agents/preview/{client_id}/{agent_slug}", response_class=HTMLResponse)
async def agent_preview_modal(
    request: Request,
    client_id: str,
    agent_slug: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Return the agent preview modal"""
    import uuid
    import json
    
    try:
        # Get agent details
        from app.core.dependencies import get_agent_service
        from app.integrations.supabase_client import supabase_manager
        
        agent = None
        
        # Handle global agents (from main agents table)
        if client_id == "global":
            try:
                result = supabase_manager.admin_client.table('agents').select('*').eq('slug', agent_slug).execute()
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
                        'voice_settings': json.loads(agent_data.get('voice_settings')) if isinstance(agent_data.get('voice_settings'), str) else agent_data.get('voice_settings', {
                            'provider': 'livekit',
                            'voice_id': 'alloy',
                            'temperature': 0.7
                        }),
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
            return HTMLResponse(
                content="""
                <div class="fixed inset-0 bg-gray-900 bg-opacity-90 flex items-center justify-center z-50">
                    <div class="bg-dark-surface p-6 rounded-lg border border-dark-border max-w-md">
                        <h3 class="text-lg font-medium text-dark-text mb-2">Agent Not Found</h3>
                        <p class="text-sm text-dark-text-secondary mb-4">The requested agent could not be found.</p>
                        <button hx-on:click="document.getElementById('modal-container').innerHTML = ''" 
                                class="btn-primary px-4 py-2 rounded text-sm">Close</button>
                    </div>
                </div>
                """,
                status_code=404
            )
        
        # Generate a unique session ID for this preview
        session_id = f"preview_{uuid.uuid4().hex[:8]}"
        
        return templates.TemplateResponse("admin/partials/agent_preview.html", {
            "request": request,
            "agent": agent,
            "client_id": client_id,
            "session_id": session_id
        })
        
    except Exception as e:
        logger.error(f"Error loading agent preview: {e}")
        return HTMLResponse(
            content=f"""
            <div class="fixed inset-0 bg-gray-900 bg-opacity-90 flex items-center justify-center z-50">
                <div class="bg-dark-surface p-6 rounded-lg border border-dark-border max-w-md">
                    <h3 class="text-lg font-medium text-dark-text mb-2">Error Loading Preview</h3>
                    <p class="text-sm text-dark-text-secondary mb-4">{str(e)}</p>
                    <button hx-on:click="document.getElementById('modal-container').innerHTML = ''" 
                            class="btn-primary px-4 py-2 rounded text-sm">Close</button>
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
            result = supabase_manager.admin_client.table('agents').select('*').eq('slug', agent_slug).execute()
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
                        'provider': 'livekit',
                        'voice_id': 'alloy',
                        'temperature': 0.7
                    }),
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
    messages = preview_sessions.get(session_id, [])
    
    # Add user message
    messages.append({"content": message, "is_user": True})
    
    # Generate AI response using the trigger endpoint
    try:
        # Use the trigger endpoint to get a real AI response
        from app.api.v1.trigger import handle_text_trigger, TriggerAgentRequest, TriggerMode
        
        # Create a mock request for the text trigger
        trigger_request = TriggerAgentRequest(
            agent_slug=agent_slug,
            client_id=client_id,
            mode=TriggerMode.TEXT,
            message=message,
            user_id=f"admin_{admin_user.get('id', 'preview')}",
            session_id=session_id,
            conversation_id=session_id
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
                        'deepgram_api_key': config.get('deepgram_api_key', ''),
                        'elevenlabs_api_key': config.get('elevenlabs_api_key', '')
                    }
            except Exception as e:
                logger.warning(f"Failed to get agent configuration: {e}")
                # Use environment variables as fallback
                api_keys = {
                    'openai_api_key': os.getenv('OPENAI_API_KEY', ''),
                    'groq_api_key': os.getenv('GROQ_API_KEY', ''),
                }
            
            # Process the message using AI
            if api_keys.get('openai_api_key') or api_keys.get('groq_api_key'):
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
        logger.warning(f"Preview AI response failed: {e}")
        # Fallback to a simple preview response
        ai_response = f"I'm {agent.name}, your AI assistant. (Preview mode - actual AI processing would happen in production)"
    
    # Add AI response
    messages.append({"content": ai_response, "is_user": False})
    
    # Store messages in session
    if not hasattr(request.app.state, 'preview_sessions'):
        request.app.state.preview_sessions = {}
    request.app.state.preview_sessions[session_id] = messages
    
    # Return updated messages
    return templates.TemplateResponse("admin/partials/chat_messages.html", {
        "request": request,
        "messages": messages,
        "is_loading": False
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
    messages = preview_sessions.get(session_id, [])
    
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
    from app.core.dependencies import get_agent_service
    from app.integrations.supabase_client import supabase_manager
    import json
    
    agent = None
    
    # Handle global agents
    if client_id == "global":
        try:
            result = supabase_manager.admin_client.table('agents').select('*').eq('slug', agent_slug).execute()
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
                        'provider': 'livekit',
                        'voice_id': 'alloy',
                        'temperature': 0.7
                    }),
                    'webhooks': agent_data.get('webhooks', {}),
                    'client_id': 'global'
                })()
        except Exception as e:
            logger.error(f"Failed to get global agent: {e}")
    else:
        agent_service = get_agent_service()
        agent = await agent_service.get_agent(client_id, agent_slug)
    
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    if mode == "voice":
        # Return voice chat interface
        return templates.TemplateResponse("admin/partials/voice_chat.html", {
            "request": request,
            "agent": agent,
            "client_id": client_id,
            "session_id": session_id
        })
    else:
        # Return text chat interface (reuse the messages partial with container)
        preview_sessions = getattr(request.app.state, 'preview_sessions', {})
        messages = preview_sessions.get(session_id, [])
        
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


@router.post("/agents/preview/{client_id}/{agent_slug}/voice-start")
async def start_voice_preview(
    request: Request,
    background_tasks: BackgroundTasks,
    client_id: str,
    agent_slug: str,
    session_id: str = Form(...),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Start a voice preview session"""
    try:
        import uuid
        from app.api.v1.trigger import TriggerAgentRequest, TriggerMode, trigger_agent
        from app.core.dependencies import get_agent_service
        from app.integrations.livekit_client import livekit_manager
        
        # Generate a unique room name for this preview, but cache it by session to prevent duplicates
        cache_key = f"preview_room_{client_id}_{agent_slug}_{session_id}"
        room_name = request.app.state.preview_rooms.get(cache_key) if hasattr(request.app.state, 'preview_rooms') else None
        
        if not room_name:
            room_name = f"preview_{agent_slug}_{uuid.uuid4().hex[:8]}"
            # Cache the room name for this session
            if not hasattr(request.app.state, 'preview_rooms'):
                request.app.state.preview_rooms = {}
            request.app.state.preview_rooms[cache_key] = room_name
            logger.info(f"🎯 Generated new preview room: {room_name} for session {session_id}")
        else:
            logger.info(f"♻️ Reusing cached preview room: {room_name} for session {session_id}")
        
        # Create trigger request for voice mode
        # Use the actual admin user ID for RAG context
        admin_user_id = admin_user.get('user_id') or admin_user.get('id')
        if not admin_user_id:
            # Fallback for preview
            admin_user_id = f"admin_preview_{admin_user.get('email', 'user')}"
        
        trigger_request = TriggerAgentRequest(
            agent_slug=agent_slug,
            client_id=client_id if client_id != "global" else None,  # Let trigger endpoint handle global agents
            mode=TriggerMode.VOICE,
            room_name=room_name,
            user_id=admin_user_id,  # Use actual admin user ID for RAG
            session_id=session_id,
            conversation_id=session_id
        )
        
        # Get agent service to trigger the agent
        agent_service = get_agent_service()
        
        # Trigger the agent in voice mode
        logger.info(f"Triggering agent {agent_slug} for room {room_name}")
        
        # Call trigger_agent directly (synchronously)
        try:
            result = await trigger_agent(trigger_request, http_request=request, background_tasks=background_tasks, agent_service=agent_service)
            logger.info(f"Trigger result type: {type(result)}")
            logger.info(f"Trigger result: {result if isinstance(result, dict) else 'Object with success=' + str(getattr(result, 'success', 'NO SUCCESS ATTR'))}")
            
            # This line was causing the error - trying to access .success on a dict
            if isinstance(result, dict):
                logger.info(f"Result is dict, success={result.get('success')}")
            else:
                logger.info(f"Result is object, success={result.success}")
        except Exception as e:
            logger.error(f"Error triggering agent: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            raise
        
        # Extract the response data - handle both dict and object responses
        if isinstance(result, dict):
            # Handle dict response (from our voice mode fix)
            success = result.get("success", False)
            data = result.get("data", {})
            message = result.get("message", "")
        else:
            # Handle object response
            success = result.success
            data = result.data
            message = result.message if hasattr(result, 'message') else ""
            
        if success and data:
            livekit_config = data.get("livekit_config", {})
            user_token = livekit_config.get("user_token", "")
            server_url = livekit_config.get("server_url", "")
            
            # Log what we're sending to the template
            logger.info(f"Voice preview starting - Room: {room_name}, Server: {server_url}, Token: {user_token[:50] if user_token else 'No token'}...")
            
            # Return voice interface with LiveKit client
            return templates.TemplateResponse("admin/partials/voice_preview_live_v2.html", {
                "request": request,
                "room_name": room_name,
                "server_url": server_url,
                "user_token": user_token,
                "agent_slug": agent_slug,
                "client_id": client_id,
                "session_id": session_id
            })
        else:
            error_msg = message or "Failed to start voice session"
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


@router.get("/agents/preview/{client_id}/{agent_slug}/voice-status")
async def get_voice_status(
    request: Request,
    client_id: str,
    agent_slug: str,
    room_name: str,
    session_id: str
):
    """Check the status of voice agent container creation"""
    logger.info(f"Voice status check for room {room_name}, session {session_id}")
    try:
        import json
        import os
        
        # Check the status file
        status_file = f"/tmp/voice_trigger_{room_name}.status"
        
        if not os.path.exists(status_file):
            logger.error(f"Status file not found: {status_file}")
            # Default: show error
            return HTMLResponse('''
                <div class="h-96 flex items-center justify-center">
                    <div class="text-center">
                        <p class="text-red-500 mb-2">Session not found</p>
                        <button onclick="location.reload()" class="px-4 py-2 bg-dark-elevated rounded">
                            Try Again
                        </button>
                    </div>
                </div>
            ''')
        
        # Read the status
        with open(status_file, 'r') as f:
            content = f.read()
        
        if content == "pending":
            # Still processing
            return templates.TemplateResponse("admin/partials/voice_preview_loading.html", {
                "request": request,
                "room_name": room_name,
                "agent_slug": agent_slug,
                "client_id": client_id,
                "session_id": session_id,
                "message": "Container starting up..."
            })
        
        # Parse the JSON result
        try:
            status_data = json.loads(content)
            
            if status_data.get("status") == "completed" and status_data.get("success"):
                data = status_data.get("data", {})
                livekit_config = data.get("livekit_config", {})
                user_token = livekit_config.get("user_token", "")
                server_url = livekit_config.get("server_url", "")
                
                # Clean up the status file
                os.remove(status_file)
                
                # Return the actual voice interface
                return templates.TemplateResponse("admin/partials/voice_preview_live_v2.html", {
                    "request": request,
                    "room_name": room_name,
                    "server_url": server_url,
                    "user_token": user_token,
                    "agent_slug": agent_slug,
                    "client_id": client_id,
                    "session_id": session_id
                })
            else:
                # Error occurred
                error_msg = status_data.get("error", "Unknown error")
                logger.error(f"Voice trigger failed: {error_msg}")
                os.remove(status_file)
        except json.JSONDecodeError:
            logger.error(f"Invalid status file content: {content}")
        
        # Default: show error
        return HTMLResponse('''
            <div class="h-96 flex items-center justify-center">
                <div class="text-center">
                    <p class="text-red-500 mb-2">Failed to start voice agent</p>
                    <button onclick="location.reload()" class="px-4 py-2 bg-dark-elevated rounded">
                        Try Again
                    </button>
                </div>
            </div>
        ''')
        
    except Exception as e:
        logger.error(f"Error checking voice status: {e}")
        return HTMLResponse(f'<div class="text-red-500">Error: {str(e)}</div>')


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
    # Return container to pool instead of stopping it
    if room_name and session_id:
        try:
            from app.services.container_manager import container_manager
            # Extract client ID from room name pattern: preview_clarence-coherence_XXXXXXXX
            # The client_id is already provided as a parameter
            
            # Return container to pool for reuse
            returned = await container_manager.return_container(client_id, session_id)
            if returned:
                logger.info(f"✅ Returned container to pool for client {client_id}, session {session_id}")
            else:
                # Fallback to stopping if return fails
                await container_manager.stop_agent_for_room(room_name)
                logger.info(f"Stopped agent container for room {room_name}")
        except Exception as e:
            logger.error(f"Error handling container: {e}")
    
    # Get agent to display correct voice settings
    from app.core.dependencies import get_agent_service
    from app.integrations.supabase_client import supabase_manager
    import json
    
    agent = None
    
    # Handle global agents
    if client_id == "global":
        try:
            result = supabase_manager.admin_client.table('agents').select('*').eq('slug', agent_slug).execute()
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
            "voice_settings": {"provider": "livekit", "voice_id": "alloy", "temperature": 0.7}
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
        request.app.state.preview_sessions[session_id] = []
    
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
        
        # Validate API keys before updating
        from app.services.api_key_validator import api_key_validator
        
        # Collect API keys that need validation
        api_keys_to_validate = {}
        if update_data.settings.api_keys:
            keys_dict = update_data.settings.api_keys.dict()
            for key_name, key_value in keys_dict.items():
                if key_value and key_name in [
                    'siliconflow_api_key', 'openai_api_key', 'groq_api_key', 
                    'cartesia_api_key', 'deepgram_api_key', 'elevenlabs_api_key',
                    'novita_api_key', 'jina_api_key'
                ]:
                    api_keys_to_validate[key_name] = key_value
        
        # Validate keys if any are provided
        if api_keys_to_validate:
            logger.info(f"Validating {len(api_keys_to_validate)} API keys before saving")
            validation_results = await api_key_validator.validate_api_keys(api_keys_to_validate)
            
            # Check for invalid keys
            invalid_keys = []
            for key_name, result in validation_results.items():
                if not result['valid']:
                    invalid_keys.append(f"{key_name}: {result['message']}")
            
            if invalid_keys:
                error_msg = "Invalid API keys detected:\\n" + "\\n".join(invalid_keys)
                logger.warning(f"API key validation failed: {error_msg}")
                return RedirectResponse(
                    url=f"/admin/clients/{client_id}?error=" + error_msg.replace('\n', '+').replace(' ', '+'),
                    status_code=303
                )
        
        # Update client only if validation passes
        updated_client = await client_service.update_client(client_id, update_data)
        
        # Debug: Log the API keys after update
        logger.info(f"After update - cartesia={updated_client.settings.api_keys.cartesia_api_key if updated_client.settings.api_keys else 'None'}, siliconflow={updated_client.settings.api_keys.siliconflow_api_key if updated_client.settings.api_keys else 'None'}")
        
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
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get documents for Knowledge Base admin interface"""
    try:
        from app.services.document_processor import document_processor
        
        # Get documents for the specified client
        documents = await document_processor.get_documents(
            user_id=None,  # Admin access doesn't need user_id
            client_id=client_id,
            status=None,
            limit=100
        )
        
        # Return documents array directly to match frontend expectation
        return documents
        
    except Exception as e:
        logger.error(f"Failed to get documents for client {client_id}: {e}")
        return []


@router.get("/knowledge-base/agents")
async def get_knowledge_base_agents(
    client_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get agents for Knowledge Base admin interface from client-specific Supabase"""
    try:
        from app.core.dependencies import get_client_service
        from supabase import create_client
        
        # Get client details to access their Supabase
        client_service = get_client_service()
        client = await client_service.get_client(client_id)
        
        if not client:
            logger.error(f"Client {client_id} not found")
            return []
        
        # Get client's Supabase credentials
        client_settings = client.get('settings', {}) if isinstance(client, dict) else getattr(client, 'settings', {})
        supabase_settings = client_settings.get('supabase', {}) if isinstance(client_settings, dict) else getattr(client_settings, 'supabase', {})
        
        supabase_url = supabase_settings.get('url', '') if isinstance(supabase_settings, dict) else getattr(supabase_settings, 'url', '')
        service_key = supabase_settings.get('service_role_key', '') if isinstance(supabase_settings, dict) else getattr(supabase_settings, 'service_role_key', '')
        
        if not supabase_url or not service_key:
            logger.warning(f"Client {client_id} missing Supabase credentials")
            return []
        
        # Create client-specific Supabase connection
        client_supabase = create_client(supabase_url, service_key)
        
        # Query agents from client's database
        result = client_supabase.table('agent_configurations')\
            .select('id, agent_name, agent_slug')\
            .order('agent_name')\
            .execute()
        
        return result.data
        
    except Exception as e:
        logger.error(f"Failed to get agents for client {client_id}: {e}")
        return []


@router.post("/knowledge-base/upload")
async def upload_knowledge_base_document(
    file: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    client_id: str = Form(...),
    agent_ids: str = Form(""),
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Upload document to client-specific knowledge base"""
    try:
        import tempfile
        import os
        from app.services.document_processor import document_processor
        
        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as tmp_file:
            content = await file.read()
            tmp_file.write(content)
            tmp_file_path = tmp_file.name
        
        try:
            # Parse agent IDs
            selected_agent_ids = []
            if agent_ids and agent_ids != "all":
                selected_agent_ids = [aid.strip() for aid in agent_ids.split(",") if aid.strip()]
            
            # Process the uploaded file with client-specific storage
            result = await document_processor.process_uploaded_file(
                file_path=tmp_file_path,
                title=title,
                description=description,
                user_id=admin_user.get('id'),
                agent_ids=selected_agent_ids if selected_agent_ids else None,
                client_id=client_id
            )
            
            # Note: We don't delete the temp file here because async processing needs it
            # The document processor should handle cleanup after processing is complete
            
            return result
            
        finally:
            # Don't delete the file here - async processing needs it
            pass
                
    except Exception as e:
        logger.error(f"Error uploading document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/clients")
async def get_admin_clients(admin_user: Dict[str, Any] = Depends(get_admin_user)):
    """Get clients available to admin user"""
    try:
        from app.core.dependencies import get_client_service
        
        # Get all clients for admin
        client_service = get_client_service()
        clients = await client_service.get_all_clients()
        
        # Format for frontend
        client_list = []
        for client in clients:
            if isinstance(client, dict):
                client_data = {
                    "id": client.get("id"),
                    "name": client.get("name", client.get("id", "Unknown Client")),
                    "domain": client.get("domain", ""),
                    "status": client.get("status", "unknown")
                }
            else:
                client_data = {
                    "id": getattr(client, 'id', None),
                    "name": getattr(client, 'name', getattr(client, 'id', 'Unknown Client')),
                    "domain": getattr(client, 'domain', ''),
                    "status": getattr(client, 'status', 'unknown')
                }
            client_list.append(client_data)
        
        return client_list
        
    except Exception as e:
        logger.error(f"Failed to get clients for admin: {e}")
        return []




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
        
        from app.integrations.supabase_client import supabase_manager
        
        # Update document with new access settings
        update_data = {
            "agent_access": agent_access,
            "agent_ids": agent_ids if agent_access == "specific" else [],
            "updated_at": datetime.utcnow().isoformat()
        }
        
        result = supabase_manager.admin_client.table("documents")\
            .update(update_data)\
            .eq("id", document_id)\
            .execute()
        
        return {"success": True, "message": "Document access updated"}
        
    except Exception as e:
        logger.error(f"Failed to update document access: {e}")
        return {"success": False, "error": str(e)}


@router.delete("/knowledge-base/documents/{document_id}")
async def delete_document(
    document_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Delete a document"""
    try:
        from app.services.document_processor import document_processor
        
        success = await document_processor.delete_document(document_id)
        
        if success:
            return {"success": True, "message": "Document deleted"}
        else:
            return {"success": False, "error": "Failed to delete document"}
            
    except Exception as e:
        logger.error(f"Failed to delete document: {e}")
        return {"success": False, "error": str(e)}


@router.post("/knowledge-base/documents/{document_id}/reprocess")
async def reprocess_document(
    document_id: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Reprocess a document"""
    try:
        from app.integrations.supabase_client import supabase_manager
        
        # Update document status to processing
        result = supabase_manager.admin_client.table("documents")\
            .update({"status": "processing", "updated_at": datetime.utcnow().isoformat()})\
            .eq("id", document_id)\
            .execute()
        
        # TODO: Trigger actual reprocessing job
        
        return {"success": True, "message": "Document reprocessing started"}
        
    except Exception as e:
        logger.error(f"Failed to reprocess document: {e}")
        return {"success": False, "error": str(e)}


@router.get("/test-voice")
async def test_voice(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Voice agent test page"""
    return templates.TemplateResponse(
        "admin/test_voice.html",
        {"request": request}
    )


@router.get("/debug")
async def debug_dashboard(
    request: Request,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Debug dashboard for voice agents"""
    return templates.TemplateResponse(
        "admin/debug_dashboard.html",
        {"request": request}
    )


# Include debug routes
router.include_router(debug_routes.router)
