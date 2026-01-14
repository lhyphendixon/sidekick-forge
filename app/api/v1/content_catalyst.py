"""
Content Catalyst API Endpoints

Provides REST API endpoints for the Content Catalyst multi-phase article generation ability.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, BackgroundTasks
from pydantic import BaseModel, Field

from app.core.dependencies import get_client_service
from app.services.client_service_supabase import ClientService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/content-catalyst", tags=["content-catalyst"])


class ContentCatalystStartRequest(BaseModel):
    """Request to start a Content Catalyst run."""
    source_type: str = Field(..., description="Type of source: 'mp3', 'url', 'text', or 'document'")
    source_content: str = Field(..., description="The source content (URL, text, or storage path)")
    target_word_count: int = Field(default=1500, ge=500, le=10000)
    style_prompt: Optional[str] = Field(None, description="Optional style guidance")
    use_perplexity: bool = Field(default=True)
    use_knowledge_base: bool = Field(default=True)
    # New fields for document source and text instructions
    document_id: Optional[int] = Field(None, description="Document ID for 'document' source type")
    document_title: Optional[str] = Field(None, description="Document title for display")
    text_instructions: Optional[str] = Field(None, description="Additional instructions for content generation")


class ContentCatalystStartResponse(BaseModel):
    """Response from starting a Content Catalyst run."""
    success: bool
    run_id: Optional[str] = None
    message: str
    article_1: Optional[dict] = None
    article_2: Optional[dict] = None


class ContentCatalystStatusResponse(BaseModel):
    """Response with Content Catalyst run status."""
    run_id: str
    status: str
    current_phase: str
    phases_completed: list
    article_1: Optional[str] = None
    article_2: Optional[str] = None
    error: Optional[str] = None


class MP3UploadResponse(BaseModel):
    """Response from MP3 upload."""
    success: bool
    file_url: Optional[str] = None
    file_path: Optional[str] = None
    message: str


@router.post("/start", response_model=ContentCatalystStartResponse)
async def start_content_catalyst(
    request: ContentCatalystStartRequest,
    client_id: str = Query(...),
    agent_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    conversation_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    background_tasks: BackgroundTasks = None,
    client_service: ClientService = Depends(get_client_service),
):
    """
    Start a new Content Catalyst run.

    This endpoint initiates the multi-phase article generation pipeline.
    The run is executed asynchronously, and progress can be monitored via the status endpoint.
    """
    try:
        # Validate client exists and has Content Catalyst enabled
        client = await client_service.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        # Check if Content Catalyst is enabled
        # Can be enabled at client level OR agent level (via agent_tools)
        platform_sb = client_service.supabase
        content_catalyst_enabled = False
        has_llm_key = False

        try:
            # Check client-level enablement
            result = platform_sb.table("clients").select(
                "content_catalyst_enabled, groq_api_key, openai_api_key, anthropic_api_key, deepinfra_api_key"
            ).eq("id", client_id).maybe_single().execute()
            if result.data:
                content_catalyst_enabled = result.data.get("content_catalyst_enabled", False)
                has_llm_key = any([
                    result.data.get("groq_api_key"),
                    result.data.get("openai_api_key"),
                    result.data.get("anthropic_api_key"),
                    result.data.get("deepinfra_api_key"),
                ])

            # If not enabled at client level, check if enabled for this specific agent
            if not content_catalyst_enabled and agent_id:
                from app.utils.supabase_credentials import SupabaseCredentialManager
                creds = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
                if creds:
                    client_supabase_url, _, client_service_key = creds
                    from supabase import create_client
                    client_sb = create_client(client_supabase_url, client_service_key)

                    # Check agent_tools for content_catalyst ability
                    agent_tools_result = client_sb.table("agent_tools") \
                        .select("id") \
                        .eq("agent_id", agent_id) \
                        .eq("tool_type", "content_catalyst") \
                        .eq("enabled", True) \
                        .maybe_single() \
                        .execute()

                    if agent_tools_result.data:
                        content_catalyst_enabled = True
                        logger.info(f"Content Catalyst enabled for agent {agent_id} via agent_tools")

            if not content_catalyst_enabled:
                raise HTTPException(
                    status_code=403,
                    detail="Content Catalyst is not enabled for this client or agent"
                )

            if not has_llm_key:
                raise HTTPException(
                    status_code=400,
                    detail="No LLM API key configured. Content Catalyst requires an LLM API key (Groq, OpenAI, Anthropic, or DeepInfra)."
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Could not check content_catalyst_enabled: {e}")

        # Import and create service
        from app.services.content_catalyst_service import (
            get_content_catalyst_service,
            ContentCatalystConfig,
            SourceType,
        )

        # Map source type
        try:
            source_type_enum = SourceType(request.source_type.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid source_type: {request.source_type}. Must be 'mp3', 'url', 'text', or 'document'"
            )

        # For MP3 sources, check STT configuration
        if source_type_enum == SourceType.MP3:
            if not client.settings or not client.settings.api_keys:
                raise HTTPException(
                    status_code=400,
                    detail="STT not configured. Audio transcription requires Deepgram API key."
                )

            deepgram_key = getattr(client.settings.api_keys, "deepgram_api_key", None)
            if not deepgram_key:
                raise HTTPException(
                    status_code=400,
                    detail="Deepgram API key not configured. Required for audio transcription."
                )

        # For document sources, validate document_id is provided
        if source_type_enum == SourceType.DOCUMENT:
            if not request.document_id:
                raise HTTPException(
                    status_code=400,
                    detail="document_id is required when source_type is 'document'"
                )

        # Create config
        config = ContentCatalystConfig(
            source_type=source_type_enum,
            source_content=request.source_content,
            target_word_count=request.target_word_count,
            style_prompt=request.style_prompt,
            use_perplexity=request.use_perplexity,
            use_knowledge_base=request.use_knowledge_base,
            document_id=request.document_id,
            document_title=request.document_title,
            text_instructions=request.text_instructions,
        )

        # Get the service (pass agent_id for per-agent configuration)
        service = await get_content_catalyst_service(client_id, agent_id=agent_id)

        # Run the pipeline (synchronously for now - can be made async with background_tasks)
        run_id, article_1, article_2 = await service.run_full_pipeline(
            config=config,
            agent_id=agent_id,
            user_id=user_id,
            conversation_id=conversation_id,
            session_id=session_id,
        )

        return ContentCatalystStartResponse(
            success=True,
            run_id=run_id,
            message="Content Catalyst completed successfully",
            article_1={"content": article_1, "word_count": len(article_1.split())},
            article_2={"content": article_2, "word_count": len(article_2.split())},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Content Catalyst failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{run_id}", response_model=ContentCatalystStatusResponse)
async def get_content_catalyst_status(
    run_id: str,
    client_id: str = Query(...),
    client_service: ClientService = Depends(get_client_service),
):
    """
    Get the status of a Content Catalyst run.
    """
    try:
        from app.services.content_catalyst_service import get_content_catalyst_service

        service = await get_content_catalyst_service(client_id)
        run = await service.get_run(run_id)

        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        return ContentCatalystStatusResponse(
            run_id=run_id,
            status=run.get("status", "unknown"),
            current_phase=run.get("current_phase", "unknown"),
            phases_completed=run.get("phases_completed", []),
            article_1=run.get("article_variation_1"),
            article_2=run.get("article_variation_2"),
            error=run.get("error"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get run status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload-mp3", response_model=MP3UploadResponse)
async def upload_mp3(
    file: UploadFile = File(...),
    client_id: str = Query(...),
    client_service: ClientService = Depends(get_client_service),
):
    """
    Upload an MP3 file for Content Catalyst transcription.

    The file is stored temporarily in Supabase storage and a signed URL is returned.
    The URL can be used as the source_content when starting a Content Catalyst run with source_type='mp3'.
    """
    try:
        # Validate file type
        if not file.content_type or 'audio' not in file.content_type:
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Only MP3/audio files are accepted."
            )

        # Validate file size (max 100MB)
        MAX_SIZE = 100 * 1024 * 1024  # 100MB
        content = await file.read()
        if len(content) > MAX_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Maximum size is 100MB."
            )

        # Get client's Supabase for storage
        client_sb = await client_service.get_client_supabase_client(client_id, auto_sync=False)

        # Generate unique filename
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        file_ext = file.filename.split('.')[-1] if file.filename and '.' in file.filename else 'mp3'
        storage_path = f"content-catalyst/{client_id}/{timestamp}_{uuid.uuid4().hex[:8]}.{file_ext}"

        # Upload to Supabase storage
        bucket_name = "audio-uploads"

        try:
            # Try to create bucket if it doesn't exist
            bucket_created = False
            try:
                # Use minimal options - file_size_limit may exceed plan limits
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
                    # Try to list buckets to see if it exists
                    try:
                        buckets = client_sb.storage.list_buckets()
                        bucket_names = [b.get('name') or b.get('id') for b in buckets]
                        logger.info(f"Available buckets: {bucket_names}")
                        if bucket_name in bucket_names:
                            bucket_created = True
                    except Exception as list_err:
                        logger.warning(f"Could not list buckets: {list_err}")

            if not bucket_created:
                # Bucket doesn't exist and we couldn't create it - give clear error
                raise HTTPException(
                    status_code=500,
                    detail=f"Storage bucket '{bucket_name}' does not exist in the client's Supabase project. Please create it in Supabase Dashboard → Storage → New Bucket."
                )

            # Upload file
            logger.info(f"Uploading file to {bucket_name}/{storage_path}")
            result = client_sb.storage.from_(bucket_name).upload(
                path=storage_path,
                file=content,
                file_options={"content-type": file.content_type or "audio/mpeg"}
            )
            logger.info(f"Upload result: {result}")

            # Get signed URL (valid for 1 hour)
            signed_url = client_sb.storage.from_(bucket_name).create_signed_url(
                path=storage_path,
                expires_in=3600  # 1 hour
            )

            if signed_url and signed_url.get("signedURL"):
                return MP3UploadResponse(
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
        logger.error(f"MP3 upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check-stt/{client_id}")
async def check_stt_configuration(
    client_id: str,
    client_service: ClientService = Depends(get_client_service),
):
    """
    Check if a client has STT configured for audio transcription.
    """
    try:
        client = await client_service.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        has_stt = False
        stt_provider = None

        if client.settings and client.settings.api_keys:
            # Check for Deepgram (primary)
            if getattr(client.settings.api_keys, "deepgram_api_key", None):
                has_stt = True
                stt_provider = "deepgram"
            # Could add other STT providers here

        return {
            "has_stt": has_stt,
            "stt_provider": stt_provider,
            "message": "STT is configured" if has_stt else "No STT provider configured. Please add a Deepgram API key in client settings."
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to check STT config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class StoreWidgetResultRequest(BaseModel):
    """Request to store widget results in conversation."""
    run_id: str = Field(..., description="Content Catalyst run ID")
    article_1: dict = Field(..., description="First article variation")
    article_2: dict = Field(..., description="Second article variation")


@router.post("/store-result")
async def store_widget_result(
    request: StoreWidgetResultRequest,
    client_id: str = Query(...),
    conversation_id: str = Query(...),
    client_service: ClientService = Depends(get_client_service),
):
    """
    Store Content Catalyst widget results in the conversation transcript.
    This enables widget state to persist when the chat is closed and reopened.
    """
    try:
        from app.utils.supabase_credentials import SupabaseCredentialManager

        # Get client's Supabase
        client_supabase_url, _, client_service_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_supabase_url, client_service_key)

        # Store as a new assistant message with widget data in metadata
        widget_data = {
            "type": "content_catalyst",
            "state": "complete",
            "run_id": request.run_id,
            "articles": {
                "article_1": request.article_1,
                "article_2": request.article_2
            }
        }

        # Insert a widget result message
        result = client_sb.table("conversation_transcripts").insert({
            "conversation_id": conversation_id,
            "role": "widget",
            "content": "",  # No text content, just widget data
            "metadata": {
                "widget": widget_data,
                "channel": "text"
            },
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        logger.info(f"Stored widget result for conversation {conversation_id}, run {request.run_id}")

        return {"success": True, "message": "Widget result stored"}

    except Exception as e:
        logger.error(f"Failed to store widget result: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class DocumentListItem(BaseModel):
    """Document item for Content Catalyst picker."""
    id: int
    title: str
    created_at: str
    document_type: Optional[str] = None


class DocumentListResponse(BaseModel):
    """Response with list of documents for Content Catalyst picker."""
    documents: list[DocumentListItem]


@router.get("/documents/{agent_id}", response_model=DocumentListResponse)
async def get_agent_documents(
    agent_id: str,
    client_id: str = Query(...),
    client_service: ClientService = Depends(get_client_service),
):
    """
    Get list of documents assigned to an agent for Content Catalyst selection.

    Returns documents from the agent_documents junction table that are enabled
    and have completed processing.
    """
    try:
        from app.utils.supabase_credentials import SupabaseCredentialManager

        # Get client's Supabase
        creds = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        if not creds:
            logger.error(f"Could not get Supabase credentials for client {client_id}")
            raise HTTPException(status_code=404, detail="Client not found or Supabase not configured")

        client_supabase_url, _, client_service_key = creds
        from supabase import create_client
        client_sb = create_client(client_supabase_url, client_service_key)

        # Query documents assigned to this agent
        # First get the document IDs from agent_documents
        agent_docs_result = client_sb.table('agent_documents') \
            .select('document_id') \
            .eq('agent_id', agent_id) \
            .eq('enabled', True) \
            .execute()

        logger.info(f"Agent documents query for {agent_id}: {len(agent_docs_result.data or [])} assignments found")

        documents = []
        if agent_docs_result.data:
            doc_ids = [item.get('document_id') for item in agent_docs_result.data if item.get('document_id')]
            logger.info(f"Document IDs to fetch: {doc_ids}")

            if doc_ids:
                # Fetch document details
                docs_result = client_sb.table('documents') \
                    .select('id, title, created_at, document_type, processing_status') \
                    .in_('id', doc_ids) \
                    .execute()

                logger.info(f"Documents fetched: {len(docs_result.data or [])} documents")

                for doc in (docs_result.data or []):
                    # Include all enabled documents - they have content if they're assigned
                    # Statuses: completed, processed, summarizing, chunking, embedding, etc.
                    status = doc.get('processing_status', '')
                    logger.debug(f"Document {doc.get('id')} '{doc.get('title')}' status: {status}")
                    # Exclude only failed documents
                    if status != 'failed':
                        documents.append(DocumentListItem(
                            id=doc.get('id'),
                            title=doc.get('title') or 'Untitled',
                            created_at=doc.get('created_at', ''),
                            document_type=doc.get('document_type'),
                        ))

        # Sort by created_at descending (newest first)
        documents.sort(key=lambda x: x.created_at, reverse=True)

        logger.info(f"Retrieved {len(documents)} documents for agent {agent_id}")
        return DocumentListResponse(documents=documents)

    except Exception as e:
        logger.error(f"Failed to get agent documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))
