"""
RAG Citations Service for Agent Container

Standalone version that works within the agent container without app dependencies.
Provides document retrieval with structured citation metadata.
"""
import logging
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime
from supabase import Client
import httpx

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
    
    def __init__(self, supabase_client: Client, embedder=None, agent_slug: str = None):
        self.supabase = supabase_client
        self.embedder = embedder
        self.agent_slug = agent_slug
        self.max_context_tokens = 8000
        self.chars_per_token = 4
        self.max_context_chars = self.max_context_tokens * self.chars_per_token
        
        # Log initialization state for debugging
        if not self.embedder:
            logger.warning("Citations service initialized without embedder - will fail on query")
        if not self.agent_slug:
            logger.warning("Citations service initialized without agent_slug - will need to pass at query time")
    
    async def retrieve_with_citations(
        self,
        query: str,
        client_id: str,
        dataset_ids: List[int] = None,
        agent_slug: str = None,
        top_k: int = 12,
        similarity_threshold: float = 0.4,
        max_documents: int = 4,
        max_chunks: int = 8
    ) -> RAGRetrievalResult:
        """
        Retrieve documents with full citation metadata.
        
        Args:
            query: The search query
            client_id: Client ID for multi-tenant isolation
            dataset_ids: List of dataset IDs to search within (deprecated, use agent_slug)
            agent_slug: Agent slug for filtering documents
            top_k: Number of chunks to retrieve initially
            similarity_threshold: Minimum similarity score
            max_documents: Maximum unique documents to include
            max_chunks: Maximum chunks to include in context
            
        Returns:
            RAGRetrievalResult with context and citations
        """
        start_time = datetime.now()
        
        try:
            # Use agent_slug if provided, otherwise fall back to self.agent_slug
            effective_agent_slug = agent_slug or self.agent_slug
            
            if not effective_agent_slug:
                logger.info("No agent_slug provided, returning empty result")
                return RAGRetrievalResult(
                    context_for_llm="",
                    citations=[],
                    total_chunks_found=0,
                    processing_time_ms=0
                )
            
            # Generate embedding for the query
            if not self.embedder:
                error_msg = "No embedder configured for citations service - cannot generate query embeddings"
                logger.error(error_msg)
                raise ValueError(error_msg)
            
            # Perform vector similarity search via RPC
            logger.info(f"Searching for query: {query[:100]}... with agent_slug: {effective_agent_slug}")
            
            # Generate query embedding
            query_embedding = await self.embedder.create_embedding(query)
            
            # Call the match_documents RPC function with correct signature
            rpc_params = {
                "p_query_embedding": query_embedding,
                "p_agent_slug": effective_agent_slug,
                "p_match_threshold": similarity_threshold,
                "p_match_count": top_k,
            }
            result = await asyncio.to_thread(
                lambda: self.supabase.rpc("match_documents", rpc_params).execute()
            )
            
            if not result.data:
                logger.info("No matching documents found")
                return RAGRetrievalResult(
                    context_for_llm="",
                    citations=[],
                    total_chunks_found=0,
                    processing_time_ms=(datetime.now() - start_time).total_seconds() * 1000
                )
            
            # Process results into citations
            citations = []
            seen_docs = set()
            context_parts = []
            total_chars = 0
            
            for chunk in result.data[:max_chunks]:
                # Skip if we've hit document limit
                doc_id = chunk.get("document_id")
                if doc_id not in seen_docs:
                    if len(seen_docs) >= max_documents:
                        continue
                    seen_docs.add(doc_id)
                
                # Create citation
                citation = CitationChunk(
                    chunk_id=chunk.get("id"),
                    doc_id=doc_id,
                    title=chunk.get("title", "Untitled"),
                    source_url=chunk.get("source_url", ""),
                    source_type=chunk.get("source_type", "document"),
                    chunk_index=chunk.get("chunk_index", 0),
                    page_number=chunk.get("page_number"),
                    char_start=chunk.get("char_start"),
                    char_end=chunk.get("char_end"),
                    content=chunk.get("content", ""),
                    similarity=chunk.get("similarity", 0.0)
                )
                
                # Check if adding this would exceed token limit
                chunk_chars = len(citation.content)
                if total_chars + chunk_chars > self.max_context_chars:
                    logger.info(f"Reached context limit at chunk {len(citations)}")
                    break
                
                citations.append(citation)
                context_parts.append(f"[Source: {citation.title}]\n{citation.content}\n")
                total_chars += chunk_chars
            
            # Build final context
            context_for_llm = "\n---\n".join(context_parts) if context_parts else ""
            
            processing_time = (datetime.now() - start_time).total_seconds() * 1000
            
            logger.info(
                f"Retrieved {len(citations)} citations from {len(seen_docs)} documents "
                f"in {processing_time:.2f}ms"
            )
            
            return RAGRetrievalResult(
                context_for_llm=context_for_llm,
                citations=citations,
                total_chunks_found=len(result.data),
                processing_time_ms=processing_time
            )
            
        except Exception as e:
            error_msg = f"RAG retrieval failed with error: {str(e)}"
            logger.error(error_msg)
            logger.error(f"Error type: {type(e).__name__}")
            # No silent failures - raise the exception for proper error handling
            raise RuntimeError(error_msg) from e


# Singleton instance (will be initialized with supabase client)
rag_citations_service: Optional[RAGCitationsService] = None

def initialize_citations_service(supabase_client: Client, embedder=None, agent_slug: str = None):
    """Initialize the citations service with a Supabase client, embedder, and agent slug"""
    global rag_citations_service
    rag_citations_service = RAGCitationsService(supabase_client, embedder, agent_slug)
    return rag_citations_service
