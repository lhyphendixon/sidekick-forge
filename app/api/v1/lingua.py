"""
LINGUA API Endpoints

Audio transcription and subtitle translation using AssemblyAI.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.core.dependencies import get_client_service
from app.services.client_service_supabase import ClientService
from app.services.lingua_service import (
    LinguaService,
    LinguaResult,
    generate_srt,
    generate_vtt,
    generate_txt,
    get_available_transcription_languages,
    get_available_translation_languages,
)

router = APIRouter(prefix="/lingua", tags=["lingua"])
logger = logging.getLogger(__name__)


# ============================================================================
# Request/Response Models
# ============================================================================

class AudioUploadResponse(BaseModel):
    """Response for audio file upload."""
    success: bool
    file_url: Optional[str] = None
    file_path: Optional[str] = None
    message: str


class LinguaStartRequest(BaseModel):
    """Request to start LINGUA processing."""
    source_audio_url: str = Field(..., description="Signed URL to the audio file")
    source_language: Optional[str] = Field(None, description="Source language code (null for auto-detect)")
    target_languages: List[str] = Field(default_factory=list, description="Languages to translate to")
    output_formats: List[str] = Field(default=["srt", "vtt", "txt"], description="Output formats to generate")


class LinguaStartResponse(BaseModel):
    """Response for LINGUA start."""
    success: bool
    run_id: Optional[str] = None
    status: Optional[str] = None
    message: str
    # Results if processing is synchronous
    transcript: Optional[Dict[str, Any]] = None
    translations: Optional[Dict[str, Any]] = None
    download_urls: Optional[Dict[str, Any]] = None


class LinguaStatusResponse(BaseModel):
    """Response for LINGUA status check."""
    run_id: str
    status: str
    progress_percent: int = 0
    current_phase: Optional[str] = None
    transcript: Optional[Dict[str, Any]] = None
    translations: Optional[Dict[str, Any]] = None
    download_urls: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class LanguagesResponse(BaseModel):
    """Response for available languages."""
    transcription_languages: Dict[str, str]
    translation_languages: Dict[str, str]


class StoreResultRequest(BaseModel):
    """Request to store LINGUA results in conversation."""
    conversation_id: str
    run_id: str
    result_data: Dict[str, Any]


# ============================================================================
# In-Memory Run Storage (for demo - production should use database)
# ============================================================================

_lingua_runs: Dict[str, Any] = {}  # Can store LinguaResult or pending status dict


async def process_lingua_async(
    run_id: str,
    service: LinguaService,
    audio_url: str,
    source_language: Optional[str],
    target_languages: Optional[List[str]],
):
    """Background task to process LINGUA transcription and translation."""
    try:
        logger.info(f"[LINGUA] Background task started for run {run_id}")

        # Update status to transcribing
        if run_id in _lingua_runs:
            _lingua_runs[run_id]["status"] = "transcribing"

        result = await service.process_full(
            audio_url=audio_url,
            source_language=source_language,
            target_languages=target_languages,
        )

        # Store the completed result
        _lingua_runs[run_id] = result
        logger.info(f"[LINGUA] Background task completed for run {run_id}")

    except Exception as e:
        logger.error(f"[LINGUA] Background task failed for run {run_id}: {e}", exc_info=True)
        # Store error status
        _lingua_runs[run_id] = {
            "run_id": run_id,
            "status": "failed",
            "error": str(e),
        }


# ============================================================================
# API Endpoints
# ============================================================================

@router.get("/languages", response_model=LanguagesResponse)
async def get_languages():
    """Get available transcription and translation languages."""
    return LanguagesResponse(
        transcription_languages=get_available_transcription_languages(),
        translation_languages=get_available_translation_languages(),
    )


@router.post("/upload", response_model=AudioUploadResponse)
async def upload_audio(
    file: UploadFile = File(...),
    client_id: str = Query(...),
    client_service: ClientService = Depends(get_client_service),
):
    """
    Upload an audio file for LINGUA transcription.

    The file is stored temporarily in Supabase storage and a signed URL is returned.
    Supported formats: MP3, WAV, M4A, FLAC, OGG, WEBM
    """
    try:
        # Validate file type
        allowed_types = ['audio/', 'video/webm']  # webm can contain audio
        content_type = file.content_type or ''
        if not any(allowed in content_type for allowed in allowed_types):
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Supported formats: MP3, WAV, M4A, FLAC, OGG, WEBM"
            )

        # Validate file size (max 100MB)
        MAX_SIZE = 100 * 1024 * 1024  # 100MB
        content = await file.read()
        if len(content) > MAX_SIZE:
            raise HTTPException(
                status_code=400,
                detail="File too large. Maximum size is 100MB."
            )

        # Get client's Supabase for storage
        client_sb = await client_service.get_client_supabase_client(client_id, auto_sync=False)

        # Generate unique filename
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        file_ext = file.filename.split('.')[-1] if file.filename and '.' in file.filename else 'mp3'
        storage_path = f"lingua/{client_id}/{timestamp}_{uuid.uuid4().hex[:8]}.{file_ext}"

        # Upload to Supabase storage
        bucket_name = "audio-uploads"

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
            logger.info(f"Uploading file to {bucket_name}/{storage_path}")
            result = client_sb.storage.from_(bucket_name).upload(
                path=storage_path,
                file=content,
                file_options={"content-type": content_type or "audio/mpeg"}
            )
            logger.info(f"Upload result: {result}")

            # Get signed URL (valid for 1 hour)
            signed_url = client_sb.storage.from_(bucket_name).create_signed_url(
                path=storage_path,
                expires_in=3600  # 1 hour
            )

            if signed_url and signed_url.get("signedURL"):
                return AudioUploadResponse(
                    success=True,
                    file_url=signed_url["signedURL"],
                    file_path=storage_path,
                    message="File uploaded successfully"
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
        logger.error(f"Audio upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start", response_model=LinguaStartResponse)
async def start_lingua(
    request: LinguaStartRequest,
    background_tasks: BackgroundTasks,
    client_id: str = Query(...),
    agent_id: Optional[str] = Query(None),
    client_service: ClientService = Depends(get_client_service),
):
    """
    Start LINGUA transcription and translation.

    This endpoint starts processing in the background and returns immediately.
    Use /status/{run_id} to poll for completion.
    """
    try:
        # Get client info and API keys
        client = await client_service.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        # Get AssemblyAI API key
        assemblyai_key = getattr(client.settings.api_keys, 'assemblyai_api_key', None)
        if not assemblyai_key:
            # Try from additional_settings
            assemblyai_key = client.additional_settings.get('api_keys', {}).get('assemblyai_api_key')

        if not assemblyai_key:
            raise HTTPException(
                status_code=400,
                detail="AssemblyAI API key not configured for this client. Add it in Settings → API Keys."
            )

        # Get LLM API key for translation (prefer Groq, fallback to others)
        llm_key = None
        llm_provider = "groq"

        if client.settings.api_keys.groq_api_key:
            llm_key = client.settings.api_keys.groq_api_key
            llm_provider = "groq"
        elif client.settings.api_keys.openai_api_key:
            llm_key = client.settings.api_keys.openai_api_key
            llm_provider = "openai"
        elif client.settings.api_keys.deepinfra_api_key:
            llm_key = client.settings.api_keys.deepinfra_api_key
            llm_provider = "deepinfra"

        if not llm_key and request.target_languages:
            raise HTTPException(
                status_code=400,
                detail="Translation requires an LLM API key (Groq, OpenAI, or DeepInfra). Add one in Settings → API Keys."
            )

        # Initialize service
        service = LinguaService(
            assemblyai_api_key=assemblyai_key,
            llm_api_key=llm_key or "",
            llm_provider=llm_provider,
        )

        # Generate run ID
        run_id = str(uuid.uuid4())

        # Store pending status
        _lingua_runs[run_id] = {
            "run_id": run_id,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
            "source_audio_url": request.source_audio_url,
            "target_languages": request.target_languages,
        }

        # Start background processing
        logger.info(f"Starting LINGUA background processing for client {client_id}, run {run_id}")
        background_tasks.add_task(
            process_lingua_async,
            run_id=run_id,
            service=service,
            audio_url=request.source_audio_url,
            source_language=request.source_language,
            target_languages=request.target_languages if request.target_languages else None,
        )

        # Return immediately with pending status
        return LinguaStartResponse(
            success=True,
            run_id=run_id,
            status="pending",
            message="Processing started. Poll /status/{run_id} for updates.",
            transcript=None,
            translations={},
            download_urls={},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"LINGUA start failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{run_id}", response_model=LinguaStatusResponse)
async def get_status(
    run_id: str,
    client_id: str = Query(...),
):
    """Get status of a LINGUA processing run."""
    result = _lingua_runs.get(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="Run not found")

    # Handle dict (pending/in-progress) vs LinguaResult (completed)
    if isinstance(result, dict):
        # Still processing or failed
        status = result.get("status", "pending")
        progress = 0
        phase = "Starting..."
        if status == "pending":
            progress = 10
            phase = "Starting transcription..."
        elif status == "transcribing":
            progress = 40
            phase = "Transcribing audio..."
        elif status == "translating":
            progress = 70
            phase = "Translating subtitles..."
        elif status == "failed":
            progress = 0
            phase = "Failed"

        return LinguaStatusResponse(
            run_id=run_id,
            status=status,
            progress_percent=progress,
            current_phase=phase,
            transcript=None,
            translations={},
            download_urls={},
            error=result.get("error"),
        )

    # LinguaResult object - processing complete
    progress = 100 if result.status == "complete" else 0
    phase = "Complete" if result.status == "complete" else "Failed"

    # Generate download content
    download_urls = {}
    if result.original_transcript:
        orig_lang = result.original_transcript.language_code
        download_urls[orig_lang] = {
            "srt": generate_srt(result.original_transcript.segments),
            "vtt": generate_vtt(result.original_transcript.segments),
            "txt": generate_txt(result.original_transcript.segments),
        }
        for lang_code, translation in result.translations.items():
            download_urls[lang_code] = {
                "srt": generate_srt(translation.segments),
                "vtt": generate_vtt(translation.segments),
                "txt": translation.text,
            }

    return LinguaStatusResponse(
        run_id=run_id,
        status=result.status,
        progress_percent=progress,
        current_phase=phase,
        transcript=result.original_transcript.to_dict() if result.original_transcript else None,
        translations={k: v.to_dict() for k, v in result.translations.items()},
        download_urls=download_urls,
        error=result.error,
    )


@router.post("/store-result")
async def store_result(
    request: StoreResultRequest,
    client_id: str = Query(...),
    client_service: ClientService = Depends(get_client_service),
):
    """Store LINGUA results in conversation history."""
    try:
        client_sb = await client_service.get_client_supabase_client(client_id, auto_sync=False)

        # Store as widget message in conversation
        client_sb.table("conversation_transcripts").insert({
            "conversation_id": request.conversation_id,
            "role": "widget",
            "content": "",
            "metadata": {
                "widget": {
                    "type": "lingua",
                    "state": "complete",
                    "run_id": request.run_id,
                    "data": request.result_data,
                },
                "channel": "text",
            },
            "created_at": datetime.utcnow().isoformat(),
        }).execute()

        return {"success": True, "message": "Result stored"}

    except Exception as e:
        logger.error(f"Failed to store LINGUA result: {e}")
        raise HTTPException(status_code=500, detail=str(e))
