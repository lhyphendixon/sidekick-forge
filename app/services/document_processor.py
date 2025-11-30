#!/usr/bin/env python3
"""
Document Processor Service for Autonomite Agent
Handles text extraction, chunking, and vectorization of uploaded documents
"""

import asyncio
import hashlib
import time
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Union
from pathlib import Path
import mimetypes
import shutil

# Text extraction libraries
import PyPDF2
try:
    from docx import Document as DocxDocument
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False
    DocxDocument = None
import magic

# Try to import textract, but make it optional
try:
    import textract
    HAS_TEXTRACT = True
except ImportError:
    HAS_TEXTRACT = False
    textract = None

from app.constants import DOCUMENT_MAX_UPLOAD_BYTES
from app.integrations.supabase_client import supabase_manager
from app.services.ai_processor import ai_processor

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """Handles document processing pipeline"""
    
    def __init__(self):
        self.supabase = None  # Will be initialized on first use
        self.ai_processor = ai_processor
        # Cache client-specific Supabase connections with the credentials used to build them
        # Structure: { client_id: { 'client': SupabaseClient, 'url': str, 'key': str, 'settings': Dict, 'fetched_at': str } }
        self.client_supabase_connections = {}
        # Limit concurrent document processing tasks to avoid CPU exhaustion
        max_concurrency = int(os.getenv("DOCUMENT_PROCESSOR_MAX_CONCURRENCY", "2"))
        self._processing_semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self.supported_types = {
            'pdf': self._extract_pdf_text,
            'txt': self._extract_text_file,
            'md': self._extract_text_file,
            'doc': self._extract_doc_text,
            'docx': self._extract_docx_text,
            'srt': self._extract_srt_text,
        }
        self.max_file_size = DOCUMENT_MAX_UPLOAD_BYTES
        self.chunk_size = 500  # words
        self.chunk_overlap = 50  # words
        # Default vector dimension expected by Supabase columns (use 1024 everywhere)
        self.default_embedding_dim = int(os.getenv("EMBEDDING_VECTOR_DIM", "1024"))

        default_upload_root = Path(__file__).resolve().parents[2] / 'data' / 'uploads'
        upload_root = os.getenv("DOCUMENT_UPLOAD_ROOT", str(default_upload_root))
        self.upload_root = Path(upload_root)
        try:
            self.upload_root.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Unable to ensure upload root {self.upload_root}: {e}")
        
    def _ensure_supabase(self):
        """Ensure Supabase client is initialized"""
        if self.supabase is None:
            self.supabase = supabase_manager.admin_client
        return self.supabase
    
    async def _get_client_context(self, client_id: str):
        """Get (supabase_client, client_settings) for a client, caching results."""
        if not client_id:
            return self._ensure_supabase(), None

        cached = self.client_supabase_connections.get(client_id)
        if cached and cached.get('client'):
            # If we already cached settings, reuse them to avoid re-syncing on every chunk
            if cached.get('settings') is not None:
                return cached['client'], cached.get('settings')

        try:
            from app.core.dependencies import get_client_service
            from supabase import create_client
            from app.config import settings as app_settings

            client_service = get_client_service()
            # Avoid triggering auto-sync on every document access; we only need stored settings here.
            client = await client_service.get_client(client_id, auto_sync=False)

            if not client:
                logger.error(f"Client {client_id} not found in database")
                return None, None

            # Extract Supabase credentials using the shared client service helper so we inherit
            # any normalization/fallback logic (e.g., when tenant-specific projects are offline).
            supabase_config = await client_service.get_client_supabase_config(client_id, auto_sync=False)
            if isinstance(client, dict):
                client_settings = client.get('settings', {})
            else:
                client_settings = getattr(client, 'settings', {})

            if hasattr(client_settings, 'dict'):
                client_settings = client_settings.dict()

            if not supabase_config:
                logger.warning(f"Client {client_id} missing Supabase credentials")
                return None, client_settings or {}

            # Re-use cached connection if credentials match
            supabase_url = supabase_config["url"]
            service_key = supabase_config["service_role_key"]

            if supabase_config.get("_fallback"):
                logger.warning(
                    "Client %s Supabase configuration unreachable; using platform Supabase fallback",
                    client_id,
                )

            if cached and cached.get('url') == supabase_url and cached.get('key') == service_key:
                cached['settings'] = client_settings or {}
                cached['fetched_at'] = datetime.now(timezone.utc).isoformat()
                return cached.get('client'), cached.get('settings')

            # Build Supabase client (or reuse admin) based on project
            if supabase_url == app_settings.supabase_url:
                client_supabase = self._ensure_supabase()
                used_key = app_settings.supabase_service_role_key
            else:
                client_supabase = create_client(supabase_url, service_key)
                used_key = service_key

            self.client_supabase_connections[client_id] = {
                'client': client_supabase,
                'url': supabase_url,
                'key': used_key,
                'settings': client_settings or {},
                'fetched_at': datetime.now(timezone.utc).isoformat(),
            }

            return client_supabase, client_settings or {}

        except Exception as e:
            logger.error(f"Error creating client Supabase connection for {client_id}: {e}")
            return None, None

    async def _get_client_supabase(self, client_id: str):
        """Compatibility helper that returns only the Supabase client."""
        supabase, _ = await self._get_client_context(client_id)
        return supabase

    def _calculate_checksum(self, file_path: str) -> str:
        """Calculate SHA256 checksum for deduplication."""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _stage_file_for_processing(self, source_path: str, client_id: Optional[str] = None) -> str:
        """Move uploaded file to a persistent staging area to survive queue delays."""
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Uploaded file missing at {source_path}")

        date_prefix = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        dest_dir = self.upload_root / (client_id or "platform") / date_prefix
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create staging directory {dest_dir}: {e}")
            raise

        dest_name = f"{uuid.uuid4().hex}{source.suffix}"
        dest_path = dest_dir / dest_name

        try:
            shutil.move(str(source), str(dest_path))
        except Exception as e:
            logger.error(f"Failed to move uploaded file to staging area: {e}")
            raise

        # Attempt to remove empty temp folder
        try:
            parent = source.parent
            if parent.exists() and parent != dest_dir and not any(parent.iterdir()):
                parent.rmdir()
        except Exception:
            pass

        return str(dest_path)

    def _remove_temp_file(self, file_path: str):
        """Remove temporary upload file created by the uploader."""
        if not file_path:
            return
        try:
            path_obj = Path(file_path)
            if path_obj.exists():
                path_obj.unlink()
            parent = path_obj.parent
            if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except Exception as e:
            logger.warning(f"Failed to remove temporary upload {file_path}: {e}")

    async def _find_existing_ready_document(
        self,
        supabase_client,
        checksum: str,
        title: str,
        file_size: int,
    ) -> Optional[Dict[str, Any]]:
        """Look for an existing ready document that matches upload (by checksum or title/size)."""
        try:
            # Prefer checksum match when available on stored metadata
            if checksum:
                try:
                    result = supabase_client.table('documents') \
                        .select('id,status,metadata,chunk_count') \
                        .eq('status', 'ready') \
                        .filter('metadata->>checksum', 'eq', checksum) \
                        .limit(1) \
                        .execute()
                    if result.data:
                        return result.data[0]
                except Exception as e:
                    logger.debug(f"Checksum lookup failed (falling back to title/size): {e}")

            # Fallback to title + file size match
            result = supabase_client.table('documents') \
                .select('id,status,chunk_count') \
                .eq('status', 'ready') \
                .eq('title', title) \
                .eq('file_size', file_size) \
                .limit(1) \
                .execute()
            if result.data:
                return result.data[0]

        except Exception as e:
            logger.error(f"Error checking for existing document: {e}")

        return None

    def _cleanup_staged_file(self, file_path: str):
        """Remove staged file and prune empty directories back to the staging root."""
        if not file_path:
            return

        path_obj = Path(file_path)
        if path_obj.exists():
            try:
                path_obj.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete staged file {file_path}: {e}")

        try:
            current = path_obj.parent
            while current != self.upload_root and self.upload_root in current.parents:
                current.rmdir()
                current = current.parent
        except OSError:
            pass
    
    async def process_uploaded_file(
        self, 
        file_path: str,
        title: str,
        description: str = "",
        user_id: str = None,
        agent_ids: List[str] = None,
        client_id: str = None,
        replace_existing: bool = False
    ) -> Dict[str, Any]:
        """Process an uploaded file through the complete pipeline"""
        try:
            # Validate file
            validation_result = await self._validate_file(file_path)
            if not validation_result['valid']:
                return {
                    'success': False,
                    'error': validation_result['error']
                }
            
            file_info = validation_result['file_info']
            file_name = file_info.get('name') or Path(file_path).name
            checksum = self._calculate_checksum(file_path)

            # Prepare Supabase client early for dedupe checks/assignments
            supabase_client = None
            if client_id:
                supabase_client = await self._get_client_supabase(client_id)
                if not supabase_client:
                    logger.error(f"Could not get Supabase connection for client {client_id}")
                    return {
                        'success': False,
                        'error': 'Failed to get client Supabase connection'
                    }
            else:
                supabase_client = self._ensure_supabase()

            existing_document = await self._find_existing_ready_document(
                supabase_client,
                checksum,
                title,
                file_info['size'],
            )

            if existing_document:
                if replace_existing:
                    try:
                        supabase_client.table('documents').delete().eq('id', existing_document['id']).execute()
                        logger.info("Deleted existing document %s to replace with new upload", existing_document['id'])
                    except Exception as delete_error:
                        logger.error(f"Failed to delete existing document {existing_document['id']}: {delete_error}")
                        return {
                            'success': False,
                            'error': f'Failed to replace existing document: {delete_error}'
                        }
                else:
                    logger.info(
                        "Skipping upload for '%s' (duplicate ready document %s)",
                        title,
                        existing_document['id'],
                    )

                    if agent_ids:
                        await self._assign_document_to_agents(
                            str(existing_document['id']),
                            agent_ids,
                            client_id,
                            supabase=supabase_client,
                        )

                    self._remove_temp_file(file_path)

                    return {
                        'success': True,
                        'document_id': str(existing_document['id']),
                        'status': existing_document.get('status', 'ready'),
                        'duplicate': True,
                        'skipped': True,
                        'message': 'Document already processed; reusing existing copy',
                        'queued': False,
                    }

            try:
                file_path = self._stage_file_for_processing(file_path, client_id)
            except Exception:
                return {
                    'success': False,
                    'error': 'Failed to stage uploaded file for processing'
                }

            # Normalize user_id to a UUID if possible; otherwise store as None
            normalized_user_id = None
            if user_id:
                try:
                    normalized_user_id = str(uuid.UUID(str(user_id)))
                except Exception:
                    logger.warning(
                        "Non-UUID user_id '%s' encountered during document upload; storing as NULL.",
                        user_id,
                    )

            if normalized_user_id and supabase_client:
                try:
                    lookup_response = (
                        supabase_client
                        .table('users')
                        .select('id')
                        .eq('id', normalized_user_id)
                        .limit(1)
                        .execute()
                    )
                except Exception as lookup_error:
                    logger.warning(
                        "Failed to verify user %s in client %s Supabase; defaulting to system upload: %s",
                        normalized_user_id,
                        client_id or "platform",
                        lookup_error,
                    )
                    normalized_user_id = None
                else:
                    data = getattr(lookup_response, "data", None)
                    if not data:
                        logger.warning(
                            "User %s not found in client %s Supabase; storing knowledge base upload without user attribution.",
                            normalized_user_id,
                            client_id or "platform",
                        )
                        normalized_user_id = None

            # Create document record
            document_id = await self._create_document_record(
                file_path=file_path,
                title=title,
                description=description,
                file_info=file_info,
                user_id=normalized_user_id,
                client_id=client_id,
                checksum=checksum,
                supabase=supabase_client,
                file_name=file_name,
            )
            
            if not document_id:
                return {
                    'success': False,
                    'error': 'Failed to create document record'
                }
            
            # Start async processing
            asyncio.create_task(
                self._process_document_async(document_id, file_path, agent_ids, client_id)
            )
            
            return {
                'success': True,
                'document_id': document_id,
                'status': 'processing'
            }
            
        except Exception as e:
            logger.error(f"Error processing uploaded file: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def _validate_file(self, file_path: str) -> Dict[str, Any]:
        """Validate uploaded file"""
        try:
            if not os.path.exists(file_path):
                return {
                    'valid': False,
                    'error': 'File not found'
                }
            
            # Check file size
            file_size = os.path.getsize(file_path)
            if file_size > self.max_file_size:
                return {
                    'valid': False,
                    'error': f'File too large. Maximum size is {self.max_file_size // (1024*1024)}MB'
                }
            
            # Detect file type
            file_path_obj = Path(file_path)
            file_extension = file_path_obj.suffix.lower().lstrip('.')
            
            # Use python-magic for more accurate detection
            try:
                mime_type = magic.from_file(file_path, mime=True)
            except:
                mime_type = mimetypes.guess_type(file_path)[0]
            
            # Validate against supported types
            if file_extension not in self.supported_types:
                return {
                    'valid': False,
                    'error': f'Unsupported file type: {file_extension}'
                }
            
            return {
                'valid': True,
                'file_info': {
                    'name': file_path_obj.name,
                    'size': file_size,
                    'extension': file_extension,
                    'mime_type': mime_type
                }
            }
            
        except Exception as e:
            logger.error(f"Error validating file: {e}")
            return {
                'valid': False,
                'error': f'File validation error: {str(e)}'
            }
    
    async def _create_document_record(
        self,
        file_path: str,
        title: str,
        description: str,
        file_info: Dict,
        user_id: str = None,
        client_id: str = None,
        checksum: str = '',
        supabase=None,
        file_name: str = None,
    ) -> str:
        """Create document record in client-specific Supabase"""
        try:
            file_name = file_name or file_info.get('name') or Path(file_path).name
            document_data = {
                'title': title,
                'file_name': file_name,
                'file_type': file_info['extension'],
                'file_size': file_info['size'],
                'user_id': user_id,
                'status': 'processing',
                'processing_status': 'processing',
                'document_type': 'knowledge_base',
                'metadata': {
                    'file_path': file_path,
                    'mime_type': file_info.get('mime_type'),
                    'upload_date': datetime.now(timezone.utc).isoformat(),
                    'processing_started': datetime.now(timezone.utc).isoformat(),
                    'description': description if description else '',  # Store description in metadata
                    'checksum': checksum,
                }
            }
            
            # Use client-specific Supabase if client_id provided
            supabase_client = supabase
            if client_id and supabase_client is None:
                supabase_client = await self._get_client_supabase(client_id)
                if not supabase_client:
                    logger.error(f"Could not get Supabase connection for client {client_id}")
                    return None
            elif supabase_client is None:
                supabase_client = self._ensure_supabase()
            
            # Don't include ID for clients using auto-incrementing bigint IDs
            # The database will auto-generate the ID
            result = supabase_client.table('documents').insert(document_data).execute()
            
            if result.data:
                # Handle both string and integer IDs
                doc_id = result.data[0]['id']
                return str(doc_id)  # Always return as string for consistency
            else:
                logger.error(f"Failed to create document record: {result}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating document record: {e}")
            return None
    
    async def _process_document_async(
        self, 
        document_id: str, 
        file_path: str, 
        agent_ids: List[str] = None,
        client_id: str = None
    ):
        """Async document processing pipeline"""
        async with self._processing_semaphore:
            supabase_client = None
            client_settings = None
            truncated_chunks = False
            total_chunks_before_truncation = 0
            success = False
            try:
                logger.info(f"Starting async processing for document {document_id}")

                if client_id:
                    supabase_client, client_settings = await self._get_client_context(client_id)
                    if not supabase_client:
                        await self._update_document_status(
                            document_id,
                            'error',
                            'Failed to initialize client Supabase connection',
                            client_id,
                            supabase=None,
                        )
                        return
                else:
                    supabase_client = self._ensure_supabase()

                # Extract text
                extracted_text = await self._extract_text(file_path)
                if not extracted_text:
                    await self._update_document_status(
                        document_id,
                        'error',
                        'Failed to extract text',
                        client_id,
                        supabase=supabase_client,
                    )
                    return

                # Clean and chunk text
                cleaned_text = self._clean_text(extracted_text)
                chunks = self._split_text_into_chunks(cleaned_text)
                total_chunks_before_truncation = len(chunks)

                max_chunks = int(os.getenv("DOCUMENT_PROCESSOR_MAX_CHUNKS", "250"))
                if total_chunks_before_truncation > max_chunks:
                    truncated_chunks = True
                    logger.warning(
                        f"Document {document_id} produced {total_chunks_before_truncation} chunks; truncating to {max_chunks}"
                    )
                    chunks = chunks[:max_chunks]

                logger.info(f"Created {len(chunks)} chunks for document {document_id}")

                # Generate embeddings for document and chunks
                document_embeddings = await self._generate_document_embeddings(
                    cleaned_text[:2000], client_settings
                )

                # Process chunks
                processed_chunks = []
                for i, chunk in enumerate(chunks):
                    try:
                        chunk_embeddings = await self._generate_chunk_embeddings(chunk, client_settings)
                        chunk_id = await self._store_document_chunk(
                            document_id=document_id,
                            chunk_text=chunk,
                            chunk_index=i,
                            embeddings=chunk_embeddings,
                            client_id=client_id,
                            supabase=supabase_client,
                        )

                        if chunk_id:
                            processed_chunks.append({
                                'id': chunk_id,
                                'index': i,
                                'text': chunk,
                                'has_embeddings': bool(chunk_embeddings)
                            })
                    except Exception as e:
                        logger.warning(f"Failed to process chunk {i} for document {document_id}: {e}")

                extra_metadata = {
                    'truncated_chunks': truncated_chunks,
                    'original_chunk_count': total_chunks_before_truncation,
                }

                # Update document with results
                await self._finalize_document_processing(
                    document_id=document_id,
                    content=cleaned_text,
                    embeddings=document_embeddings,
                    chunk_count=len(processed_chunks),
                    agent_ids=agent_ids,
                    client_id=client_id,
                    supabase=supabase_client,
                    extra_metadata=extra_metadata,
                )

                logger.info(f"Completed processing for document {document_id}")
                success = True

            except Exception as e:
                logger.error(f"Error in async document processing for {document_id}: {e}")
                await self._update_document_status(
                    document_id,
                    'error',
                    str(e),
                    client_id,
                    supabase=supabase_client,
                )
            finally:
                # Only clean up staged file if processing succeeded so we can retry with the original file on failure
                if success:
                    try:
                        self._cleanup_staged_file(file_path)
                    except Exception as cleanup_error:
                        logger.warning(f"Failed to clean up staged file {file_path}: {cleanup_error}")
    
    async def _extract_text(self, file_path: str) -> str:
        """Extract text from file based on type"""
        try:
            file_extension = Path(file_path).suffix.lower().lstrip('.')
            
            if file_extension in self.supported_types:
                extractor = self.supported_types[file_extension]
                return await extractor(file_path)
            else:
                raise ValueError(f"Unsupported file type: {file_extension}")
                
        except Exception as e:
            logger.error(f"Error extracting text from {file_path}: {e}")
            return ""
    
    async def _extract_pdf_text(self, file_path: str) -> str:
        """Extract text from PDF file"""
        try:
            text = ""
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                for page_num in range(len(pdf_reader.pages)):
                    page = pdf_reader.pages[page_num]
                    text += page.extract_text()
            return text
        except Exception as e:
            # Fallback to textract if available
            if HAS_TEXTRACT:
                try:
                    return textract.process(file_path).decode('utf-8')
                except Exception as e2:
                    logger.error(f"Both PyPDF2 and textract failed for {file_path}: {e}, {e2}")
                    return ""
            else:
                logger.error(f"PyPDF2 failed for {file_path}: {e} (textract not available)")
                return ""
    
    async def _extract_docx_text(self, file_path: str) -> str:
        """Extract text from DOCX file"""
        if not HAS_DOCX:
            logger.error(f"python-docx not available, cannot extract text from {file_path}")
            return ""
        try:
            doc = DocxDocument(file_path)
            text = []
            for paragraph in doc.paragraphs:
                text.append(paragraph.text)
            return '\n'.join(text)
        except Exception as e:
            logger.error(f"Error extracting DOCX text from {file_path}: {e}")
            return ""
    
    async def _extract_doc_text(self, file_path: str) -> str:
        """Extract text from DOC file using textract"""
        if not HAS_TEXTRACT:
            logger.warning(f"Cannot extract from DOC file {file_path}: textract not available")
            return ""
        
        try:
            return textract.process(file_path).decode('utf-8')
        except Exception as e:
            logger.error(f"Error extracting DOC text from {file_path}: {e}")
            return ""
    
    async def _extract_text_file(self, file_path: str) -> str:
        """Extract text from plain text files"""
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                return file.read()
        except UnicodeDecodeError:
            # Try with different encoding
            try:
                with open(file_path, 'r', encoding='latin-1') as file:
                    return file.read()
            except Exception as e:
                logger.error(f"Error reading text file {file_path}: {e}")
                return ""
        except Exception as e:
            logger.error(f"Error reading text file {file_path}: {e}")
            return ""

    async def _extract_srt_text(self, file_path: str) -> str:
        """Extract readable text from .srt subtitle files by stripping indices/timestamps."""
        def _load_lines() -> List[str]:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read().splitlines()
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='latin-1') as f:
                    return f.read().splitlines()
            except Exception as e:
                logger.error(f"Error reading srt file {file_path}: {e}")
                return []

        lines = _load_lines()
        cleaned_lines: List[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Skip numeric sequence lines
            if line.isdigit():
                continue
            # Skip timestamp lines containing '-->'
            if '-->' in line:
                continue
            cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)
    
    def _clean_text(self, text: str) -> str:
        """Clean extracted text"""
        if not isinstance(text, str):
            text = str(text)
        
        # Remove excessive whitespace
        import re
        text = re.sub(r'\s+', ' ', text)
        
        # Remove control characters
        text = re.sub(r'[\x00-\x1F\x7F]', '', text)
        
        # Trim
        text = text.strip()
        
        return text
    
    def _split_text_into_chunks(self, text: str) -> List[str]:
        """Split text into overlapping chunks"""
        words = text.split()
        total_words = len(words)
        chunks = []
        
        start = 0
        while start < total_words:
            end = min(start + self.chunk_size, total_words)
            chunk_words = words[start:end]
            chunk_text = ' '.join(chunk_words)
            
            if chunk_text.strip():
                chunks.append(chunk_text)
            
            # Move start position, accounting for overlap
            start = end - self.chunk_overlap
            
            # If we're at the end, break
            if end >= total_words:
                break
        
        return chunks
    
    async def _generate_document_embeddings(self, text: str, client_settings: Optional[Dict] = None) -> Optional[List[float]]:
        """Generate embeddings for document-level content"""
        try:
            if not text.strip():
                return None
            
            # Use AI processor to generate embeddings with client settings
            embeddings = await self.ai_processor.generate_embeddings(text, context='document', client_settings=client_settings)
            return self._normalize_embedding_length(embeddings, client_settings)
            
        except Exception as e:
            logger.error(f"Error generating document embeddings: {e}")
            return None
    
    async def _generate_chunk_embeddings(self, chunk_text: str, client_settings: Optional[Dict] = None) -> Optional[List[float]]:
        """Generate embeddings for a text chunk"""
        try:
            if not chunk_text.strip():
                return None
            
            # Use AI processor to generate embeddings with client settings
            embeddings = await self.ai_processor.generate_embeddings(chunk_text, context='chunk', client_settings=client_settings)
            return self._normalize_embedding_length(embeddings, client_settings)
            
        except Exception as e:
            logger.error(f"Error generating chunk embeddings: {e}")
            return None

    async def reprocess_from_chunks(
        self,
        document_id: str,
        client_id: Optional[str] = None,
        supabase=None,
    ) -> bool:
        """Rebuild a document from stored chunks when original file is missing."""
        supabase_client = supabase
        if client_id and supabase_client is None:
            supabase_client, _ = await self._get_client_context(client_id)
        elif supabase_client is None:
            supabase_client = self._ensure_supabase()

        if supabase_client is None:
            return False

        try:
            chunks_resp = supabase_client.table('document_chunks').select('id,content,chunk_index').eq('document_id', document_id).order('chunk_index').execute()
            chunks = chunks_resp.data or []
            if not chunks:
                await self._update_document_status(
                    document_id,
                    'error',
                    'Reprocess failed: no stored chunks to rebuild content',
                    client_id,
                    supabase=supabase_client,
                )
                return False

            content = '\n'.join([(c.get('content') or '') for c in chunks])
            cleaned = self._clean_text(content)
            # Get client settings for embeddings
            _, client_settings = await self._get_client_context(client_id) if client_id else (None, None)
            doc_emb = await self._generate_document_embeddings(cleaned[:2000], client_settings)

            # Refresh chunk embeddings
            for c in chunks:
                emb = await self._generate_chunk_embeddings(c.get('content') or '', client_settings)
                supabase_client.table('document_chunks').update({'embeddings': emb}).eq('id', c['id']).execute()

            await self._finalize_document_processing(
                document_id=document_id,
                content=cleaned,
                embeddings=doc_emb,
                chunk_count=len(chunks),
                agent_ids=[],
                client_id=client_id,
                supabase=supabase_client,
                extra_metadata={'reembedded_from_chunks': True, 'recovered_at': time.time()},
            )
            return True
        except Exception as e:
            logger.error(f"reprocess_from_chunks failed for {document_id}: {e}")
            await self._update_document_status(
                document_id,
                'error',
                f'Reprocess failed: {e}',
                client_id,
                supabase=supabase_client,
            )
            return False

    def _normalize_embedding_length(self, embeddings: Optional[List[float]], client_settings: Optional[Dict] = None) -> Optional[List[float]]:
        """Pad or trim embeddings to match the expected vector dimension."""
        if embeddings is None:
            return None
        try:
            target_dim = self._get_target_embedding_dim(client_settings)
            vec = list(embeddings)
            if len(vec) == target_dim:
                return vec
            if len(vec) > target_dim:
                return vec[:target_dim]
            # Pad with zeros if shorter
            vec.extend([0.0] * (target_dim - len(vec)))
            return vec
        except Exception as e:
            logger.warning(f"Failed to normalize embedding length: {e}")
            return embeddings

    def _get_target_embedding_dim(self, client_settings: Optional[Dict]) -> int:
        """Determine target embedding dimension from client settings or default."""
        target = None
        if client_settings:
            # Handle both dict and object forms
            try:
                if isinstance(client_settings, dict):
                    embedding_cfg = client_settings.get('embedding', {})
                    target = embedding_cfg.get('dimension')
                else:
                    embedding_cfg = getattr(client_settings, 'embedding', None)
                    target = getattr(embedding_cfg, 'dimension', None) if embedding_cfg else None
            except Exception:
                target = None
        return int(target) if target else self.default_embedding_dim
    
    async def _store_document_chunk(
        self,
        document_id: str,
        chunk_text: str,
        chunk_index: int,
        embeddings: Optional[List[float]] = None,
        client_id: str = None,
        supabase=None
    ) -> Optional[str]:
        """Store a document chunk in Supabase"""
        try:
            # For clients with bigint document IDs, convert to int
            try:
                doc_id_for_chunk = int(document_id)
            except ValueError:
                # If it's not a valid int, keep as string (UUID case)
                doc_id_for_chunk = document_id
            
            chunk_data = {
                'id': str(uuid.uuid4()),
                'document_id': doc_id_for_chunk,
                'content': chunk_text,
                'chunk_index': chunk_index,
                'embeddings': embeddings,
                'chunk_metadata': {
                    'word_count': len(chunk_text.split()),
                    'character_count': len(chunk_text),
                    'has_embeddings': bool(embeddings)
                }
            }
            
            # Use client-specific Supabase if client_id provided
            supabase_client = supabase
            if client_id and supabase_client is None:
                supabase_client, _ = await self._get_client_context(client_id)
                if not supabase_client:
                    logger.error(f"Could not get Supabase connection for client {client_id}")
                    return None
            elif supabase_client is None:
                supabase_client = self._ensure_supabase()
            
            result = supabase_client.table('document_chunks').insert(chunk_data).execute()
            
            if result.data:
                return result.data[0]['id']
            else:
                logger.error(f"Failed to store chunk for document {document_id}")
                return None
                
        except Exception as e:
            logger.error(f"Error storing document chunk: {e}")
            return None
    
    async def _finalize_document_processing(
        self,
        document_id: str,
        content: str,
        embeddings: Optional[List[float]],
        chunk_count: int,
        agent_ids: List[str] = None,
        client_id: str = None,
        supabase=None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ):
        """Finalize document processing and update status"""
        try:
            metadata = {
                'processed_at': datetime.now(timezone.utc).isoformat(),
                'text_length': len(content),
                'chunk_count': chunk_count,
                'has_document_embeddings': bool(embeddings)
            }

            if extra_metadata:
                metadata.update(extra_metadata)

            update_data = {
                'content': content,
                'status': 'ready',
                'processing_status': 'completed',
                'chunk_count': chunk_count,
                'processing_metadata': metadata
            }
            
            if embeddings:
                update_data['embeddings'] = embeddings
            
            # Update document using appropriate Supabase connection
            if client_id and supabase is None:
                supabase, _ = await self._get_client_context(client_id)
                if not supabase:
                    logger.error(f"Could not get Supabase connection for client {client_id}")
                    raise Exception("Failed to get client Supabase connection")
            elif supabase is None:
                supabase = self._ensure_supabase()
            
            # Convert document_id to int if it's numeric
            try:
                doc_id_for_update = int(document_id)
            except ValueError:
                doc_id_for_update = document_id
            
            result = supabase.table('documents').update(update_data).eq('id', doc_id_for_update).execute()
            
            if not result.data:
                raise Exception("Failed to update document status")
            
            # Handle agent permissions
            if agent_ids:
                await self._assign_document_to_agents(
                    document_id,
                    agent_ids,
                    client_id,
                    supabase=supabase,
                )
            
        except Exception as e:
            logger.error(f"Error finalizing document processing: {e}")
            await self._update_document_status(
                document_id,
                'error',
                str(e),
                client_id,
                supabase=supabase,
            )
    
    async def _assign_document_to_agents(
        self,
        document_id: str,
        agent_ids: List[str],
        client_id: str = None,
        supabase=None,
    ):
        """Assign document to specific agents"""
        try:
            assigned_agent_ids: List[str] = []
            for agent_id in agent_ids:
                agent_doc_data = {
                    'agent_id': agent_id,
                    'document_id': document_id,
                    'access_type': 'read',
                    'enabled': True
                }
                
                # Use client-specific Supabase if client_id provided
                supabase_client = supabase
                if client_id and supabase_client is None:
                    supabase_client, _ = await self._get_client_context(client_id)
                    if not supabase_client:
                        logger.error(f"Could not get Supabase connection for client {client_id}")
                        continue
                elif supabase_client is None:
                    supabase_client = self._ensure_supabase()

                # Skip duplicate assignments
                try:
                    existing = supabase_client.table('agent_documents') \
                        .select('id') \
                        .eq('agent_id', agent_id) \
                        .eq('document_id', document_id) \
                        .limit(1) \
                        .execute()
                    if existing.data:
                        continue
                except Exception as e:
                    logger.debug(f"Assignment lookup failed for agent {agent_id}, doc {document_id}: {e}")

                supabase_client.table('agent_documents').insert(agent_doc_data).execute()
                assigned_agent_ids.append(agent_id)

            if assigned_agent_ids:
                await self._sync_document_agent_permissions(
                    document_id=document_id,
                    agent_ids=assigned_agent_ids,
                    client_id=client_id,
                    supabase=supabase_client,
                )
                
        except Exception as e:
            logger.error(f"Error assigning document to agents: {e}")
    
    async def _sync_document_agent_permissions(
        self,
        document_id: str,
        agent_ids: List[str],
        client_id: Optional[str] = None,
        supabase=None,
    ):
        """Ensure the document.agent_permissions array stays in sync with agent assignments."""
        try:
            if not agent_ids:
                return

            supabase_client = supabase
            if client_id and supabase_client is None:
                supabase_client, _ = await self._get_client_context(client_id)
            elif supabase_client is None:
                supabase_client = self._ensure_supabase()

            if not supabase_client:
                logger.warning(
                    "Unable to update agent_permissions for document %s; Supabase client missing.",
                    document_id,
                )
                return

            agent_rows = (
                supabase_client
                .table('agents')
                .select('id,slug')
                .in_('id', agent_ids)
                .execute()
            )

            slugs = {row.get('slug') for row in getattr(agent_rows, 'data', []) if row.get('slug')}
            if not slugs:
                return

            try:
                doc_id_for_update = int(document_id)
            except (TypeError, ValueError):
                doc_id_for_update = document_id

            existing = (
                supabase_client
                .table('documents')
                .select('agent_permissions')
                .eq('id', doc_id_for_update)
                .limit(1)
                .execute()
            )

            current = set()
            if getattr(existing, 'data', None):
                doc_row = existing.data[0]
                current_values = doc_row.get('agent_permissions') or []
                current = {str(val) for val in current_values if val}

            merged = sorted(current | {str(slug) for slug in slugs})

            supabase_client.table('documents').update(
                {'agent_permissions': merged}
            ).eq('id', doc_id_for_update).execute()

        except Exception as exc:
            logger.warning(
                "Failed to sync agent_permissions for document %s: %s",
                document_id,
                exc,
            )
    
    async def _update_document_status(
        self,
        document_id: str,
        status: str,
        error_message: str = None,
        client_id: str = None,
        supabase=None,
    ):
        """Update document status"""
        try:
            update_data = {'status': status}

            if status == 'ready':
                update_data['processing_status'] = 'completed'
            elif status == 'error':
                update_data['processing_status'] = 'failed'
            elif status == 'processing':
                update_data['processing_status'] = 'processing'
            
            if error_message:
                update_data['processing_metadata'] = {
                    'error_message': error_message,
                    'error_at': datetime.now(timezone.utc).isoformat()
                }
            
            # Use client-specific Supabase if client_id provided
            supabase_client = supabase
            if client_id and supabase_client is None:
                supabase_client, _ = await self._get_client_context(client_id)
                if not supabase_client:
                    logger.error(f"Could not get Supabase connection for client {client_id}")
                    return
            elif supabase_client is None:
                supabase_client = self._ensure_supabase()
            
            # Convert document_id to int if it's numeric
            try:
                doc_id_for_update = int(document_id)
            except ValueError:
                doc_id_for_update = document_id
            
            supabase_client.table('documents').update(update_data).eq('id', doc_id_for_update).execute()

        except Exception as e:
            logger.error(f"Error updating document status: {e}")
    
    async def get_documents(
        self,
        user_id: str = None,
        client_id: str = None,
        status: str = None,
        limit: int = 50,
        offset: int = 0,
        with_count: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get documents with optional filtering and pagination"""
        try:
            supabase = None
            if client_id:
                logger.info(f"Fetching documents for client {client_id}")
                supabase, _ = await self._get_client_context(client_id)
                if not supabase:
                    logger.warning(
                        f"Client {client_id} Supabase connection unavailable; falling back to platform instance"
                    )
                    supabase = self._ensure_supabase()
            else:
                logger.info("No client_id provided, using platform Supabase")
                supabase = self._ensure_supabase()

            # Build base query
            query = supabase.table('documents').select('*', count='exact')

            if user_id:
                query = query.eq('user_id', user_id)

            if status:
                query = query.eq('status', status)

            safe_offset = max(0, int(offset or 0))
            safe_limit = max(1, int(limit or 50))
            range_end = safe_offset + safe_limit - 1

            result = query.order('created_at', desc=True).range(safe_offset, range_end).execute()

            documents = result.data if result.data else []
            if client_id:
                for doc in documents:
                    doc['client_id'] = client_id

            if not with_count:
                return documents

            total_count = getattr(result, 'count', None)
            if total_count is None:
                total_count = len(documents)

            # Compute total size and collect all filenames separately (use generous range to avoid default limits)
            size_query = supabase.table('documents').select('file_size,file_name')
            if user_id:
                size_query = size_query.eq('user_id', user_id)
            if status:
                size_query = size_query.eq('status', status)

            size_result = size_query.range(0, 50000).execute()
            total_size = 0
            all_filenames = []
            if size_result and getattr(size_result, 'data', None):
                for row in size_result.data:
                    if not isinstance(row, dict):
                        continue
                    total_size += (row.get('file_size') or 0)
                    if row.get('file_name'):
                        all_filenames.append(str(row['file_name']).lower())

            return documents, total_count, total_size, all_filenames

        except Exception as e:
            logger.error(f"Error fetching documents: {e}")
            return [] if not with_count else ([], 0, 0, [])
    
    async def delete_document(self, document_id: str, user_id: str = None, client_id: str = None) -> bool:
        """Delete a document and its chunks from the appropriate client database"""
        try:
            # If no client_id provided, we need to find which client owns this document
            # This is a multi-tenant system where each client has their own database
            if not client_id:
                # Try to find the document in each client's database
                from app.core.dependencies import get_client_service
                client_service = get_client_service()
                clients = await client_service.get_all_clients()
                
                for client in clients:
                    try:
                        client_supabase = await self._get_client_supabase(client.id)
                        if client_supabase:
                            # Check if document exists in this client's database
                            check_result = client_supabase.table('documents').select('id').eq('id', document_id).execute()
                            if check_result.data:
                                client_id = client.id
                                logger.info(f"Found document {document_id} in client {client_id} database")
                                break
                    except Exception as e:
                        logger.debug(f"Document not in client {client.id}: {e}")
                        continue
                
                if not client_id:
                    logger.error(f"Document {document_id} not found in any client database")
                    return False
            
            # Get the client-specific Supabase connection
            supabase = await self._get_client_supabase(client_id)
            if not supabase:
                logger.error(f"Could not get Supabase connection for client {client_id}")
                return False
            
            # Get document info first
            doc_result = supabase.table('documents').select('*').eq('id', document_id).execute()
            
            if not doc_result.data:
                logger.error(f"Document {document_id} not found in client {client_id} database")
                return False
            
            document = doc_result.data[0] if isinstance(doc_result.data, list) else doc_result.data
            
            # Check ownership if user_id provided
            if user_id and document.get('user_id') != user_id:
                logger.error(f"User {user_id} does not own document {document_id}")
                return False
            
            # Delete physical file if it exists
            metadata = document.get('metadata', {})
            file_path = metadata.get('file_path')
            if file_path:
                existed = os.path.exists(file_path)
                self._cleanup_staged_file(file_path)
                if existed:
                    logger.info(f"Deleted staged file: {file_path}")
            
            # Delete from database (cascades to chunks due to foreign key)
            delete_result = supabase.table('documents').delete().eq('id', document_id).execute()
            
            logger.info(f"Successfully deleted document {document_id} from client {client_id} database")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting document {document_id}: {e}")
            return False


# Create singleton instance
document_processor = DocumentProcessor()
