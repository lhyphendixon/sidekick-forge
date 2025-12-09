#!/bin/bash
# Quick check of agent status and version
# Usage: ./check-agent.sh

echo "Agent Status Check"
echo "=================="
echo ""

# Container status
echo "📦 Container:"
docker ps --format "  {{.Names}}: {{.Status}} ({{.CreatedAt}})" | grep agent || echo "  No agent container running"

# Build version from logs
echo ""
echo "🏷️  Running Version:"
docker logs sidekick-forge-agent-worker-1 2>&1 | grep "BUILD VERSION" | tail -1 | sed 's/^/  /'

# Source file version
echo ""
echo "📄 Source Version (entrypoint.py):"
grep "^AGENT_BUILD" /root/sidekick-forge/docker/agent/entrypoint.py | sed 's/^/  /'

# Check if they match
RUNNING=$(docker logs sidekick-forge-agent-worker-1 2>&1 | grep "BUILD VERSION" | tail -1 | grep -oP '\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z' || echo "unknown")
SOURCE=$(grep "^AGENT_BUILD_VERSION" /root/sidekick-forge/docker/agent/entrypoint.py | grep -oP '\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z' || echo "unknown")

echo ""
if [ "$RUNNING" = "$SOURCE" ] && [ "$RUNNING" != "unknown" ]; then
    echo "✅ Versions match - deployment is current"
else
    echo "⚠️  VERSION MISMATCH - rebuild required!"
    echo "   Running: $RUNNING"
    echo "   Source:  $SOURCE"
    echo ""
    echo "   Run: ./deploy-agent.sh"
fi

# Worker registration
echo ""
echo "🔗 Worker Registration:"
docker logs sidekick-forge-agent-worker-1 2>&1 | grep "registered worker" | tail -1 | sed 's/^/  /'

echo ""
