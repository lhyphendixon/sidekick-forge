"""
Content Recon Ability (Ahrefs)

Builds a LiveKit function tool that proxies requests to the Ahrefs remote
MCP server over Streamable HTTP transport. Provides 42+ SEO analytics tools
via a single meta-tool interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

AHREFS_MCP_URL = "https://api.ahrefs.com/mcp/mcp"


class AhrefsAbilityConfigError(Exception):
    pass


class AhrefsMCPClient:
    """Communicates with the Ahrefs remote MCP server over Streamable HTTP."""

    def __init__(self, api_key: str, *, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._next_id = 1
        self._initialized = False

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            )
        return self._session

    async def _send_request(self, method: str, params: Dict[str, Any]) -> Any:
        session = await self._ensure_session()
        req_id = self._next_id
        self._next_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        async with session.post(AHREFS_MCP_URL, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise AhrefsAbilityConfigError(
                    f"Ahrefs MCP returned HTTP {resp.status}: {body[:500]}"
                )
            data = await resp.json()

        if "error" in data:
            err = data["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise AhrefsAbilityConfigError(f"Ahrefs MCP error: {msg}")

        return data.get("result")

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "sidekick-forge", "version": "1.0.0"},
        })
        # Send initialized notification (no response expected)
        session = await self._ensure_session()
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        async with session.post(AHREFS_MCP_URL, json=notification) as resp:
            await resp.read()
        self._initialized = True
        logger.info("Ahrefs MCP remote server initialized successfully")

    async def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Call an Ahrefs MCP tool via the remote server."""
        await self._ensure_initialized()

        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": params,
        })

        # MCP tool results come as {"content": [{"type": "text", "text": "..."}]}
        if isinstance(result, dict) and "content" in result:
            contents = result["content"]
            texts = []
            for item in contents:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item["text"])
            combined = "\n".join(texts)
            try:
                return json.loads(combined)
            except (json.JSONDecodeError, TypeError):
                return {"raw_text": combined}
        return result or {}

    async def get_tool_doc(self, tool_name: str) -> Dict[str, Any]:
        """Get the full schema documentation for a specific tool."""
        return await self.call_tool("doc", {"tool": tool_name})

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._initialized = False


def build_ahrefs_tool(
    tool_def: Dict[str, Any],
    config: Dict[str, Any],
    *,
    api_keys: Optional[Dict[str, str]] = None,
) -> Any:
    """Build the Content Recon (Ahrefs) LiveKit function tool.

    Uses a single meta-tool that accepts an Ahrefs tool name + params,
    proxying requests to the remote Ahrefs MCP server.
    """
    from livekit.agents.llm.tool_context import function_tool as lk_function_tool

    slug = tool_def.get("slug") or "content_recon"

    # Resolve API key: config > api_keys > env
    api_key = config.get("ahrefs_api_key")
    if not api_key and api_keys:
        api_key = api_keys.get("ahrefs_api_key")
    if not api_key:
        api_key = os.getenv("AHREFS_API_KEY")
    if not api_key:
        raise AhrefsAbilityConfigError(
            "Ahrefs API key not configured. Set ahrefs_api_key in client settings "
            "or AHREFS_API_KEY environment variable."
        )

    timeout = float(config.get("timeout", 30))
    mcp_client = AhrefsMCPClient(api_key=api_key, timeout=timeout)

    description = (
        "Query Ahrefs for SEO and content marketing intelligence. "
        "Pass an Ahrefs tool name and its parameters to get domain analytics, "
        "keyword research, backlink data, competitor analysis, SERP data, and more.\n\n"
        "IMPORTANT: Use the 'doc' tool first to get the exact input schema for any tool:\n"
        '  {"tool": "doc", "params": {"tool": "site-explorer-organic-keywords"}}\n\n'
        "Common examples:\n"
        '- Domain rating: {"tool": "site-explorer-domain-rating", "params": {"target": "example.com", "date": "2026-04-01"}}\n'
        '- Organic keywords: {"tool": "site-explorer-organic-keywords", "params": {"select": "keyword,position,volume,traffic", "target": "example.com", "date": "2026-04-01", "country": "us"}}\n'
        '- Keyword overview: {"tool": "keywords-explorer-overview", "params": {"select": "keyword,volume,keyword_difficulty,cpc", "country": "us", "keywords": ["content marketing"]}}\n'
        '- Competitors: {"tool": "site-explorer-organic-competitors", "params": {"select": "domain,common_keywords,organic_traffic", "target": "example.com", "country": "us", "date": "2026-04-01"}}\n'
        '- Backlinks overview: {"tool": "site-explorer-backlinks-stats", "params": {"target": "example.com", "date": "2026-04-01"}}\n'
        '- Related keywords: {"tool": "keywords-explorer-related-terms", "params": {"select": "keyword,volume,keyword_difficulty", "country": "us", "keywords": ["seo strategy"]}}\n'
        '- Top pages: {"tool": "site-explorer-top-pages", "params": {"select": "url,organic_traffic,organic_keywords", "target": "example.com", "date": "2026-04-01", "country": "us"}}\n'
        '- SERP overview: {"tool": "serp-overview-serp-overview", "params": {"select": "url,position,backlinks,domain_rating", "country": "us", "keyword": "content marketing"}}\n\n'
        "Key parameters: target (domain/URL), date (YYYY-MM-DD), country (2-letter code), "
        "select (comma-separated columns), mode (exact|prefix|domain|subdomains).\n"
        "Always include 'select' — it controls which columns are returned."
    )

    raw_schema = {
        "name": slug,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "description": (
                        "The Ahrefs MCP tool name to call (e.g. site-explorer-organic-keywords, "
                        "keywords-explorer-overview, site-explorer-domain-rating, doc)"
                    ),
                },
                "params": {
                    "type": "object",
                    "description": "Tool-specific parameters. Use the 'doc' tool to get the exact schema.",
                    "additionalProperties": True,
                },
            },
            "required": ["tool", "params"],
            "additionalProperties": False,
        },
    }

    async def _invoke(**kwargs: Any) -> str:
        tool_name = kwargs.get("tool", "")
        params = kwargs.get("params", {})

        if not tool_name:
            return json.dumps({"error": "Missing 'tool' parameter. Specify which Ahrefs tool to call."})

        logger.info("Content Recon (Ahrefs) tool call: %s with params: %s", tool_name, params)

        try:
            result = await mcp_client.call_tool(tool_name, params)
            output = {
                "tool": tool_name,
                "params": params,
                "data": result,
                "source": "Ahrefs",
            }
            return json.dumps(output, default=str)
        except AhrefsAbilityConfigError as exc:
            logger.error("Ahrefs MCP error: %s", exc)
            return json.dumps({"error": str(exc), "tool": tool_name})
        except Exception as exc:
            logger.error("Ahrefs tool call failed: %s", exc, exc_info=True)
            return json.dumps({"error": f"Ahrefs request failed: {exc}", "tool": tool_name})

    return lk_function_tool(raw_schema=raw_schema)(_invoke)
