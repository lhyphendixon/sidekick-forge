# Perplexity MCP Integration

## Overview

The Sidekick Forge platform now routes Perplexity "Ask" ability calls through a shared Model Context Protocol (MCP) bridge. The bridge lives in a lightweight container that exposes an SSE transport at `/mcp/sse` and forwards tool invocations to Perplexity's `https://api.perplexity.ai/chat/completions` endpoint. Each request must include the tenant's Perplexity API key; the server simply forwards the payload and relays the raw error text when Perplexity rejects a call.

Key characteristics:

- Single shared container per deployment (no per-client sprawl)
- Per-request API key scoping; credentials are never baked into the image
- Automatic restart when assignments require the tool and the container is not healthy
- Strict error surfacing so agents see the exact Perplexity response

## Source layout

```
perplexity_mcp/
  app.py            # FastAPI + FastMCP bridge with the `perplexity_ask` tool
  Dockerfile        # Minimal Python 3.11 image running `uvicorn`
  requirements.txt  # Only FastAPI, httpx, and MCP SDK dependencies
```

The service only reads one environment variable: `PERPLEXITY_API_KEY`. If the value is missing the tool raises a configuration error.

## Container orchestration

`app/services/perplexity_mcp_manager.py` manages the shared container via the Docker API. When a client enables the Perplexity ability, `ToolsService.list_agent_tools` calls `PerplexityMCPManager.ensure_running()` which:

1. Looks for the container defined by `PERPLEXITY_MCP_CONTAINER_NAME` (defaults to `perplexity-mcp`).
2. Starts it if it exists but is stopped.
3. Otherwise runs the image defined by `PERPLEXITY_MCP_IMAGE` (default `perplexity-mcp:latest`) on the Sidekick Forge network and exposes port `PERPLEXITY_MCP_PORT` (defaults to 8081).
4. Returns the reachability URL (`http://perplexity-mcp:8081/mcp/sse`).

The manager injects a default API key from the host environment if `PERPLEXITY_API_KEY` is set. You can stop the container manually with `PerplexityMCPManager.stop()` when needed.

### Relevant settings

The following `app.config.Settings` fields control the container runtime:

- `PERPLEXITY_MCP_IMAGE` — image tag or name (default `perplexity-mcp:latest`)
- `PERPLEXITY_MCP_CONTAINER_NAME` — container name/alias (default `perplexity-mcp`)
- `PERPLEXITY_MCP_PORT` — container HTTP port (default `8081`)
- `PERPLEXITY_MCP_HOST` — hostname advertised to other containers (default `perplexity-mcp`)
- `PERPLEXITY_MCP_NETWORK` — override docker network; defaults to `${APP_NAME}-network`

## Ability activation flow

1. Admin assigns the "Perplexity Ask" ability to an agent.
2. `ToolsService.list_agent_tools` verifies the client has a `perplexity_api_key` stored in the platform database.
3. The service starts (or restarts) the MCP container and injects its `server_url`, schema, and API key metadata into the tool definition.
4. The agent worker receives the tool list, detects the MCP configuration, and connects to the MCP server through LiveKit's MCP client.
5. Every tool invocation passes the client's API key as the `api_key` argument to the remote MCP tool.

If the client lacks an API key the assignment fails fast with a 400 response so admins can correct the configuration.

## Running the MCP service manually

```bash
# From repo root
cd perplexity_mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PERPLEXITY_API_KEY="pplx-..."
uvicorn perplexity_mcp.app:app --host 0.0.0.0 --port 8081
```

Or build and run the container:

```bash
cd perplexity_mcp
docker build -t perplexity-mcp:latest .
docker run --rm -p 8081:8081 \
  -e PERPLEXITY_API_KEY="pplx-..." \
  --name perplexity-mcp \
  perplexity-mcp:latest
```

## Monitoring and troubleshooting

- **Container state**: `docker ps | grep perplexity-mcp`
- **Logs**: `docker logs perplexity-mcp`
- **Health check**: `curl http://perplexity-mcp:8081/healthz`

If agents report `Perplexity MCP ability is missing a server_url`, ensure the container is running and reachable on the shared network. If Perplexity rejects a request, the raw error message is propagated back to the agent session log.

## Rollback strategy

To fall back to the previous inline HTTP integration set `ENABLE_PERPLEXITY_INLINE_TOOL=true` in the agent environment. The tool registry will bypass the MCP container and call Perplexity directly until you remove the flag.

