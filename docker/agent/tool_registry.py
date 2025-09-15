from __future__ import annotations

import json
import aiohttp
from typing import Any, Dict, List, Callable
from livekit.agents import llm


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, llm.FunctionTool] = {}

    def build(self, tool_defs: List[Dict[str, Any]]) -> List[llm.FunctionTool]:
        self._tools.clear()
        out: List[llm.FunctionTool] = []
        for t in tool_defs or []:
            try:
                ttype = t.get("type")
                if ttype == "n8n":
                    ft = self._build_n8n_tool(t)
                elif ttype == "sidekick":
                    ft = self._build_sidekick_tool(t)
                elif ttype == "mcp":
                    ft = self._build_mcp_tool(t)
                elif ttype == "code":
                    ft = self._build_code_tool(t)
                else:
                    continue
                self._tools[t.get("id")] = ft
                out.append(ft)
            except Exception:
                # Skip misconfigured tools; no fallback
                continue
        return out

    def _build_n8n_tool(self, t: Dict[str, Any]) -> llm.FunctionTool:
        cfg = t.get("config", {})
        url = cfg.get("webhook_url")
        method = (cfg.get("method") or "POST").upper()
        headers = cfg.get("headers") or {}
        schema = t.get("json_schema") or {
            "type": "object",
            "properties": {"payload": {"type": "object"}},
            "required": ["payload"],
        }
        if not url:
            raise ValueError("n8n tool missing webhook_url")

        async def _invoke(args: Dict[str, Any]) -> str:
            payload = args or {}
            async with aiohttp.ClientSession() as sess:
                async with sess.request(method, url, json=payload, headers=headers, timeout=20) as resp:
                    txt = await resp.text()
                    if resp.status >= 400:
                        raise RuntimeError(f"n8n error {resp.status}: {txt[:256]}")
                    return txt

        return llm.FunctionTool(
            name=t.get("slug") or t.get("name") or "n8n_tool",
            description=t.get("description") or "n8n webhook tool",
            parameters_json_schema=schema,
            fn=_invoke,
        )

    def _build_sidekick_tool(self, t: Dict[str, Any]) -> llm.FunctionTool:
        cfg = t.get("config", {})
        agent_slug = cfg.get("agent_slug")
        if not agent_slug:
            raise ValueError("sidekick tool missing agent_slug")

        async def _invoke(args: Dict[str, Any]) -> str:
            # Minimal: delegate to text trigger endpoint via backend (accessible env) if provided
            # This stub can be extended to call internal pipeline directly
            prompt = args.get("message") or args.get("input") or ""
            return f"[handoff to {agent_slug}] {prompt}"

        schema = t.get("json_schema") or {
            "type": "object",
            "properties": {"message": {"type": "string"}},
        }
        return llm.FunctionTool(
            name=t.get("slug") or f"handoff_{agent_slug}",
            description=t.get("description") or f"Handoff to agent {agent_slug}",
            parameters_json_schema=schema,
            fn=_invoke,
        )

    def _build_mcp_tool(self, t: Dict[str, Any]) -> llm.FunctionTool:
        # Placeholder: integrate MCP client here
        schema = t.get("json_schema") or {"type": "object"}

        async def _invoke(args: Dict[str, Any]) -> str:
            raise RuntimeError("MCP integration not yet implemented")

        return llm.FunctionTool(
            name=t.get("slug") or t.get("name") or "mcp_tool",
            description=t.get("description") or "MCP tool",
            parameters_json_schema=schema,
            fn=_invoke,
        )

    def _build_code_tool(self, t: Dict[str, Any]) -> llm.FunctionTool:
        schema = t.get("json_schema") or {"type": "object"}

        async def _invoke(args: Dict[str, Any]) -> str:
            # Stub safe code registry invocation
            return json.dumps({"ok": True, "args": args})

        return llm.FunctionTool(
            name=t.get("slug") or t.get("name") or "code_tool",
            description=t.get("description") or "Custom code tool",
            parameters_json_schema=schema,
            fn=_invoke,
        )


