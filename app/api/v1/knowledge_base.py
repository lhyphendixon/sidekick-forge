#!/usr/bin/env python3
"""
Knowledge Base API endpoints
Handles document upload, processing, and management
"""

import os
import uuid
import tempfile
import shutil
from datetime import datetime
from typing import List, Optional
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.constants import (
    DOCUMENT_MAX_UPLOAD_BYTES,
    DOCUMENT_MAX_UPLOAD_MB,
    KNOWLEDGE_BASE_ALLOWED_EXTENSIONS,
)
from app.services.document_processor import document_processor
from app.core.dependencies import get_client_service
from app.middleware.auth import require_user_auth
from app.services.document_processor import document_processor

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


class DocumentResponse(BaseModel):
    id: str
    title: str
    file_name: str
    file_type: str
    file_size: int
    status: str
    chunk_count: Optional[int] = 0
    created_at: str
    processing_metadata: Optional[dict] = None


class UploadResponse(BaseModel):
    success: bool
    document_id: Optional[str] = None
    message: str
    status: Optional[str] = None


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    agent_ids: str = Form(""),  # Comma-separated list of agent IDs
    client_id: str = Form(...),
    replace_existing: str = Form("false"),
    auth = Depends(require_user_auth)
):
    """Upload a single document for processing"""
    try:
        # Validate file type
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")
        
        file_extension = Path(file.filename).suffix.lower().lstrip('.')
        allowed_types = KNOWLEDGE_BASE_ALLOWED_EXTENSIONS
        
        if file_extension not in allowed_types:
            raise HTTPException(
                status_code=400, 
                detail=f"Unsupported file type. Allowed: {', '.join(allowed_types)}"
            )
        
        # Validate file size
        max_size = DOCUMENT_MAX_UPLOAD_BYTES
        file_size = 0
        temp_file_path = None
        
        try:
            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_extension}') as temp_file:
                temp_file_path = temp_file.name
                
                # Read and save file content
                while True:
                    chunk = await file.read(8192)  # 8KB chunks
                    if not chunk:
                        break
                    file_size += len(chunk)
                    
                    if file_size > max_size:
                        raise HTTPException(
                            status_code=413, 
                            detail=f"File too large. Maximum size is {DOCUMENT_MAX_UPLOAD_MB}MB"
                        )
                    
                    temp_file.write(chunk)
            
            # Process agent IDs
            agent_id_list = []
            if agent_ids and agent_ids.strip():
                agent_id_list = [aid.strip() for aid in agent_ids.split(',') if aid.strip()]
            
            # Process the document
            result = await document_processor.process_uploaded_file(
                file_path=temp_file_path,
                title=title,
                description=description,
                user_id=auth.user_id,
                agent_ids=agent_id_list,
                client_id=client_id,
                replace_existing=replace_existing.lower() == "true"
            )
            
            if result['success']:
                return UploadResponse(
                    success=True,
                    document_id=result['document_id'],
                    message="Document uploaded and processing started",
                    status=result['status']
                )
            else:
                # Clean up temp file on error
                if temp_file_path and os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                
                return UploadResponse(
                    success=False,
                    message=result['error']
                )
        
        except HTTPException:
            # Clean up temp file on HTTP exceptions
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            raise
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/upload-batch")
async def upload_documents_batch(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    titles: str = Form(""),  # JSON string of titles
    descriptions: str = Form(""),  # JSON string of descriptions
    agent_ids: str = Form(""),  # Comma-separated agent IDs
    client_id: str = Form(...),
    auth = Depends(require_user_auth)
):
    """Upload multiple documents for batch processing"""
    try:
        import json
        
        # Parse titles and descriptions
        try:
            title_list = json.loads(titles) if titles else []
            description_list = json.loads(descriptions) if descriptions else []
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in titles or descriptions")
        
        # Process agent IDs
        agent_id_list = []
        if agent_ids and agent_ids.strip():
            agent_id_list = [aid.strip() for aid in agent_ids.split(',') if aid.strip()]
        
        results = []
        max_files = 20  # Limit batch size
        
        if len(files) > max_files:
            raise HTTPException(status_code=400, detail=f"Too many files. Maximum {max_files} files per batch")
        
        for i, file in enumerate(files):
            try:
                # Get title and description for this file
                title = title_list[i] if i < len(title_list) else file.filename
                description = description_list[i] if i < len(description_list) else ""
                
                # Validate file
                if not file.filename:
                    results.append({
                        'filename': f'file_{i}',
                        'success': False,
                        'error': 'No filename provided'
                    })
                    continue
                
                file_extension = Path(file.filename).suffix.lower().lstrip('.')
                allowed_types = KNOWLEDGE_BASE_ALLOWED_EXTENSIONS
                
                if file_extension not in allowed_types:
                    results.append({
                        'filename': file.filename,
                        'success': False,
                        'error': f'Unsupported file type: {file_extension}'
                    })
                    continue
                
                # Save to temporary file
                with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_extension}') as temp_file:
                    temp_file_path = temp_file.name
                    content = await file.read()
                    
                    # Check file size
                    if len(content) > DOCUMENT_MAX_UPLOAD_BYTES:
                        results.append({
                            'filename': file.filename,
                            'success': False,
                            'error': f'File too large ({DOCUMENT_MAX_UPLOAD_MB}MB max)'
                        })
                        os.unlink(temp_file_path)
                        continue
                    
                    temp_file.write(content)
                
                # Process the document
                result = await document_processor.process_uploaded_file(
                    file_path=temp_file_path,
                    title=title,
                    description=description,
                    user_id=auth.user_id,
                    agent_ids=agent_id_list,
                    client_id=client_id
                )
                
                results.append({
                    'filename': file.filename,
                    'success': result['success'],
                    'document_id': result.get('document_id'),
                    'error': result.get('error')
                })
                
            except Exception as e:
                results.append({
                    'filename': file.filename,
                    'success': False,
                    'error': str(e)
                })
        
        return {
            'success': True,
            'results': results,
            'total_files': len(files),
            'successful_uploads': len([r for r in results if r['success']])
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch upload failed: {str(e)}")


@router.get("/documents", response_model=List[DocumentResponse])
async def get_documents(
    response: Response,
    client_id: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    auth = Depends(require_user_auth)
):
    """Get list of documents"""
    try:
        safe_page = max(1, int(page or 1))
        safe_page_size = max(1, int(page_size or 50))
        offset = (safe_page - 1) * safe_page_size

        documents, total_count, total_size, _all_filenames = await document_processor.get_documents(
            user_id=auth.user_id,
            client_id=client_id,
            status=status,
            limit=safe_page_size,
            offset=offset,
            with_count=True,
        )

        # Expose pagination metadata via headers for compatibility
        response.headers['X-Total-Count'] = str(total_count)
        response.headers['X-Total-Size'] = str(total_size)
        response.headers['X-Page'] = str(safe_page)
        response.headers['X-Page-Size'] = str(safe_page_size)

        return [
            DocumentResponse(
                id=doc['id'],
                title=doc['title'],
                file_name=doc['file_name'],
                file_type=doc['file_type'],
                file_size=doc['file_size'],
                status=doc['status'],
                chunk_count=doc.get('chunk_count', 0),
                created_at=doc['created_at'],
                processing_metadata=doc.get('processing_metadata')
            )
            for doc in documents
        ]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch documents: {str(e)}")


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    auth = Depends(require_user_auth)
):
    """Get a specific document"""
    try:
        from app.integrations.supabase_client import supabase_manager
        
        result = supabase_manager.admin_client.table('documents')\
            .select('*')\
            .eq('id', document_id)\
            .single()\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Document not found")
        
        document = result.data
        
        # Check if user has access
        if document.get('user_id') != auth.user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        return DocumentResponse(
            id=document['id'],
            title=document['title'],
            file_name=document['file_name'],
            file_type=document['file_type'],
            file_size=document['file_size'],
            status=document['status'],
            chunk_count=document.get('chunk_count', 0),
            created_at=document['created_at'],
            processing_metadata=document.get('processing_metadata')
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch document: {str(e)}")


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    auth = Depends(require_user_auth)
):
    """Delete a document"""
    try:
        success = await document_processor.delete_document(
            document_id=document_id,
            user_id=auth.user_id
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Document not found or access denied")
        
        return {"success": True, "message": "Document deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {str(e)}")


@router.post("/documents/{document_id}/reprocess")
async def reprocess_document(
    document_id: str,
    auth = Depends(require_user_auth)
):
    """Reprocess a document (for stuck or failed documents)"""
    try:
        from app.integrations.supabase_client import supabase_manager
        
        # Get document info
        result = supabase_manager.admin_client.table('documents')\
            .select('*')\
            .eq('id', document_id)\
            .single()\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Document not found")
        
        document = result.data
        
        # Check access
        if document.get('user_id') != auth.user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Get file path from metadata
        metadata = document.get('metadata', {})
        file_path = metadata.get('file_path')
        
        # Update status to processing
        supabase_manager.admin_client.table('documents')\
            .update({'status': 'processing'})\
            .eq('id', document_id)\
            .execute()

        # If we have the original file, re-run normal processing, otherwise rebuild from chunks
        if file_path and os.path.exists(file_path):
            import asyncio
            asyncio.create_task(
                document_processor._process_document_async(
                    document_id=document_id,
                    file_path=file_path,
                    agent_ids=[],  # Keep existing agent assignments
                    client_id=document.get('client_id') or None
                )
            )
        else:
            rebuilt = await document_processor.reprocess_from_chunks(
                document_id=document_id,
                client_id=document.get('client_id') or None,
                supabase=supabase_manager.admin_client
            )
            if not rebuilt:
                raise HTTPException(status_code=400, detail="Failed to reprocess document from stored chunks")
        
        return {"success": True, "message": "Document reprocessing started"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reprocess document: {str(e)}")


@router.get("/stats")
async def get_knowledge_base_stats(
    client_id: Optional[str] = None,
    auth = Depends(require_user_auth)
):
    """Get knowledge base statistics"""
    try:
        from app.integrations.supabase_client import supabase_manager
        
        # Base query
        query = supabase_manager.admin_client.table('documents').select('status', count='exact')
        
        if client_id:
            query = query.eq('client_id', client_id)
        
        # Get total count
        total_result = query.execute()
        total_count = total_result.count
        
        # Get count by status
        stats = {}
        for status in ['processing', 'ready', 'error']:
            status_query = supabase_manager.admin_client.table('documents').select('id', count='exact').eq('status', status)
            if client_id:
                status_query = status_query.eq('client_id', client_id)
            
            status_result = status_query.execute()
            stats[status] = status_result.count
        
        return {
            'total_documents': total_count,
            'processing': stats.get('processing', 0),
            'ready': stats.get('ready', 0),
            'error': stats.get('error', 0)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


class EmbeddingMigrationRequest(BaseModel):
    client_id: str


class EmbeddingMigrationStatus(BaseModel):
    job_id: str
    status: str
    total_documents: int
    processed_documents: int
    failed_documents: int
    progress_percent: float
    error: Optional[str] = None


# In-memory storage for migration job status (in production, use Redis)
_migration_jobs: dict = {}


@router.post("/embedding-migration/start")
async def start_embedding_migration(
    request: EmbeddingMigrationRequest,
    background_tasks: BackgroundTasks,
    auth = Depends(require_user_auth)
):
    """
    Start bulk embedding migration for all documents of a client.
    This re-embeds all documents with the current embedding configuration.
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        from app.integrations.supabase_client import supabase_manager

        client_id = request.client_id
        job_id = str(uuid.uuid4())

        # Get all documents for this client that are in 'ready' status
        docs_result = supabase_manager.admin_client.table('documents')\
            .select('id, title, status')\
            .eq('client_id', client_id)\
            .eq('status', 'ready')\
            .execute()

        documents = docs_result.data or []
        total_docs = len(documents)

        if total_docs == 0:
            return {
                "success": True,
                "job_id": job_id,
                "message": "No documents to migrate",
                "total_documents": 0
            }

        # Initialize job status
        _migration_jobs[job_id] = {
            "status": "running",
            "total_documents": total_docs,
            "processed_documents": 0,
            "failed_documents": 0,
            "progress_percent": 0.0,
            "error": None,
            "client_id": client_id
        }

        # Run migration in background
        async def run_migration():
            processed = 0
            failed = 0

            for doc in documents:
                try:
                    doc_id = doc['id']
                    # Use reprocess_from_chunks which re-generates embeddings
                    success = await document_processor.reprocess_from_chunks(
                        document_id=doc_id,
                        client_id=client_id,
                        supabase=supabase_manager.admin_client
                    )
                    if success:
                        processed += 1
                    else:
                        failed += 1
                        logger.warning(f"Migration failed for document {doc_id}")

                except Exception as e:
                    failed += 1
                    logger.error(f"Migration error for document {doc.get('id')}: {e}")

                # Update job status
                total_processed = processed + failed
                _migration_jobs[job_id]["processed_documents"] = processed
                _migration_jobs[job_id]["failed_documents"] = failed
                _migration_jobs[job_id]["progress_percent"] = (total_processed / total_docs) * 100

            # Mark job complete
            _migration_jobs[job_id]["status"] = "completed" if failed == 0 else "completed_with_errors"

        background_tasks.add_task(run_migration)

        return {
            "success": True,
            "job_id": job_id,
            "message": f"Embedding migration started for {total_docs} documents",
            "total_documents": total_docs
        }

    except Exception as e:
        logger.error(f"Failed to start embedding migration: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start embedding migration: {str(e)}")


@router.get("/embedding-migration/status/{job_id}", response_model=EmbeddingMigrationStatus)
async def get_embedding_migration_status(
    job_id: str,
    auth = Depends(require_user_auth)
):
    """Get the status of an embedding migration job"""
    if job_id not in _migration_jobs:
        raise HTTPException(status_code=404, detail="Migration job not found")

    job = _migration_jobs[job_id]
    return EmbeddingMigrationStatus(
        job_id=job_id,
        status=job["status"],
        total_documents=job["total_documents"],
        processed_documents=job["processed_documents"],
        failed_documents=job["failed_documents"],
        progress_percent=job["progress_percent"],
        error=job.get("error")
    )


@router.get("/agents")
async def get_available_agents(
    client_id: str,
    auth = Depends(require_user_auth)
):
    """Get list of available agents for document assignment"""
    try:
        from app.integrations.supabase_client import supabase_manager
        
        result = supabase_manager.admin_client.table('agents')\
            .select('id, name, slug, enabled')\
            .eq('client_id', client_id)\
            .eq('enabled', True)\
            .order('name')\
            .execute()
        
        return result.data if result.data else []
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get agents: {str(e)}")


@router.post("/documents/{document_id}/assign-agents")
async def assign_document_to_agents(
    document_id: str,
    agent_ids: List[str],
    auth = Depends(require_user_auth)
):
    """Assign document to specific agents"""
    try:
        from app.integrations.supabase_client import supabase_manager
        
        # Verify document exists and user has access
        doc_result = supabase_manager.admin_client.table('documents')\
            .select('*')\
            .eq('id', document_id)\
            .single()\
            .execute()
        
        if not doc_result.data:
            raise HTTPException(status_code=404, detail="Document not found")
        
        if doc_result.data.get('user_id') != auth.user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Remove existing assignments
        supabase_manager.admin_client.table('agent_documents')\
            .delete()\
            .eq('document_id', document_id)\
            .execute()
        
        # Add new assignments
        if agent_ids:
            assignments = []
            for agent_id in agent_ids:
                assignments.append({
                    'agent_id': agent_id,
                    'document_id': document_id,
                    'access_type': 'read',
                    'enabled': True
                })
            
            supabase_manager.admin_client.table('agent_documents')\
                .insert(assignments)\
                .execute()
        
        return {"success": True, "message": "Document assigned to agents successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to assign document: {str(e)}")


@router.post("/fix-unassigned-documents")
async def fix_unassigned_documents(
    client_id: str,
    auth = Depends(require_user_auth)
):
    """
    Fix documents that aren't assigned to any agents.
    Assigns all unassigned 'ready' documents to all agents for the client.
    """
    try:
        from app.services.client_connection_manager import get_connection_manager

        connection_manager = get_connection_manager()
        client_supabase = await connection_manager.get_client_connection(client_id)

        if not client_supabase:
            raise HTTPException(status_code=404, detail="Client not found or no database connection")

        # Get all agents for this client
        agents_result = client_supabase.table('agents').select('id, name').execute()
        if not agents_result.data:
            return {"success": True, "message": "No agents found", "fixed_count": 0}

        agent_ids = [a['id'] for a in agents_result.data]

        # Get all ready documents
        docs_result = client_supabase.table('documents').select('id, title').eq('status', 'ready').execute()
        if not docs_result.data:
            return {"success": True, "message": "No documents found", "fixed_count": 0}

        # Get existing assignments
        existing_assignments_result = client_supabase.table('agent_documents').select('agent_id, document_id').execute()
        existing_set = set()
        if existing_assignments_result.data:
            for a in existing_assignments_result.data:
                existing_set.add((str(a['agent_id']), str(a['document_id'])))

        # Find and fix unassigned combinations
        new_assignments = []
        for doc in docs_result.data:
            doc_id = str(doc['id'])
            for agent_id in agent_ids:
                if (str(agent_id), doc_id) not in existing_set:
                    new_assignments.append({
                        'agent_id': agent_id,
                        'document_id': doc['id'],
                        'access_type': 'read',
                        'enabled': True
                    })

        if new_assignments:
            # Insert in batches of 100 to avoid timeout
            batch_size = 100
            for i in range(0, len(new_assignments), batch_size):
                batch = new_assignments[i:i + batch_size]
                try:
                    client_supabase.table('agent_documents').insert(batch).execute()
                except Exception as batch_error:
                    logger.warning(f"Batch insert error (continuing): {batch_error}")

        return {
            "success": True,
            "message": f"Fixed {len(new_assignments)} document-agent assignments",
            "fixed_count": len(new_assignments),
            "agents_count": len(agent_ids),
            "documents_count": len(docs_result.data)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fix unassigned documents: {str(e)}")
