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

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.document_processor import document_processor
from app.core.dependencies import get_client_service
from app.middleware.auth import require_user_auth

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
    auth = Depends(require_user_auth)
):
    """Upload a single document for processing"""
    try:
        # Validate file type
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")
        
        file_extension = Path(file.filename).suffix.lower().lstrip('.')
        allowed_types = ['pdf', 'doc', 'docx', 'txt', 'md']
        
        if file_extension not in allowed_types:
            raise HTTPException(
                status_code=400, 
                detail=f"Unsupported file type. Allowed: {', '.join(allowed_types)}"
            )
        
        # Validate file size (50MB max)
        max_size = 50 * 1024 * 1024
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
                            detail=f"File too large. Maximum size is {max_size // (1024*1024)}MB"
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
                client_id=client_id
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
                allowed_types = ['pdf', 'doc', 'docx', 'txt', 'md']
                
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
                    if len(content) > 50 * 1024 * 1024:  # 50MB
                        results.append({
                            'filename': file.filename,
                            'success': False,
                            'error': 'File too large (50MB max)'
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
    client_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    auth = Depends(require_user_auth)
):
    """Get list of documents"""
    try:
        documents = await document_processor.get_documents(
            user_id=auth.user_id,
            client_id=client_id,
            status=status,
            limit=limit
        )
        
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
        
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=400, detail="Original file not found")
        
        # Update status to processing
        supabase_manager.admin_client.table('documents')\
            .update({'status': 'processing'})\
            .eq('id', document_id)\
            .execute()
        
        # Start reprocessing
        import asyncio
        asyncio.create_task(
            document_processor._process_document_async(
                document_id=document_id,
                file_path=file_path,
                agent_ids=[]  # Keep existing agent assignments
            )
        )
        
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