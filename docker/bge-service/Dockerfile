# BGE Local Embedding & Reranking Service
# Provides on-premise BGE-M3 embeddings and BGE-reranker-v2-m3 reranking

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create model cache directory
RUN mkdir -p /app/models

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HF_HOME=/app/models
ENV TRANSFORMERS_CACHE=/app/models
ENV TORCH_HOME=/app/models

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config.py .
COPY service.py .

# Create non-root user for security
RUN useradd -m -u 1000 bgeuser && \
    chown -R bgeuser:bgeuser /app
USER bgeuser

# Expose service port
EXPOSE 8090

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8090/health || exit 1

# Run service
CMD ["python", "service.py"]
