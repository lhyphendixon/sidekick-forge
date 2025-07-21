from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Query
from typing import List, Optional
from uuid import UUID, uuid4
from datetime import datetime, timedelta

from app.models.document import (
    Document, DocumentUploadRequest, DocumentUploadResponse,
    DocumentProcessingStatus, DocumentSearchRequest, DocumentSearchResponse,
    DocumentListResponse
)
from app.models.common import APIResponse, SuccessResponse, DeleteResponse
from app.middleware.auth import get_current_auth, require_user_auth
from app.integrations.supabase_client import supabase_manager
from app.utils.exceptions import NotFoundError, ValidationError
from app.utils.helpers import sanitize_filename, calculate_file_hash, format_file_size

router = APIRouter()

@router.get("/", response_model=APIResponse[DocumentListResponse])
async def list_documents(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, pattern="^(pending|uploading|completed|failed)$"),
    auth=Depends(require_user_auth)
):
    """
    List documents for the authenticated user
    """
    try:
        # Build query
        query = supabase_manager.admin_client.table("documents").select("*")
        query = query.eq("user_id", auth.user_id)
        
        if status:
            query = query.eq("upload_status", status)
        
        # Pagination
        offset = (page - 1) * per_page
        query = query.order("created_at", desc=True).limit(per_page).offset(offset)
        
        # Execute query
        result = await supabase_manager.execute_query(query)
        
        return APIResponse(
            success=True,
            data=DocumentListResponse(
                documents=result,
                total=len(result),
                page=page,
                per_page=per_page
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/upload/request", response_model=APIResponse[DocumentUploadResponse])
async def request_document_upload(
    request: DocumentUploadRequest,
    auth=Depends(require_user_auth)
):
    """
    Request a pre-signed URL for document upload
    """
    try:
        # Validate file size
        if request.file_size > 104857600:  # 100MB
            raise ValidationError("File size exceeds maximum allowed (100MB)")
        
        # Sanitize filename
        safe_filename = sanitize_filename(request.filename)
        
        # Create document record
        document_id = str(uuid4())
        document_data = {
            "id": document_id,
            "user_id": auth.user_id,
            "filename": safe_filename,
            "file_type": request.content_type,
            "file_size": request.file_size,
            "upload_status": "pending",
            "processing_status": "pending",
            "metadata": request.metadata,
            "created_at": datetime.utcnow().isoformat()
        }
        
        await supabase_manager.create_document(document_data)
        
        # Generate upload URL (Supabase Storage)
        # In production, this would create a pre-signed URL for Supabase Storage
        upload_path = f"documents/{auth.user_id}/{document_id}/{safe_filename}"
        
        # For now, return a placeholder URL
        upload_url = f"{supabase_manager.admin_client.url}/storage/v1/object/documents/{upload_path}"
        
        return APIResponse(
            success=True,
            data=DocumentUploadResponse(
                document_id=document_id,
                upload_url=upload_url,
                upload_method="PUT",
                expires_at=datetime.utcnow() + timedelta(hours=1)
            )
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/upload/complete/{document_id}", response_model=APIResponse[Document])
async def complete_document_upload(
    document_id: UUID,
    auth=Depends(require_user_auth)
):
    """
    Mark document upload as complete and trigger processing
    """
    try:
        # Get document
        query = supabase_manager.admin_client.table("documents").select("*").eq("id", str(document_id)).eq("user_id", auth.user_id)
        result = await supabase_manager.execute_query(query)
        
        if not result:
            raise NotFoundError("Document not found")
        
        document = result[0]
        
        # Update status
        update_data = {
            "upload_status": "completed",
            "processing_status": "processing"
        }
        
        await supabase_manager.update_document(str(document_id), update_data)
        
        # TODO: Trigger document processing (chunking, embeddings)
        # This would typically be handled by a background job
        
        # Return updated document
        document.update(update_data)
        return APIResponse(
            success=True,
            data=document
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/{document_id}", response_model=APIResponse[Document])
async def get_document(
    document_id: UUID,
    auth=Depends(require_user_auth)
):
    """
    Get document details
    """
    try:
        query = supabase_manager.admin_client.table("documents").select("*").eq("id", str(document_id)).eq("user_id", auth.user_id)
        result = await supabase_manager.execute_query(query)
        
        if not result:
            raise NotFoundError("Document not found")
        
        return APIResponse(
            success=True,
            data=result[0]
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/{document_id}/status", response_model=APIResponse[DocumentProcessingStatus])
async def get_document_status(
    document_id: UUID,
    auth=Depends(require_user_auth)
):
    """
    Get document processing status
    """
    try:
        query = supabase_manager.admin_client.table("documents").select("*").eq("id", str(document_id)).eq("user_id", auth.user_id)
        result = await supabase_manager.execute_query(query)
        
        if not result:
            raise NotFoundError("Document not found")
        
        document = result[0]
        
        return APIResponse(
            success=True,
            data=DocumentProcessingStatus(
                document_id=document["id"],
                upload_status=document["upload_status"],
                processing_status=document["processing_status"],
                word_count=document.get("word_count"),
                chunk_count=document.get("chunk_count", 0),
                error_message=document.get("metadata", {}).get("error_message")
            )
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/search", response_model=APIResponse[DocumentSearchResponse])
async def search_documents(
    request: DocumentSearchRequest,
    auth=Depends(require_user_auth)
):
    """
    Search documents using RAG (semantic search)
    """
    try:
        # TODO: Implement actual vector search using Supabase pgvector
        # This is a placeholder implementation
        
        # For now, return empty results
        return APIResponse(
            success=True,
            data=DocumentSearchResponse(
                query=request.query,
                results=[],
                total_results=0,
                processing_time_ms=0.0
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.delete("/{document_id}", response_model=APIResponse[DeleteResponse])
async def delete_document(
    document_id: UUID,
    auth=Depends(require_user_auth)
):
    """
    Delete a document and its chunks
    """
    try:
        # Check document exists
        query = supabase_manager.admin_client.table("documents").select("*").eq("id", str(document_id)).eq("user_id", auth.user_id)
        result = await supabase_manager.execute_query(query)
        
        if not result:
            raise NotFoundError("Document not found")
        
        # Delete chunks first
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("document_chunks")
            .delete()
            .eq("document_id", str(document_id))
        )
        
        # Delete document
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("documents")
            .delete()
            .eq("id", str(document_id))
        )
        
        # TODO: Delete from Supabase Storage
        
        return APIResponse(
            success=True,
            data=DeleteResponse(
                deleted_id=str(document_id),
                deleted_at=datetime.utcnow()
            )
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )