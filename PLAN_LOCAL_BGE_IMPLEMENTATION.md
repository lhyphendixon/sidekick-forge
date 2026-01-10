# Implementation Plan: Local BGE-M3 Embeddings & BGE-Reranker-v2-M3

## Overview

Add on-premise/local BGE-M3 embedding model and BGE-reranker-v2-m3 reranking model as options for clients who want:
- Lower latency (no cross-region API calls)
- No per-API-call costs after setup
- Data privacy (embeddings never leave server)
- Open-source models (MIT licensed)

## Model Details

### BGE-M3 Embedding Model
- **Model**: `BAAI/bge-m3`
- **License**: MIT
- **Dimensions**: 1024 (matches current Qwen embeddings)
- **Size**: ~2.3GB
- **MTEB Score**: ~65-67 (vs Qwen3-Embedding at ~70)
- **Features**: Multilingual, dense + sparse + multi-vector retrieval
- **ONNX Support**: Yes (optimized inference)

### BGE-Reranker-v2-M3
- **Model**: `BAAI/bge-reranker-v2-m3`
- **License**: MIT
- **Size**: ~568M parameters (~2.2GB)
- **Features**: Multilingual, cross-encoder architecture
- **Use Case**: Re-score top-k candidates from initial retrieval

## Architecture Decision

### Option A: Sidecar Service (Recommended)
Run BGE models in a separate FastAPI container alongside the agent:
```
┌─────────────────┐     ┌─────────────────────┐
│  Agent Container │────▶│  BGE Service (Local)│
│  (stateless)     │     │  - /embed           │
│                  │◀────│  - /rerank          │
└─────────────────┘     └─────────────────────┘
```

**Pros:**
- Agent container stays small and stateless
- BGE service can be GPU-accelerated
- Models loaded once, shared across agent instances
- Easy to scale independently
- Can run on different hardware (GPU vs CPU)

**Cons:**
- Additional container to manage
- Network hop (localhost, ~1-2ms)

### Option B: In-Agent Embedding (Not Recommended)
Load models directly in agent container.

**Cons:**
- Agent container becomes stateful (model loading time)
- Memory bloat (~5GB+ per agent instance)
- Slower cold starts
- Cannot leverage GPU easily

**Decision**: Proceed with **Option A (Sidecar Service)**

## Implementation Plan

### Phase 1: BGE Service Container

#### 1.1 Create BGE Service
**New Files:**
- `/root/sidekick-forge/docker/bge-service/Dockerfile`
- `/root/sidekick-forge/docker/bge-service/requirements.txt`
- `/root/sidekick-forge/docker/bge-service/service.py`
- `/root/sidekick-forge/docker/bge-service/config.py`

**Service Endpoints:**
```
POST /embed
  Input: {"texts": ["query1", "query2"], "model": "bge-m3"}
  Output: {"embeddings": [[...], [...]], "dimensions": 1024}

POST /rerank
  Input: {"query": "...", "documents": ["doc1", "doc2"], "model": "bge-reranker-v2-m3", "top_n": 5}
  Output: {"results": [{"index": 0, "score": 0.95}, ...]}

GET /health
  Output: {"status": "ok", "models_loaded": ["bge-m3", "bge-reranker-v2-m3"]}
```

#### 1.2 Docker Compose Integration
**Modify:** `/root/sidekick-forge/docker-compose.yml`
```yaml
services:
  bge-service:
    build: ./docker/bge-service
    ports:
      - "8090:8090"
    environment:
      - DEVICE=cuda  # or cpu
      - BGE_M3_ENABLED=true
      - BGE_RERANKER_ENABLED=true
    volumes:
      - bge-models:/app/models  # Persistent model cache
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

### Phase 2: Agent Integration

#### 2.1 Add Local Embedder Class
**Modify:** `/root/sidekick-forge/docker/agent/context.py`

Add new class alongside `RemoteEmbedder`:
```python
class LocalBGEEmbedder:
    """Local BGE-M3 embeddings via sidecar service"""

    def __init__(self, service_url: str = "http://bge-service:8090"):
        self.service_url = service_url
        self.model = "bge-m3"

    async def create_embedding(self, text: str) -> List[float]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.service_url}/embed",
                json={"texts": [text], "model": self.model},
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()["embeddings"][0]
```

#### 2.2 Add Local Reranker Support
**Modify:** `/root/sidekick-forge/docker/agent/citations_service.py`

Add local reranking in `_model_rerank()`:
```python
elif rerank_provider == "local":
    # Call local BGE reranker service
    response = await self._call_local_reranker(query, documents, top_n)
