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
        # Fast duplicate guard to return a friendly error before insert
        existing = await agent_service.get_agent(client_id, agent.slug)
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Agent slug '{agent.slug}' already exists for this client"
            )

        created_agent = await agent_service.create_agent(client_id, agent)
        if not created_agent:
            raise HTTPException(status_code=400, detail="Failed to create agent")
        
        logger.info(f"Created agent '{agent.slug}' for client {client_id}")
        return created_agent
    except ClientConfigurationError as e:
        logger.error(f"Client configuration error: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # Map duplicate key errors to a user-friendly message
        message = str(e)
        if "duplicate key value" in message or "already exists" in message:
            raise HTTPException(
                status_code=400,
                detail=f"Agent slug '{agent.slug}' already exists for this client"
            )
        logger.error(f"Error creating agent: {e}")
        raise HTTPException(status_code=500, detail="Failed to create agent")


@router.put("/agents/{agent_slug}", response_model=Agent)
async def update_agent(
    agent_slug: str,
    agent_update: AgentUpdate,
    client_id: str = Query(..., description="Client ID of the agent (UUID or 'global')")
) -> Agent:
    """
    Update an existing agent

    This endpoint updates the configuration of an existing agent.
    """
    import json
    from datetime import datetime

    try:
        if agent_update.voice_settings:
            logger.info(f"AVATAR API DEBUG - Received voice_settings: avatar_image_url={agent_update.voice_settings.avatar_image_url}, avatar_model_type={agent_update.voice_settings.avatar_model_type}")

        # Log sound_settings for debugging
        if agent_update.sound_settings:
            logger.info(f"SOUND API DEBUG - Received sound_settings: {agent_update.sound_settings}")

        # Handle global agents specially
        if client_id == "global":
            from app.integrations.supabase_client import supabase_manager

            # Build update dictionary
            update_dict = json.loads(agent_update.json(exclude_unset=True))
            logger.info(f"[GLOBAL] update_dict keys: {list(update_dict.keys())}")
            logger.info(f"[GLOBAL] sound_settings in update_dict: {update_dict.get('sound_settings')}")

            if update_dict:
                update_dict["updated_at"] = datetime.utcnow().isoformat()

                # Convert voice_settings to JSON string if present
                if "voice_settings" in update_dict and update_dict["voice_settings"]:
                    update_dict["voice_settings"] = json.dumps(update_dict["voice_settings"])

                # Convert sound_settings to JSON string if present
                if "sound_settings" in update_dict and update_dict["sound_settings"]:
                    logger.info(f"[GLOBAL] Converting sound_settings to JSON: {update_dict['sound_settings']}")
                    update_dict["sound_settings"] = json.dumps(update_dict["sound_settings"])

                logger.info(f"[GLOBAL] Final update_dict being sent to Supabase: {list(update_dict.keys())}")

                # Update in main agents table
                result = supabase_manager.admin_client.table("agents").update(update_dict).eq("slug", agent_slug).execute()
                logger.info(f"[GLOBAL] Supabase update result count: {len(result.data) if result.data else 0}")

                if result.data and len(result.data) > 0:
                    agent_data = result.data[0]
                    # Parse JSON fields back
                    if isinstance(agent_data.get("voice_settings"), str):
                        try:
                            agent_data["voice_settings"] = json.loads(agent_data["voice_settings"])
                        except:
                            agent_data["voice_settings"] = {}

                    if isinstance(agent_data.get("sound_settings"), str):
                        try:
                            agent_data["sound_settings"] = json.loads(agent_data["sound_settings"])
                        except:
                            agent_data["sound_settings"] = {}

                    if isinstance(agent_data.get("webhooks"), str):
                        try:
                            agent_data["webhooks"] = json.loads(agent_data["webhooks"])
                        except:
                            agent_data["webhooks"] = {}

                    # Add client_id for global agents
                    agent_data["client_id"] = "global"

                    logger.info(f"[GLOBAL] Returning updated agent with sound_settings: {agent_data.get('sound_settings')}")
                    return Agent(**agent_data)
                else:
                    raise HTTPException(status_code=404, detail=f"Agent {agent_slug} not found")
            else:
                raise HTTPException(status_code=400, detail="No update data provided")
        else:
            # Regular client-specific update
            client_uuid = UUID(client_id)
            updated_agent = await agent_service.update_agent(client_uuid, agent_slug, agent_update)
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
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to update agent: {str(e)}")


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
