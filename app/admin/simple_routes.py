from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, Any
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(prefix="/admin", tags=["admin"])

# Initialize template engine
templates = Jinja2Templates(directory="/opt/autonomite-saas/app/templates")

async def get_admin_user(request: Request) -> Dict[str, Any]:
    """Simple admin authentication for development"""
    return {"username": "admin", "role": "superadmin"}

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main admin dashboard"""
    # Mock data for initial testing
    summary = {
        "total_clients": 0,
        "active_containers": 0,
        "stopped_containers": 0,
        "total_sessions": 0,
        "avg_cpu": 0,
        "total_memory_gb": 0,
        "timestamp": datetime.now().isoformat()
    }
    
    user = await get_admin_user(request)
    
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "summary": summary,
        "user": user
    })

@router.get("/test", response_class=HTMLResponse)
async def test_page(request: Request):
    """Simple test page to verify admin routes work"""
    return """
    <html>
        <head>
            <title>Autonomite Admin - Test</title>
            <style>
                body { font-family: Arial, sans-serif; padding: 20px; }
                .container { max-width: 800px; margin: 0 auto; }
                .status { padding: 10px; background: #e8f5e8; border-radius: 4px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🎉 Autonomite Admin Dashboard</h1>
                <div class="status">
                    <h2>✅ Admin Routes Working!</h2>
                    <p>The admin dashboard is successfully connected.</p>
                    <p><strong>Current Time:</strong> """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + """</p>
                </div>
                <h3>Next Steps:</h3>
                <ul>
                    <li>Configure container management</li>
                    <li>Set up metrics collection</li>
                    <li>Connect WordPress plugin</li>
                </ul>
                <p><a href="/admin/">← Back to Dashboard</a></p>
            </div>
        </body>
    </html>
    """

@router.get("/health")
async def admin_health():
    """Admin health check"""
    return {
        "status": "healthy",
        "admin_dashboard": "operational",
        "timestamp": datetime.now().isoformat()
    }