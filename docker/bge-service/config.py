"""Configuration for BGE Service."""
import os
from typing import Literal

# Device configuration
DEVICE: Literal["cpu", "cuda"] = os.getenv("BGE_DEVICE", "cpu")

# Model configuration
BGE_M3_MODEL = os.getenv("BGE_M3_MODEL", "BAAI/bge-m3")
BGE_RERANKER_MODEL = os.getenv("BGE_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")

# Feature flags
BGE_M3_ENABLED = os.getenv("BGE_M3_ENABLED", "true").lower() == "true"
BGE_RERANKER_ENABLED = os.getenv("BGE_RERANKER_ENABLED", "true").lower() == "true"

# Model cache directory
MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", "/app/models")

# Service configuration
SERVICE_HOST = os.getenv("BGE_SERVICE_HOST", "0.0.0.0")
SERVICE_PORT = int(os.getenv("BGE_SERVICE_PORT", "8090"))

# Batch processing limits
MAX_BATCH_SIZE = int(os.getenv("BGE_MAX_BATCH_SIZE", "32"))
MAX_RERANK_DOCUMENTS = int(os.getenv("BGE_MAX_RERANK_DOCUMENTS", "100"))

# Timeouts
EMBED_TIMEOUT_SECONDS = int(os.getenv("BGE_EMBED_TIMEOUT", "60"))
RERANK_TIMEOUT_SECONDS = int(os.getenv("BGE_RERANK_TIMEOUT", "120"))

# Embedding dimensions (BGE-M3 outputs 1024-dim vectors)
EMBEDDING_DIMENSION = 1024
