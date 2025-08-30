#!/usr/bin/env python3
"""
Simple dashboard to verify client configurations
"""
import os
import asyncio
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

# Load .env manually
with open('.env', 'r') as f:
    for line in f:
        if '=' in line and not line.strip().startswith('#'):
            key, value = line.strip().split('=', 1)
            os.environ[key] = value

from app.services.client_service_supabase_enhanced import ClientService

app = FastAPI(title="Sidekick Forge Dashboard")

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Simple dashboard to show client configurations"""
    try:
        client_service = ClientService(
            os.getenv('SUPABASE_URL'), 
            os.getenv('SUPABASE_SERVICE_KEY')
        )
        
        # Get all clients
        clients = await client_service.get_all_clients()
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Sidekick Forge - Client Dashboard</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                .client { border: 1px solid #ddd; margin: 20px 0; padding: 20px; border-radius: 8px; }
                .status-active { color: green; font-weight: bold; }
                .status-inactive { color: red; font-weight: bold; }
                .key-present { color: green; }
                .key-missing { color: red; }
                .embedding { background: #f0f0f0; padding: 10px; margin: 10px 0; border-radius: 4px; }
            </style>
        </head>
        <body>
            <h1>🚀 Sidekick Forge - Client Configuration Dashboard</h1>
            <p>Environment reloaded with corrected client service</p>
        """
        
        for client in clients:
            status_class = "status-active" if client.active else "status-inactive"
            status_text = "✅ ACTIVE" if client.active else "❌ INACTIVE"
            
            # Check Supabase credentials
            supabase_status = ""
            if client.settings and client.settings.supabase:
                anon_key_status = "✅ Present" if client.settings.supabase.anon_key else "❌ Missing"
                service_key_status = "✅ Present" if client.settings.supabase.service_role_key else "❌ Missing"
                supabase_status = f"""
                <strong>Supabase Configuration:</strong><br>
                - URL: {client.settings.supabase.url}<br>
                - Anon Key: <span class="{'key-present' if client.settings.supabase.anon_key else 'key-missing'}">{anon_key_status}</span><br>
                - Service Key: <span class="{'key-present' if client.settings.supabase.service_role_key else 'key-missing'}">{service_key_status}</span><br>
                """
            
            # Check embedding settings
            embedding_status = ""
            if hasattr(client.settings, '__dict__') and 'embedding' in client.settings.__dict__:
                embedding = client.settings.__dict__['embedding']
                embedding_status = f"""
                <div class="embedding">
                <strong>🧠 Embedding Configuration:</strong><br>
                - Provider: <strong>{getattr(embedding, 'provider', 'N/A')}</strong><br>
                - Document Model: {getattr(embedding, 'document_model', 'N/A')}<br>
                - Conversation Model: {getattr(embedding, 'conversation_model', 'N/A')}<br>
                - Dimension: {getattr(embedding, 'dimension', 'N/A')}
                </div>
                """
            
            html += f"""
            <div class="client">
                <h2>{client.name}</h2>
                <p><strong>ID:</strong> {client.id}</p>
                <p><strong>Status:</strong> <span class="{status_class}">{status_text}</span></p>
                <p><strong>Domain:</strong> {client.domain or 'Not set'}</p>
                <p><strong>Description:</strong> {client.description or 'Not set'}</p>
                <p>{supabase_status}</p>
                {embedding_status}
            </div>
            """
        
        html += """
            </body>
            </html>
        """
        
        return html
        
    except Exception as e:
        return f"""
        <html><body>
        <h1>❌ Error loading dashboard</h1>
        <p>Error: {str(e)}</p>
        </body></html>
        """

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "sidekick-forge-dashboard", "corrected_client_service": True}

if __name__ == "__main__":
    print("🚀 Starting Simple Dashboard with Corrected Client Service...")
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="info")