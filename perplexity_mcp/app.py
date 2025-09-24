import logging
import os
from typing import Annotated, List, Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("perplexity_mcp")
DEFAULT_MODEL = "sonar-pro"
DEFAULT_TIMEOUT = 45.0
PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO)
    LOGGER.setLevel(logging.INFO)


class ChatMessage(BaseModel):
    role: Annotated[str, Field(description="system | user | assistant")]
    content: Annotated[str, Field(description="Plain text content of the message")]


MessagesParam = Annotated[
    List[ChatMessage],
    Field(description="Conversation messages in chronological order"),
]
ModelOverride = Annotated[
    Optional[str],
    Field(description="Override the default Perplexity model", example="sonar-reasoning"),
]
APIKeyParam = Annotated[
    Optional[str],
    Field(description="Perplexity API key override. If omitted, the PERPLEXITY_API_KEY env var is used."),
]


server = FastMCP(
    name="Perplexity MCP Gateway",
    instructions=(
        "A lightweight Model Context Protocol bridge that forwards tool calls to "
        "Perplexity's Sonar chat completions API."
    ),
    mount_path="/mcp",
    sse_path="/sse",
    message_path="/messages/",
)


async def _perform_perplexity_request(
    messages: List[ChatMessage],
    model: Optional[str],
    api_key: Optional[str],
    ctx: Optional[Context],
) -> str:
    key = (api_key or os.getenv("PERPLEXITY_API_KEY", "")).strip()
    if not key:
        raise RuntimeError(
            "Missing Perplexity API key. Provide it via the api_key argument or PERPLEXITY_API_KEY env var."
        )

    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": [m.model_dump() for m in messages],
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(DEFAULT_TIMEOUT, connect=10.0, read=DEFAULT_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(PERPLEXITY_API_URL, json=payload, headers=headers)

    body = response.text
    if response.status_code >= 400:
        detail = body.strip() or response.reason_phrase
        raise RuntimeError(f"Perplexity API error {response.status_code}: {detail}")

    try:
        data = response.json()
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise RuntimeError(f"Failed to parse Perplexity response: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Perplexity response missing message content") from exc

    citations = data.get("citations") or []
    if isinstance(citations, list) and citations:
        lines = [content, "", "Citations:"]
        lines.extend(f"[{idx + 1}] {cite}" for idx, cite in enumerate(citations))
        content = "\n".join(lines)

    if ctx is not None:
        await ctx.info(
            "Perplexity request completed",
            model=payload["model"],
            message_count=len(messages),
            citation_count=len(citations),
        )

    return content


@server.tool(
    name="perplexity_ask",
    title="Perplexity Ask",
    description="Query Perplexity Sonar for web-grounded answers.",
)
async def perplexity_ask(
    messages: MessagesParam,
    model: ModelOverride = None,
    api_key: APIKeyParam = None,
    ctx: Optional[Context] = None,
) -> str:
    """Forward a conversation history to Perplexity Sonar and return the textual answer."""

    if not messages:
        raise RuntimeError("messages must contain at least one entry")

    # Validate roles eagerly to provide clear feedback
    for idx, message in enumerate(messages):
        role = (message.role or "").lower()
        if role not in {"system", "user", "assistant"}:
            raise RuntimeError(f"messages[{idx}].role must be system, user, or assistant")

    if ctx is not None:
        await ctx.debug(
            "Dispatching Perplexity request",
            message_count=len(messages),
            model=model or DEFAULT_MODEL,
        )

    return await _perform_perplexity_request(messages, model, api_key, ctx)


PLUGIN_METADATA = {
    "schema_version": "v1",
    "name_for_human": "Perplexity Ask",
    "name_for_model": "perplexity_ask",
    "description_for_human": (
        "Get fresh answers from Perplexity's Sonar model without leaving Sidekick Forge."
    ),
    "description_for_model": (
        "Use this tool to answer questions that need up-to-the-minute web research via Perplexity Sonar."
    ),
    "auth": {"type": "none"},
    "api": {"type": "sse", "url": "/mcp/sse"},
    "contact_email": "support@sidekickforge.com",
    "legal_info_url": "https://sidekickforge.com/legal",
}


def create_app() -> FastAPI:
    _configure_logging()
    app = FastAPI(title="Perplexity MCP", version="1.0.0")

    @app.get("/.well-known/ai-plugin.json", response_model=dict)
    async def well_known_manifest() -> JSONResponse:
        return JSONResponse(PLUGIN_METADATA)

    @app.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "perplexity-mcp", "status": "ready"}

    # Mount the MCP SSE transport under /mcp
    app.mount("/mcp", server.sse_app())

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8081)
