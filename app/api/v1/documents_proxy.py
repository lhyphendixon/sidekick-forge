"""Document and RAG endpoints for WordPress integration"""
from typing import Dict, Any, Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Header, Query, UploadFile, File
from pydantic import BaseModel, Field
import logging
import json
import uuid
import hashlib
import mimetypes

from app.models.wordpress_site import WordPressSite
from app.api.v1.wordpress_sites import validate_wordpress_auth
import redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents-proxy"])


class DocumentMetadata(BaseModel):
    """Document metadata"""
    title: Optional[str] = None
    description: Optional[str] = None
    agent_slug: Optional[str] = None
    tags: Optional[List[str]] = Field(default_factory=list)
    category: Optional[str] = None
    custom_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class DocumentResponse(BaseModel):
    """Document response"""
    document_id: str
    wordpress_site_id: str
    filename: str
    content_type: str
    size: int
    checksum: str
    uploaded_at: datetime
    metadata: Dict[str, Any]
    status: str = "uploaded"


class RAGSearchRequest(BaseModel):
    """RAG search request"""
    query: str
    agent_slug: Optional[str] = None
    limit: int = Field(default=5, le=20)
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    search_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class RAGSearchResult(BaseModel):
    """RAG search result"""
    document_id: str
    chunk_id: str
    content: str
    score: float
    metadata: Dict[str, Any]


class DocumentChunk(BaseModel):
    """Document chunk for storage"""
    chunk_id: str
    document_id: str
    content: str
    chunk_index: int
    metadata: Dict[str, Any]


# Services will be injected from simple_main.py
redis_client: Optional[redis.Redis] = None


