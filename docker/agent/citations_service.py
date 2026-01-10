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
    query_embedding: Optional[List[float]] = None  # Cache embedding for reuse


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
        # Context expansion: number of adjacent chunks to fetch (before and after)
        self.context_expansion_before = 1  # Fetch 1 chunk before
        self.context_expansion_after = 1   # Fetch 1 chunk after

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

            # Generate query embedding - TIMED
            embed_start = datetime.now()
            query_embedding = await self.embedder.create_embedding(query)
            embed_duration = (datetime.now() - embed_start).total_seconds() * 1000
            logger.info(f"[PERF] Embedding generation took {embed_duration:.0f}ms")

            # Call the match_documents RPC function with correct signature - TIMED
            rpc_start = datetime.now()
            rpc_params = {
                "p_query_embedding": query_embedding,
                "p_agent_slug": effective_agent_slug,
                "p_match_threshold": similarity_threshold,
                "p_match_count": top_k,
            }
            result = await asyncio.to_thread(
                lambda: self.supabase.rpc("match_documents", rpc_params).execute()
            )
            rpc_duration = (datetime.now() - rpc_start).total_seconds() * 1000
            logger.info(f"[PERF] match_documents RPC took {rpc_duration:.0f}ms (returned {len(result.data) if result.data else 0} chunks)")
            
            if not result.data:
                logger.info("No matching documents found")
                return RAGRetrievalResult(
                    context_for_llm="",
                    citations=[],
                    total_chunks_found=0,
                    processing_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
                    query_embedding=query_embedding  # Return embedding for reuse
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
                "pre_titles": [r.get("title") for r in result.data[:10] if r and isinstance(r, dict)],
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
                    # Filter out None items before sorting
                    valid_data = [x for x in result.data if x and isinstance(x, dict)]
                    reranked = sorted(valid_data, key=lambda x: x.get("similarity", 0.0), reverse=True)[:candidates_limit]
            else:
                # Filter out None items before sorting
                valid_data = [x for x in result.data if x and isinstance(x, dict)]
                reranked = sorted(valid_data, key=lambda x: x.get("similarity", 0.0), reverse=True)[:candidates_limit]
            rerank_debug["returned"] = len(reranked)
            if reranked:
                rerank_debug["post_titles"] = [r.get("title") for r in reranked[:10] if r and isinstance(r, dict)]
            else:
                rerank_debug["post_titles"] = []

            top_doc_ids: List[Any] = []
            # Encourage document diversity: limit chunks per document so a single doc doesn't crowd out others.
            # Raised to 6 to allow more from a strong doc but still leave room for diversity.
            max_chunks_per_doc = 6

            # First pass: select which chunks to include (without expansion yet)
            # This filters down from reranked candidates to final selection
            selected_chunks: List[Dict[str, Any]] = []
            temp_seen_docs = set()
            temp_per_doc_counts: Dict[Any, int] = {}

            for chunk in reranked[:max_chunks]:
                if not chunk or not isinstance(chunk, dict):
                    continue
                doc_id = chunk.get("document_id")
                if doc_id not in temp_seen_docs:
                    if len(temp_seen_docs) >= max_documents:
                        continue
                    temp_seen_docs.add(doc_id)
                    temp_per_doc_counts[doc_id] = 0
                else:
                    if temp_per_doc_counts.get(doc_id, 0) >= max_chunks_per_doc:
                        continue
                temp_per_doc_counts[doc_id] = temp_per_doc_counts.get(doc_id, 0) + 1
                selected_chunks.append(chunk)
                if len(selected_chunks) >= top_k_limit:
                    break

            # Now expand ONLY the selected chunks (not all reranked candidates) - TIMED
            if selected_chunks and (self.context_expansion_before > 0 or self.context_expansion_after > 0):
                try:
                    expand_start = datetime.now()
                    selected_chunks = await self._expand_chunk_context(selected_chunks)
                    expand_duration = (datetime.now() - expand_start).total_seconds() * 1000
                    logger.info(f"[PERF] Context expansion took {expand_duration:.0f}ms for {len(selected_chunks)} chunks")
                except Exception as exp_err:
                    logger.warning(f"Context expansion failed: {exp_err}. Continuing with original chunks.")

            # Second pass: build citations from selected (and expanded) chunks
            # Document/chunk limits were already enforced in first pass
            for chunk in selected_chunks:
                if not chunk or not isinstance(chunk, dict):
                    continue

                doc_id = chunk.get("document_id")
                if doc_id not in seen_docs:
                    seen_docs.add(doc_id)

                # Get and potentially truncate content
                raw_content = chunk.get("content", "")
                if len(raw_content) > self.max_chunk_chars:
                    # Truncate at a word boundary if possible
                    truncate_point = raw_content.rfind(' ', 0, self.max_chunk_chars)
                    if truncate_point < self.max_chunk_chars * 0.5:  # If no good break point, hard truncate
                        truncate_point = self.max_chunk_chars
                    raw_content = raw_content[:truncate_point] + "... [truncated]"
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
                top_doc_ids.append(doc_id)
                context_parts.append(f"[Source: {citation.title}]\n{citation.content}\n")
                total_chars += chunk_chars
            
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
                query_embedding=query_embedding,  # Return embedding for reuse
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
        Perform model-based reranking.

        Supports:
        - local/on-prem/bge-local: Local BGE-reranker-v2-m3 via sidecar service
        - siliconflow: SiliconFlow cloud API

        Falls back to similarity sort on failure.
        """
        if not candidates:
            return []
        top_n = min(top_n or len(candidates), len(candidates))
        provider = (provider or "").lower()

        # Extract document contents for reranking
        documents = []
        for c in candidates[:max(top_n, len(candidates))]:
            content = c.get("content") or ""
            documents.append(content)

        # Route to appropriate reranker
        if provider in ("local", "on-prem", "bge-local", "bge", "bge-reranker"):
            return await self._local_bge_rerank(query, candidates, documents, top_n)
        elif provider == "siliconflow":
            return await self._siliconflow_rerank(query, candidates, documents, model, api_keys, top_n)
        else:
            logger.info(f"Rerank provider {provider} not supported; using similarity fallback.")
            return sorted(candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)[:top_n]

    async def _local_bge_rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        documents: List[str],
        top_n: int,
    ) -> List[Dict[str, Any]]:
        """Rerank using local BGE-reranker-v2-m3 service (on-premise)."""
        import os
        bge_service_url = os.getenv("BGE_SERVICE_URL", "http://bge-service:8090")

        payload = {
            "query": query,
            "documents": documents,
            "model": "bge-reranker-v2-m3",
            "top_n": top_n,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{bge_service_url}/rerank", json=payload)

            if resp.status_code == 503:
                logger.warning("BGE reranker service not ready; using similarity fallback.")
                return sorted(candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)[:top_n]

            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            results = data.get("results", [])

            if not results:
                raise ValueError("Empty rerank response from BGE service")

            # BGE service returns results in order with index and score
            reranked_chunks: List[Dict[str, Any]] = []
            for item in results[:top_n]:
                idx = item.get("index")
                if idx is not None and 0 <= idx < len(candidates):
                    reranked_chunks.append(candidates[idx])

            if reranked_chunks:
                logger.info(f"Local BGE rerank completed in {data.get('processing_time_ms', 0):.0f}ms")
                return reranked_chunks

            # Fallback if parsing failed
            return sorted(candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)[:top_n]

        except httpx.ConnectError as e:
            logger.warning(f"Cannot connect to BGE reranker at {bge_service_url}: {e}; using similarity fallback.")
            return sorted(candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)[:top_n]
        except Exception as e:
            logger.warning(f"Local BGE rerank failed: {e}; using similarity fallback.")
            return sorted(candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)[:top_n]

    async def _siliconflow_rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        documents: List[str],
        model: str,
        api_keys: Dict[str, Any],
        top_n: int,
    ) -> List[Dict[str, Any]]:
        """Rerank using SiliconFlow cloud API."""
        api_key = (api_keys or {}).get("siliconflow_api_key")
        if not api_key:
            logger.warning("SiliconFlow rerank requested but siliconflow_api_key is missing; using similarity fallback.")
            return sorted(candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)[:top_n]

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

    async def _expand_chunk_context(
        self,
        chunks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Expand chunks by fetching adjacent chunks from the same document.
        This helps preserve semantic coherence when headers are split from content.

        For each chunk, fetches chunks with adjacent chunk_index values from the same document
        and merges them into a single expanded chunk.
        """
        if not chunks:
            return chunks

        # Group chunks by document_id for efficient fetching
        doc_chunks_map: Dict[Any, List[Dict[str, Any]]] = {}
        for chunk in chunks:
            doc_id = chunk.get("document_id")
            if doc_id not in doc_chunks_map:
                doc_chunks_map[doc_id] = []
            doc_chunks_map[doc_id].append(chunk)

        # For each document, determine which chunk indices we need
        expanded_chunks: List[Dict[str, Any]] = []
        chunks_already_fetched: Dict[Any, Dict[int, Dict[str, Any]]] = {}  # doc_id -> {chunk_index -> chunk}

        for doc_id, doc_chunks in doc_chunks_map.items():
            # Get all chunk indices we already have for this document
            existing_indices = {c.get("chunk_index", 0) for c in doc_chunks}

            # Determine which adjacent indices we need to fetch
            indices_to_fetch = set()
            for chunk in doc_chunks:
                chunk_idx = chunk.get("chunk_index", 0)
                # Add indices before and after
                for i in range(1, self.context_expansion_before + 1):
                    if chunk_idx - i >= 0 and (chunk_idx - i) not in existing_indices:
                        indices_to_fetch.add(chunk_idx - i)
                for i in range(1, self.context_expansion_after + 1):
                    if (chunk_idx + i) not in existing_indices:
                        indices_to_fetch.add(chunk_idx + i)

            # Fetch adjacent chunks if needed
            adjacent_chunks: Dict[int, Dict[str, Any]] = {}
            if indices_to_fetch:
                try:
                    # Query document_chunks table for adjacent chunks
                    result = await asyncio.to_thread(
                        lambda: self.supabase.table("document_chunks")
                        .select("id, document_id, chunk_index, content, chunk_metadata")
                        .eq("document_id", doc_id)
                        .in_("chunk_index", list(indices_to_fetch))
                        .execute()
                    )
                    if result.data:
                        for adj_chunk in result.data:
                            adjacent_chunks[adj_chunk.get("chunk_index", -1)] = adj_chunk
                        logger.debug(f"Fetched {len(result.data)} adjacent chunks for doc {doc_id}")
                except Exception as e:
                    logger.warning(f"Failed to fetch adjacent chunks for doc {doc_id}: {e}")

            # Store fetched chunks for this document
            chunks_already_fetched[doc_id] = adjacent_chunks

        # Now expand each original chunk by merging with adjacent content
        for chunk in chunks:
            doc_id = chunk.get("document_id")
            chunk_idx = chunk.get("chunk_index", 0)
            adjacent = chunks_already_fetched.get(doc_id, {})

            # Collect content parts in order: [before chunks] + [original] + [after chunks]
            content_parts = []

            # Add chunks before (in order)
            for i in range(self.context_expansion_before, 0, -1):
                prev_idx = chunk_idx - i
                if prev_idx in adjacent:
                    prev_content = adjacent[prev_idx].get("content", "")
                    if prev_content:
                        content_parts.append(prev_content)
                        logger.debug(f"Prepending chunk {prev_idx} ({len(prev_content)} chars) to chunk {chunk_idx}")

            # Add original chunk content
            original_content = chunk.get("content", "")
            content_parts.append(original_content)

            # Add chunks after (in order)
            for i in range(1, self.context_expansion_after + 1):
                next_idx = chunk_idx + i
                if next_idx in adjacent:
                    next_content = adjacent[next_idx].get("content", "")
                    if next_content:
                        content_parts.append(next_content)
                        logger.debug(f"Appending chunk {next_idx} ({len(next_content)} chars) to chunk {chunk_idx}")

            # Merge content
            expanded_content = "\n\n".join(content_parts)

            # Create expanded chunk (copy original and update content)
            expanded_chunk = chunk.copy()
            expanded_chunk["content"] = expanded_content
            expanded_chunk["_expanded"] = True  # Mark as expanded for debugging
            expanded_chunk["_expansion_range"] = (
                chunk_idx - self.context_expansion_before,
                chunk_idx + self.context_expansion_after
            )

            expanded_chunks.append(expanded_chunk)

            if len(expanded_content) > len(original_content):
                logger.info(
                    f"Expanded chunk {chunk_idx} from {len(original_content)} to {len(expanded_content)} chars "
                    f"(doc_id={doc_id})"
                )

        return expanded_chunks


# Singleton instance (will be initialized with supabase client)
rag_citations_service: Optional[RAGCitationsService] = None

def initialize_citations_service(supabase_client: Client, embedder=None, agent_slug: str = None):
    """Initialize the citations service with a Supabase client, embedder, and agent slug"""
    global rag_citations_service
    rag_citations_service = RAGCitationsService(supabase_client, embedder, agent_slug)
    return rag_citations_service
