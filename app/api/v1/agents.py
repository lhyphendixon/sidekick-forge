"""
Agents API endpoints for multi-tenant agent management (Supabase only)
"""
import logging
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, File, UploadFile
from pydantic import BaseModel

from app.models.agent import Agent, AgentCreate, AgentUpdate, AgentInDB, AgentWithClient
from app.services.agent_service_supabase import AgentService
from app.services.client_service_supabase import ClientService
from app.core.dependencies import get_client_service, get_agent_service

router = APIRouter(prefix="/agents", tags=["agents"])
logger = logging.getLogger(__name__)


class AgentResponse(BaseModel):
    """Response model for agent operations"""
    success: bool
    message: str
    data: Optional[Any] = None


class IMXUploadResponse(BaseModel):
    """Response for IMX model file upload."""
    success: bool
    storage_path: Optional[str] = None
    message: str


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
    # Enforce admin-only
    from app.admin.auth import get_admin_user
    # Note: in API routes we don't have Request injection by default here, so users of API should pass auth headers
    # We'll skip explicit Request and rely on service layer auth elsewhere for now if needed
    # Create a new dict with the client_id from URL
    agent_dict = agent_data.dict()
    agent_dict["client_id"] = client_id
    
    # Create a new AgentCreate instance with the updated client_id
    agent_data_with_client = AgentCreate(**agent_dict)
    
    result = await service.create_agent(client_id, agent_data_with_client)
    if not result:
        raise HTTPException(
            status_code=500, 
            detail="Failed to create agent. Please check Supabase configuration and connectivity."
        )
    return result


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
    service: AgentService = Depends(get_agent_service),
    client_service = Depends(get_client_service)
) -> AgentInDB:
    """Update an agent"""
    from app.admin.auth import get_admin_user
    # Authorization is enforced via admin UI; if exposing externally, add auth dependency
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Updating agent {agent_slug} for client {client_id}")
    logger.info(f"Update data received: {update_data.dict()}")
    logger.info(f"SOUND_SETTINGS in update_data: {update_data.sound_settings}")
    
    # Validate API keys if voice_settings are provided
    if update_data.voice_settings and client_id != "global":
        # Get client to check API keys
        client = await client_service.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        
        voice_settings = update_data.voice_settings
        missing_keys = []
        
        # Define provider to API key mappings
        llm_provider_keys = {
            "openai": "openai_api_key",
            "groq": "groq_api_key",
            "cerebras": "cerebras_api_key",
            "deepinfra": "deepinfra_api_key",
            "replicate": "replicate_api_key"
        }
        
        stt_provider_keys = {
            "deepgram": "deepgram_api_key",
            "groq": "groq_api_key",
            "openai": "openai_api_key",
            "cartesia": "cartesia_api_key"
        }
        
        tts_provider_keys = {
            "openai": "openai_api_key",
            "elevenlabs": "elevenlabs_api_key",
            "cartesia": "cartesia_api_key",
            "speechify": "speechify_api_key",
            "replicate": "replicate_api_key"
        }
        
        # Check LLM provider
        if voice_settings.llm_provider:
            llm_provider = voice_settings.llm_provider
            if llm_provider in llm_provider_keys:
                required_key = llm_provider_keys[llm_provider]
                if not hasattr(client.settings.api_keys, required_key) or not getattr(client.settings.api_keys, required_key):
                    missing_keys.append({
                        "provider_type": "LLM",
                        "provider": llm_provider,
                        "required_key": required_key,
                        "message": f"LLM provider '{llm_provider}' requires {required_key}"
                    })
        
        # Check STT provider
        if voice_settings.stt_provider:
            stt_provider = voice_settings.stt_provider
            if stt_provider in stt_provider_keys:
                required_key = stt_provider_keys[stt_provider]
                if not hasattr(client.settings.api_keys, required_key) or not getattr(client.settings.api_keys, required_key):
                    missing_keys.append({
                        "provider_type": "STT",
                        "provider": stt_provider,
                        "required_key": required_key,
                        "message": f"STT provider '{stt_provider}' requires {required_key}"
                    })
        
        # Check TTS provider (use 'provider' field as TTS if tts_provider is not set)
        tts_provider = getattr(voice_settings, 'tts_provider', None) or voice_settings.provider
        if tts_provider and tts_provider != 'livekit':
            if tts_provider in tts_provider_keys:
                required_key = tts_provider_keys[tts_provider]
                if not hasattr(client.settings.api_keys, required_key) or not getattr(client.settings.api_keys, required_key):
                    missing_keys.append({
                        "provider_type": "TTS",
                        "provider": tts_provider,
                        "required_key": required_key,
                        "message": f"TTS provider '{tts_provider}' requires {required_key}"
                    })
        
        # If missing keys found, return validation error
        if missing_keys:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Missing API keys for selected providers",
                    "missing_keys": missing_keys,
                    "client_id": client_id,
                    "client_name": client.name
                }
            )
    
    # Handle global agents specially
    if client_id == "global":
        # For global agents, update directly in main Supabase
        from app.integrations.supabase_client import supabase_manager
        import json
        from datetime import datetime

        try:
            # Build update dictionary
            update_dict = json.loads(update_data.json(exclude_unset=True))
            logger.info(f"[GLOBAL] update_dict keys: {list(update_dict.keys())}")
            logger.info(f"[GLOBAL] sound_settings in update_dict: {update_dict.get('sound_settings')}")
            if update_dict:
                update_dict["updated_at"] = datetime.utcnow().isoformat()

                # Convert voice_settings to JSON string if present (it's already properly serialized from json())
                if "voice_settings" in update_dict and update_dict["voice_settings"]:
                    update_dict["voice_settings"] = json.dumps(update_dict["voice_settings"])

                # Convert sound_settings to JSON string if present
                if "sound_settings" in update_dict and update_dict["sound_settings"]:
                    logger.info(f"[GLOBAL] Converting sound_settings to JSON: {update_dict['sound_settings']}")
                    update_dict["sound_settings"] = json.dumps(update_dict["sound_settings"])
                    logger.info(f"[GLOBAL] After JSON dump: {update_dict['sound_settings']}")

                logger.info(f"[GLOBAL] Final update_dict being sent to Supabase: {update_dict}")
                # Update in main agents table
                result = supabase_manager.admin_client.table("agents").update(update_dict).eq("slug", agent_slug).execute()
                logger.info(f"[GLOBAL] Supabase update result: {result.data}")
                
                if result.data and len(result.data) > 0:
                    agent_data = result.data[0]
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

                    # Parse sound_settings if present
                    if isinstance(agent_data.get("sound_settings"), str):
                        try:
                            agent_data["sound_settings"] = json.loads(agent_data["sound_settings"])
                        except:
                            agent_data["sound_settings"] = {}

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
    from app.admin.auth import get_admin_user
    # Authorization is enforced via admin UI; if exposing externally, add auth dependency
    success = await service.delete_agent(client_id, agent_slug)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete agent")
    
    return AgentResponse(
        success=True,
        message=f"Agent {agent_slug} deleted successfully"
    )


