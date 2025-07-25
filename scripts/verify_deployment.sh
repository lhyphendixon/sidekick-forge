#!/bin/bash

echo "🔍 AGENT DEPLOYMENT VERIFICATION"
echo "================================"

# Find agent containers
CONTAINERS=$(docker ps --filter "label=autonomite.managed=true" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Labels}}")

if [ -z "$CONTAINERS" ]; then
    echo "❌ No managed agent containers found"
    exit 1
fi

echo "📦 Running Containers:"
echo "$CONTAINERS"

# Check each container
for CONTAINER in $(docker ps -q --filter "label=autonomite.managed=true"); do
    NAME=$(docker inspect $CONTAINER --format='{{.Name}}' | sed 's/^\/*//')
    echo ""
    echo "🔍 Checking container: $NAME"
    echo "----------------------------"
    
    # Get deployment info
    DEPLOY_TAG=$(docker inspect $CONTAINER --format='{{index .Config.Labels "autonomite.deploy_tag"}}')
    DEPLOYED_AT=$(docker inspect $CONTAINER --format='{{index .Config.Labels "autonomite.deployed_at"}}')
    CLIENT_ID=$(docker inspect $CONTAINER --format='{{index .Config.Labels "autonomite.client_id"}}')
    
    echo "Deploy Tag: $DEPLOY_TAG"
    echo "Deployed At: $DEPLOYED_AT"
    echo "Client ID: $CLIENT_ID"
    
    # Check health
    HEALTH=$(docker inspect $CONTAINER --format='{{.State.Health.Status}}' 2>/dev/null || echo "none")
    echo "Health Status: $HEALTH"
    
    # Check for critical code markers
    echo ""
    echo "Code Verification:"
    
    # Check session agent
    if docker exec $CONTAINER test -f /app/session_agent.py; then
        echo "✅ session_agent.py present"
        
        # Check for greeting code
        if docker exec $CONTAINER grep -q "Greeting sent successfully" /app/session_agent.py; then
            echo "✅ Greeting success logging present"
        else
            echo "❌ Greeting success logging missing"
        fi
        
        # Check for event handlers
        if docker exec $CONTAINER grep -q "user_speech_committed" /app/session_agent.py; then
            echo "✅ Speech event handlers present"
        else
            echo "❌ Speech event handlers missing"
        fi
    else
        echo "❌ session_agent.py not found"
    fi
    
    # Check recent logs
    echo ""
    echo "Recent Activity (last 10 lines):"
    docker logs $CONTAINER --tail 10 2>&1 | grep -E "registered worker|Job request|Greeting|ERROR" || echo "No significant activity"
    
    # Check for stuck processes
    PROCESS_ERRORS=$(docker logs $CONTAINER 2>&1 | grep -c "process did not exit in time" || true)
    if [ $PROCESS_ERRORS -gt 0 ]; then
        echo "⚠️  Warning: Found $PROCESS_ERRORS stuck process errors"
    fi
done

# Check for build cache
echo ""
echo "📦 Build Cache Status:"
if [ -d "/tmp/buildx-cache" ]; then
    CACHE_SIZE=$(du -sh /tmp/buildx-cache | cut -f1)
    echo "Cache size: $CACHE_SIZE"
else
    echo "No build cache found"
fi

# Last deployment info
if [ -f "/tmp/last_deployment.json" ]; then
    echo ""
    echo "📋 Last Deployment:"
    cat /tmp/last_deployment.json | jq . || cat /tmp/last_deployment.json
fi