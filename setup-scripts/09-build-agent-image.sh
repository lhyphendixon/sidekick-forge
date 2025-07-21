#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Building LiveKit Agent Docker Image${NC}"

# Change to docker directory
cd /opt/autonomite-saas/docker

# Build the agent image
echo -e "${GREEN}Building autonomite/livekit-agent:latest${NC}"
docker-compose -f docker-compose.production.yml --profile build build agent-builder

# Tag the image with version
VERSION=$(date +%Y%m%d-%H%M%S)
docker tag autonomite/livekit-agent:latest autonomite/livekit-agent:$VERSION

echo -e "${GREEN}Agent image built successfully${NC}"
echo -e "${YELLOW}Image tags:${NC}"
echo "  - autonomite/livekit-agent:latest"
echo "  - autonomite/livekit-agent:$VERSION"

# Clean up builder container
docker-compose -f docker-compose.production.yml --profile build down

echo -e "${GREEN}Agent image is ready for deployment${NC}"