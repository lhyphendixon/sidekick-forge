"""
Image Catalyst API Endpoints

AI image generation with Thumbnail/Promotional and General modes.
Uses Runware API with GPT Image 1.5 and FLUX.2 Dev models.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.core.dependencies import get_client_service
from app.services.client_service_supabase import ClientService

router = APIRouter(prefix="/image-catalyst", tags=["image-catalyst"])
logger = logging.getLogger(__name__)


# ============================================================================
# Request/Response Models
# ============================================================================

class ImageUploadResponse(BaseModel):
    """Response for reference image upload."""
    success: bool
    file_url: Optional[str] = None
    file_path: Optional[str] = None
    message: str


class ImageCatalystStartRequest(BaseModel):
    """Request to generate an image."""
    mode: str = Field(..., description="'thumbnail' or 'general'")
    prompt: str = Field(..., min_length=1, max_length=32000, description="Image description")
    reference_image_url: Optional[str] = Field(None, description="Signed URL of uploaded reference image")
    width: Optional[int] = Field(None, description="Image width in pixels")
    height: Optional[int] = Field(None, description="Image height in pixels")
    quality: Optional[str] = Field(None, description="Quality tier for thumbnail mode: low/medium/high")
    steps: Optional[int] = Field(None, ge=1, le=50, description="Inference steps for general mode")
    cfg_scale: Optional[float] = Field(None, ge=0, le=20, description="CFG scale for general mode")
    strength: Optional[float] = Field(None, ge=0, le=1, description="Img2img strength for general mode")
    seed: Optional[int] = Field(None, description="Seed for reproducibility")


class ImageCatalystStartResponse(BaseModel):
    """Response from image generation."""
    success: bool
    run_id: Optional[str] = None
    status: Optional[str] = None
    message: str
    image_url: Optional[str] = None
    seed: Optional[int] = None
    generation_time_ms: Optional[float] = None
    cost: Optional[float] = None
    error: Optional[str] = None


class ImageCatalystStatusResponse(BaseModel):
    """Response for status check."""
    run_id: str
    status: str
    mode: Optional[str] = None
    prompt: Optional[str] = None
    image_url: Optional[str] = None
    seed: Optional[int] = None
    generation_time_ms: Optional[float] = None
    cost: Optional[float] = None
    error: Optional[str] = None


class CostSummaryResponse(BaseModel):
    """Cost summary for a client."""
    total_cost: float
    total_count: int
    per_agent: List[Dict[str, Any]]


class StoreResultRequest(BaseModel):
    """Request to store Image Catalyst result in conversation."""
    run_id: str = Field(..., description="Image Catalyst run ID")
    conversation_id: str = Field(..., description="Conversation to store result in")


# ============================================================================
# Brand Style Guide Helpers
# ============================================================================

async def _get_brand_style_config(
    client_service: ClientService,
    client_id: str,
    agent_id: str,
) -> dict:
    """Fetch Image Catalyst brand style config from agent's tools_config in client DB."""
    try:
        client_sb = await client_service.get_client_supabase_client(client_id, auto_sync=False)
        if not client_sb:
            return {}
        result = client_sb.table("agents").select("tools_config").eq(
            "id", agent_id
        ).maybe_single().execute()
        if result.data and result.data.get("tools_config"):
            tools_config = result.data["tools_config"]
            if isinstance(tools_config, str):
                import json
                tools_config = json.loads(tools_config)
            return tools_config.get("image-catalyst", {})
        return {}
    except Exception as e:
        logger.warning(f"Failed to fetch brand style config for agent {agent_id}: {e}")
        return {}


