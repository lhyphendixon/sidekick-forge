#!/bin/bash
# Build script for the new agent implementation

set -e

echo "🏗️ Building Autonomite Agent Docker image..."

# Navigate to repository root
cd "$(dirname "$0")/../.."

# Build the image
docker build -f docker/agent/Dockerfile -t autonomite/agent:latest .

echo "✅ Build complete!"
echo ""
echo "To run the agent:"
echo "docker run --rm -it \\"
echo "  -e LIVEKIT_URL=wss://your-livekit-server.com \\"
echo "  -e LIVEKIT_API_KEY=your-api-key \\"
echo "  -e LIVEKIT_API_SECRET=your-api-secret \\"
echo "  autonomite/agent:latest"