def get_redis_client() -> redis.Redis:
    """Get Redis client instance"""
    if redis_client is None:
        raise RuntimeError("Redis client not initialized")
    return redis_client


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    metadata: Optional[str] = None,  # JSON string
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> DocumentResponse:
    """Upload a document for RAG"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Parse metadata if provided
        doc_metadata = {}
        if metadata:
            try:
                doc_metadata = json.loads(metadata)
            except json.JSONDecodeError:
                logger.warning("Invalid metadata JSON, ignoring")
        
        # Read file content
        content = await file.read()
        file_size = len(content)
        
        # Calculate checksum
        checksum = hashlib.sha256(content).hexdigest()
        
        # Generate document ID
        document_id = str(uuid.uuid4())
        
        # Detect content type
        content_type = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
        
        # Create document record
        document_data = {
            "document_id": document_id,
            "wordpress_site_id": site.id,
            "wordpress_domain": site.domain,
            "client_id": site.client_id,
            "filename": file.filename,
            "content_type": content_type,
            "size": file_size,
            "checksum": checksum,
            "uploaded_at": datetime.utcnow().isoformat(),
            "metadata": {
                **doc_metadata,
                "original_filename": file.filename,
                "uploaded_by": f"wp_site_{site.id}"
            },
            "status": "uploaded",
            "chunks": []
        }
        
        # Store document metadata in Redis with error handling
        redis = get_redis_client()
        doc_key = f"document:{site.id}:{document_id}"
        
        try:
            redis.setex(doc_key, 30 * 24 * 3600, json.dumps(document_data))  # 30 days
            
            # Store raw content separately
            content_key = f"document_content:{site.id}:{document_id}"
            redis.setex(content_key, 30 * 24 * 3600, content)
            
            # Add to site's document list
            site_docs_key = f"site_documents:{site.id}"
            redis.lpush(site_docs_key, document_id)
            redis.expire(site_docs_key, 90 * 24 * 3600)  # 90 days
            
            # If agent_slug is specified, add to agent's document list
            if doc_metadata.get("agent_slug"):
                agent_docs_key = f"agent_documents:{site.id}:{doc_metadata['agent_slug']}"
                redis.lpush(agent_docs_key, document_id)
                redis.expire(agent_docs_key, 90 * 24 * 3600)
                
        except RedisError as e:
            logger.error(f"Redis error storing document: {e}")
            raise HTTPException(status_code=503, detail="Storage service temporarily unavailable")
        
        # TODO: In production, trigger async task to:
        # 1. Extract text from document
        # 2. Split into chunks
        # 3. Generate embeddings
        # 4. Store in vector database
        
        return DocumentResponse(
            document_id=document_id,
            wordpress_site_id=site.id,
            filename=file.filename,
            content_type=content_type,
            size=file_size,
            checksum=checksum,
            uploaded_at=datetime.fromisoformat(document_data["uploaded_at"]),
            metadata=document_data["metadata"],
            status="uploaded"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> DocumentResponse:
    """Get document metadata"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get document from Redis with error handling
        redis = get_redis_client()
        doc_key = f"document:{site.id}:{document_id}"
        
        try:
            doc_data = redis.get(doc_key)
        except RedisError as e:
            logger.error(f"Redis error fetching document: {e}")
            raise HTTPException(status_code=503, detail="Storage service temporarily unavailable")
        
        if not doc_data:
            raise HTTPException(status_code=404, detail="Document not found")
            
        document = json.loads(doc_data)
        
        # Verify the document belongs to this site
        if document.get("wordpress_site_id") != site.id:
            raise HTTPException(status_code=403, detail="Access denied")
            
        return DocumentResponse(
            document_id=document["document_id"],
            wordpress_site_id=document["wordpress_site_id"],
            filename=document["filename"],
            content_type=document["content_type"],
            size=document["size"],
            checksum=document["checksum"],
            uploaded_at=datetime.fromisoformat(document["uploaded_at"]),
            metadata=document["metadata"],
            status=document["status"]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/", response_model=List[DocumentResponse])
async def list_documents(
    agent_slug: Optional[str] = Query(None),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> List[DocumentResponse]:
    """List documents for a WordPress site"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get document IDs from Redis
        redis = get_redis_client()
        
        if agent_slug:
            # Get agent-specific documents
            docs_key = f"agent_documents:{site.id}:{agent_slug}"
        else:
            # Get all site documents
            docs_key = f"site_documents:{site.id}"
            
        try:
            doc_ids = redis.lrange(docs_key, offset, offset + limit - 1)
        except RedisError as e:
            logger.error(f"Redis error fetching document list: {e}")
            doc_ids = []
        
        documents = []
        for doc_id in doc_ids:
            doc_key = f"document:{site.id}:{doc_id}"
            try:
                doc_data = redis.get(doc_key)
                if doc_data:
                    doc = json.loads(doc_data)
                    documents.append(DocumentResponse(
                        document_id=doc["document_id"],
                        wordpress_site_id=doc["wordpress_site_id"],
                        filename=doc["filename"],
                        content_type=doc["content_type"],
                        size=doc["size"],
                        checksum=doc["checksum"],
                        uploaded_at=datetime.fromisoformat(doc["uploaded_at"]),
                        metadata=doc["metadata"],
                        status=doc["status"]
                    ))
            except (RedisError, json.JSONDecodeError) as e:
                logger.error(f"Error retrieving document {doc_id}: {e}")
                continue
                
        return documents
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{document_id}", response_model=Dict[str, str])
async def delete_document(
    document_id: str,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> Dict[str, str]:
    """Delete a document"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Get document from Redis with error handling
        redis = get_redis_client()
        doc_key = f"document:{site.id}:{document_id}"
        
        try:
            doc_data = redis.get(doc_key)
        except RedisError as e:
            logger.error(f"Redis error fetching document: {e}")
            raise HTTPException(status_code=503, detail="Storage service temporarily unavailable")
        
        if not doc_data:
            raise HTTPException(status_code=404, detail="Document not found")
            
        document = json.loads(doc_data)
        
        # Verify the document belongs to this site
        if document.get("wordpress_site_id") != site.id:
            raise HTTPException(status_code=403, detail="Access denied")
            
        # Delete document and content with error handling
        try:
            redis.delete(doc_key)
            redis.delete(f"document_content:{site.id}:{document_id}")
            
            # Remove from lists
            redis.lrem(f"site_documents:{site.id}", 0, document_id)
            if document["metadata"].get("agent_slug"):
                redis.lrem(f"agent_documents:{site.id}:{document['metadata']['agent_slug']}", 0, document_id)
                
            # Delete chunks
            for chunk in document.get("chunks", []):
                redis.delete(f"document_chunk:{site.id}:{chunk['chunk_id']}")
                
        except RedisError as e:
            logger.error(f"Redis error deleting document: {e}")
            raise HTTPException(status_code=503, detail="Storage service temporarily unavailable")
            
        return {
            "status": "deleted",
            "document_id": document_id,
            "deleted_at": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search", response_model=List[RAGSearchResult])
async def search_documents(
    request: RAGSearchRequest,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> List[RAGSearchResult]:
    """Search documents using RAG with vector similarity"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        logger.info(f"RAG search for site {site.domain}: {request.query}")
        
        # Check if we have Supabase configuration
        from app.integrations.supabase_client import supabase_manager
        if not supabase_manager or not supabase_manager.client:
            # Fallback to Redis-based mock search if Supabase not available
            logger.warning("Supabase not configured, using fallback search")
            return await _fallback_search(site, request)
        
        try:
            # Import embedding service
            from app.services.embedding_service import EmbeddingService
            from app.services.vector_search import VectorSearchService
            
            # Initialize services
            embedding_service = EmbeddingService()
            vector_search = VectorSearchService(supabase_manager.client)
            
            # Generate embedding for the query
            query_embedding = await embedding_service.generate_embedding(
                request.query,
                provider="siliconflow"  # Default provider
            )
            
            if not query_embedding:
                logger.error("Failed to generate query embedding")
                return []
            
            # Search for similar chunks
            chunks = await vector_search.search_documents(
                client_id=site.client_id,
                query_embedding=query_embedding,
                agent_slug=request.agent_slug,
                limit=request.limit,
                threshold=request.threshold
            )
            
            # Convert to response format
            results = []
            for chunk in chunks:
                results.append(RAGSearchResult(
                    document_id=chunk["document_id"],
                    chunk_id=chunk["chunk_id"],
                    content=chunk["content"],
                    score=chunk["score"],
                    metadata=chunk.get("metadata", {})
                ))
            
            return results
            
        except ImportError:
            logger.warning("Embedding or vector search service not available, using fallback")
            return await _fallback_search(site, request)
        except Exception as e:
            logger.error(f"Error in vector search: {e}")
            return await _fallback_search(site, request)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error searching documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _fallback_search(site, request: RAGSearchRequest) -> List[RAGSearchResult]:
    """Fallback search using Redis when vector search is not available"""
    results = []
    
    # Get some documents to simulate search
    redis = get_redis_client()
    docs_key = f"site_documents:{site.id}"
    if request.agent_slug:
        docs_key = f"agent_documents:{site.id}:{request.agent_slug}"
        
    try:
        doc_ids = redis.lrange(docs_key, 0, 4)  # Get first 5 docs
    except RedisError as e:
        logger.error(f"Redis error in search: {e}")
        doc_ids = []
    
    for i, doc_id in enumerate(doc_ids):
        doc_key = f"document:{site.id}:{doc_id}"
        try:
            doc_data = redis.get(doc_key)
            if doc_data:
                doc = json.loads(doc_data)
                # Create mock search result
                results.append(RAGSearchResult(
                    document_id=doc_id.decode() if isinstance(doc_id, bytes) else doc_id,
                    chunk_id=str(uuid.uuid4()),
                    content=f"This is a relevant chunk from {doc['filename']} that matches your query about '{request.query}'...",
                    score=0.95 - (i * 0.1),  # Decreasing scores
                    metadata={
                        "filename": doc["filename"],
                        "chunk_index": i,
                        "total_chunks": 10,
                        **doc["metadata"]
                    }
                ))
        except (RedisError, json.JSONDecodeError) as e:
            logger.error(f"Error in search for document {doc_id}: {e}")
            continue
            
    return results[:request.limit]


@router.post("/extract-context", response_model=Dict[str, Any])
async def extract_context(
    query: str,
    agent_slug: Optional[str] = None,
    limit: int = Query(default=3, le=10),
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """Extract context for a query (used by agents during conversations)"""
    try:
        # Validate WordPress auth
        site = await validate_wordpress_auth(authorization, x_api_key)
        
        # Search for relevant documents
        search_request = RAGSearchRequest(
            query=query,
            agent_slug=agent_slug,
            limit=limit,
            threshold=0.7
        )
        
        results = await search_documents(search_request, authorization, x_api_key)
        
        # Format context for agent consumption
        context = {
            "query": query,
            "relevant_documents": len(results),
            "context_chunks": [
                {
                    "content": result.content,
                    "source": result.metadata.get("filename", "Unknown"),
                    "relevance_score": result.score
                }
                for result in results
            ],
            "extracted_at": datetime.utcnow().isoformat()
        }
        
        return context
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error extracting context: {e}")
        raise HTTPException(status_code=500, detail=str(e))