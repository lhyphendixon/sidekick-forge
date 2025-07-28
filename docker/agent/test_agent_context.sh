#!/bin/bash
# Test script to run agent with context system

# Set environment variables
export LIVEKIT_URL="wss://litebridge-hw6srhvi.livekit.cloud"
export LIVEKIT_API_KEY="APIUtuiQ47BQBsk"
export LIVEKIT_API_SECRET="test_secret"
export AGENT_NAME="sidekick-agent"
export DEVELOPMENT_MODE="true"

# Platform Supabase (for loading API keys)
export SUPABASE_URL="https://eukudpgfpihxsypulopm.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="test_key"

echo "Starting agent with context system test..."
echo "Development mode enabled - will show enhanced prompts"

# Run the agent directly
docker run --rm \
  -e LIVEKIT_URL="$LIVEKIT_URL" \
  -e LIVEKIT_API_KEY="$LIVEKIT_API_KEY" \
  -e LIVEKIT_API_SECRET="$LIVEKIT_API_SECRET" \
  -e AGENT_NAME="$AGENT_NAME" \
  -e DEVELOPMENT_MODE="$DEVELOPMENT_MODE" \
  -e SUPABASE_URL="$SUPABASE_URL" \
  -e SUPABASE_SERVICE_ROLE_KEY="$SUPABASE_SERVICE_ROLE_KEY" \
  autonomite/agent-runtime:test \
  python /app/entrypoint.py --version

echo "Test completed."