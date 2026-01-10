"""
BGE Local Embedding and Reranking Service

Provides local inference for:
- BGE-M3: Dense embeddings (1024 dimensions)
- BGE-reranker-v2-m3: Cross-encoder reranking

This service runs as a sidecar container, keeping the agent container stateless.
"""
import logging
import time
from contextlib import asynccontextmanager
from typing import List, Optional

import psutil
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from config import (
    BGE_M3_ENABLED,
    BGE_M3_MODEL,
    BGE_RERANKER_ENABLED,
    BGE_RERANKER_MODEL,
    DEVICE,
    EMBEDDING_DIMENSION,
    MAX_BATCH_SIZE,
    MAX_RERANK_DOCUMENTS,
    MODEL_CACHE_DIR,
    SERVICE_HOST,
    SERVICE_PORT,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("bge-service")

# Global model instances
embedding_model = None
reranker_model = None


def load_embedding_model():
    """Load BGE-M3 embedding model."""
    global embedding_model
    if not BGE_M3_ENABLED:
        logger.info("BGE-M3 embeddings disabled by config")
        return

    logger.info(f"Loading BGE-M3 embedding model: {BGE_M3_MODEL}")
    start = time.time()

    try:
        from FlagEmbedding import BGEM3FlagModel

        embedding_model = BGEM3FlagModel(
            BGE_M3_MODEL,
            use_fp16=(DEVICE == "cuda"),
            device=DEVICE,
        )
        elapsed = time.time() - start
        logger.info(f"BGE-M3 model loaded in {elapsed:.1f}s on {DEVICE}")
    except Exception as e:
        logger.error(f"Failed to load BGE-M3 model: {e}")
        raise


def load_reranker_model():
    """Load BGE-reranker-v2-m3 model."""
    global reranker_model
    if not BGE_RERANKER_ENABLED:
        logger.info("BGE reranker disabled by config")
        return

    logger.info(f"Loading BGE reranker model: {BGE_RERANKER_MODEL}")
    start = time.time()

    try:
        from FlagEmbedding import FlagReranker

        reranker_model = FlagReranker(
            BGE_RERANKER_MODEL,
            use_fp16=(DEVICE == "cuda"),
            device=DEVICE,
        )
        elapsed = time.time() - start
        logger.info(f"BGE reranker model loaded in {elapsed:.1f}s on {DEVICE}")
    except Exception as e:
        logger.error(f"Failed to load BGE reranker model: {e}")
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize service (models loaded lazily on first use)."""
    logger.info("=" * 60)
    logger.info("BGE Service Starting")
    logger.info(f"Device: {DEVICE}")
    logger.info(f"BGE-M3 Enabled: {BGE_M3_ENABLED}")
    logger.info(f"BGE Reranker Enabled: {BGE_RERANKER_ENABLED}")
    logger.info("=" * 60)

    # Models are now loaded lazily on first use to reduce startup memory pressure
    # This allows the service to start quickly and only load models when needed
    logger.info("BGE Service ready (models will load on first use)")
    yield

    # Cleanup
    logger.info("BGE Service shutting down")


app = FastAPI(
    title="BGE Local Embedding & Reranking Service",
    description="On-premise BGE-M3 embeddings and BGE-reranker-v2-m3 reranking",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================================
# Request/Response Models
# ============================================================================

class EmbedRequest(BaseModel):
    """Request for embedding generation."""
    texts: List[str] = Field(..., description="List of texts to embed", min_length=1)
    model: str = Field(default="bge-m3", description="Model name (currently only bge-m3)")

    class Config:
        json_schema_extra = {
            "example": {
                "texts": ["What is machine learning?", "How do neural networks work?"],
                "model": "bge-m3"
            }
        }


class EmbedResponse(BaseModel):
    """Response containing embeddings."""
    embeddings: List[List[float]] = Field(..., description="List of embedding vectors")
    dimensions: int = Field(..., description="Embedding dimension")
    model: str = Field(..., description="Model used")
    processing_time_ms: float = Field(..., description="Processing time in milliseconds")


class RerankRequest(BaseModel):
    """Request for reranking documents."""
    query: str = Field(..., description="Query to rank documents against")
    documents: List[str] = Field(..., description="List of documents to rerank", min_length=1)
    model: str = Field(default="bge-reranker-v2-m3", description="Reranker model")
    top_n: Optional[int] = Field(default=None, description="Return top N results (None = all)")

    class Config:
        json_schema_extra = {
            "example": {
                "query": "What is the capital of France?",
                "documents": [
                    "Paris is the capital of France.",
                    "London is the capital of England.",
                    "France is a country in Europe."
                ],
                "top_n": 2
            }
        }


class RerankResult(BaseModel):
    """Single reranking result."""
    index: int = Field(..., description="Original document index")
    score: float = Field(..., description="Relevance score")
    document: str = Field(..., description="Document text")


class RerankResponse(BaseModel):
    """Response containing reranked results."""
    results: List[RerankResult] = Field(..., description="Reranked results, highest score first")
    model: str = Field(..., description="Model used")
    processing_time_ms: float = Field(..., description="Processing time in milliseconds")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    device: str
    models_loaded: List[str]
    memory_used_mb: float
    gpu_memory_used_mb: Optional[float] = None


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Check service health and loaded models."""
    models_loaded = []
    if embedding_model is not None:
        models_loaded.append("bge-m3")
    if reranker_model is not None:
        models_loaded.append("bge-reranker-v2-m3")

    # Get memory usage
    process = psutil.Process()
    memory_mb = process.memory_info().rss / (1024 * 1024)

    # Get GPU memory if available
    gpu_memory_mb = None
    if DEVICE == "cuda" and torch.cuda.is_available():
        gpu_memory_mb = torch.cuda.memory_allocated() / (1024 * 1024)

    return HealthResponse(
        status="ok" if models_loaded else "no_models_loaded",
        device=DEVICE,
        models_loaded=models_loaded,
        memory_used_mb=round(memory_mb, 1),
        gpu_memory_used_mb=round(gpu_memory_mb, 1) if gpu_memory_mb else None,
    )


@app.post("/embed", response_model=EmbedResponse)
async def create_embeddings(request: EmbedRequest):
    """
    Generate embeddings for a list of texts.

    Supports batch processing for efficiency.
    """
    global embedding_model

    # Lazy load model on first use
    if embedding_model is None:
        if not BGE_M3_ENABLED:
            raise HTTPException(
                status_code=503,
                detail="BGE-M3 embedding model disabled. Check BGE_M3_ENABLED config."
            )
        load_embedding_model()

    if len(request.texts) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Batch size {len(request.texts)} exceeds maximum {MAX_BATCH_SIZE}"
        )

    # Filter empty texts
    texts = [t.strip() for t in request.texts if t.strip()]
    if not texts:
        raise HTTPException(status_code=400, detail="No valid texts provided")

    start = time.time()

    try:
        # BGE-M3 returns dense, sparse, and colbert vectors
        # We only use dense vectors for compatibility with existing 1024-dim setup
        embeddings_dict = embedding_model.encode(
            texts,
            batch_size=min(len(texts), MAX_BATCH_SIZE),
            max_length=8192,  # BGE-M3 supports up to 8192 tokens
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )

        # Extract dense embeddings
        embeddings = embeddings_dict["dense_vecs"].tolist()

        elapsed_ms = (time.time() - start) * 1000
        logger.info(f"Generated {len(embeddings)} embeddings in {elapsed_ms:.0f}ms")

        return EmbedResponse(
            embeddings=embeddings,
            dimensions=EMBEDDING_DIMENSION,
            model="bge-m3",
            processing_time_ms=round(elapsed_ms, 1),
        )

    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Embedding failed: {str(e)}")


