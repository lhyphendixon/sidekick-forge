"""
Client management API endpoints
"""
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import os
import redis

from app.models.client import Client, ClientCreate, ClientUpdate, ClientInDB
from app.services.client_service_hybrid import ClientService
from app.core.dependencies import get_redis_client

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def get_client_service(redis_client: redis.Redis = Depends(get_redis_client)) -> ClientService:
    """Get client service instance"""
    MASTER_SUPABASE_URL = os.getenv("MASTER_SUPABASE_URL", "https://YOUR_MASTER_PROJECT.supabase.co")
    MASTER_SUPABASE_KEY = os.getenv("MASTER_SUPABASE_SERVICE_KEY", "YOUR_MASTER_SERVICE_KEY")
    return ClientService(MASTER_SUPABASE_URL, MASTER_SUPABASE_KEY, redis_client)


@router.get("/", response_model=List[Client])
async def list_clients(
    service: ClientService = Depends(get_client_service)
) -> List[Client]:
    """List all clients"""
    return await service.get_all_clients()


@router.post("/", response_model=Client)
async def create_client(
    client: ClientCreate,
    service: ClientService = Depends(get_client_service)
) -> Client:
    """Create a new client"""
    return await service.create_client(client)


@router.get("/{client_id}", response_model=Client)
async def get_client(
    client_id: str,
    service: ClientService = Depends(get_client_service)
) -> Client:
    """Get a specific client"""
    client = await service.get_client(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.put("/{client_id}", response_model=Client)
async def update_client(
    client_id: str,
    update_data: ClientUpdate,
    service: ClientService = Depends(get_client_service)
) -> Client:
    """Update a client"""
    updated_client = await service.update_client(client_id, update_data)
    if not updated_client:
        raise HTTPException(status_code=404, detail="Client not found")
    return updated_client


@router.delete("/{client_id}")
async def delete_client(
    client_id: str,
    service: ClientService = Depends(get_client_service)
):
    """Delete a client"""
    deleted = await service.delete_client(client_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"status": "deleted", "client_id": client_id}


@router.get("/by-domain/{domain}", response_model=Client)
async def get_client_by_domain(
    domain: str,
    service: ClientService = Depends(get_client_service)
) -> Client:
    """Get a client by domain"""
    client = await service.get_client_by_domain(domain)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found for domain")
    return client


@router.post("/initialize-defaults")
async def initialize_defaults(
    service: ClientService = Depends(get_client_service)
):
    """Initialize default clients"""
    await service.initialize_default_clients()
    return {"status": "initialized"}


@router.get("/cache-stats", response_model=Dict[str, Any])
async def get_cache_stats(
    service: ClientService = Depends(get_client_service)
) -> Dict[str, Any]:
    """Get cache statistics for monitoring"""
    return service.get_cache_stats()


class SupabaseCredentials(BaseModel):
    """Supabase credentials for syncing settings"""
    url: str
    service_role_key: str


@router.post("/sync-settings")
async def sync_settings_from_supabase(
    credentials: SupabaseCredentials,
    service: ClientService = Depends(get_client_service)
) -> Dict[str, Any]:
    """
    Fetch settings from a Supabase instance's agent_configurations table.
    Supabase is the source of truth - both WordPress and SaaS backend
    sync their settings from Supabase.
    """
    # Demo mode: If using the dummy URL, return sample data
    if credentials.url == "https://xyzxyzxyzxyzxyzxyz.supabase.co":
        return {
            "api_keys": {
                # LLM Providers
                "openai_api_key": "sk-proj-demo-xxxxxxxxxxxxx",
                "groq_api_key": "gsk_demo_xxxxxxxxxxxxx",
                "deepinfra_api_key": "demo_deepinfra_key_xxxxx",
                "replicate_api_key": "r8_demo_replicate_xxxxx",
                # Voice/Speech Providers
                "deepgram_api_key": "demo_deepgram_key",
                "elevenlabs_api_key": "demo_elevenlabs_key",
                "cartesia_api_key": "demo_cartesia_key",
                "speechify_api_key": "demo_speechify_key",
                # Embedding/Reranking Providers
                "novita_api_key": "demo_novita_key_xxxxx",
                "cohere_api_key": "demo_cohere_key",
                "siliconflow_api_key": "demo_siliconflow_key_xxxxx",
                "jina_api_key": "demo_jina_key_xxxxx",
            },
            "livekit": {
                "server_url": "https://demo-livekit-server.com",
                "api_key": "APIDemo123",
                "api_secret": "SecretDemo456"
            },
            "message": "Demo sync successful - using sample data since this is a test Supabase URL"
        }
    
    try:
        settings = await service.fetch_settings_from_supabase(
            credentials.url,
            credentials.service_role_key
        )
        return settings
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch settings from Supabase: {str(e)}"
        )