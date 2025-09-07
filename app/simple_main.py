from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
from datetime import datetime
import redis

# Import hybrid client service and agent service
from app.services.client_service_hybrid import ClientService
from app.services.agent_service import AgentService

# Initialize Redis with connection pooling
redis_pool = redis.ConnectionPool(
    host=os.getenv("REDIS_HOST", "localhost"), 
    port=int(os.getenv("REDIS_PORT", 6379)), 
    decode_responses=True,
    max_connections=50,
    socket_keepalive=True,
    socket_keepalive_options={
        1: 1,  # TCP_KEEPIDLE
        2: 3,  # TCP_KEEPINTVL  
        3: 5   # TCP_KEEPCNT
    }
)
redis_client = redis.Redis(connection_pool=redis_pool)

# Initialize client service with master Supabase credentials and Redis
# These should come from environment variables
# Using a valid dummy URL to prevent connection errors during demo
MASTER_SUPABASE_URL = os.getenv("MASTER_SUPABASE_URL", "https://xyzxyzxyzxyzxyzxyz.supabase.co")
MASTER_SUPABASE_KEY = os.getenv("MASTER_SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inh5enh5enh5enh5enh5enh5eiIsInJvbGUiOiJzZXJ2aWNlX3JvbGUiLCJpYXQiOjE2NDYyMzkwMjIsImV4cCI6MTk2MTgxNTAyMn0.dummy-key-for-testing")

client_service = ClientService(MASTER_SUPABASE_URL, MASTER_SUPABASE_KEY, redis_client)
agent_service = AgentService(client_service, redis_client)