```

#### 2.3 Update Provider Selection
**Modify:** `/root/sidekick-forge/docker/agent/context.py` - `_initialize_embedder()`

```python
provider = embedding_config.get("provider", "").lower()
if provider in ("local", "on-prem", "bge-local"):
    self.embedder = LocalBGEEmbedder(
        service_url=os.getenv("BGE_SERVICE_URL", "http://bge-service:8090")
    )
elif provider == "siliconflow":
    # existing code
```

### Phase 3: Configuration & Admin UI

#### 3.1 Update Data Models
**Modify:** `/root/sidekick-forge/app/models/client.py`

Add new provider options:
```python
class EmbeddingProvider(str, Enum):
    SILICONFLOW = "siliconflow"
    OPENAI = "openai"
    LOCAL_BGE = "local"  # "Local (BGE-M3)"

class RerankProvider(str, Enum):
    SILICONFLOW = "siliconflow"
    LOCAL_BGE = "local"  # "Local (BGE-Reranker)"
```

#### 3.2 Update Admin Dashboard
**Modify:** `/root/sidekick-forge/app/admin/routes.py`

Add dropdown options:
```html
<option value="local">Local (BGE-M3 On-Prem)</option>
```

#### 3.3 Update Schema Sync
**Modify:** `/root/sidekick-forge/app/services/schema_sync.py`

No changes needed - embedding/rerank config is JSON, already flexible.

### Phase 4: Testing & Validation

#### 4.1 Unit Tests
- Test LocalBGEEmbedder with mock service
- Test local reranker integration
- Test fallback behavior when service unavailable

#### 4.2 Integration Tests
- End-to-end RAG with local embeddings
- Compare latency: local vs SiliconFlow
- Validate embedding dimension compatibility (1024)

#### 4.3 Performance Benchmarks
- Measure embedding latency (target: <100ms)
- Measure reranking latency (target: <200ms for 20 docs)
- Memory usage of BGE service container

## Provider Naming Convention

For UI display:
| Internal Value | Display Name |
|---------------|--------------|
| `local` | Local (BGE-M3 On-Prem) |
| `siliconflow` | SiliconFlow (Cloud) |
| `openai` | OpenAI (Cloud) |

For reranker:
| Internal Value | Display Name |
|---------------|--------------|
| `local` | Local (BGE-Reranker On-Prem) |
| `siliconflow` | SiliconFlow (Cloud) |

## Environment Variables

New variables for agent container:
```bash
# BGE Service Configuration
BGE_SERVICE_URL=http://bge-service:8090
BGE_SERVICE_TIMEOUT=30

# Enable/disable local models
BGE_EMBEDDINGS_ENABLED=true
BGE_RERANKER_ENABLED=true
```

## Rollout Strategy

1. **Development**: Deploy BGE service on staging server
2. **Testing**: Migrate Autonomite (your test client) to local embeddings
3. **Validation**: Compare RAG quality between SiliconFlow and local
4. **Production**: Offer as opt-in for Champion tier clients

## Resource Requirements

### BGE Service Container
- **CPU Mode**: 4+ cores, 8GB RAM
- **GPU Mode**: NVIDIA GPU with 8GB+ VRAM, 4GB RAM
- **Disk**: 10GB for model cache

### Estimated Latency (GPU)
- Embedding: ~20-50ms per query
- Reranking: ~50-100ms for 20 documents

### Estimated Latency (CPU)
- Embedding: ~100-300ms per query
- Reranking: ~200-500ms for 20 documents

## Files to Create/Modify

### New Files
1. `/root/sidekick-forge/docker/bge-service/Dockerfile`
2. `/root/sidekick-forge/docker/bge-service/requirements.txt`
3. `/root/sidekick-forge/docker/bge-service/service.py`
4. `/root/sidekick-forge/docker/bge-service/config.py`

### Modified Files
1. `/root/sidekick-forge/docker-compose.yml` - Add bge-service
2. `/root/sidekick-forge/docker/agent/context.py` - Add LocalBGEEmbedder
3. `/root/sidekick-forge/docker/agent/citations_service.py` - Add local reranker
4. `/root/sidekick-forge/app/models/client.py` - Add provider enums
5. `/root/sidekick-forge/app/admin/routes.py` - Update UI dropdowns

## Decisions Made

1. **GPU vs CPU**: Default to CPU for broader compatibility (staging has no GPU)
2. **Model Preloading**: Preload at container start for consistent latency
3. **Batch Support**: Yes, add batch endpoint for document ingestion efficiency
4. **Fallback Behavior**: Fail fast (per existing NO FALLBACK policy)
5. **Testing**: Test on Autonomite first before offering to other clients

---

## Phase 5: Embedding Migration Flow

When a user changes their embedding provider (e.g., SiliconFlow → Local BGE-M3), existing embeddings become incompatible. We need a migration flow.

### 5.1 Migration Detection

**Trigger**: When admin saves agent/client settings with a different embedding provider or model.

**Detection Logic** (in admin routes):
```python
# Check if embedding config changed
old_provider = current_config.get("embedding", {}).get("provider")
new_provider = form_data.get("embedding_provider")
old_model = current_config.get("embedding", {}).get("document_model")
new_model = form_data.get("document_embedding_model")