def _enrich_prompt_with_brand_style(prompt: str, brand_config: dict) -> str:
    """Append brand style guide context to the user's prompt."""
    if not brand_config:
        return prompt

    brand_parts = []

    brand_colors = brand_config.get("brand_colors", [])
    if brand_colors and isinstance(brand_colors, list):
        colors_str = ", ".join(str(c) for c in brand_colors if c)
        if colors_str:
            brand_parts.append(f"Brand colors: {colors_str}")

    style_desc = brand_config.get("brand_style_description", "")
    if isinstance(style_desc, str) and style_desc.strip():
        brand_parts.append(f"Image style: {style_desc.strip()}")

    if not brand_parts:
        return prompt

    brand_suffix = "\n\n[Brand Style Guide]\n" + "\n".join(brand_parts)
    return prompt + brand_suffix


# ============================================================================
# API Endpoints
# ============================================================================

@router.post("/upload", response_model=ImageUploadResponse)
async def upload_reference_image(
    file: UploadFile = File(...),
    client_id: str = Query(...),
    client_service: ClientService = Depends(get_client_service),
):
    """
    Upload a reference image for Image Catalyst generation.

    Stored in Supabase storage. Returns a signed URL valid for 1 hour.
    Supported formats: PNG, JPG, JPEG, WEBP. Max size: 10MB.
    """
    try:
        # Validate file type
        content_type = file.content_type or ''
        if not content_type.startswith('image/'):
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Supported formats: PNG, JPG, JPEG, WEBP"
            )

        # Validate file size (max 10MB)
        MAX_SIZE = 10 * 1024 * 1024
        content = await file.read()
        if len(content) > MAX_SIZE:
            raise HTTPException(
                status_code=400,
                detail="File too large. Maximum size is 10MB."
            )

        # Get client's Supabase for storage
        client_sb = await client_service.get_client_supabase_client(client_id, auto_sync=False)

        # Generate unique filename
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        file_ext = file.filename.split('.')[-1] if file.filename and '.' in file.filename else 'png'
        storage_path = f"image-catalyst/{client_id}/{timestamp}_{uuid.uuid4().hex[:8]}.{file_ext}"

        # Upload to Supabase storage
        bucket_name = "image-uploads"

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

            # Upload file
            logger.info(f"Uploading reference image to {bucket_name}/{storage_path}")
            result = client_sb.storage.from_(bucket_name).upload(
                path=storage_path,
                file=content,
                file_options={"content-type": content_type or "image/png"}
            )
            logger.info(f"Upload result: {result}")

            # Get signed URL (valid for 1 hour)
            signed_url = client_sb.storage.from_(bucket_name).create_signed_url(
                path=storage_path,
                expires_in=3600
            )

            if signed_url and signed_url.get("signedURL"):
                return ImageUploadResponse(
                    success=True,
                    file_url=signed_url["signedURL"],
                    file_path=storage_path,
                    message="Reference image uploaded successfully"
                )
            else:
                raise HTTPException(status_code=500, detail="Failed to generate signed URL")

        except HTTPException:
            raise
        except Exception as storage_error:
            logger.error(f"Storage upload failed: {storage_error}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload file: {str(storage_error)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reference image upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start", response_model=ImageCatalystStartResponse)
