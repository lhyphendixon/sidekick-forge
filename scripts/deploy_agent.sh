#!/bin/bash
set -e

echo "🚀 AUTOMATED AGENT DEPLOYMENT PIPELINE"
echo "====================================="

# Configuration
AGENT_IMAGE="autonomite/agent-runtime"
BUILD_TAG="latest"
DEPLOY_TAG="deployed-$(date +%Y%m%d-%H%M%S)"
AGENT_DIR="/opt/autonomite-saas/agent-runtime"
CLIENT_ID="${CLIENT_ID:-df91fd06-816f-4273-a903-5a4861277040}"
AGENT_SLUG="${AGENT_SLUG:-clarence-coherence}"

# Step 1: Check for Buildx
echo "📦 Checking Docker Buildx..."
if ! docker buildx version &>/dev/null; then
    echo "Buildx not available, using standard build"
    USE_BUILDX=false
else
    echo "Buildx available"
    USE_BUILDX=true
    # Create builder if it doesn't exist
    if ! docker buildx ls | grep -q agent-builder; then
        docker buildx create --name agent-builder --driver docker-container
    fi
    docker buildx use agent-builder
fi

# Step 2: Build with Buildx (faster, cached)
echo "🔨 Building new agent image..."
cd "$AGENT_DIR"

# Add build timestamp to verify deployment
echo "BUILD_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > build_info.txt

# Build with proper caching
if [ "$USE_BUILDX" = "true" ]; then
    docker buildx build \
        -t "${AGENT_IMAGE}:${BUILD_TAG}" \
        -t "${AGENT_IMAGE}:${DEPLOY_TAG}" \
        --platform linux/amd64 \
        --load \
        --cache-from type=local,src=/tmp/buildx-cache \
        --cache-to type=local,dest=/tmp/buildx-cache \
        --progress=plain \
        .
else
    # Standard build
    docker build \
        -t "${AGENT_IMAGE}:${BUILD_TAG}" \
        -t "${AGENT_IMAGE}:${DEPLOY_TAG}" \
        --no-cache \
        .
fi

# Step 3: Verify build
echo "🔍 Verifying build..."
BUILD_ID=$(docker inspect "${AGENT_IMAGE}:${DEPLOY_TAG}" --format='{{.Id}}')
echo "Build ID: ${BUILD_ID}"

# Check for critical files in image
docker run --rm "${AGENT_IMAGE}:${DEPLOY_TAG}" ls -la /app/ | grep -E "session_agent.py|start_agent_session.sh" || {
    echo "❌ Build verification failed: Missing critical files"
    exit 1
}

# Step 4: Stop old containers gracefully
echo "🛑 Stopping old containers..."
OLD_CONTAINERS=$(docker ps -q --filter "name=agent_${CLIENT_ID:0:8}" || true)
if [ -n "$OLD_CONTAINERS" ]; then
    echo "Found containers to stop: $OLD_CONTAINERS"
    docker stop -t 30 $OLD_CONTAINERS || true
    docker rm $OLD_CONTAINERS || true
fi

# Step 5: Deploy new container
echo "🚀 Deploying new container..."
CONTAINER_NAME="agent_${CLIENT_ID:0:8}_${AGENT_SLUG//-/_}"

# Get environment from existing deployment or use defaults
ENV_FILE="/tmp/agent_env_${CLIENT_ID}.env"
if [ -f "$ENV_FILE" ]; then
    echo "Using existing environment file"
else
    echo "Creating default environment file"
    cat > "$ENV_FILE" <<EOF
LIVEKIT_URL=wss://litebridge-hw6srhvi.livekit.cloud
LIVEKIT_API_KEY=APIUtuiQ47BQBsk
LIVEKIT_API_SECRET=qLhQa9NP5J7XtKOsm7b1rH04idgdxQFJRJ4IzwIxQcjM
CLIENT_ID=${CLIENT_ID}
AGENT_SLUG=${AGENT_SLUG}
AGENT_NAME=Clarence Coherence
GROQ_API_KEY=${GROQ_API_KEY}
DEEPGRAM_API_KEY=${DEEPGRAM_API_KEY}
CARTESIA_API_KEY=${CARTESIA_API_KEY}
OPENAI_API_KEY=${OPENAI_API_KEY}
LOG_LEVEL=INFO
EOF
fi

docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    --env-file "$ENV_FILE" \
    --network autonomite-agents-network \
    -p 8080:8080 \
    --label "autonomite.managed=true" \
    --label "autonomite.client_id=${CLIENT_ID}" \
    --label "autonomite.deploy_tag=${DEPLOY_TAG}" \
    --label "autonomite.deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "${AGENT_IMAGE}:${DEPLOY_TAG}"

# Step 6: Wait for startup
echo "⏳ Waiting for container startup..."
sleep 10

# Step 7: Verify deployment
echo "✅ Verifying deployment..."
CONTAINER_ID=$(docker ps -q --filter "name=${CONTAINER_NAME}")

if [ -z "$CONTAINER_ID" ]; then
    echo "❌ Container failed to start"
    exit 1
fi

# Check health
HEALTH=$(docker inspect "${CONTAINER_NAME}" --format='{{.State.Health.Status}}' 2>/dev/null || echo "none")
echo "Container health: ${HEALTH}"

# Check for new code markers
echo "🔍 Checking for deployment markers..."
MARKERS_FOUND=0

# Check for session agent startup
if docker logs "${CONTAINER_NAME}" 2>&1 | grep -q "Starting session agent"; then
    echo "✅ Session agent startup confirmed"
    ((MARKERS_FOUND++))
else
    echo "❌ Session agent startup not found"
fi

# Check for event handler registration
if docker logs "${CONTAINER_NAME}" 2>&1 | grep -q "Event handlers registered"; then
    echo "✅ Event handlers registered"
    ((MARKERS_FOUND++))
else
    echo "❌ Event handlers not registered"
fi

# Check for greeting capability
if docker logs "${CONTAINER_NAME}" 2>&1 | grep -q "Attempting to send greeting"; then
    echo "✅ Greeting capability present"
    ((MARKERS_FOUND++))
else
    echo "⚠️  No greeting attempts yet (normal if no room joined)"
fi

# Check worker registration
if docker logs "${CONTAINER_NAME}" 2>&1 | grep -q "registered worker"; then
    echo "✅ Worker registered with LiveKit"
    ((MARKERS_FOUND++))
else
    echo "❌ Worker not registered"
fi

# Step 8: Runtime test
echo "🧪 Running runtime test..."
TEST_ROOM="deploy_test_$(date +%s)"
curl -X POST "http://localhost:8000/api/v1/trigger-agent" \
    -H "Content-Type: application/json" \
    -d "{
        \"agent_slug\": \"${AGENT_SLUG}\",
        \"mode\": \"voice\",
        \"room_name\": \"${TEST_ROOM}\",
        \"user_id\": \"deploy-test\",
        \"client_id\": \"${CLIENT_ID}\"
    }" \
    -o /tmp/trigger_response.json \
    -w "\nHTTP Status: %{http_code}\n" \
    -s

# Wait for agent to process
sleep 5

# Check if agent received the test room
if docker logs "${CONTAINER_NAME}" 2>&1 | grep -q "${TEST_ROOM}"; then
    echo "✅ Agent received test room request"
    ((MARKERS_FOUND++))
else
    echo "❌ Agent did not receive test room"
fi

# Final report
echo ""
echo "📊 DEPLOYMENT REPORT"
echo "===================="
echo "Image: ${AGENT_IMAGE}:${DEPLOY_TAG}"
echo "Container: ${CONTAINER_NAME}"
echo "Build ID: ${BUILD_ID}"
echo "Health: ${HEALTH}"
echo "Verification markers found: ${MARKERS_FOUND}/5"

if [ $MARKERS_FOUND -ge 3 ]; then
    echo ""
    echo "✅ DEPLOYMENT SUCCESSFUL"
    echo "The agent is ready for preview testing."
    
    # Save deployment info
    cat > "/tmp/last_deployment.json" <<EOF
{
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "image": "${AGENT_IMAGE}:${DEPLOY_TAG}",
    "container": "${CONTAINER_NAME}",
    "build_id": "${BUILD_ID}",
    "markers_found": ${MARKERS_FOUND},
    "status": "success"
}
EOF
else
    echo ""
    echo "❌ DEPLOYMENT VERIFICATION FAILED"
    echo "Check logs: docker logs ${CONTAINER_NAME}"
    exit 1
fi