# LiveKit Agent Container - Client-Specific Deployment
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/app \
    HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface \
    HUGGINGFACE_HUB_CACHE=/app/.cache/huggingface

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    ffmpeg \
    libpq-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR $APP_HOME

# Create non-root user
RUN groupadd -r agent && useradd -r -g agent agent

# Copy requirements file from the agent directory
COPY docker/agent/requirements-agent.txt .

# Install Python dependencies (using full requirements for RAG support)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-agent.txt

# Copy the entire agent directory into the image
COPY docker/agent/ /app

# Create necessary directories including home for model cache and Bithuman IMX models
RUN mkdir -p /app/logs /app/temp /app/models /home/agent/.cache && \
    chown -R agent:agent /app /home/agent

# Make entrypoint executable
RUN chmod +x /app/entrypoint.py

# Set dummy environment variables for download-files command
ENV LIVEKIT_URL="wss://dummy.livekit.cloud" \
    LIVEKIT_API_KEY="dummy_key" \
    LIVEKIT_API_SECRET="dummy_secret"

# Switch to root to download models with correct permissions
USER root

# Create cache directories and download turn detector model from HuggingFace
RUN mkdir -p /app/.cache/huggingface /home/agent/.cache/huggingface && \
    python -c "from huggingface_hub import snapshot_download; path = snapshot_download('livekit/turn-detector', revision='v1.2.2-en', cache_dir='/app/.cache/huggingface'); print(f'Model downloaded to: {path}')" && \
    ls -la /app/.cache/huggingface/ && \
    chown -R agent:agent /app /home/agent && \
    chmod -R 777 /app/.cache /home/agent/.cache

# Switch back to non-root user
USER agent

# Expose health check port
EXPOSE 8080

# Health check temporarily disabled
# HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
#     CMD curl -f http://localhost:8080/health || exit 1

# Run the entrypoint
CMD ["python", "/app/entrypoint.py"]