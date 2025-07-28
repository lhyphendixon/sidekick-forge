# LiveKit Agent Container - Client-Specific Deployment
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/app

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
COPY requirements-agent.txt .

# Install Python dependencies (using full requirements for RAG support)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-agent.txt

# Copy the entire agent directory into the image
COPY . /app

# Create necessary directories
RUN mkdir -p /app/logs /app/temp && \
    chown -R agent:agent /app

# Make entrypoint executable
RUN chmod +x /app/entrypoint.py

# Switch to non-root user
USER agent

# Expose health check port
EXPOSE 8080

# Health check temporarily disabled
# HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
#     CMD curl -f http://localhost:8080/health || exit 1

# Run the entrypoint
CMD ["python", "/app/entrypoint.py"]