# Create FastAPI app
app = FastAPI(
    title="Autonomite Agent SaaS API",
    description="AI Agent management platform",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Mount static files
if os.path.exists("/opt/autonomite-saas/app/static"):
    app.mount("/static", StaticFiles(directory="/opt/autonomite-saas/app/static"), name="static")

# Initialize templates
templates = Jinja2Templates(directory="/opt/autonomite-saas/app/templates")

# Root endpoint - welcome page
@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>Autonomite SaaS Platform</title>
            <style>
                body { 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                    margin: 0; 
                    padding: 0; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                .container { 
                    background: white; 
                    padding: 60px; 
                    border-radius: 20px; 
                    box-shadow: 0 20px 40px rgba(0,0,0,0.1);
                    text-align: center;
                    max-width: 600px;
                    margin: 20px;
                }
                h1 { 
                    color: #1d2327; 
                    margin-bottom: 10px;
                    font-size: 2.5em;
                }
                .logo {
                    font-size: 4em;
                    margin-bottom: 20px;
                }
                p { 
                    color: #646970; 
                    line-height: 1.6;
                    font-size: 1.1em;
                }
                .buttons {
                    margin-top: 40px;
                    display: flex;
                    gap: 15px;
                    justify-content: center;
                    flex-wrap: wrap;
                }
                .button {
                    display: inline-block;
                    padding: 12px 30px;
                    background: #667eea;
                    color: white;
                    text-decoration: none;
                    border-radius: 8px;
                    font-weight: 600;
                    transition: all 0.3s ease;
                }
                .button:hover {
                    background: #764ba2;
                    transform: translateY(-2px);
                    box-shadow: 0 5px 15px rgba(118, 75, 162, 0.3);
                }
                .button.secondary {
                    background: #f0f0f0;
                    color: #333;
                }
                .button.secondary:hover {
                    background: #e0e0e0;
                }
                .status {
                    margin-top: 30px;
                    padding: 10px 20px;
                    background: #e8f5e8;
                    border-radius: 8px;
                    color: #2e7d32;
                    font-size: 0.9em;
                    display: inline-block;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="logo">🚀</div>
                <h1>Autonomite SaaS Platform</h1>
                <p>AI Agent Management Platform for WordPress</p>
                
                <div class="status">
                    ✅ System Operational
                </div>
                
                <div class="buttons">
                    <a href="/admin/" class="button">Admin Dashboard</a>
                    <a href="/docs" class="button secondary">API Documentation</a>
                    <a href="/health" class="button secondary">Health Status</a>
                </div>
                
                <p style="margin-top: 40px; font-size: 0.9em; color: #999;">
                    Version 1.0.0 | <a href="https://autonomite.net" style="color: #667eea;">autonomite.net</a>
                </p>
            </div>
        </body>
    </html>
    """

# Health check
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "autonomite-saas",
        "timestamp": datetime.utcnow().isoformat()
    }

# Admin root endpoint - main dashboard
@app.get("/admin/", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    # Get client count
    clients = await client_service.get_all_clients()
    
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "summary": {
            "total_clients": len(clients),
            "active_containers": 0,
            "stopped_containers": 0,
            "total_sessions": 0,
            "avg_cpu": 0,
            "total_memory_gb": 0,
            "timestamp": datetime.now().isoformat()
        },
        "user": {"username": "admin", "role": "superadmin"}
    })

# Simple admin test page
@app.get("/admin/test", response_class=HTMLResponse)
async def admin_test(request: Request):
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>Autonomite Admin - Test</title>
            <style>
                body { font-family: Arial, sans-serif; padding: 20px; background: #f5f5f5; }
                .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                .status { padding: 15px; background: #e8f5e8; border-radius: 4px; border-left: 4px solid #46b450; margin: 20px 0; }
                .info { padding: 15px; background: #e3f2fd; border-radius: 4px; border-left: 4px solid #2196f3; margin: 20px 0; }
                .warning { padding: 15px; background: #fff3cd; border-radius: 4px; border-left: 4px solid #ffc107; margin: 20px 0; }
                h1 { color: #1d2327; margin-bottom: 10px; }
                h2 { color: #46b450; margin-top: 0; }
                ul { margin: 10px 0; padding-left: 20px; }
                a { color: #2271b1; text-decoration: none; }
                a:hover { text-decoration: underline; }
                .footer { margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; color: #666; font-size: 14px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🎉 Autonomite Admin Dashboard</h1>
                
                <div class="status">
                    <h2>✅ System Status: ONLINE</h2>
                    <p>The FastAPI backend is running successfully!</p>
                    <p><strong>Current Time:</strong> """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC") + """</p>
                    <p><strong>URL:</strong> http://{settings.domain_name}/admin/test</p>
                </div>
                
                <div class="info">
                    <h3>📊 Available Endpoints</h3>
                    <ul>
                        <li><a href="/">API Root</a> - Basic service info</li>
                        <li><a href="/health">Health Check</a> - Service health status</li>
                        <li><a href="/docs">API Documentation</a> - Interactive API docs</li>
                        <li><a href="/redoc">ReDoc</a> - Alternative API documentation</li>
                        <li><a href="/admin/test">Admin Test</a> - This page</li>
                    </ul>
                </div>
                
                <div class="warning">
                    <h3>⚠️ Next Steps</h3>
                    <ul>
                        <li>✅ FastAPI backend is running</li>
                        <li>✅ Nginx is configured and proxying requests</li>
                        <li>🔄 Set up SSL certificates with Let's Encrypt</li>
                        <li>🔄 Configure Supabase connection</li>
                        <li>🔄 Set up LiveKit integration</li>
                        <li>🔄 Enable container management</li>
                        <li>🔄 Connect WordPress plugin</li>
                    </ul>
                </div>
                
                <div class="info">
                    <h3>🔧 Quick Setup Commands</h3>
                    <p>To complete the setup, run these commands on your server:</p>
                    <pre style="background: #f8f9fa; padding: 10px; border-radius: 4px; overflow-x: auto;">
# Set up SSL certificates
sudo certbot --nginx -d {domain_name}

# Configure environment variables
sudo nano /opt/autonomite-saas/.env

# Restart services
sudo systemctl restart nginx</pre>
                </div>
                
                <div class="footer">
                    <p>🚀 <strong>Autonomite SaaS Platform</strong> - AI Agent Management for WordPress</p>
                    <p>For support, visit: <a href="https://autonomite.net">autonomite.net</a></p>
                </div>
            </div>
        </body>
    </html>
    """

# Clients management page
@app.get("/admin/clients", response_class=HTMLResponse)
async def admin_clients(request: Request):
    clients = await client_service.get_all_clients()
    return templates.TemplateResponse("admin/clients.html", {
        "request": request,
        "clients": clients,
        "user": {"username": "admin", "role": "superadmin"}
    })

# Client detail/edit page
@app.get("/admin/clients/{client_id}", response_class=HTMLResponse)
async def admin_client_detail(request: Request, client_id: str):
    client = await client_service.get_client(client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    
    return templates.TemplateResponse("admin/client_edit.html", {
        "request": request,
        "client": client,
        "user": {"username": "admin", "role": "superadmin"}
    })

# Agents management page
@app.get("/admin/agents", response_class=HTMLResponse)
async def admin_agents(request: Request):
    # Get all agents with client info
    agents = await agent_service.get_all_agents_with_clients()
    clients = await client_service.get_all_clients()
    
    return templates.TemplateResponse("admin/agents.html", {
        "request": request,
        "agents": agents,
        "clients": clients,
        "user": {"username": "admin", "role": "superadmin"}
    })

# Agent detail/edit page
@app.get("/admin/agents/{client_id}/{agent_slug}", response_class=HTMLResponse)
async def admin_agent_detail(request: Request, client_id: str, agent_slug: str):
    agent = await agent_service.get_agent(client_id, agent_slug)
    if not agent:
        return HTMLResponse("Agent not found", status_code=404)
    
    client = await client_service.get_client(client_id)
    
    return templates.TemplateResponse("admin/agent_edit.html", {
        "request": request,
        "agent": agent,
        "client": client,
        "user": {"username": "admin", "role": "superadmin"}
    })

# API endpoints for clients, agents, and triggers
# Import locally to avoid circular imports
import app.api.v1.clients as clients_api
import app.api.v1.agents as agents_api
import app.api.v1.trigger as trigger_api
import app.api.v1.wordpress_sites as wordpress_sites_api
import app.api.v1.livekit_proxy as livekit_proxy_api
import app.api.v1.conversations_proxy as conversations_proxy_api
import app.api.v1.documents_proxy as documents_proxy_api
import app.api.v1.text_chat_proxy as text_chat_proxy_api
import app.api.v1.voice_transcripts as voice_transcripts_api
from app.services.wordpress_site_service import WordPressSiteService

# Initialize WordPress site service
wordpress_site_service = WordPressSiteService(MASTER_SUPABASE_URL, MASTER_SUPABASE_KEY, redis_client)
wordpress_sites_api.wordpress_service = wordpress_site_service

# Initialize LiveKit proxy services
livekit_proxy_api.client_service = client_service
livekit_proxy_api.agent_service = agent_service

# Initialize conversations proxy services
conversations_proxy_api.redis_client = redis_client
conversations_proxy_api.client_service = client_service

# Initialize documents proxy services
documents_proxy_api.redis_client = redis_client

# Initialize text chat proxy services
text_chat_proxy_api.redis_client = redis_client
text_chat_proxy_api.client_service = client_service
text_chat_proxy_api.agent_service = agent_service

# Initialize voice transcripts service
voice_transcripts_api.client_service = client_service
app.include_router(clients_api.router, prefix="/api/v1/clients", tags=["clients"])
app.include_router(agents_api.router, prefix="/api/v1", tags=["agents"])
app.include_router(trigger_api.router, prefix="", tags=["trigger"])
app.include_router(wordpress_sites_api.router, tags=["wordpress-sites"])  # Already has prefix in router
app.include_router(livekit_proxy_api.router, tags=["livekit-proxy"])
app.include_router(conversations_proxy_api.router, tags=["conversations-proxy"])
app.include_router(documents_proxy_api.router, tags=["documents-proxy"])
app.include_router(text_chat_proxy_api.router, tags=["text-chat-proxy"])
app.include_router(voice_transcripts_api.router, tags=["voice-transcripts"])

# Initialize default clients on startup
# @app.on_event("startup")
# async def startup_event():
#     await client_service.initialize_default_clients()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "simple_main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )