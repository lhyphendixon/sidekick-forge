#!/bin/bash
# Quick restart script for immediate testing

echo "🔄 QUICK AGENT RESTART"
echo "===================="

CONTAINER_NAME="agent_df91fd06_clarence_coherence"

# Check if container exists
if ! docker ps -a | grep -q "$CONTAINER_NAME"; then
    echo "❌ Container $CONTAINER_NAME not found"
    exit 1
fi

# Get current container info
echo "📦 Current container info:"
docker ps --filter "name=$CONTAINER_NAME" --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"

# Restart container
echo ""
echo "🔄 Restarting container..."
docker restart "$CONTAINER_NAME"

# Wait for startup
echo "⏳ Waiting for startup..."
sleep 10

# Verify health
HEALTH=$(docker inspect "$CONTAINER_NAME" --format='{{.State.Health.Status}}' 2>/dev/null || echo "none")
echo "✅ Health status: $HEALTH"

# Check logs for startup
echo ""
echo "📋 Startup logs:"
docker logs "$CONTAINER_NAME" --tail 20 2>&1 | grep -E "registered worker|Starting session agent|Event handlers registered"

# Check for errors
ERROR_COUNT=$(docker logs "$CONTAINER_NAME" 2>&1 | grep -c "ERROR" || true)
if [ $ERROR_COUNT -gt 0 ]; then
    echo "⚠️  Found $ERROR_COUNT errors in logs"
fi

echo ""
echo "✅ Container restarted and ready for testing"
echo "Try your preview now!"