async def start_image_generation(
    request: ImageCatalystStartRequest,
    client_id: str = Query(...),
    agent_id: str = Query(...),
    user_id: Optional[str] = Query(None),
    conversation_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    client_service: ClientService = Depends(get_client_service),
):
    """
    Generate an image using Image Catalyst.

    Synchronous -- returns result directly since image generation is fast (~5-15s).
    """
    try:
        # Validate client exists
        client = await client_service.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        # Validate Runware API key is configured
        from app.config import settings as app_settings
        if not app_settings.runware_api_key:
            raise HTTPException(
                status_code=400,
                detail="RunWare API key not configured. Image Catalyst requires a RunWare API key."
            )

        # Validate mode
        from app.services.image_catalyst_service import ImageMode, ImageCatalystService
        try:
            mode = ImageMode(request.mode)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode: {request.mode}. Must be 'thumbnail' or 'general'"
            )

        # Validate quality for thumbnail mode
        if mode == ImageMode.THUMBNAIL and request.quality:
            if request.quality not in ("low", "medium", "high"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid quality: {request.quality}. Must be 'low', 'medium', or 'high'"
                )

        # Fetch brand style config and enrich prompt
        brand_config = await _get_brand_style_config(client_service, client_id, agent_id)
        enriched_prompt = _enrich_prompt_with_brand_style(request.prompt, brand_config)
        if enriched_prompt != request.prompt:
            logger.info(f"Brand style applied to prompt for agent {agent_id}")

        # Create service and generate
        service = ImageCatalystService()
        result = await service.generate(
            mode=mode,
            prompt=request.prompt,
            enriched_prompt=enriched_prompt if enriched_prompt != request.prompt else None,
            client_id=client_id,
            agent_id=agent_id,
            user_id=user_id,
            conversation_id=conversation_id,
            session_id=session_id,
            reference_image_url=request.reference_image_url,
            width=request.width,
            height=request.height,
            quality=request.quality,
            steps=request.steps,
            cfg_scale=request.cfg_scale,
            strength=request.strength,
            seed=request.seed,
        )

        if result.status == "failed":
            return ImageCatalystStartResponse(
                success=False,
                run_id=result.run_id,
                status="failed",
                message=f"Image generation failed: {result.error}",
                error=result.error,
            )

        return ImageCatalystStartResponse(
            success=True,
            run_id=result.run_id,
            status="complete",
            message="Image generated successfully",
            image_url=result.image_url,
            seed=result.seed,
            generation_time_ms=result.generation_time_ms,
            cost=result.cost,
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Image Catalyst failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{run_id}", response_model=ImageCatalystStatusResponse)
async def get_status(
    run_id: str,
    client_id: str = Query(...),
):
    """Get status of an Image Catalyst generation run."""
    try:
        from app.services.image_catalyst_service import ImageCatalystService

        service = ImageCatalystService()
        run = await service.get_run(run_id)

        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        return ImageCatalystStatusResponse(
            run_id=run_id,
            status=run.get("status", "unknown"),
            mode=run.get("mode"),
            prompt=run.get("prompt"),
            image_url=run.get("output_image_url"),
            seed=run.get("seed"),
            generation_time_ms=run.get("generation_time_ms"),
            cost=float(run.get("cost", 0) or 0),
            error=run.get("error"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get Image Catalyst run status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/costs", response_model=CostSummaryResponse)
async def get_cost_summary(
    client_id: str = Query(...),
):
    """Get image generation cost summary for a client (current billing period)."""
    try:
        from app.services.image_catalyst_service import ImageCatalystService

        service = ImageCatalystService()
        summary = await service.get_client_cost_summary(client_id)

        return CostSummaryResponse(**summary)

    except Exception as e:
        logger.error(f"Failed to get cost summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/store-result")
async def store_result(
    request: StoreResultRequest,
    client_id: str = Query(...),
    client_service: ClientService = Depends(get_client_service),
):
    """Store Image Catalyst result in conversation history as a widget message."""
    try:
        from app.services.image_catalyst_service import ImageCatalystService

        service = ImageCatalystService()
        run = await service.get_run(request.run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        # Get client's Supabase to store in conversation
        client_sb = await client_service.get_client_supabase_client(client_id, auto_sync=False)

        widget_data = {
            "type": "image_catalyst",
            "state": "complete",
            "run_id": request.run_id,
            "data": {
                "mode": run.get("mode"),
                "prompt": run.get("prompt"),
                "image_url": run.get("output_image_url"),
                "cost": float(run.get("cost", 0) or 0),
            },
        }

        client_sb.table("conversation_transcripts").insert({
            "conversation_id": request.conversation_id,
            "role": "widget",
            "content": "",
            "metadata": {
                "widget": widget_data,
                "channel": "text",
            },
            "created_at": datetime.utcnow().isoformat(),
        }).execute()

        logger.info(
            f"Stored Image Catalyst result for conversation {request.conversation_id}, "
            f"run {request.run_id}"
        )

        return {"success": True, "message": "Result stored"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to store Image Catalyst result: {e}")
        raise HTTPException(status_code=500, detail=str(e))