@app.post("/rerank", response_model=RerankResponse)
async def rerank_documents(request: RerankRequest):
    """
    Rerank documents by relevance to a query.

    Uses cross-encoder architecture for accurate relevance scoring.
    """
    global reranker_model

    # Lazy load model on first use
    if reranker_model is None:
        if not BGE_RERANKER_ENABLED:
            raise HTTPException(
                status_code=503,
                detail="BGE reranker model disabled. Check BGE_RERANKER_ENABLED config."
            )
        load_reranker_model()

    if len(request.documents) > MAX_RERANK_DOCUMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Document count {len(request.documents)} exceeds maximum {MAX_RERANK_DOCUMENTS}"
        )

    # Filter empty documents
    docs_with_idx = [(i, d.strip()) for i, d in enumerate(request.documents) if d.strip()]
    if not docs_with_idx:
        raise HTTPException(status_code=400, detail="No valid documents provided")

    start = time.time()

    try:
        # Create query-document pairs for cross-encoder
        pairs = [[request.query, doc] for _, doc in docs_with_idx]

        # Get relevance scores
        scores = reranker_model.compute_score(pairs, normalize=True)

        # Handle single document case (returns float instead of list)
        if isinstance(scores, (int, float)):
            scores = [scores]

        # Combine with original indices and sort by score
        results = []
        for (orig_idx, doc), score in zip(docs_with_idx, scores):
            results.append(RerankResult(
                index=orig_idx,
                score=float(score),
                document=doc,
            ))

        # Sort by score descending
        results.sort(key=lambda x: x.score, reverse=True)

        # Apply top_n limit if specified
        if request.top_n is not None and request.top_n > 0:
            results = results[:request.top_n]

        elapsed_ms = (time.time() - start) * 1000
        logger.info(f"Reranked {len(docs_with_idx)} documents in {elapsed_ms:.0f}ms")

        return RerankResponse(
            results=results,
            model="bge-reranker-v2-m3",
            processing_time_ms=round(elapsed_ms, 1),
        )

    except Exception as e:
        logger.error(f"Reranking failed: {e}")
        raise HTTPException(status_code=500, detail=f"Reranking failed: {str(e)}")


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "BGE Local Embedding & Reranking Service",
        "version": "1.0.0",
        "endpoints": {
            "/embed": "Generate embeddings (POST)",
            "/rerank": "Rerank documents (POST)",
            "/health": "Health check (GET)",
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "service:app",
        host=SERVICE_HOST,
        port=SERVICE_PORT,
        reload=False,
        workers=1,  # Single worker to share model in memory
    )