if old_provider != new_provider or old_model != new_model:
    # Trigger migration flow
    return redirect(f"/admin/clients/{client_id}/migrate-embeddings?new_provider={new_provider}&new_model={new_model}")
```

### 5.2 Migration UI

**New Route**: `GET /admin/clients/{client_id}/migrate-embeddings`

**UI Components**:
1. **Warning Modal** explaining:
   - Current embeddings will be invalidated
   - All documents need re-embedding
   - Estimated time based on document count
   - Option to proceed or cancel

2. **Progress Page** showing:
   - Total documents to process
   - Current document being processed
   - Progress bar (percentage)
   - Estimated time remaining
   - Cancel button
   - Real-time log of processed documents

### 5.3 Migration Backend

**Reuse Existing Flow**: Leverage the document processing pipeline that already handles embeddings.

**Migration Job Structure**:
```python
@dataclass
class EmbeddingMigrationJob:
    client_id: str
    old_provider: str
    old_model: str
    new_provider: str
    new_model: str
    total_documents: int
    processed_documents: int
    status: str  # "pending", "in_progress", "completed", "failed", "cancelled"
    started_at: datetime
    error_message: Optional[str]
```

**Migration Steps**:
1. **Pause**: Disable RAG for client temporarily (optional, or allow degraded mode)
2. **Clear**: Set all `document_chunks.embeddings` to NULL for client's documents
3. **Queue**: Create re-embedding jobs for all documents
4. **Process**: Use existing document processor with new embedding config
5. **Complete**: Update client's embedding config, re-enable RAG

**API Endpoints**:
```
POST /api/admin/clients/{client_id}/migrate-embeddings
  Input: {"new_provider": "local", "new_model": "bge-m3"}
  Output: {"job_id": "uuid", "total_documents": 150}

GET /api/admin/clients/{client_id}/migrate-embeddings/status
  Output: {"status": "in_progress", "processed": 45, "total": 150, "percent": 30}

POST /api/admin/clients/{client_id}/migrate-embeddings/cancel
  Output: {"status": "cancelled"}
```

### 5.4 Progress Tracking

**Option A: Polling** (simpler)
- Frontend polls `/status` endpoint every 2 seconds
- Updates progress bar based on response

**Option B: WebSocket** (smoother)
- Real-time updates via WebSocket connection
- More complex but better UX

**Recommendation**: Start with polling (Option A), can upgrade later.

### 5.5 Files to Create/Modify for Migration

**New Files**:
- `/root/sidekick-forge/app/admin/templates/migrate_embeddings.html` - Migration UI
- `/root/sidekick-forge/app/services/embedding_migration.py` - Migration logic

**Modified Files**:
- `/root/sidekick-forge/app/admin/routes.py` - Add migration routes
- `/root/sidekick-forge/app/models/client.py` - Add migration job model

### 5.6 Database Schema for Migration Jobs

```sql
CREATE TABLE IF NOT EXISTS public.embedding_migration_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES public.clients(id),
    old_provider TEXT,
    old_model TEXT,
    new_provider TEXT NOT NULL,
    new_model TEXT NOT NULL,
    total_documents INTEGER DEFAULT 0,
    processed_documents INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
```
