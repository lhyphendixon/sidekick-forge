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
    rerank_info: Optional[Dict[str, Any]] = None


class RAGCitationsService:
    """Service for RAG document retrieval with citation tracking"""
    
    def __init__(self, supabase_client: Client, embedder=None, agent_slug: str = None):
        self.supabase = supabase_client
        self.embedder = embedder
        self.agent_slug = agent_slug
        self.max_context_tokens = 32000  # Increased to allow multiple chunks
        self.chars_per_token = 4
        self.max_context_chars = self.max_context_tokens * self.chars_per_token
        # Maximum characters per individual chunk (truncate if larger)
        self.max_chunk_chars = 8000
        
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
        max_chunks: int = 8,
        rerank_enabled: bool = True,
        rerank_candidates: Optional[int] = None,
        rerank_top_k: Optional[int] = None,
        rerank_provider: Optional[str] = None,
        rerank_model: Optional[str] = None,
        api_keys: Optional[Dict[str, Any]] = None
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
            citations: List[CitationChunk] = []
            seen_docs = set()
            per_doc_counts: Dict[Any, int] = {}
            context_parts: List[str] = []
            total_chars = 0
            
            # Determine rerank controls
            candidates_limit = rerank_candidates if rerank_candidates and rerank_candidates > 0 else top_k
            top_k_limit = rerank_top_k if rerank_top_k and rerank_top_k > 0 else max_chunks or top_k

            # Respect provided limits but avoid overly aggressive caps
            max_documents = max_documents or candidates_limit
            max_chunks = max_chunks or candidates_limit

            # Model-based rerank when configured; otherwise fall back to similarity sort
            reranked = result.data
            rerank_debug: Dict[str, Any] = {
                "enabled": bool(rerank_enabled and rerank_provider and rerank_model),
                "provider": rerank_provider,
                "model": rerank_model,
                "candidates_evaluated": len(result.data),
                "returned": 0,
                "top_doc_ids": [],
                "pre_titles": [r.get("title") for r in result.data[:10]],
            }
            if rerank_enabled and rerank_provider and rerank_model:
                try:
                    logger.info(
                        f"Model rerank enabled (provider={rerank_provider}, model={rerank_model}, "
                        f"candidates={len(result.data)}, top_n={candidates_limit})"
                    )
                    reranked = await self._model_rerank(
                        query=query,
                        candidates=result.data,
                        provider=rerank_provider,
                        model=rerank_model,
                        api_keys=api_keys or {},
                        top_n=candidates_limit,
                    )
                    logger.info(f"Model rerank returned {len(reranked)} items")
                except Exception as rerank_err:
                    logger.warning(f"Model rerank failed ({type(rerank_err).__name__}): {rerank_err}. Falling back to similarity sort.")
                    reranked = sorted(result.data, key=lambda x: x.get("similarity", 0.0), reverse=True)[:candidates_limit]
            else:
                reranked = sorted(result.data, key=lambda x: x.get("similarity", 0.0), reverse=True)[:candidates_limit]
            rerank_debug["returned"] = len(reranked)
            if reranked:
                rerank_debug["post_titles"] = [r.get("title") for r in reranked[:10]]
            else:
                rerank_debug["post_titles"] = []

            top_doc_ids: List[Any] = []
            # Encourage document diversity: limit chunks per document so a single doc doesn't crowd out others.
            # Raised to 6 to allow more from a strong doc but still leave room for diversity.
            max_chunks_per_doc = 6

            for chunk in reranked[:max_chunks]:
                # Skip if we've hit document limit
                doc_id = chunk.get("document_id")
                if doc_id not in seen_docs:
                    if len(seen_docs) >= max_documents:
                        continue
                    seen_docs.add(doc_id)
                    per_doc_counts[doc_id] = 0
                else:
                    # Enforce per-document cap
                    if per_doc_counts.get(doc_id, 0) >= max_chunks_per_doc:
                        continue
                
                # Get and potentially truncate content
                raw_content = chunk.get("content", "")
                truncated = False
                if len(raw_content) > self.max_chunk_chars:
                    # Truncate at a word boundary if possible
                    truncate_point = raw_content.rfind(' ', 0, self.max_chunk_chars)
                    if truncate_point < self.max_chunk_chars * 0.5:  # If no good break point, hard truncate
                        truncate_point = self.max_chunk_chars
                    raw_content = raw_content[:truncate_point] + "... [truncated]"
                    truncated = True
                    logger.debug(f"Truncated chunk {chunk.get('id')} from {len(chunk.get('content', ''))} to {len(raw_content)} chars")
                
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
                    content=raw_content,
                    similarity=chunk.get("similarity", 0.0)
                )
                
                # Check if adding this would exceed total context limit; always allow first chunk
                chunk_chars = len(citation.content)
                if total_chars + chunk_chars > self.max_context_chars and citations:
                    logger.info(f"Reached context limit at chunk {len(citations)} (total_chars={total_chars}, would add {chunk_chars})")
                    break
                
                citations.append(citation)
                per_doc_counts[doc_id] = per_doc_counts.get(doc_id, 0) + 1
                top_doc_ids.append(doc_id)
                context_parts.append(f"[Source: {citation.title}]\n{citation.content}\n")
                total_chars += chunk_chars

                # Enforce top_k_limit on returned citations
                if len(citations) >= top_k_limit:
                    break
            
            # Build final context
            context_for_llm = "\n---\n".join(context_parts) if context_parts else ""
            
            processing_time = (datetime.now() - start_time).total_seconds() * 1000
            rerank_debug["top_doc_ids"] = top_doc_ids

            logger.info(
                f"Retrieved {len(citations)} citations from {len(seen_docs)} documents "
                f"in {processing_time:.2f}ms"
            )
            
            return RAGRetrievalResult(
                context_for_llm=context_for_llm,
                citations=citations,
                total_chunks_found=len(result.data),
                processing_time_ms=processing_time,
                rerank_info=rerank_debug,
            )
            
        except Exception as e:
            error_msg = f"RAG retrieval failed with error: {str(e)}"
            logger.error(error_msg)
            logger.error(f"Error type: {type(e).__name__}")
            # No silent failures - raise the exception for proper error handling
            raise RuntimeError(error_msg) from e

    async def _model_rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        provider: str,
        model: str,
        api_keys: Dict[str, Any],
        top_n: int,
    ) -> List[Dict[str, Any]]:
        """
        Perform model-based reranking. Currently supports SiliconFlow-style rerank API.
        Falls back to similarity sort on failure.
        """
        if not candidates:
            return []
        top_n = min(top_n or len(candidates), len(candidates))
        provider = (provider or "").lower()

        if provider != "siliconflow":
            logger.info(f"Rerank provider {provider} not supported in agent container; using similarity fallback.")
            return sorted(candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)[:top_n]

        api_key = (api_keys or {}).get("siliconflow_api_key")
        if not api_key:
            logger.warning("SiliconFlow rerank requested but siliconflow_api_key is missing; using similarity fallback.")
            return sorted(candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)[:top_n]

        documents = []
        for c in candidates[:max(top_n, len(candidates))]:
            content = c.get("content") or ""
            documents.append(content)

        payload = {
            "model": model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post("https://api.siliconflow.com/v1/rerank", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            results = data.get("results") or data.get("data") or []
            if not isinstance(results, list):
                raise ValueError("Unexpected rerank response format")

            # Each result is expected to have an index into the documents list
            ordered: List[Tuple[int, float]] = []
            for item in results:
                idx = item.get("index") if isinstance(item, dict) else None
                score = item.get("relevance_score") if isinstance(item, dict) else None
                if idx is None:
                    # Cohere-style may use "document" key
                    idx = item.get("document") if isinstance(item, dict) else None
                if idx is None:
                    continue
                ordered.append((int(idx), float(score) if score is not None else 0.0))

            if not ordered:
                raise ValueError("No indices returned from rerank response")

            # Sort by model score descending, keep top_n, map back to candidate chunks
            ordered = sorted(ordered, key=lambda x: x[1], reverse=True)[:top_n]
            reranked_chunks: List[Dict[str, Any]] = []
            for idx, _ in ordered:
                if 0 <= idx < len(candidates):
                    reranked_chunks.append(candidates[idx])

            if reranked_chunks:
                return reranked_chunks
            # Fallback if parsing failed
            return sorted(candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)[:top_n]
        except Exception as e:
            logger.warning(f"SiliconFlow rerank call failed: {e}")
            return sorted(candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)[:top_n]


# Singleton instance (will be initialized with supabase client)
rag_citations_service: Optional[RAGCitationsService] = None

def initialize_citations_service(supabase_client: Client, embedder=None, agent_slug: str = None):
    """Initialize the citations service with a Supabase client, embedder, and agent slug"""
    global rag_citations_service
    rag_citations_service = RAGCitationsService(supabase_client, embedder, agent_slug)
    return rag_citations_service
