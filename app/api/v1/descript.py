"""
Descript Connect API Endpoints

Provides REST API endpoints for video upload, Descript editing pipeline,
and job status monitoring.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, BackgroundTasks
from pydantic import BaseModel, Field

import httpx

from app.core.dependencies import get_client_service
from app.services.client_service_supabase import ClientService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/descript", tags=["descript"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class VideoUploadResponse(BaseModel):
    success: bool
    file_url: Optional[str] = None
    file_path: Optional[str] = None
    message: str


class DescriptEditRequest(BaseModel):
    """Request to start a Descript editing run."""
    video_url: str = Field(..., description="Signed URL of the uploaded video")
    filename: str = Field(..., description="Original filename of the video")
    project_name: Optional[str] = Field(None, description="Name for the Descript project")
    # Preset options
    remove_filler_words: bool = Field(default=False)
    remove_silences: bool = Field(default=False)
    studio_sound: bool = Field(default=False)
    generate_captions: bool = Field(default=False)
    # Clips settings
    create_clips: bool = Field(default=False)
    clip_count: int = Field(default=1, ge=1, le=5)
    clip_length_seconds: int = Field(default=30, ge=5, le=300)
    clip_resolution: str = Field(default="1080p")
    # Custom
    custom_instructions: Optional[str] = Field(None, description="Free-form editing instructions")


class DescriptEditResponse(BaseModel):
    success: bool
    run_id: str
    message: str
    import_job_id: Optional[str] = None
    project_id: Optional[str] = None
    project_url: Optional[str] = None


class DescriptStatusResponse(BaseModel):
    run_id: str
    status: str  # "importing", "editing", "complete", "error"
    phase: str
    import_job_id: Optional[str] = None
    edit_job_id: Optional[str] = None
    project_id: Optional[str] = None
    project_url: Optional[str] = None
    agent_response: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# In-memory run store (keyed by run_id)
# ---------------------------------------------------------------------------
_runs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Background pipeline
# ---------------------------------------------------------------------------

async def _run_descript_pipeline(
    run_id: str,
    api_key: str,
    video_url: str,
    filename: str,
    project_name: str,
    edit_options: dict,
):
    """Background task: import → poll → edit → poll → complete."""
    from app.services.descript_service import DescriptService, DescriptAPIError

    run = _runs[run_id]
    svc = DescriptService(api_key)

    try:
        # --- Phase 1: Import ---
        run["status"] = "importing"
        run["phase"] = "Importing video into Descript"
        import_result = await svc.import_media(
            media_url=video_url,
            filename=filename,
            project_name=project_name,
        )
        run["import_job_id"] = import_result.get("job_id")
        run["project_id"] = import_result.get("project_id")
        run["project_url"] = import_result.get("project_url")

        # Wait for import to finish
        run["phase"] = "Waiting for import to complete"
        import_job = await svc.poll_job_completion(
            import_result["job_id"],
            timeout_seconds=600,
            poll_interval=5.0,
        )

        result_status = (import_job.get("result") or {}).get("status", "")
        if result_status == "failed":
            raise DescriptAPIError(500, "Media import failed in Descript")

        # --- Phase 2: Agent Edit ---
        from app.services.descript_service import DescriptService as _DS
        prompt = _DS.build_edit_prompt(edit_options)

        run["status"] = "editing"
        run["phase"] = "Applying AI edits"
        edit_result = await svc.agent_edit(
            project_id=import_result["project_id"],
            prompt=prompt,
        )
        run["edit_job_id"] = edit_result.get("job_id")

        # Wait for edits to finish
        run["phase"] = "Waiting for edits to complete"
        edit_job = await svc.poll_job_completion(
            edit_result["job_id"],
            timeout_seconds=600,
            poll_interval=5.0,
        )

        edit_result_status = (edit_job.get("result") or {}).get("status", "")
        if edit_result_status == "failed":
            raise DescriptAPIError(500, "Agent editing failed in Descript")

        # --- Complete ---
        run["status"] = "complete"
        run["phase"] = "Complete"
        run["agent_response"] = (edit_job.get("result") or {}).get("agent_response", "Edits applied successfully")
        logger.info(f"[descript] Run {run_id} completed successfully")

    except DescriptAPIError as e:
        run["status"] = "error"
        run["phase"] = "Error"
        run["error"] = e.detail
        logger.error(f"[descript] Run {run_id} failed: {e}")
    except Exception as e:
        run["status"] = "error"
        run["phase"] = "Error"
        run["error"] = str(e)
        logger.error(f"[descript] Run {run_id} failed unexpectedly: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/upload-video", response_model=VideoUploadResponse)
async def upload_video(
    file: UploadFile = File(...),
    client_id: str = Query(...),
    client_service: ClientService = Depends(get_client_service),
):
    """Upload a video file to Supabase storage for Descript processing."""
    try:
        # Validate file type
        allowed_types = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/webm"}
        if not file.content_type or file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type '{file.content_type}'. Accepted: MP4, MOV, AVI, WebM.",
            )

        # Read and validate size (max 500MB)
        MAX_SIZE = 500 * 1024 * 1024
        content = await file.read()
        file_size_mb = len(content) / (1024 * 1024)
        logger.info(f"[descript] Uploading video: {file.filename}, size: {file_size_mb:.1f}MB")
        if len(content) > MAX_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File too large ({file_size_mb:.1f}MB). Maximum is 500MB.",
            )

        # Get client's Supabase
        client_sb = await client_service.get_client_supabase_client(client_id, auto_sync=False)

        # Generate storage path
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        file_ext = file.filename.rsplit(".", 1)[-1] if file.filename and "." in file.filename else "mp4"
        storage_path = f"descript/{client_id}/{timestamp}_{uuid.uuid4().hex[:8]}.{file_ext}"

        bucket_name = "video-uploads"

        # Ensure bucket exists
        bucket_ready = False
        try:
            client_sb.storage.create_bucket(bucket_name, options={"public": False})
            bucket_ready = True
        except Exception as e:
            error_str = str(e).lower()
            if "already exists" in error_str or "duplicate" in error_str:
                bucket_ready = True
            else:
                try:
                    buckets = client_sb.storage.list_buckets()
                    bucket_names = [b.get("name") or b.get("id") for b in buckets]
                    if bucket_name in bucket_names:
                        bucket_ready = True
                except Exception:
                    pass

        if not bucket_ready:
            raise HTTPException(
                status_code=500,
                detail=f"Storage bucket '{bucket_name}' does not exist. Please create it in Supabase Dashboard -> Storage -> New Bucket.",
            )

        # Upload — use TUS resumable for files > 6MB
        if len(content) > 6 * 1024 * 1024:
            import base64

            supabase_url = client_sb.supabase_url
            supabase_key = client_sb.supabase_key
            tus_endpoint = f"{supabase_url}/storage/v1/upload/resumable"

            bucket_b64 = base64.b64encode(bucket_name.encode()).decode()
            path_b64 = base64.b64encode(storage_path.encode()).decode()
            ctype_b64 = base64.b64encode((file.content_type or "video/mp4").encode()).decode()

            headers = {
                "Authorization": f"Bearer {supabase_key}",
                "apikey": supabase_key,
                "x-upsert": "true",
                "upload-length": str(len(content)),
                "upload-metadata": f"bucketName {bucket_b64},objectName {path_b64},contentType {ctype_b64}",
                "tus-resumable": "1.0.0",
            }

            async with httpx.AsyncClient(timeout=300.0) as http_client:
                create_resp = await http_client.post(tus_endpoint, headers=headers)
                if create_resp.status_code not in (200, 201):
                    raise Exception(f"TUS create failed: {create_resp.text}")

                upload_url = create_resp.headers.get("location")
                if not upload_url:
                    raise Exception("No upload URL returned from TUS endpoint")

                patch_headers = {
                    "Authorization": f"Bearer {supabase_key}",
                    "apikey": supabase_key,
                    "tus-resumable": "1.0.0",
                    "upload-offset": "0",
                    "content-type": "application/offset+octet-stream",
                }
                patch_resp = await http_client.patch(upload_url, headers=patch_headers, content=content)
                if patch_resp.status_code not in (200, 204):
                    raise Exception(f"TUS upload failed: {patch_resp.text}")
        else:
            client_sb.storage.from_(bucket_name).upload(
                path=storage_path,
                file=content,
                file_options={"content-type": file.content_type or "video/mp4"},
            )

        # Signed URL — Descript requires URLs valid for 12-48h; use 24h
        signed_url = client_sb.storage.from_(bucket_name).create_signed_url(
            path=storage_path,
            expires_in=86400,  # 24 hours
        )

        if signed_url and signed_url.get("signedURL"):
            return VideoUploadResponse(
                success=True,
                file_url=signed_url["signedURL"],
                file_path=storage_path,
                message="Video uploaded successfully",
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to generate signed URL")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[descript] Video upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/edit", response_model=DescriptEditResponse)
async def start_descript_edit(
    request: DescriptEditRequest,
    client_id: str = Query(...),
    background_tasks: BackgroundTasks = None,
    client_service: ClientService = Depends(get_client_service),
):
    """Start a Descript editing pipeline (import → edit → complete)."""
    try:
        # Get the client's Descript API key
        platform_sb = client_service.supabase
        result = platform_sb.table("clients").select(
            "descript_api_key"
        ).eq("id", client_id).maybe_single().execute()

        api_key = (result.data or {}).get("descript_api_key") if result.data else None
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="Descript API key not configured. Add your Descript API key in Settings.",
            )

        run_id = uuid.uuid4().hex
        project_name = request.project_name or f"Sidekick Forge Edit {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"

        edit_options = {
            "remove_filler_words": request.remove_filler_words,
            "remove_silences": request.remove_silences,
            "studio_sound": request.studio_sound,
            "generate_captions": request.generate_captions,
            "create_clips": request.create_clips,
            "clip_count": request.clip_count,
            "clip_length_seconds": request.clip_length_seconds,
            "clip_resolution": request.clip_resolution,
            "custom_instructions": request.custom_instructions,
        }

        # Initialize run tracking
        _runs[run_id] = {
            "run_id": run_id,
            "client_id": client_id,
            "status": "queued",
            "phase": "Queued",
            "import_job_id": None,
            "edit_job_id": None,
            "project_id": None,
            "project_url": None,
            "agent_response": None,
            "error": None,
            "created_at": datetime.utcnow().isoformat(),
        }

        # Launch background pipeline
        background_tasks.add_task(
            _run_descript_pipeline,
            run_id=run_id,
            api_key=api_key,
            video_url=request.video_url,
            filename=request.filename,
            project_name=project_name,
            edit_options=edit_options,
        )

        return DescriptEditResponse(
            success=True,
            run_id=run_id,
            message="Descript editing pipeline started",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[descript] Failed to start edit: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{run_id}", response_model=DescriptStatusResponse)
async def get_descript_status(run_id: str):
    """Get the current status of a Descript editing run."""
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    return DescriptStatusResponse(
        run_id=run["run_id"],
        status=run["status"],
        phase=run["phase"],
        import_job_id=run.get("import_job_id"),
        edit_job_id=run.get("edit_job_id"),
        project_id=run.get("project_id"),
        project_url=run.get("project_url"),
        agent_response=run.get("agent_response"),
        error=run.get("error"),
    )


@router.get("/check-key/{client_id}")
async def check_descript_key(
    client_id: str,
    client_service: ClientService = Depends(get_client_service),
):
    """Check if a client has a Descript API key configured."""
    try:
        platform_sb = client_service.supabase
        result = platform_sb.table("clients").select(
            "descript_api_key"
        ).eq("id", client_id).maybe_single().execute()

        has_key = bool((result.data or {}).get("descript_api_key"))
        return {"has_key": has_key}
    except Exception as e:
        logger.error(f"[descript] Failed to check API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))
