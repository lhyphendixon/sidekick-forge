#!/usr/bin/env python3
"""
Document Processor Service for Autonomite Agent
Handles text extraction, chunking, and vectorization of uploaded documents
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Union
from pathlib import Path
import mimetypes

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

from app.integrations.supabase_client import supabase_manager
from app.services.ai_processor import ai_processor

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """Handles document processing pipeline"""
    
    def __init__(self):
        self.supabase = None  # Will be initialized on first use
        self.ai_processor = ai_processor
        # Cache client-specific Supabase connections with the credentials used to build them
        # Structure: { client_id: { 'client': SupabaseClient, 'url': str, 'key': str } }
        self.client_supabase_connections = {}
        self.supported_types = {
            'pdf': self._extract_pdf_text,
            'txt': self._extract_text_file,
            'md': self._extract_text_file,
            'doc': self._extract_doc_text,
            'docx': self._extract_docx_text,
        }
        self.max_file_size = 50 * 1024 * 1024  # 50MB
        self.chunk_size = 500  # words
        self.chunk_overlap = 50  # words
        
    def _ensure_supabase(self):
        """Ensure Supabase client is initialized"""
        if self.supabase is None:
            self.supabase = supabase_manager.admin_client
        return self.supabase
    
    async def _get_client_supabase(self, client_id: str):
        """Get client-specific Supabase connection, refreshing cache if credentials changed"""
        cached = self.client_supabase_connections.get(client_id)
        
        try:
            logger.info(f"Creating new Supabase connection for client {client_id}")
            from app.core.dependencies import get_client_service
            from supabase import create_client
            
            # Get client details
            client_service = get_client_service()
            client = await client_service.get_client(client_id)
            
            if not client:
                logger.error(f"Client {client_id} not found in database")
                return None
            
            # Get client's Supabase credentials
            # Check if credentials are stored directly on client record (new structure)
            if isinstance(client, dict):
                supabase_url = client.get('supabase_url', '')
                service_key = client.get('supabase_service_role_key', '')
            else:
                supabase_url = getattr(client, 'supabase_url', '')
                service_key = getattr(client, 'supabase_service_role_key', '')
            
            # Fall back to checking settings.supabase (old structure) if not found
            if not supabase_url or not service_key:
                client_settings = client.get('settings', {}) if isinstance(client, dict) else getattr(client, 'settings', {})
                supabase_settings = client_settings.get('supabase', {}) if isinstance(client_settings, dict) else getattr(client_settings, 'supabase', {})
                
                if not supabase_url:
                    supabase_url = supabase_settings.get('url', '') if isinstance(supabase_settings, dict) else getattr(supabase_settings, 'url', '')
                if not service_key:
                    service_key = supabase_settings.get('service_role_key', '') if isinstance(supabase_settings, dict) else getattr(supabase_settings, 'service_role_key', '')
            
            if not supabase_url or not service_key:
                logger.warning(f"Client {client_id} missing Supabase credentials")
                return None
            
            # Check if this client is using the main Supabase instance
            from app.config import settings
            if supabase_url == settings.supabase_url:
                logger.info(f"Client {client_id} uses main Supabase instance, using admin client")
                client_supabase = self._ensure_supabase()
                used_key = settings.supabase_service_role_key
            else:
                client_supabase = create_client(supabase_url, service_key)
                used_key = service_key

            # If we had a cached connection, ensure credentials match; if not, overwrite cache
            if cached:
                cached_url = cached.get('url')
                cached_key = cached.get('key')
                if cached_url == supabase_url and cached_key == used_key:
                    # Keep existing cached client
                    return cached.get('client')

            # Store/refresh cache entry
            self.client_supabase_connections[client_id] = {
                'client': client_supabase,
                'url': supabase_url,
                'key': used_key,
            }

            return client_supabase
            
        except Exception as e:
            logger.error(f"Error creating client Supabase connection for {client_id}: {e}")
            return None
    
    async def process_uploaded_file(
        self, 
        file_path: str,
        title: str,
        description: str = "",
        user_id: str = None,
        agent_ids: List[str] = None,
        client_id: str = None
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
            
            # Create document record
            document_id = await self._create_document_record(
                file_path=file_path,
                title=title,
                description=description,
                file_info=file_info,
                user_id=user_id,
                client_id=client_id
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
        client_id: str = None
    ) -> str:
        """Create document record in client-specific Supabase"""
        try:
            document_data = {
                'title': title,
                'file_name': file_info['name'],
                'file_type': file_info['extension'],
                'file_size': file_info['size'],
                'user_id': user_id,
                'status': 'processing',
                'document_type': 'knowledge_base',
                'metadata': {
                    'file_path': file_path,
                    'mime_type': file_info.get('mime_type'),
                    'upload_date': datetime.now(timezone.utc).isoformat(),
                    'processing_started': datetime.now(timezone.utc).isoformat(),
                    'description': description if description else ''  # Store description in metadata
                }
            }
            
            # Use client-specific Supabase if client_id provided
            if client_id:
                supabase = await self._get_client_supabase(client_id)
                if not supabase:
                    logger.error(f"Could not get Supabase connection for client {client_id}")
                    return None
            else:
                supabase = self._ensure_supabase()
            
            # Don't include ID for clients using auto-incrementing bigint IDs
            # The database will auto-generate the ID
            result = supabase.table('documents').insert(document_data).execute()
            
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
        try:
            logger.info(f"Starting async processing for document {document_id}")
            
            # Extract text
            extracted_text = await self._extract_text(file_path)
            if not extracted_text:
                await self._update_document_status(document_id, 'error', 'Failed to extract text', client_id)
                return
            
            # Clean and chunk text
            cleaned_text = self._clean_text(extracted_text)
            chunks = self._split_text_into_chunks(cleaned_text)
            
            logger.info(f"Created {len(chunks)} chunks for document {document_id}")
            
            # Get client settings for embedding generation
            client_settings = None
            if client_id:
                try:
                    from app.core.dependencies import get_client_service
                    client_service = get_client_service()
                    client = await client_service.get_client(client_id)
                    if client:
                        client_settings = client.get('settings', {}) if isinstance(client, dict) else getattr(client, 'settings', {})
                        # Convert to dict if it's a model object
                        if hasattr(client_settings, 'dict'):
                            client_settings = client_settings.dict()
                        
                        logger.info(f"[DEBUG] Got client settings for {client_id}")
                        logger.info(f"[DEBUG] Embedding provider: {client_settings.get('embedding', {}).get('provider')}")
                        logger.info(f"[DEBUG] API keys available: {list(client_settings.get('api_keys', {}).keys())}")
                        logger.info(f"[DEBUG] SiliconFlow key present: {'siliconflow_api_key' in client_settings.get('api_keys', {})}")
                except Exception as e:
                    logger.warning(f"Could not get client settings for embeddings: {e}")
            
            # Generate embeddings for document and chunks
            document_embeddings = await self._generate_document_embeddings(cleaned_text[:2000], client_settings)  # Use first 2000 chars for doc-level embedding
            
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
                        client_id=client_id
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
            
            # Update document with results
            await self._finalize_document_processing(
                document_id=document_id,
                content=cleaned_text,
                embeddings=document_embeddings,
                chunk_count=len(processed_chunks),
                agent_ids=agent_ids,
                client_id=client_id
            )
            
            logger.info(f"Completed processing for document {document_id}")
            
            # Clean up temporary file after processing
            try:
                if os.path.exists(file_path):
                    os.unlink(file_path)
                    logger.debug(f"Cleaned up temporary file: {file_path}")
            except Exception as cleanup_error:
                logger.warning(f"Failed to clean up temporary file {file_path}: {cleanup_error}")
            
        except Exception as e:
            logger.error(f"Error in async document processing for {document_id}: {e}")
            await self._update_document_status(document_id, 'error', str(e), client_id)
            
            # Clean up temporary file on error too
            try:
                if os.path.exists(file_path):
                    os.unlink(file_path)
            except:
                pass
    
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
            return embeddings
            
        except Exception as e:
            logger.error(f"Error generating document embeddings: {e}")
            return None
    
    async def _generate_chunk_embeddings(self, chunk_text: str, client_settings: Optional[Dict] = None) -> Optional[List[float]]:
        """Generate embeddings for a text chunk"""
        try:
            if not chunk_text.strip():
                return None
            
            # Use AI processor to generate embeddings with client settings
            embeddings = await self.ai_processor.generate_embeddings(chunk_text, context='document', client_settings=client_settings)
            return embeddings
            
        except Exception as e:
            logger.error(f"Error generating chunk embeddings: {e}")
            return None
    
    async def _store_document_chunk(
        self,
        document_id: str,
        chunk_text: str,
        chunk_index: int,
        embeddings: Optional[List[float]] = None,
        client_id: str = None
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
            if client_id:
                supabase = await self._get_client_supabase(client_id)
                if not supabase:
                    logger.error(f"Could not get Supabase connection for client {client_id}")
                    return None
            else:
                supabase = self._ensure_supabase()
            
            result = supabase.table('document_chunks').insert(chunk_data).execute()
            
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
        client_id: str = None
    ):
        """Finalize document processing and update status"""
        try:
            update_data = {
                'content': content,
                'status': 'ready',
                'chunk_count': chunk_count,
                'processing_metadata': {
                    'processed_at': datetime.now(timezone.utc).isoformat(),
                    'text_length': len(content),
                    'chunk_count': chunk_count,
                    'has_document_embeddings': bool(embeddings)
                }
            }
            
            if embeddings:
                update_data['embeddings'] = embeddings
            
            # Update document using appropriate Supabase connection
            if client_id:
                supabase = await self._get_client_supabase(client_id)
                if not supabase:
                    logger.error(f"Could not get Supabase connection for client {client_id}")
                    raise Exception("Failed to get client Supabase connection")
            else:
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
                await self._assign_document_to_agents(document_id, agent_ids, client_id)
            
        except Exception as e:
            logger.error(f"Error finalizing document processing: {e}")
            await self._update_document_status(document_id, 'error', str(e), client_id)
    
    async def _assign_document_to_agents(self, document_id: str, agent_ids: List[str], client_id: str = None):
        """Assign document to specific agents"""
        try:
            for agent_id in agent_ids:
                agent_doc_data = {
                    'agent_id': agent_id,
                    'document_id': document_id,
                    'access_type': 'read',
                    'enabled': True
                }
                
                # Use client-specific Supabase if client_id provided
                if client_id:
                    supabase = await self._get_client_supabase(client_id)
                    if not supabase:
                        logger.error(f"Could not get Supabase connection for client {client_id}")
                        continue
                else:
                    supabase = self._ensure_supabase()
                
                supabase.table('agent_documents').insert(agent_doc_data).execute()
                
        except Exception as e:
            logger.error(f"Error assigning document to agents: {e}")
    
    async def _update_document_status(self, document_id: str, status: str, error_message: str = None, client_id: str = None):
        """Update document status"""
        try:
            update_data = {'status': status}
            
            if error_message:
                update_data['processing_metadata'] = {
                    'error_message': error_message,
                    'error_at': datetime.now(timezone.utc).isoformat()
                }
            
            # Use client-specific Supabase if client_id provided
            if client_id:
                supabase = await self._get_client_supabase(client_id)
                if not supabase:
                    logger.error(f"Could not get Supabase connection for client {client_id}")
                    return
            else:
                supabase = self._ensure_supabase()
            
            # Convert document_id to int if it's numeric
            try:
                doc_id_for_update = int(document_id)
            except ValueError:
                doc_id_for_update = document_id
            
            supabase.table('documents').update(update_data).eq('id', doc_id_for_update).execute()
            
        except Exception as e:
            logger.error(f"Error updating document status: {e}")
    
    async def get_documents(
        self, 
        user_id: str = None, 
        client_id: str = None, 
        status: str = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get documents with optional filtering"""
        try:
            # Use client-specific Supabase if client_id provided
            if client_id:
                logger.info(f"Fetching documents for client {client_id}")
                supabase = await self._get_client_supabase(client_id)
                if not supabase:
                    logger.error(f"Could not get Supabase connection for client {client_id}, falling back to master")
                    supabase = self._ensure_supabase()
                else:
                    logger.info(f"Using client-specific Supabase for client {client_id}")
            else:
                logger.info("No client_id provided, using master Supabase")
                supabase = self._ensure_supabase()
            
            query = supabase.table('documents').select('*')
            
            if user_id:
                query = query.eq('user_id', user_id)
            
            if status:
                query = query.eq('status', status)
            
            result = query.order('created_at', desc=True).limit(limit).execute()
            
            # Add client_id to each document for frontend filtering
            documents = result.data if result.data else []
            if client_id:
                for doc in documents:
                    doc['client_id'] = client_id
            
            return documents
            
        except Exception as e:
            logger.error(f"Error fetching documents: {e}")
            return []
    
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
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"Deleted physical file: {file_path}")
                except Exception as e:
                    logger.warning(f"Could not delete physical file {file_path}: {e}")
            
            # Delete from database (cascades to chunks due to foreign key)
            delete_result = supabase.table('documents').delete().eq('id', document_id).execute()
            
            logger.info(f"Successfully deleted document {document_id} from client {client_id} database")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting document {document_id}: {e}")
            return False


# Create singleton instance
document_processor = DocumentProcessor()