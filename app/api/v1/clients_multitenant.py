"""
Multi-tenant Client management endpoints for Sidekick Forge Platform
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
import logging

from app.models.platform_client import PlatformClient as Client, PlatformClientCreate as ClientCreate, PlatformClientUpdate as ClientUpdate
from app.services.client_service_multitenant import ClientService
from app.services.client_connection_manager import ClientConfigurationError

logger = logging.getLogger(__name__)

router = APIRouter()

# Create service instance
client_service = ClientService()


@router.get("/clients", response_model=List[Client])
async def list_clients() -> List[Client]:
    """
    List all clients in the platform
    
    This endpoint retrieves all registered clients from the platform database.
    """
    try:
        clients = await client_service.get_clients()
        return clients
    except Exception as e:
        logger.error(f"Error listing clients: {e}")
        raise HTTPException(status_code=500, detail="Failed to list clients")


@router.get("/clients/{client_id}", response_model=Client)
async def get_client(client_id: str) -> Client:
    """
    Get a specific client by ID
    
    This endpoint retrieves detailed information about a specific client.
    """
    try:
        client = await client_service.get_client(client_id)
        if not client:
            raise HTTPException(
                status_code=404,
                detail=f"Client {client_id} not found"
            )
        return client
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting client: {e}")
        raise HTTPException(status_code=500, detail="Failed to get client")


@router.post("/clients", response_model=Client)
async def create_client(client: ClientCreate) -> Client:
    """
    Create a new client
    
    This endpoint registers a new client in the platform with their own database credentials.
    """
    try:
        # Validate required fields
        if not client.name:
            raise HTTPException(status_code=400, detail="Client name is required")
        
        if not client.settings or not client.settings.supabase:
            raise HTTPException(
                status_code=400,
                detail="Supabase configuration is required"
            )
        
        if not client.settings.supabase.url or not client.settings.supabase.service_role_key:
            raise HTTPException(
                status_code=400,
                detail="Supabase project URL and service role key are required"
            )
        
        created_client = await client_service.create_client(client)
        if not created_client:
            raise HTTPException(status_code=400, detail="Failed to create client")
        
        logger.info(f"Created client '{client.name}' with ID {created_client.id}")
        return created_client
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating client: {e}")
        raise HTTPException(status_code=500, detail="Failed to create client")


@router.put("/clients/{client_id}", response_model=Client)
async def update_client(
    client_id: str,
    client_update: ClientUpdate
) -> Client:
    """
    Update an existing client
    
    This endpoint updates client information including credentials and API keys.
    """
    try:
        updated_client = await client_service.update_client(client_id, client_update)
        if not updated_client:
            raise HTTPException(
                status_code=404,
                detail=f"Client {client_id} not found"
            )
        
        logger.info(f"Updated client {client_id}")
        return updated_client
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating client: {e}")
        raise HTTPException(status_code=500, detail="Failed to update client")


@router.delete("/clients/{client_id}")
async def delete_client(client_id: str) -> dict:
    """
    Delete a client
    
    This endpoint removes a client from the platform (use with caution).
    """
    try:
        success = await client_service.delete_client(client_id)
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Client {client_id} not found"
            )
        
        logger.info(f"Deleted client {client_id}")
        return {"success": True, "message": f"Client {client_id} deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting client: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete client")


@router.post("/clients/{client_id}/sync")
async def sync_client_from_supabase(client_id: str) -> Client:
    """
    Sync client settings from their Supabase database
    
    This endpoint attempts to pull settings from the client's own database
    and update their platform record.
    """
    try:
        synced_client = await client_service.sync_from_supabase(client_id)
        if not synced_client:
            raise HTTPException(
                status_code=404,
                detail=f"Client {client_id} not found or sync failed"
            )
        
        logger.info(f"Synced settings for client {client_id}")
        return synced_client
    except HTTPException:
        raise
    except ClientConfigurationError as e:
        logger.error(f"Client configuration error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error syncing client: {e}")
        raise HTTPException(status_code=500, detail="Failed to sync client")


@router.get("/clients/{client_id}/api-keys")
async def get_client_api_keys(client_id: str) -> dict:
    """
    Get API keys configured for a client
    
    This endpoint returns the API keys (with sensitive values masked) for a client.
    """
    try:
        from app.services.client_connection_manager import get_connection_manager
        from uuid import UUID
        
        connection_manager = get_connection_manager()
        api_keys = connection_manager.get_client_api_keys(UUID(client_id))
        
        # Mask sensitive values for security
        masked_keys = {}
        for key, value in api_keys.items():
            if value and isinstance(value, str) and len(value) > 10:
                # Show first 4 and last 4 characters
                masked_keys[key] = f"{value[:4]}...{value[-4:]}"
            else:
                masked_keys[key] = "Not configured" if not value else value
        
        return {
            "client_id": client_id,
            "api_keys": masked_keys
        }
    except Exception as e:
        logger.error(f"Error getting client API keys: {e}")
        raise HTTPException(status_code=500, detail="Failed to get API keys")