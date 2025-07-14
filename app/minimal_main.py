from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
from datetime import datetime
import redis

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
                    ✅ System Operational (Minimal Mode)
                </div>
                
                <div class="buttons">
                    <a href="/admin/" class="button">Admin Dashboard</a>
                    <a href="/docs" class="button secondary">API Documentation</a>
                    <a href="/health" class="button secondary">Health Status</a>
                </div>
                
                <p style="margin-top: 40px; font-size: 0.9em; color: #999;">
                    Minimal Version 1.0.0 | <a href="https://autonomite.net" style="color: #667eea;">autonomite.net</a>
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
        "service": "autonomite-saas-minimal",
        "timestamp": datetime.utcnow().isoformat(),
        "mode": "minimal"
    }

# Simple admin dashboard
@app.get("/admin/", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>Autonomite Admin Dashboard</title>
            <style>
                body { font-family: Arial, sans-serif; padding: 20px; background: #f5f5f5; }
                .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                .status { padding: 15px; background: #e8f5e8; border-radius: 4px; border-left: 4px solid #46b450; margin: 20px 0; }
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
                    <h2>✅ System Status: ONLINE (Minimal Mode)</h2>
                    <p>The FastAPI backend is running in minimal configuration!</p>
                    <p><strong>Current Time:</strong> """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC") + """</p>
                </div>
                
                <div class="warning">
                    <h3>⚠️ Minimal Mode Active</h3>
                    <p>This is a minimal version for agent development handoff. Full features require proper Supabase configuration.</p>
                    <ul>
                        <li>✅ Basic health checks working</li>
                        <li>✅ Redis connection working</li>
                        <li>⚠️ Agent management requires full configuration</li>
                        <li>⚠️ Authentication requires Supabase setup</li>
                    </ul>
                </div>
                
                <div class="warning">
                    <h3>🛠️ For Agent Development</h3>
                    <p>To work on agents, you have these options:</p>
                    <ol>
                        <li><strong>Use Heavy Plugin</strong>: Activate the original autonomite-agent.php for full development features</li>
                        <li><strong>Configure Backend</strong>: Set up proper Supabase credentials in .env file</li>
                        <li><strong>Local Development</strong>: Use the existing heavy plugin infrastructure on this server</li>
                    </ol>
                </div>
                
                <div class="footer">
                    <p>🚀 <strong>Autonomite SaaS Platform</strong> - AI Agent Management for WordPress</p>
                    <p>Minimal mode for development handoff | Server: agents.autonomite.net</p>
                </div>
            </div>
        </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "minimal_main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )