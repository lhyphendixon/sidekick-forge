"""
Agents API endpoints for multi-tenant agent management (Supabase only)
"""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.models.agent import Agent, AgentCreate, AgentUpdate, AgentInDB, AgentWithClient
from app.services.agent_service_supabase import AgentService
from app.services.client_service_supabase import ClientService
from app.core.dependencies import get_client_service, get_agent_service

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentResponse(BaseModel):
    """Response model for agent operations"""
    success: bool
    message: str
    data: Optional[Any] = None


@router.get("/", response_model=List[Dict[str, Any]])
async def get_all_agents(
    service: AgentService = Depends(get_agent_service)
) -> List[Dict[str, Any]]:
    """Get all agents across all clients with client info"""
    return await service.get_all_agents_with_clients()


@router.get("/client/{client_id}", response_model=List[AgentInDB])
async def get_client_agents(
    client_id: str,
    service: AgentService = Depends(get_agent_service)
) -> List[AgentInDB]:
    """Get all agents for a specific client"""
    return await service.get_client_agents(client_id)


@router.post("/client/{client_id}", response_model=AgentInDB)
async def create_agent(
    client_id: str,
    agent_data: AgentCreate,
    service: AgentService = Depends(get_agent_service)
) -> AgentInDB:
    """Create a new agent for a client"""
    # Ensure client_id matches
    agent_data.client_id = client_id
    return await service.create_agent(client_id, agent_data)


@router.get("/client/{client_id}/{agent_slug}", response_model=AgentInDB)
async def get_agent(
    client_id: str,
    agent_slug: str,
    service: AgentService = Depends(get_agent_service)
) -> AgentInDB:
    """Get a specific agent by slug"""
    agent = await service.get_agent(client_id, agent_slug)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_slug} not found")
    return agent


@router.put("/client/{client_id}/{agent_slug}", response_model=AgentInDB)
async def update_agent(
    client_id: str,
    agent_slug: str,
    update_data: AgentUpdate,
    service: AgentService = Depends(get_agent_service)
) -> AgentInDB:
    """Update an agent"""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Updating agent {agent_slug} for client {client_id}")
    logger.info(f"Update data received: {update_data.dict()}")
    
    # Handle global agents specially
    if client_id == "global":
        # For global agents, update directly in main Supabase
        from app.integrations.supabase_client import supabase_manager
        import json
        from datetime import datetime
        
        try:
            # Ensure supabase_manager is initialized
            if not supabase_manager._initialized:
                await supabase_manager.initialize()
            # Build update dictionary
            update_dict = json.loads(update_data.json(exclude_unset=True))
            if update_dict:
                update_dict["updated_at"] = datetime.utcnow().isoformat()
                
                # Convert voice_settings to JSON string if present (it's already properly serialized from json())
                if "voice_settings" in update_dict and update_dict["voice_settings"]:
                    update_dict["voice_settings"] = json.dumps(update_dict["voice_settings"])
                
                # Store the original voice_settings before converting to JSON
                voice_settings_dict = update_dict.get("voice_settings", {})
                if isinstance(voice_settings_dict, str):
                    try:
                        voice_settings_dict = json.loads(voice_settings_dict)
                    except:
                        voice_settings_dict = {}
                
                # Update in main agents table
                result = supabase_manager.admin_client.table("agents").update(update_dict).eq("slug", agent_slug).execute()
                
                if result.data and len(result.data) > 0:
                    agent_data = result.data[0]
                    
                    # Update agent_configurations table for global agents
                    await _update_global_agent_configuration(supabase_manager.admin_client, agent_slug, update_data, voice_settings_dict)
                    
                    # Parse JSON fields back
                    if isinstance(agent_data.get("voice_settings"), str):
                        try:
                            agent_data["voice_settings"] = json.loads(agent_data["voice_settings"])
                        except:
                            agent_data["voice_settings"] = {}
                    
                    # Parse webhooks if present
                    if isinstance(agent_data.get("webhooks"), str):
                        try:
                            agent_data["webhooks"] = json.loads(agent_data["webhooks"])
                        except:
                            agent_data["webhooks"] = {}
                    
                    # Add client_id for global agents
                    agent_data["client_id"] = "global"
                    
                    from app.models.agent import Agent
                    return Agent(**agent_data)
                else:
                    raise HTTPException(status_code=404, detail=f"Agent {agent_slug} not found")
        except Exception as e:
            import traceback
            logger.error(f"Failed to update global agent {agent_slug}: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            logger.error(f"Update data: {update_dict}")
            raise HTTPException(status_code=500, detail=f"Failed to update global agent: {str(e)}")
    else:
        # Regular client-specific update
        return await service.update_agent(client_id, agent_slug, update_data)


@router.delete("/client/{client_id}/{agent_slug}")
async def delete_agent(
    client_id: str,
    agent_slug: str,
    service: AgentService = Depends(get_agent_service)
) -> AgentResponse:
    """Delete an agent"""
    success = await service.delete_agent(client_id, agent_slug)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete agent")
    
    return AgentResponse(
        success=True,
        message=f"Agent {agent_slug} deleted successfully"
    )


@router.post("/client/{client_id}/sync")
async def sync_agents(
    client_id: str,
    service: AgentService = Depends(get_agent_service)
) -> AgentResponse:
    """Force sync agents from client's Supabase"""
    count = await service.sync_agents_from_supabase(client_id)
    
    return AgentResponse(
        success=True,
        message=f"Synced {count} agents from Supabase",
        data={"count": count}
    )


# Demo endpoints for testing without real Supabase
@router.post("/demo/create-defaults/{client_id}")
async def create_default_agents(
    client_id: str,
    service: AgentService = Depends(get_agent_service)
) -> AgentResponse:
    """Create default demo agents for testing"""
    demo_agents = [
        AgentCreate(
            slug="support-agent",
            name="Customer Support Agent",
            description="Helps with customer inquiries and support tickets",
            client_id=client_id,
            system_prompt="You are a helpful customer support agent. Be professional, empathetic, and solution-oriented.",
            voice_settings={
                "provider": "livekit",
                "voice_id": "alloy",
                "temperature": 0.7
            },
            enabled=True
        ),
        AgentCreate(
            slug="sales-assistant",
            name="Sales Assistant",
            description="Assists with sales inquiries and product information",
            client_id=client_id,
            system_prompt="You are a knowledgeable sales assistant. Help customers find the right products and answer their questions.",
            voice_settings={
                "provider": "livekit",
                "voice_id": "echo",
                "temperature": 0.8
            },
            enabled=True
        ),
        AgentCreate(
            slug="tech-helper",
            name="Technical Helper",
            description="Provides technical support and troubleshooting",
            client_id=client_id,
            system_prompt="You are a technical support specialist. Help users solve technical problems step by step.",
            voice_settings={
                "provider": "livekit",
                "voice_id": "fable",
                "temperature": 0.5
            },
            enabled=True
        )
    ]
    
    created_count = 0
    for agent_data in demo_agents:
        try:
            await service.create_agent(client_id, agent_data)
            created_count += 1
        except HTTPException as e:
            if "already exists" not in str(e.detail):
                raise
    
    return AgentResponse(
        success=True,
        message=f"Created {created_count} default agents",
        data={"created": created_count}
    )


async def _update_global_agent_configuration(supabase_client, agent_slug: str, update_data: AgentUpdate, voice_settings_dict: Dict[str, Any]) -> None:
    """Update the agent_configurations table for global agents"""
    import json
    from datetime import datetime
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        # First, check if a configuration exists for this agent
        result = supabase_client.table("agent_configurations").select("*").eq("agent_slug", agent_slug).execute()
        
        if result.data and len(result.data) > 0:
            # Configuration exists, update it
            config_data = result.data[0]
            config_id = config_data.get("id")
            
            # Build the update payload for agent_configurations
            config_update = {
                "last_updated": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            
            # Update basic fields if they're provided
            if hasattr(update_data, "name") and update_data.name:
                config_update["agent_name"] = update_data.name
            
            if hasattr(update_data, "system_prompt") and update_data.system_prompt is not None:
                config_update["system_prompt"] = update_data.system_prompt
            
            if hasattr(update_data, "agent_image") and update_data.agent_image is not None:
                config_update["agent_image"] = update_data.agent_image
            
            # Update voice settings
            if voice_settings_dict:
                # Update the voice_settings JSON field
                config_update["voice_settings"] = json.dumps(voice_settings_dict)
                
                # Extract specific voice settings
                voice_id = voice_settings_dict.get("voice_id")
                if voice_id:
                    config_update["voice_id"] = voice_id
                
                temperature = voice_settings_dict.get("temperature")
                if temperature is not None:
                    config_update["temperature"] = float(temperature)
                
                # Build provider_config based on voice settings
                provider_config = config_data.get("provider_config", {})
                if isinstance(provider_config, str):
                    provider_config = json.loads(provider_config)
                
                # Update LLM settings
                if voice_settings_dict.get("llm_provider"):
                    if "llm" not in provider_config:
                        provider_config["llm"] = {}
                    provider_config["llm"]["provider"] = voice_settings_dict["llm_provider"]
                    if voice_settings_dict.get("llm_model"):
                        provider_config["llm"]["model"] = voice_settings_dict["llm_model"]
                    if temperature is not None:
                        provider_config["llm"]["temperature"] = float(temperature)
                
                # Update STT settings
                if voice_settings_dict.get("stt_provider"):
                    if "stt" not in provider_config:
                        provider_config["stt"] = {}
                    provider_config["stt"]["provider"] = voice_settings_dict["stt_provider"]
                    # Map provider to model if not specified
                    if voice_settings_dict["stt_provider"] == "deepgram":
                        provider_config["stt"]["model"] = "nova-2"
                    elif voice_settings_dict["stt_provider"] == "groq":
                        provider_config["stt"]["model"] = "whisper-large-v3"
                
                # Update TTS settings
                tts_provider = voice_settings_dict.get("provider") or voice_settings_dict.get("tts_provider")
                if tts_provider:
                    if "tts" not in provider_config:
                        provider_config["tts"] = {}
                    provider_config["tts"]["provider"] = tts_provider
                    
                    # Add provider-specific settings
                    if tts_provider == "openai":
                        if voice_id:
                            provider_config["tts"]["voice"] = voice_id
                        if voice_settings_dict.get("model"):
                            provider_config["tts"]["model"] = voice_settings_dict["model"]
                    elif tts_provider == "elevenlabs":
                        if voice_id:
                            provider_config["tts"]["voice_id"] = voice_id
                        if voice_settings_dict.get("model"):
                            provider_config["tts"]["model"] = voice_settings_dict["model"]
                        if voice_settings_dict.get("stability") is not None:
                            provider_config["tts"]["stability"] = float(voice_settings_dict["stability"])
                        if voice_settings_dict.get("similarity_boost") is not None:
                            provider_config["tts"]["similarity_boost"] = float(voice_settings_dict["similarity_boost"])
                    elif tts_provider == "cartesia":
                        if voice_id:
                            provider_config["tts"]["voice_id"] = voice_id
                        if voice_settings_dict.get("model"):
                            provider_config["tts"]["model"] = voice_settings_dict["model"]
                        if voice_settings_dict.get("output_format"):
                            provider_config["tts"]["output_format"] = voice_settings_dict["output_format"]
                    elif tts_provider == "speechify":
                        if voice_id:
                            provider_config["tts"]["speechify_voice_id"] = voice_id
                        if voice_settings_dict.get("model"):
                            provider_config["tts"]["speechify_model"] = voice_settings_dict["model"]
                        if voice_settings_dict.get("loudness_normalization") is not None:
                            provider_config["tts"]["speechify_loudness_normalization"] = voice_settings_dict["loudness_normalization"]
                        if voice_settings_dict.get("text_normalization") is not None:
                            provider_config["tts"]["speechify_text_normalization"] = voice_settings_dict["text_normalization"]
                
                # Update the provider_config field
                config_update["provider_config"] = json.dumps(provider_config)
            
            # Update the configuration
            result = supabase_client.table("agent_configurations").update(config_update).eq("id", config_id).execute()
            
            if result.data:
                logger.info(f"Successfully updated agent_configurations for global agent {agent_slug}")
            else:
                logger.warning(f"No data returned when updating agent_configurations for global agent {agent_slug}")
        else:
            logger.warning(f"No agent_configurations entry found for global agent {agent_slug} - skipping configuration update")
            
    except Exception as e:
        logger.error(f"Error updating agent_configurations for global agent {agent_slug}: {e}")
        # Don't raise the exception - we don't want to fail the whole update if just the configuration update fails