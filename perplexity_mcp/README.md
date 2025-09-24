# Perplexity MCP Bridge

This directory contains the shared Model Context Protocol server that forwards Perplexity "Ask" ability requests to `https://api.perplexity.ai/chat/completions`.

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PERPLEXITY_API_KEY="pplx-..."
uvicorn perplexity_mcp.app:app --host 0.0.0.0 --port 8081
```

## Docker usage

```bash
docker build -t perplexity-mcp:latest .
docker run --rm -p 8081:8081 \
  -e PERPLEXITY_API_KEY="pplx-..." \
  perplexity-mcp:latest
```

The service implements the MCP SSE handshake at `/mcp/sse` and publishes plugin metadata at `/.well-known/ai-plugin.json`. Only `PERPLEXITY_API_KEY` is read from the environment; tenants can still provide a key per request.

Operational guidance for the platform integration lives in `../docs/perplexity-mcp.md`.

