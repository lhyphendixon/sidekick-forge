"""
RAG Citations Service

Provides document retrieval with structured citation metadata for accurate source attribution.
Implements the no-fallback RAG policy and multi-tenant data isolation.
"""
import logging
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from uuid import UUID
from dataclasses import dataclass
from datetime import datetime

from app.integrations.supabase_client import supabase_manager
from app.core.dependencies import get_client_service
from app.utils.exceptions import RAGError, DatabaseError

logger = logging.getLogger(__name__)

@dataclass
class CitationChunk:
    """Single citation chunk with full metadata"""
    chunk_id: str
    doc_id: str
    title: str
    source_url: str
    source_type: str
    chunk_index: int
    page_number: Optional[int]
    char_start: Optional[int]
    char_end: Optional[int]
    content: str
    similarity: float

@dataclass
class RAGRetrievalResult:
    """Result from RAG retrieval including context and citations"""
    context_for_llm: str
    citations: List[CitationChunk]
    total_chunks_found: int
    processing_time_ms: float


class RAGCitationsService:
    """Service for RAG document retrieval with citation tracking"""
    
    def __init__(self):
        self.max_context_tokens = 8000  # Conservative token limit
        self.chars_per_token = 4  # Rough approximation
        self.max_context_chars = self.max_context_tokens * self.chars_per_token
    
    async def retrieve_with_citations(
        self,
        query: str,
        client_id: str,
        dataset_ids: List[str],
        top_k: int = 12,
        similarity_threshold: Optional[float] = None,
        max_documents: int = 4,
        max_chunks: int = 8
    ) -> RAGRetrievalResult:
        """
        Retrieve document chunks for RAG with full citation metadata.
        
        Args:
            query: User query text
            client_id: Client identifier for multi-tenant isolation
            dataset_ids: List of dataset UUIDs to search within
            top_k: Initial number of chunks to retrieve for reranking
            similarity_threshold: Minimum similarity score (0.0-1.0)
            max_documents: Maximum unique documents to include
            max_chunks: Maximum chunks to include in final context
        
        Returns:
            RAGRetrievalResult with context and citations
            
        Raises:
            RAGError: If retrieval fails or returns no results (no-fallback policy)
        """
        start_time = datetime.now()
        
        try:
            # Step 1: Get client's Supabase configuration for multi-tenant isolation
            client_config = await self._get_client_supabase_config(client_id)
            client_supabase = await self._create_client_supabase(client_config)
            
            # Step 2: Generate embedding for the query
            query_embedding = await self._generate_embedding(query, client_config)
            
            # Step 3: Perform vector search using the RPC function
            raw_chunks = await self._search_document_chunks(
                client_supabase=client_supabase,
                query_embedding=query_embedding,
                dataset_ids=[UUID(did) for did in dataset_ids],
                match_count=top_k,
                similarity_threshold=similarity_threshold
            )
            
            if not raw_chunks:
                # No-fallback policy: error if no chunks found
                raise RAGError(f"No relevant documents found for query. "
                             f"Searched {len(dataset_ids)} datasets with threshold {similarity_threshold}")
            
            # Step 4: Consolidate and rerank chunks
            selected_chunks = await self._consolidate_and_rerank(
                raw_chunks=raw_chunks,
                max_documents=max_documents,
                max_chunks=max_chunks
            )
            
            if not selected_chunks:
                raise RAGError("No chunks remaining after consolidation and reranking")
            
            # Step 5: Build context and citations
            context_text = self._build_context_text(selected_chunks)
            citations = self._build_citations(selected_chunks)
            
            processing_time = (datetime.now() - start_time).total_seconds() * 1000
            
            logger.info(f"RAG retrieval completed: {len(citations)} citations from "
                       f"{len(set(c.doc_id for c in citations))} documents in {processing_time:.1f}ms")
            
            # Log analytics (async, don't await to avoid blocking)
            try:
                from app.integrations.analytics.citations_analytics import citation_analytics
                asyncio.create_task(citation_analytics.log_citation_usage(
                    client_id=client_id,
                    agent_slug="",  # Would be populated by caller
                    session_id="",  # Would be populated by caller  
                    message_id="",  # Would be populated by caller
                    citations=[
                        {
                            "chunk_id": c.chunk_id,
                            "doc_id": c.doc_id,
                            "source_type": c.source_type,
                            "similarity": c.similarity,
                            "chunk_index": c.chunk_index,
                            "page_number": c.page_number
                        }
                        for c in citations
                    ],
                    user_query_length=len(query),
                    retrieval_time_ms=processing_time,
                    total_chunks_found=len(raw_chunks)
                ))
            except Exception as e:
                logger.debug(f"Analytics logging failed (non-critical): {e}")
            
            return RAGRetrievalResult(
                context_for_llm=context_text,
                citations=citations,
                total_chunks_found=len(raw_chunks),
                processing_time_ms=processing_time
            )
            
        except RAGError:
            # Re-raise RAG errors without wrapping
            raise
        except Exception as e:
            logger.error(f"RAG retrieval failed: {type(e).__name__}: {e}")
            raise RAGError(f"Document retrieval failed: {str(e)}")
    
    async def _get_client_supabase_config(self, client_id: str) -> Dict[str, Any]:
        """Get client's Supabase configuration for multi-tenant access"""
        try:
            client_service = get_client_service()
            client = await client_service.get_client(client_id)
            
            if not client or not client.settings:
                raise RAGError(f"Client {client_id} not found or has no settings configured")
            
            # Extract Supabase credentials from client settings
            settings = client.settings.dict() if hasattr(client.settings, 'dict') else client.settings
            
            supabase_url = settings.get('supabase_url')
            supabase_service_key = settings.get('supabase_service_role_key')
            
            if not supabase_url or not supabase_service_key:
                raise RAGError(f"Client {client_id} missing required Supabase configuration")
            
            return {
                'url': supabase_url,
                'service_key': supabase_service_key,
                'client_id': client_id
            }
            
        except Exception as e:
            logger.error(f"Failed to get client Supabase config: {e}")
            raise RAGError(f"Client configuration error: {str(e)}")
    
    async def _create_client_supabase(self, config: Dict[str, Any]):
        """Create Supabase client for tenant-specific database access"""
        try:
            from supabase import create_client
            return create_client(config['url'], config['service_key'])
        except Exception as e:
            logger.error(f"Failed to create client Supabase instance: {e}")
            raise RAGError(f"Database connection failed: {str(e)}")
    
    async def _generate_embedding(self, text: str, client_config: Dict[str, Any]) -> List[float]:
        """Generate embedding for the query text using client's configured provider"""
        try:
            # For now, use a placeholder. In production, this would use the client's
            # configured embedding provider (OpenAI, etc.) with their API keys
            # This is where we'd integrate with the embedding service
            
            # Placeholder: return a 1024-dimensional zero vector
            # TODO: Replace with actual embedding generation
            embedding = [0.0] * 1024
            
            logger.info(f"Generated embedding for query (length: {len(text)} chars)")
            return embedding
            
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise RAGError(f"Failed to generate query embedding: {str(e)}")
    
    async def _search_document_chunks(
        self,
        client_supabase,
        query_embedding: List[float],
        dataset_ids: List[UUID],
        match_count: int,
        similarity_threshold: Optional[float]
    ) -> List[Dict[str, Any]]:
        """Search document chunks using the Supabase RPC function"""
        try:
            # Convert embedding to the format expected by pgvector
            embedding_vector = f"[{','.join(map(str, query_embedding))}]"
            
            # Call the RPC function
            result = client_supabase.rpc(
                'match_document_chunks',
                {
                    'query_embedding': embedding_vector,
                    'dataset_ids': [str(did) for did in dataset_ids],
                    'match_count': match_count,
                    'similarity_threshold': similarity_threshold,
                    'filters': None  # Could be extended for additional filtering
                }
            ).execute()
            
            chunks = result.data or []
            logger.info(f"Vector search returned {len(chunks)} chunks")
            
            return chunks
            
        except Exception as e:
            logger.error(f"Document chunk search failed: {e}")
            raise RAGError(f"Vector search failed: {str(e)}")
    
    async def _consolidate_and_rerank(
        self,
        raw_chunks: List[Dict[str, Any]],
        max_documents: int,
        max_chunks: int
    ) -> List[Dict[str, Any]]:
        """
        Consolidate chunks by document and rerank to fit token budget.
        
        Strategy:
        1. Group chunks by document
        2. Calculate average similarity per document
        3. Select top M documents by average similarity
        4. Within selected documents, take top chunks up to max_chunks limit
        5. Ensure total content fits within token budget
        """
        try:
            # Group chunks by document
            docs_map = {}
            for chunk in raw_chunks:
                doc_id = chunk['doc_id']
                if doc_id not in docs_map:
                    docs_map[doc_id] = {
                        'chunks': [],
                        'title': chunk.get('title', 'Untitled'),
                        'source_url': chunk.get('source_url', '#'),
                        'avg_similarity': 0.0
                    }
                docs_map[doc_id]['chunks'].append(chunk)
            
            # Calculate average similarity per document
            for doc_id, doc_data in docs_map.items():
                similarities = [chunk['similarity'] for chunk in doc_data['chunks']]
                doc_data['avg_similarity'] = sum(similarities) / len(similarities)
            
            # Select top documents by average similarity
            sorted_docs = sorted(
                docs_map.items(),
                key=lambda x: x[1]['avg_similarity'],
                reverse=True
            )[:max_documents]
            
            # Collect chunks from selected documents
            selected_chunks = []
            total_chars = 0
            
            for doc_id, doc_data in sorted_docs:
                # Sort chunks within this document by similarity
                doc_chunks = sorted(
                    doc_data['chunks'],
                    key=lambda x: x['similarity'],
                    reverse=True
                )
                
                for chunk in doc_chunks:
                    if len(selected_chunks) >= max_chunks:
                        break
                    
                    chunk_content = chunk.get('content', '')
                    chunk_chars = len(chunk_content)
                    
                    # Check if adding this chunk would exceed token budget
                    if total_chars + chunk_chars > self.max_context_chars:
                        logger.info(f"Stopping at {len(selected_chunks)} chunks due to token budget")
                        break
                    
                    selected_chunks.append(chunk)
                    total_chars += chunk_chars
                
                if len(selected_chunks) >= max_chunks:
                    break
            
            logger.info(f"Selected {len(selected_chunks)} chunks from "
                       f"{len(sorted_docs)} documents ({total_chars} chars)")
            
            return selected_chunks
            
        except Exception as e:
            logger.error(f"Chunk consolidation failed: {e}")
            raise RAGError(f"Failed to consolidate search results: {str(e)}")
    
    def _build_context_text(self, chunks: List[Dict[str, Any]]) -> str:
        """Build the context text to be sent to the LLM"""
        try:
            context_parts = []
            
            for i, chunk in enumerate(chunks, 1):
                title = chunk.get('title', 'Document')
                content = chunk.get('content', '').strip()
                chunk_index = chunk.get('chunk_index', 0)
                
                # Format: [Source N: Title] content
                context_parts.append(f"[Source {i}: {title}#{chunk_index}]\n{content}")
            
            return "\n\n".join(context_parts)
            
        except Exception as e:
            logger.error(f"Context building failed: {e}")
            return ""
    
    def _build_citations(self, chunks: List[Dict[str, Any]]) -> List[CitationChunk]:
        """Build citation objects from selected chunks"""
        try:
            citations = []
            
            for chunk in chunks:
                citation = CitationChunk(
                    chunk_id=str(chunk.get('id', '')),
                    doc_id=str(chunk.get('doc_id', '')),
                    title=chunk.get('title', 'Untitled Document'),
                    source_url=chunk.get('source_url', '#'),
                    source_type=chunk.get('source_type', 'unknown'),
                    chunk_index=chunk.get('chunk_index', 0),
                    page_number=chunk.get('page_number'),
                    char_start=chunk.get('char_start'),
                    char_end=chunk.get('char_end'),
                    content=chunk.get('content', ''),
                    similarity=float(chunk.get('similarity', 0.0))
                )
                citations.append(citation)
            
            return citations
            
        except Exception as e:
            logger.error(f"Citation building failed: {e}")
            return []


# Singleton instance
rag_citations_service = RAGCitationsService()