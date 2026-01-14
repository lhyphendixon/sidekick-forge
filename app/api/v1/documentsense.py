"""
DocumentSense API Endpoints

Provides endpoints for:
- Getting DocumentSense processing status for a client
- Retrieving extracted intelligence for specific documents
- Triggering batch extraction for a client's documents
- Searching documents by intelligence
"""

import logging
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel

from app.config import settings
from app.utils.supabase_credentials import SupabaseCredentialManager
from app.services.documentsense_executor import documentsense_executor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documentsense", tags=["documentsense"])


class TriggerExtractionRequest(BaseModel):
    """Request body for triggering document extraction."""
    document_ids: Optional[List[int]] = None  # If None, process all unprocessed documents


class ExtractionStatusResponse(BaseModel):
    """Response for extraction status."""
    pending: int
    in_progress: List[dict]
    completed: int
    failed: int
    has_active_jobs: bool


class DocumentIntelligenceResponse(BaseModel):
    """Response for document intelligence."""
    exists: bool
    document_id: Optional[int] = None
    document_title: Optional[str] = None
    intelligence: Optional[dict] = None
    extraction_model: Optional[str] = None
    extraction_timestamp: Optional[str] = None
    version: Optional[int] = None


@router.get("/status/{client_id}", response_model=ExtractionStatusResponse)
async def get_documentsense_status(
    client_id: str,
    x_internal_request: Optional[str] = Header(None, alias="X-Internal-Request")
):
    """
    Get DocumentSense extraction status for a client.

    Returns counts of pending, in-progress, completed, and failed extraction jobs.
    """
    try:
        from supabase import create_client
        platform_sb = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key
        )

        result = platform_sb.rpc('get_client_documentsense_status', {
            'p_client_id': client_id
        }).execute()

        if result.data:
            return ExtractionStatusResponse(
                pending=result.data.get('pending', 0),
                in_progress=result.data.get('in_progress', []),
                completed=result.data.get('completed', 0),
                failed=result.data.get('failed', 0),
                has_active_jobs=result.data.get('has_active_jobs', False)
            )

        return ExtractionStatusResponse(
            pending=0,
            in_progress=[],
            completed=0,
            failed=0,
            has_active_jobs=False
        )

    except Exception as e:
        logger.error(f"Failed to get DocumentSense status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/intelligence/{client_id}/{document_id}", response_model=DocumentIntelligenceResponse)
async def get_document_intelligence(
    client_id: str,
    document_id: int,
    x_internal_request: Optional[str] = Header(None, alias="X-Internal-Request")
):
    """
    Get extracted intelligence for a specific document.

    Returns the full intelligence data including summary, quotes, themes, entities, etc.
    """
    try:
        result = await documentsense_executor.get_document_intelligence(
            client_id=client_id,
            document_id=document_id
        )

        if result and result.get('exists'):
            return DocumentIntelligenceResponse(
                exists=True,
                document_id=result.get('document_id'),
                document_title=result.get('document_title'),
                intelligence=result.get('intelligence'),
                extraction_model=result.get('extraction_model'),
                extraction_timestamp=str(result.get('extraction_timestamp')) if result.get('extraction_timestamp') else None,
                version=result.get('version')
            )

        return DocumentIntelligenceResponse(exists=False)

    except Exception as e:
        logger.error(f"Failed to get document intelligence: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search/{client_id}")
async def search_document_intelligence(
    client_id: str,
    query: str = Query(..., min_length=1, description="Search query for document title"),
    limit: int = Query(10, ge=1, le=50, description="Maximum results to return"),
    x_internal_request: Optional[str] = Header(None, alias="X-Internal-Request")
):
    """
    Search documents by title and return their intelligence data.

    Uses full-text search on document titles.
    """
    try:
        results = await documentsense_executor.search_documents(
            client_id=client_id,
            query=query,
            limit=limit
        )

        return {
            "success": True,
            "query": query,
            "count": len(results),
            "documents": results
        }

    except Exception as e:
        logger.error(f"Failed to search document intelligence: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/process/{client_id}")
async def trigger_documentsense_processing(
    client_id: str,
    request: TriggerExtractionRequest = None,
    x_internal_request: Optional[str] = Header(None, alias="X-Internal-Request")
):
    """
    Trigger DocumentSense extraction processing for a client.

    If document_ids are provided, queues those specific documents.
    Otherwise, queues all unprocessed documents for the client.
    """
    try:
        from supabase import create_client
        platform_sb = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key
        )

        document_ids = request.document_ids if request else None

        result = platform_sb.rpc('queue_client_documentsense_extraction', {
            'p_client_id': client_id,
            'p_document_ids': document_ids
        }).execute()

        if result.data:
            return {
                "success": result.data.get('success', False),
                "jobs_created": result.data.get('jobs_created', 0),
                "message": result.data.get('message', 'Processing queued')
            }

        return {
            "success": True,
            "jobs_created": 0,
            "message": "Processing queued"
        }

    except Exception as e:
        logger.error(f"Failed to trigger DocumentSense processing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/extract-single/{client_id}/{document_id}")
async def extract_single_document(
    client_id: str,
    document_id: int,
    x_internal_request: Optional[str] = Header(None, alias="X-Internal-Request")
):
    """
    Immediately extract intelligence from a single document.

    Unlike /process which queues jobs, this performs extraction synchronously.
    Useful for testing or extracting newly uploaded documents.
    """
    try:
        # Get document content from client's database
        client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        from supabase import create_client
        client_sb = create_client(client_url, client_key)

        doc_result = client_sb.table('documents').select(
            'id, title, content, status'
        ).eq('id', document_id).limit(1).execute()

        if not doc_result.data:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")

        doc = doc_result.data[0]
        doc_title = doc.get('title', f'Document {document_id}')
        doc_content = doc.get('content', '')

        if not doc_content or len(doc_content.strip()) < 50:
            raise HTTPException(status_code=400, detail="Document has insufficient content for extraction")

        # Extract intelligence
        result = await documentsense_executor.extract_intelligence(
            client_id=client_id,
            document_id=document_id,
            document_title=doc_title,
            document_content=doc_content
        )

        if result.success:
            return {
                "success": True,
                "document_id": document_id,
                "document_title": doc_title,
                "extraction_model": result.extraction_model,
                "intelligence": {
                    "summary": result.intelligence.summary if result.intelligence else "",
                    "key_quotes_count": len(result.intelligence.key_quotes) if result.intelligence else 0,
                    "themes": result.intelligence.themes if result.intelligence else [],
                    "document_type": result.intelligence.document_type_inferred if result.intelligence else None
                }
            }
        else:
            raise HTTPException(status_code=500, detail=result.error or "Extraction failed")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to extract document intelligence: {e}")
        raise HTTPException(status_code=500, detail=str(e))
