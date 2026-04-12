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

logger = logging.getLogger(__name__)

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
            "inworld": "inworld_api_key",
            "fish_audio": "fish_audio_api_key",
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
            if update_dict:
                update_dict["updated_at"] = datetime.utcnow().isoformat()
                
                # Convert voice_settings to JSON string if present (it's already properly serialized from json())
                if "voice_settings" in update_dict and update_dict["voice_settings"]:
                    update_dict["voice_settings"] = json.dumps(update_dict["voice_settings"])
                
                # Update in main agents table
                result = supabase_manager.admin_client.table("agents").update(update_dict).eq("slug", agent_slug).execute()
                
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


@router.post("/client/{client_id}/{agent_slug}/upload-imx")
async def upload_imx_model(
    client_id: str,
    agent_slug: str,
    file: UploadFile = File(...),
    client_service: ClientService = Depends(get_client_service),
):
    """
    Upload a Bithuman .imx model file to the client's Supabase Storage.

    Returns the ``supabase://`` storage path that should be saved in the
    agent's ``voice_settings.avatar_model_path``.
    """
    # --- Validate the upload -------------------------------------------------
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    if not file.filename.lower().endswith(".imx"):
        raise HTTPException(status_code=400, detail="Only .imx files are accepted")

    try:
        contents = await file.read()
    except Exception as exc:
        logger.error("Failed to read uploaded IMX file: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read uploaded file") from exc

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # 500 MB limit (matches the frontend constraint)
    max_size = 500 * 1024 * 1024
    if len(contents) > max_size:
        raise HTTPException(status_code=413, detail="File exceeds 500 MB limit")

    # --- Obtain the client's Supabase connection -----------------------------
    client_sb = await client_service.get_client_supabase_client(client_id, auto_sync=False)
    if not client_sb:
        raise HTTPException(
            status_code=500,
            detail="Could not connect to client Supabase project",
        )

    # --- Upload to Supabase Storage ------------------------------------------
    bucket_name = "avatars"
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    storage_file = f"imx/{client_id}/{agent_slug}_{timestamp}_{unique_id}.imx"

    try:
        # Ensure bucket exists with a 500 MB file size limit for IMX models
        bucket_opts = {"public": False, "file_size_limit": "500MB"}
        try:
            client_sb.storage.create_bucket(bucket_name, options=bucket_opts)
        except Exception:
            # Bucket already exists — update its file size limit
            try:
                client_sb.storage.update_bucket(bucket_name, options=bucket_opts)
            except Exception:
                pass

        # Files > 6 MB must use the TUS resumable upload protocol;
        # the standard Supabase upload has a 50 MB API-gateway limit.
        if len(contents) > 6 * 1024 * 1024:
            import base64
            import httpx

            supabase_url = client_sb.supabase_url
            supabase_key = client_sb.supabase_key
            tus_endpoint = f"{supabase_url}/storage/v1/upload/resumable"

            bucket_b64 = base64.b64encode(bucket_name.encode()).decode()
            path_b64 = base64.b64encode(storage_file.encode()).decode()
            ctype_b64 = base64.b64encode(b"application/octet-stream").decode()

            create_headers = {
                "Authorization": f"Bearer {supabase_key}",
                "apikey": supabase_key,
                "x-upsert": "true",
                "upload-length": str(len(contents)),
                "upload-metadata": f"bucketName {bucket_b64},objectName {path_b64},contentType {ctype_b64}",
                "tus-resumable": "1.0.0",
            }

            async with httpx.AsyncClient(timeout=600.0) as http_client:
                # Step 1: create the resumable upload
                create_resp = await http_client.post(tus_endpoint, headers=create_headers)
                if create_resp.status_code not in (200, 201):
                    raise Exception(f"TUS create failed ({create_resp.status_code}): {create_resp.text}")

                upload_url = create_resp.headers.get("location")
                if not upload_url:
                    raise Exception("No upload URL returned from TUS endpoint")

                # Step 2: send the file content
                patch_headers = {
                    "Authorization": f"Bearer {supabase_key}",
                    "apikey": supabase_key,
                    "tus-resumable": "1.0.0",
                    "upload-offset": "0",
                    "content-type": "application/offset+octet-stream",
                }
                patch_resp = await http_client.patch(upload_url, headers=patch_headers, content=contents)
                if patch_resp.status_code not in (200, 204):
                    raise Exception(f"TUS upload failed ({patch_resp.status_code}): {patch_resp.text}")

            logger.info("IMX uploaded via TUS resumable protocol")
        else:
            # Standard upload for small files (< 6 MB)
            client_sb.storage.from_(bucket_name).upload(
                path=storage_file,
                file=contents,
                file_options={"content-type": "application/octet-stream"},
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Supabase storage upload failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {exc}") from exc

    storage_path = f"supabase://{bucket_name}/{storage_file}"
    size_mb = len(contents) / (1024 * 1024)
    logger.info(
        "Uploaded IMX model for %s/%s: %s (%.1f MB)",
        client_id, agent_slug, storage_path, size_mb,
    )

    return {
        "success": True,
        "storage_path": storage_path,
        "message": f"IMX model uploaded ({size_mb:.1f} MB)",
    }


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