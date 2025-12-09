#!/bin/bash
# Deploy script for the agent worker
# Usage: ./deploy-agent.sh [version-tag]
# Example: ./deploy-agent.sh "fix-echo-suppression"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Generate version info
BUILD_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
VERSION_TAG="${1:-$(date +%Y%m%d-%H%M%S)}"

echo "========================================"
echo "  Agent Deployment"
echo "========================================"
echo "Build time: $BUILD_TIME"
echo "Version tag: $VERSION_TAG"
echo ""

# Update version in entrypoint.py
echo "📝 Updating build version in entrypoint.py..."
sed -i "s/^AGENT_BUILD_VERSION = .*/AGENT_BUILD_VERSION = \"$BUILD_TIME\"/" docker/agent/entrypoint.py
sed -i "s/^AGENT_BUILD_HASH = .*/AGENT_BUILD_HASH = \"$VERSION_TAG\"/" docker/agent/entrypoint.py

# Show the updated version
echo "   Version updated to: $BUILD_TIME ($VERSION_TAG)"

# Build the new image
echo ""
echo "🔨 Building Docker image..."
docker compose build agent-worker --no-cache

# Stop and remove old container
echo ""
echo "🛑 Stopping old container..."
docker compose down agent-worker

# Start new container
echo ""
echo "🚀 Starting new container..."
docker compose up -d agent-worker

# Wait for startup and show logs
echo ""
echo "⏳ Waiting for worker to register..."
sleep 8

# Show startup logs to verify version
echo ""
echo "📋 Startup logs:"
echo "----------------------------------------"
docker logs sidekick-forge-agent-worker-1 --since 30s 2>&1 | grep -E "BUILD VERSION|registered worker|Starting agent"
echo "----------------------------------------"

echo ""
echo "✅ Deployment complete!"
echo ""
echo "To verify the deployment:"
echo "  docker logs sidekick-forge-agent-worker-1 --since 1m | grep 'BUILD VERSION'"
echo ""