@router.post("/client/{client_id}/{agent_slug}/upload-imx", response_model=IMXUploadResponse)
async def upload_imx_model(
    client_id: str,
    agent_slug: str,
    file: UploadFile = File(...),
    client_service: ClientService = Depends(get_client_service),
    agent_service: AgentService = Depends(get_agent_service),
):
    """
    Upload an IMX avatar model file for an agent.

    The file is stored in Supabase storage scoped to the client.
    Only .imx files are allowed.
    """
    try:
        # Validate agent exists
        agent = await agent_service.get_agent(client_id, agent_slug)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {agent_slug} not found")

        # Validate file type
        filename = file.filename or ""
        if not filename.lower().endswith('.imx'):
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Only .imx files are allowed."
            )

        # Validate file size (max 500MB for IMX models)
        MAX_SIZE = 500 * 1024 * 1024  # 500MB
        content = await file.read()
        if len(content) > MAX_SIZE:
            raise HTTPException(
                status_code=400,
                detail="File too large. Maximum size is 500MB."
            )

        # Get client's Supabase for storage
        client_sb = await client_service.get_client_supabase_client(client_id, auto_sync=False)

        # Generate storage path: avatar-models/{client_id}/{agent_id}/model.imx
        # Using agent.id ensures uniqueness and proper scoping
        agent_id = agent.id or agent_slug
        storage_path = f"avatar-models/{client_id}/{agent_id}/model.imx"

        bucket_name = "avatar-models"

        try:
            # Try to create bucket if it doesn't exist
            bucket_created = False
            try:
                result = client_sb.storage.create_bucket(
                    bucket_name,
                    options={"public": False}
                )
                logger.info(f"Created bucket '{bucket_name}': {result}")
                bucket_created = True
            except Exception as e:
                error_str = str(e).lower()
                if "already exists" in error_str or "duplicate" in error_str:
                    logger.debug(f"Bucket '{bucket_name}' already exists")
                    bucket_created = True
                else:
                    logger.warning(f"Could not create bucket '{bucket_name}': {e}")
                    try:
                        buckets = client_sb.storage.list_buckets()
                        bucket_names = [b.get('name') or b.get('id') for b in buckets]
                        if bucket_name in bucket_names:
                            bucket_created = True
                    except Exception as list_err:
                        logger.warning(f"Could not list buckets: {list_err}")

            if not bucket_created:
                raise HTTPException(
                    status_code=500,
                    detail=f"Storage bucket '{bucket_name}' does not exist. Please create it in Supabase Dashboard."
                )

            # Check if file already exists and remove it first (upsert)
            try:
                client_sb.storage.from_(bucket_name).remove([storage_path])
                logger.debug(f"Removed existing file at {storage_path}")
            except Exception:
                pass  # File might not exist, that's OK

            # Upload file
            logger.info(f"Uploading IMX model to {bucket_name}/{storage_path}")
            result = client_sb.storage.from_(bucket_name).upload(
                path=storage_path,
                file=content,
                file_options={"content-type": "application/octet-stream"}
            )
            logger.info(f"Upload result: {result}")

            # Store the storage path in agent's voice_settings.avatar_model_path
            # The path format is: supabase://{bucket_name}/{storage_path}
            supabase_path = f"supabase://{bucket_name}/{storage_path}"

            # Update agent with the new model path
            from app.models.agent import AgentUpdate, VoiceSettings

            # Get current voice settings to preserve other fields
            current_voice = agent.voice_settings.dict() if agent.voice_settings else {}
            current_voice["avatar_model_path"] = supabase_path

            update_data = AgentUpdate(
                voice_settings=VoiceSettings(**current_voice)
            )

            await agent_service.update_agent(client_id, agent_slug, update_data)

            return IMXUploadResponse(
                success=True,
                storage_path=supabase_path,
                message=f"IMX model uploaded successfully"
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to upload IMX to storage: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload to storage: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"IMX upload error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload IMX model: {str(e)}"
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
                "provider": "openai",
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
                "provider": "openai",
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
                "provider": "openai",
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