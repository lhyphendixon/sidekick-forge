"""
Multi-tenant Agent management endpoints for Sidekick Forge Platform
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import UUID
import logging

from app.models.agent import Agent, AgentCreate, AgentUpdate
from app.services.agent_service_multitenant import AgentService
from app.services.client_connection_manager import ClientConfigurationError

logger = logging.getLogger(__name__)

router = APIRouter()

# Create service instance
agent_service = AgentService()


@router.get("/agents", response_model=List[Agent])
async def list_agents(
    client_id: UUID = Query(..., description="Client ID to fetch agents for")
) -> List[Agent]:
    """
    List all agents for a specific client
    
    This endpoint retrieves all agents configured for the specified client.
    """
    try:
        agents = await agent_service.get_agents(client_id)
        return agents
    except ClientConfigurationError as e:
        logger.error(f"Client configuration error: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error listing agents: {e}")
        raise HTTPException(status_code=500, detail="Failed to list agents")


@router.get("/agents/{agent_slug}", response_model=Agent)
async def get_agent(
    agent_slug: str,
    client_id: Optional[UUID] = Query(None, description="Client ID (auto-detected if not provided)")
) -> Agent:
    """
    Get a specific agent by slug
    
    If client_id is not provided, the system will attempt to find which client owns the agent.
    """
    try:
        # Auto-detect client if not provided
        if not client_id:
            client_id = await agent_service.find_agent_client(agent_slug)
            if not client_id:
                raise HTTPException(
                    status_code=404,
                    detail=f"Agent '{agent_slug}' not found in any client"
                )
        
        agent = await agent_service.get_agent(client_id, agent_slug)
        if not agent:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{agent_slug}' not found for client {client_id}"
            )
        
        return agent
    except HTTPException:
        raise
    except ClientConfigurationError as e:
        logger.error(f"Client configuration error: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting agent: {e}")
        raise HTTPException(status_code=500, detail="Failed to get agent")


@router.post("/agents", response_model=Agent)
async def create_agent(
    agent: AgentCreate,
    client_id: UUID = Query(..., description="Client ID to create agent for")
) -> Agent:
    """
    Create a new agent for a client
    
    This endpoint creates a new agent configuration for the specified client.
    """
    try:
        created_agent = await agent_service.create_agent(client_id, agent)
        if not created_agent:
            raise HTTPException(status_code=400, detail="Failed to create agent")
        
        logger.info(f"Created agent '{agent.slug}' for client {client_id}")
        return created_agent
    except ClientConfigurationError as e:
        logger.error(f"Client configuration error: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating agent: {e}")
        raise HTTPException(status_code=500, detail="Failed to create agent")


@router.put("/agents/{agent_slug}", response_model=Agent)
async def update_agent(
    agent_slug: str,
    agent_update: AgentUpdate,
    client_id: UUID = Query(..., description="Client ID of the agent")
) -> Agent:
    """
    Update an existing agent
    
    This endpoint updates the configuration of an existing agent.
    """
    try:
        updated_agent = await agent_service.update_agent(client_id, agent_slug, agent_update)
        if not updated_agent:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{agent_slug}' not found for client {client_id}"
            )
        
        logger.info(f"Updated agent '{agent_slug}' for client {client_id}")
        return updated_agent
    except HTTPException:
        raise
    except ClientConfigurationError as e:
        logger.error(f"Client configuration error: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating agent: {e}")
        raise HTTPException(status_code=500, detail="Failed to update agent")


@router.delete("/agents/{agent_slug}")
async def delete_agent(
    agent_slug: str,
    client_id: UUID = Query(..., description="Client ID of the agent")
) -> dict:
    """
    Delete an agent
    
    This endpoint deletes an agent configuration from the client's database.
    """
    try:
        success = await agent_service.delete_agent(client_id, agent_slug)
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{agent_slug}' not found for client {client_id}"
            )
        
        logger.info(f"Deleted agent '{agent_slug}' for client {client_id}")
        return {"success": True, "message": f"Agent '{agent_slug}' deleted successfully"}
    except HTTPException:
        raise
    except ClientConfigurationError as e:
        logger.error(f"Client configuration error: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting agent: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete agent")


@router.post("/agents/sync")
async def sync_agents_from_client(
    client_id: UUID = Query(..., description="Client ID to sync agents from")
) -> dict:
    """
    Sync agents from a client's Supabase database
    
    This endpoint pulls agent configurations from the client's own database.
    """
    try:
        agents = await agent_service.get_agents(client_id)
        return {
            "success": True,
            "message": f"Synced {len(agents)} agents from client {client_id}",
            "agent_count": len(agents),
            "agents": [{"slug": a.slug, "name": a.name} for a in agents]
        }
    except ClientConfigurationError as e:
        logger.error(f"Client configuration error: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error syncing agents: {e}")
        raise HTTPException(status_code=500, detail="Failed to sync agents")