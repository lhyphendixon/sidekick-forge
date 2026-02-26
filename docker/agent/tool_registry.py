from __future__ import annotations

import json
import asyncio
import os
import functools
from types import SimpleNamespace

try:
    from livekit.agents.llm import mcp as lk_mcp
except ImportError:
    lk_mcp = None

import aiohttp
from typing import Any, Dict, List, Callable, Optional
import logging
from livekit.agents import llm
from livekit.agents.llm.tool_context import function_tool as lk_function_tool, ToolError

try:
    from app.agent_modules.abilities.asana import (  # type: ignore
        AsanaAbilityConfigError,
        build_asana_tool,
    )
    from app.services.asana_oauth_service import AsanaOAuthService  # type: ignore
except Exception as exc:  # pragma: no cover - agent runtime runs standalone
    logging.getLogger(__name__).warning(
        "Failed to import Asana ability modules in agent runtime: %s", exc,
        exc_info=True,
    )
    build_asana_tool = None
    AsanaAbilityConfigError = None  # type: ignore
    AsanaOAuthService = None  # type: ignore


class ToolRegistry:
    def __init__(
        self,
        tools_config: Optional[Dict[str, Any]] = None,
        api_keys: Optional[Dict[str, Any]] = None,
        primary_supabase_client: Optional[Any] = None,
        platform_supabase_client: Optional[Any] = None,
        tool_result_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._tools: Dict[str, Any] = {}
        self._tools_config = tools_config or {}
        self._api_keys = api_keys or {}
        self._runtime_context: Dict[str, Dict[str, Any]] = {}
        self._primary_supabase = primary_supabase_client
        self._platform_supabase = platform_supabase_client
        self._tool_result_callback = tool_result_callback

    def build(self, tool_defs: List[Dict[str, Any]]) -> List[Any]:
        try:
            self._logger.info(f"🔧 ToolRegistry.build: received {len(tool_defs or [])} tool defs")
        except Exception:
            pass
        self._tools.clear()
        out: List[Any] = []
        for t in tool_defs or []:
            try:
                ttype = t.get("type")
                slug = t.get("slug") or t.get("name") or t.get("id")
                self._logger.info(f"🔧 Building tool: type={ttype}, slug={slug}")
                if ttype == "n8n":
                    ft = self._build_n8n_tool(t)
                elif ttype == "sidekick":
                    ft = self._build_sidekick_tool(t)
                elif ttype == "mcp":
                    ft = self._build_mcp_tool(t)
                elif ttype == "code":
                    ft = self._build_code_tool(t)
                elif ttype == "asana":
                    ft = self._build_asana_tool(t)
                elif ttype == "content_catalyst":
                    ft = self._build_content_catalyst_tool(t)
                elif ttype == "image_catalyst":
                    ft = self._build_image_catalyst_tool(t)
                else:
                    self._logger.warning(f"Unsupported tool type '{ttype}' for slug={slug}; skipping")
                    continue
                if ft is None:
                    self._logger.info(f"ℹ️ Tool {slug} managed externally; skipping inline registration")
                    continue
                self._tools[t.get("id")] = ft
                out.append(ft)
                self._logger.info(f"✅ Built stream tool ok: slug={slug}")
            except Exception:
                # Log the error with context; still skip misconfigured tools per no-fallback policy
                try:
                    self._logger.exception(f"❌ Failed to build tool: {t}")
                except Exception:
                    pass
                continue
        try:
            self._logger.info(f"🔧 ToolRegistry.build: built {len(out)} tools successfully")
        except Exception:
            pass
        return out

    def _emit_tool_result(
        self,
        *,
        slug: Optional[str],
        tool_type: Optional[str],
        success: bool,
        output: Any = None,
        raw_output: Any = None,
        error: Optional[str] = None,
    ) -> None:
        if not self._tool_result_callback:
            return
        entry: Dict[str, Any] = {
            "slug": slug,
            "type": tool_type,
            "success": bool(success),
            "output": output,
            "raw_call_output": raw_output,
        }
        if error:
            entry["error"] = error
        try:
            self._tool_result_callback(entry)
        except Exception as callback_err:
            try:
                self._logger.debug("Tool result callback failed: %s", callback_err)
            except Exception:
                pass

    def update_runtime_context(self, slug: str, updates: Dict[str, Any]) -> None:
        if not slug or not isinstance(updates, dict):
            return
        ctx = self._runtime_context.setdefault(slug, {})
        for key, value in updates.items():
            if value is None:
                continue
            ctx[key] = value

    def _build_n8n_tool(self, t: Dict[str, Any]) -> Any:
        slug = t.get("slug") or t.get("name") or t.get("id") or "n8n_tool"
        cfg = dict(t.get("config") or {})

        per_tool_cfg: Dict[str, Any] = {}
        if isinstance(self._tools_config, dict):
            lookup_keys = (
                slug,
                t.get("id"),
                t.get("name"),
            )
            for key in lookup_keys:
                if not key:
                    continue
                candidate = self._tools_config.get(str(key))
                if isinstance(candidate, dict):
                    per_tool_cfg = candidate
                    break

        def _merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
            merged = dict(base)
            for key, value in (override or {}).items():
                if value is not None:
                    merged[key] = value
            return merged

        merged_cfg = _merge_dict(cfg, per_tool_cfg if isinstance(per_tool_cfg, dict) else {})

        def _coerce_bool(value: Any, default: bool) -> bool:
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)

        url = str(merged_cfg.get("webhook_url") or "").strip()
        if not url:
            raise ValueError("n8n tool missing webhook_url")

        method = (merged_cfg.get("method") or "POST").upper()
        try:
            timeout_seconds = float(merged_cfg.get("timeout") or 20)
        except (TypeError, ValueError):
            timeout_seconds = 20.0
        if timeout_seconds <= 0:
            timeout_seconds = 20.0

        include_context = _coerce_bool(merged_cfg.get("include_context"), True)
        strip_nulls = _coerce_bool(merged_cfg.get("strip_nulls"), True)
        inquiry_field = merged_cfg.get("user_inquiry_field") or "userInquiry"

        headers: Dict[str, Any] = {}
        for candidate in [cfg.get("headers"), per_tool_cfg.get("headers") if isinstance(per_tool_cfg, dict) else None, merged_cfg.get("headers")]:
            if isinstance(candidate, dict):
                for key, value in candidate.items():
                    if value is not None:
                        headers[str(key)] = str(value)

        base_payload: Dict[str, Any] = {}
        for candidate in [cfg.get("default_payload"), per_tool_cfg.get("default_payload") if isinstance(per_tool_cfg, dict) else None, merged_cfg.get("default_payload")]:
            if isinstance(candidate, dict):
                base_payload.update(candidate)

        context_payload: Dict[str, Any] = {}
        for candidate in [cfg.get("context"), merged_cfg.get("context"), per_tool_cfg.get("context") if isinstance(per_tool_cfg, dict) else None]:
            if isinstance(candidate, dict):
                for key, value in candidate.items():
                    if value is not None:
                        context_payload[key] = value

        description = t.get("description") or f"Trigger the {slug} n8n automation via webhook."

        raw_schema = {
            "name": slug,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "user_inquiry": {
                        "type": "string",
                        "description": "Plain language summary of the user's request to pass to the automation.",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional structured fields to merge into the webhook payload.",
                        "additionalProperties": True,
                    },
                },
                "required": ["user_inquiry"],
                "additionalProperties": False,
            },
        }

        def _strip_null_values(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: _strip_null_values(v) for k, v in value.items() if v is not None}
            if isinstance(value, list):
                return [_strip_null_values(v) for v in value if v is not None]
            return value

        async def _invoke_raw(**kwargs: Any) -> str:
            try:
                self._logger.info("🌐 n8n tool call arguments", extra={"slug": slug, "args": kwargs})
            except Exception:
                pass

            metadata_payload = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}

            dynamic_context: Dict[str, Any] = dict(context_payload)
            runtime_ctx = self._runtime_context.get(slug)
            if isinstance(runtime_ctx, dict):
                for key, value in runtime_ctx.items():
                    if value is not None:
                        dynamic_context[key] = value

            user_inquiry = kwargs.get("user_inquiry")
            if not isinstance(user_inquiry, str) or not user_inquiry.strip():
                candidate = None
                candidate_source = None
                if isinstance(metadata_payload, dict):
                    for key in (
                        "user_inquiry",
                        "query",
                        "question",
                        "prompt",
                        "text",
                        "message",
                        "latest_user_text",
                        "latestUserText",
                    ):
                        val = metadata_payload.get(key)
                        if isinstance(val, str) and val.strip():
                            candidate = val.strip()
                            candidate_source = f"metadata.{key}"
                            break
                if not candidate and isinstance(runtime_ctx, dict):
                    user_text = runtime_ctx.get("latest_user_text")
                    if isinstance(user_text, str) and user_text.strip():
                        candidate = user_text.strip()
                        candidate_source = "runtime.latest_user_text"
                if candidate:
                    user_inquiry = candidate
                    try:
                        self._logger.info(
                            "🌐 n8n user_inquiry autopopulated",
                            extra={
                                "slug": slug,
                                "source": candidate_source or "unknown",
                                "preview": candidate[:120],
                            },
                        )
                    except Exception:
                        pass
                else:
                    raise ToolError(
                        "user_inquiry must be a non-empty string. Provide a short natural language summary of the user's request."
                    )

            if metadata_payload is not None and not isinstance(metadata_payload, dict):
                raise ToolError("metadata must be an object when provided.")

            payload: Dict[str, Any] = {}
            payload.update(base_payload)
            if include_context and dynamic_context:
                payload.update(dynamic_context)

            final_inquiry = user_inquiry.strip()
            payload[inquiry_field] = final_inquiry

            if isinstance(metadata_payload, dict):
                for key, value in metadata_payload.items():
                    if key == inquiry_field:
                        continue
                    payload[key] = value

            final_payload = _strip_null_values(payload) if strip_nulls else payload

            try:
                self._logger.info(
                    "🌐 Invoking n8n webhook",
                    extra={
                        "slug": slug,
                        "method": method,
                        "url": url,
                        "payload_keys": list(final_payload.keys()) if isinstance(final_payload, dict) else None,
                    },
                )
            except Exception:
                pass

            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.request(method, url, json=final_payload, headers=headers or None) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        raise ToolError(f"n8n webhook returned HTTP {resp.status}: {text[:256]}")
                    try:
                        self._logger.info(
                            "🌐 n8n webhook response",
                            extra={
                                "slug": slug,
                                "status": resp.status,
                                "length": len(text) if text is not None else 0,
                                "preview": text[:200],
                            },
                        )
                    except Exception:
                        pass
                    parsed: Any = None
                    try:
                        parsed = json.loads(text)
                        output_value = json.dumps(parsed)
                    except json.JSONDecodeError:
                        output_value = text

                    self._emit_tool_result(
                        slug=slug,
                        tool_type="n8n",
                        success=True,
                        output=output_value,
                        raw_output=parsed if parsed is not None else text,
                    )
                    return output_value

        return lk_function_tool(raw_schema=raw_schema)(_invoke_raw)

    def _build_sidekick_tool(self, t: Dict[str, Any]) -> Any:
        cfg = t.get("config", {})
        agent_slug = cfg.get("agent_slug")
        if not agent_slug:
            raise ValueError("sidekick tool missing agent_slug")

        async def _invoke(message: str) -> str:
            # Minimal: delegate to text trigger endpoint via backend (accessible env) if provided
            # This stub can be extended to call internal pipeline directly
            prompt = message or ""
            return f"[handoff to {agent_slug}] {prompt}"

        schema = t.get("json_schema") or {
            "type": "object",
            "properties": {"message": {"type": "string"}},
        }
        return lk_function_tool(
            name=t.get("slug") or f"handoff_{agent_slug}",
            description=t.get("description") or f"Handoff to agent {agent_slug}",
        )(_invoke)

    def _build_mcp_tool(self, t: Dict[str, Any]) -> Any:
        cfg = t.get("config") or {}
        provider = cfg.get("provider")
        schema = cfg.get("parameters") or t.get("json_schema") or {"type": "object"}
        description = t.get("description") or "MCP tool"

        if provider == "perplexity":
            return self._build_perplexity_mcp_tool(t, cfg, schema, description)

        if lk_mcp is None:
            async def _invoke_missing(_args: Dict[str, Any]) -> str:
                raise RuntimeError("MCP support requires the optional dependency 'livekit-agents[mcp]'.")

            return lk_function_tool(
                name=t.get("slug") or t.get("name") or "mcp_tool",
                description=description,
            )(_invoke_missing)

        server_url = cfg.get("server_url")
        if not server_url:
            raise RuntimeError("MCP ability is missing a server_url in its configuration.")

        headers: Dict[str, Any] = {}
        api_key = cfg.get("api_key")
        if api_key:
            headers["Authorization"] = api_key

        namespace = cfg.get("namespace") or (t.get("slug") or t.get("name") or "mcp")
        remote_tool_name = cfg.get("tool_name")

        server = lk_mcp.MCPServerHTTP(url=server_url, headers=headers or None)
        selected_tool: Optional[lk_mcp.MCPTool] = None
        tool_lock = asyncio.Lock()

        async def _ensure_tool() -> lk_mcp.MCPTool:
            nonlocal selected_tool
            async with tool_lock:
                if selected_tool is not None:
                    return selected_tool
                if not server.initialized:
                    await server.initialize()
                tools = await server.list_tools()
                if not tools:
                    raise RuntimeError("No tools available from MCP server at " + server_url)
                candidate = None
                if remote_tool_name:
                    for tool_fn in tools:
                        info = getattr(tool_fn, '__livekit_raw_tool_info', None)
                        if info and info.name == remote_tool_name:
                            candidate = tool_fn
                            break
                if candidate is None:
                    for tool_fn in tools:
                        info = getattr(tool_fn, '__livekit_raw_tool_info', None)
                        if info and info.name == (t.get('slug') or t.get('name')):
                            candidate = tool_fn
                            break
                selected_tool = candidate or tools[0]
                return selected_tool

        async def _invoke(**kwargs: Any) -> Any:
            tool_fn = await _ensure_tool()
            return await tool_fn(kwargs)

        return lk_function_tool(
            name=t.get("slug") or t.get("name") or remote_tool_name or "mcp_tool",
            description=description,
        )(_invoke)

    def _build_perplexity_mcp_tool(
        self,
        t: Dict[str, Any],
        cfg: Dict[str, Any],
        schema: Dict[str, Any],
        description: str,
    ) -> Any:
        inline = os.getenv("ENABLE_PERPLEXITY_INLINE_TOOL", "false").lower() == "true"
        schema = self._coerce_perplexity_schema(schema)

        if inline:
            return self._build_perplexity_inline_tool(t, cfg, schema, description)

        return self._build_perplexity_remote_tool(t, cfg, schema, description)

    def _build_perplexity_remote_tool(
        self,
        t: Dict[str, Any],
        cfg: Dict[str, Any],
        schema: Dict[str, Any],
        description: str,
    ) -> Any:
        if lk_mcp is None:
            raise RuntimeError("MCP support requires the optional dependency 'livekit-agents[mcp]'.")

        slug = t.get("slug") or t.get("name") or "perplexity_ask"
        remote_tool_name = cfg.get("tool_name") or slug

        per_tool_cfg: Dict[str, Any] = {}
        if isinstance(self._tools_config, dict):
            per_tool_cfg = self._tools_config.get(slug) or self._tools_config.get(remote_tool_name) or {}

        merged_cfg: Dict[str, Any] = dict(cfg or {})
        if isinstance(per_tool_cfg, dict):
            for key, value in per_tool_cfg.items():
                if value is not None:
                    merged_cfg[key] = value

        server_url = (merged_cfg.get("server_url") or "").strip()
        if not server_url:
            raise RuntimeError(
                "Perplexity MCP ability is missing a server_url. Ensure the shared MCP container is running."
            )

        api_param = merged_cfg.get("api_key_parameter") or "api_key"
        key_name = merged_cfg.get("api_key_name") or "perplexity_api_key"

        api_key = merged_cfg.get("api_key") or merged_cfg.get("perplexity_api_key")
        if not api_key and key_name:
            api_key = self._api_keys.get(key_name)
        env_key = merged_cfg.get("api_key_env")
        if not api_key and env_key:
            api_key = os.getenv(env_key)
        if not api_key and key_name:
            api_key = os.getenv(key_name.upper())
        if not api_key:
            raise RuntimeError("Perplexity API key is required for MCP integration")

        server = lk_mcp.MCPServerHTTP(url=server_url)

        selected_tool: Optional[lk_mcp.MCPTool] = None
        tool_lock = asyncio.Lock()

        async def _ensure_tool() -> lk_mcp.MCPTool:
            nonlocal selected_tool
            async with tool_lock:
                if selected_tool is not None:
                    return selected_tool
                if not server.initialized:
                    await server.initialize()
                tools = await server.list_tools()
                if not tools:
                    raise RuntimeError("Perplexity MCP server did not expose any tools")
                candidate = None
                if remote_tool_name:
                    for tool_fn in tools:
                        info = getattr(tool_fn, "__livekit_raw_tool_info", None)
                        if info and info.name == remote_tool_name:
                            candidate = tool_fn
                            break
                if candidate is None:
                    for tool_fn in tools:
                        info = getattr(tool_fn, "__livekit_raw_tool_info", None)
                        if info and info.name == slug:
                            candidate = tool_fn
                            break
                selected_tool = candidate or tools[0]
                return selected_tool

        async def _invoke(**kwargs: Any) -> Any:
            tool_fn = await _ensure_tool()
            payload = dict(kwargs)
            if api_param and api_param not in payload:
                payload[api_param] = api_key
            return await tool_fn(payload)

        return lk_function_tool(
            name=slug,
            description=description,
        )(_invoke)

    def _build_perplexity_inline_tool(
        self,
        t: Dict[str, Any],
        cfg: Dict[str, Any],
        schema: Dict[str, Any],
        description: str,
    ) -> Any:
        try:
            self._logger.info(f"🔧 Building inline Perplexity tool for slug={t.get('slug') or t.get('name')}")
        except Exception:
            pass

        schema = self._coerce_perplexity_schema(schema)

        slug = t.get("slug") or t.get("name") or "perplexity_ask"
        remote_tool_name = cfg.get("tool_name") or slug
        default_model = cfg.get("model") or "sonar-pro"
        timeout_seconds = float(cfg.get("timeout") or 60)

        per_tool_cfg: Dict[str, Any] = {}
        if isinstance(self._tools_config, dict):
            per_tool_cfg = self._tools_config.get(slug) or self._tools_config.get(remote_tool_name) or {}

        api_key = cfg.get("api_key")
        api_key = per_tool_cfg.get("api_key") or per_tool_cfg.get("perplexity_api_key") or api_key
        key_name = cfg.get("api_key_name") or "perplexity_api_key"
        if not api_key and key_name:
            api_key = self._api_keys.get(key_name)
        env_key = cfg.get("api_key_env")
        if not api_key and env_key:
            api_key = os.getenv(env_key)
        if not api_key and key_name:
            api_key = os.getenv(key_name.upper())
        if not api_key:
            raise RuntimeError(
                "Perplexity API key is required. Set it in the client's API keys or the agent's tools configuration."
            )

        model = per_tool_cfg.get("model") or default_model

        async def _invoke(messages: List[Dict[str, Any]]) -> Any:
            try:
                self._logger.info(f"🔎 Invoking Perplexity inline with model={model}")
            except Exception:
                pass
            if not isinstance(messages, list):
                raise RuntimeError("Perplexity Ask requires a 'messages' array argument.")

            payload = {"model": model, "messages": messages}
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session:
                async with session.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        raise RuntimeError(f"Perplexity API error {resp.status}: {text}")
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(f"Invalid response from Perplexity API: {exc}")

            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise RuntimeError("Perplexity API response missing message content")

            citations = data.get("citations") or []
            if isinstance(citations, list) and citations:
                lines = [content, "", "Citations:"]
                lines.extend(f"[{i+1}] {citation}" for i, citation in enumerate(citations))
                content = "\n".join(lines)

            try:
                self._logger.info(f"✅ Perplexity call succeeded; content length={len(content)}")
            except Exception:
                pass
            return content

        return lk_function_tool(
            name=slug,
            description=description,
        )(_invoke)

    @staticmethod
    def _coerce_perplexity_schema(schema: Dict[str, Any] | None) -> Dict[str, Any]:
        default_schema = {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "description": "Array of conversation messages",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {
                                "type": "string",
                                "description": "system | user | assistant",
                            },
                            "content": {
                                "type": "string",
                                "description": "Message text",
                            },
                        },
                        "required": ["role", "content"],
                        "additionalProperties": False,
                    },
                },
                "model": {
                    "type": "string",
                    "description": "Override the default Perplexity model",
                },
                "api_key": {
                    "type": "string",
                    "description": "Optional API key override for multi-tenant usage",
                },
            },
            "required": ["messages"],
            "additionalProperties": False,
        }

        if not isinstance(schema, dict) or not schema:
            schema = default_schema

        try:
            if schema.get("type") == "object":
                schema.setdefault("additionalProperties", False)
                props = schema.setdefault("properties", {})
                messages = props.get("messages")
                if isinstance(messages, dict):
                    messages.setdefault("additionalProperties", False)
                    items = messages.setdefault("items", {"type": "object"})
                    if isinstance(items, dict):
                        items.setdefault("type", "object")
                        items.setdefault("additionalProperties", False)
                        item_props = items.setdefault("properties", {})
                        item_props.setdefault("role", {"type": "string"})
                        item_props.setdefault("content", {"type": "string"})
                        items.setdefault("required", ["role", "content"])
        except Exception:
            pass

        return schema


    def _build_code_tool(self, t: Dict[str, Any]) -> Any:
        schema = t.get("json_schema") or {"type": "object"}

        async def _invoke(**kwargs: Any) -> str:
            # Stub safe code registry invocation
            return json.dumps({"ok": True, "args": kwargs})

        return lk_function_tool(
            name=t.get("slug") or t.get("name") or "code_tool",
            description=t.get("description") or "Custom code tool",
        )(_invoke)

    def _build_asana_tool(self, t: Dict[str, Any]) -> Any:
        slug = t.get("slug") or t.get("name") or t.get("id") or "asana_tasks"
        cfg = dict(t.get("config") or {})

        per_tool_cfg: Dict[str, Any] = {}
        if isinstance(self._tools_config, dict):
            lookup_keys = (
                slug,
                t.get("id"),
                t.get("name"),
            )
            for key in lookup_keys:
                if not key:
                    continue
                candidate = self._tools_config.get(str(key))
                if isinstance(candidate, dict):
                    per_tool_cfg = candidate
                    break

        merged_cfg = dict(cfg)
        if isinstance(per_tool_cfg, dict):
            for key, value in per_tool_cfg.items():
                if value is not None:
                    merged_cfg[key] = value

        access_token = merged_cfg.get("access_token")
        key_name = merged_cfg.get("access_token_key") or merged_cfg.get("api_key_name") or "asana_access_token"
        if not access_token and key_name:
            access_token = self._api_keys.get(key_name)
        env_key = merged_cfg.get("access_token_env")
        if not access_token and env_key:
            access_token = os.getenv(str(env_key))
        if not access_token and key_name:
            env_candidate = os.getenv(str(key_name).upper())
            if env_candidate:
                access_token = env_candidate
        if access_token:
            merged_cfg["access_token"] = access_token
        if not build_asana_tool:
            self._logger.warning("Asana ability not available in agent runtime; skipping.")
            return None

        oauth_service = None
        client_service = None
        if AsanaOAuthService is not None:
            try:  # pragma: no cover - best-effort in agent runtime
                from app.core.dependencies import get_client_service
                client_service = get_client_service()
                platform_client = self._platform_supabase or getattr(client_service, "supabase", None)
                oauth_service = AsanaOAuthService(
                    client_service,
                    primary_supabase=self._primary_supabase,
                    platform_supabase=platform_client,
                )
            except Exception:
                oauth_service = None
                client_service = None

        if oauth_service is None and AsanaOAuthService is not None:
            platform_client = self._platform_supabase or getattr(client_service, "supabase", None) if client_service else self._platform_supabase
            if platform_client is not None or self._primary_supabase is not None:
                try:
                    stub_service = SimpleNamespace(supabase=platform_client)
                    oauth_service = AsanaOAuthService(
                        stub_service,
                        primary_supabase=self._primary_supabase,
                        platform_supabase=platform_client,
                    )
                    self._logger.info(
                        "Asana OAuth fallback initialized via Supabase clients: platform=%s primary=%s",
                        bool(platform_client),
                        bool(self._primary_supabase),
                    )
                except Exception as exc:  # pragma: no cover - logging path
                    self._logger.warning("Failed to initialize Asana OAuth fallback: %s", exc)
                    oauth_service = None

        try:
            original_tool = build_asana_tool(t, merged_cfg, oauth_service=oauth_service)
        except Exception as exc:
            if not AsanaAbilityConfigError or not isinstance(exc, AsanaAbilityConfigError):
                raise
            message = f"Asana ability is not ready: {exc}"
            try:
                self._logger.warning(
                    "Asana ability could not be built",
                    extra={"slug": slug, "reason": str(exc)},
                )
            except Exception:
                pass

            async def _misconfigured_tool(**_: Any) -> Dict[str, str]:
                return {"error": message, "slug": slug}

            description = t.get("description") or "Asana integration is not configured yet."
            return lk_function_tool(
                raw_schema={
                    "name": slug,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_inquiry": {
                                "type": "string",
                                "description": "Latest user request describing the desired Asana action.",
                            },
                            "metadata": {
                                "type": "object",
                                "description": "Additional session metadata.",
                                "additionalProperties": True,
                            },
                        },
                        "required": ["user_inquiry"],
                    },
                }
            )(_misconfigured_tool)

        if original_tool is None:
            return None

        @functools.wraps(original_tool)
        async def _invoke_with_context(**kwargs: Any) -> Any:
            runtime_ctx = self._runtime_context.get(slug) or {}

            incoming_metadata = kwargs.get("metadata")
            merged_metadata: Dict[str, Any] = {}
            if isinstance(runtime_ctx, dict):
                merged_metadata.update(runtime_ctx)
            if isinstance(incoming_metadata, dict):
                merged_metadata.update(incoming_metadata)

            if merged_metadata:
                kwargs["metadata"] = merged_metadata

            user_inquiry = kwargs.get("user_inquiry")
            if not isinstance(user_inquiry, str) or not user_inquiry.strip():
                for key in (
                    "user_inquiry",
                    "latest_user_text",
                    "user_text",
                    "text",
                    "message",
                    "transcript",
                ):
                    candidate = merged_metadata.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        kwargs["user_inquiry"] = candidate.strip()
                        break

            try:
                result = await original_tool(**kwargs)
            except Exception as exc:
                self._emit_tool_result(
                    slug=slug,
                    tool_type="asana",
                    success=False,
                    error=str(exc),
                )
                raise

            summary = None
            if isinstance(result, dict):
                summary = result.get("summary") or result.get("text")
            output_value = summary or (result if isinstance(result, str) else str(result))

            self._emit_tool_result(
                slug=slug,
                tool_type="asana",
                success=True,
                output=output_value,
                raw_output=result,
            )
            return result

        if hasattr(original_tool, "__livekit_tool_info"):
            setattr(_invoke_with_context, "__livekit_tool_info", getattr(original_tool, "__livekit_tool_info"))

        return _invoke_with_context

    def _build_content_catalyst_tool(self, t: Dict[str, Any]) -> Any:
        """Build the Content Catalyst tool for multi-phase article generation."""
        slug = t.get("slug") or t.get("name") or t.get("id") or "content_catalyst"
        cfg = dict(t.get("config") or {})
        # LLM-optimized description for function tool schema (NOT the user-facing DB description).
        # Must be unambiguous so the LLM never confuses this with image-catalyst.
        description = (
            "Generate a WRITTEN article, blog post, essay, or long-form TEXT content. "
            "ONLY use this tool when the user wants WRITTEN TEXT — e.g. 'write an article', "
            "'draft a blog post', 'compose an essay'. "
            "NEVER use this tool for images, pictures, thumbnails, banners, photos, or any "
            "visual content — use the image-catalyst tool for those instead."
        )

        raw_schema = {
            "name": slug,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "trigger_widget": {
                        "type": "boolean",
                        "default": True,
                        "description": "Set to true to trigger the Content Catalyst widget UI",
                    },
                    "suggested_topic": {
                        "type": "string",
                        "description": "Optional topic suggestion from the conversation to pre-fill in the widget",
                    },
                    "source_type": {
                        "type": "string",
                        "enum": ["text", "url", "mp3", "topic", "audio"],
                        "description": "Source type for content generation: 'text' or 'topic' for topic-based, 'url' for URL-based, 'mp3' or 'audio' for audio transcription",
                    },
                    "source_content": {
                        "type": "string",
                        "description": "The source content - topic text, URL, or audio reference",
                    },
                    "target_word_count": {
                        "type": "integer",
                        "description": "Target word count for the generated article (e.g., 500, 1000, 2000)",
                    },
                    "style_prompt": {
                        "type": "string",
                        "description": "Writing style instructions or preferences",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

        async def _invoke_raw(**kwargs: Any) -> str:
            """Trigger the Content Catalyst widget UI for user configuration."""
            try:
                self._logger.info("🎨 Content Catalyst widget trigger invoked", extra={"args": kwargs})
            except Exception:
                pass

            suggested_topic = kwargs.get("suggested_topic", "") or kwargs.get("source_content", "")
            source_type = kwargs.get("source_type", "topic")
            source_content = kwargs.get("source_content", "")
            target_word_count = kwargs.get("target_word_count")
            style_prompt = kwargs.get("style_prompt", "")

            widget_trigger = {
                "widget_type": "content_catalyst",
                "suggested_topic": suggested_topic,
                "source_type": source_type,
                "source_content": source_content,
                "target_word_count": target_word_count,
                "style_prompt": style_prompt,
                "message": "Opening Content Catalyst configuration...",
            }

            self._emit_tool_result(
                slug=slug,
                tool_type="content_catalyst",
                success=True,
                output="Widget triggered",
                raw_output=widget_trigger,
            )

            return f"WIDGET_TRIGGER:content_catalyst:{suggested_topic}"

        return lk_function_tool(raw_schema=raw_schema)(_invoke_raw)

    def _build_image_catalyst_tool(self, t: Dict[str, Any]) -> Any:
        """Build the Image Catalyst tool for AI image generation."""
        slug = t.get("slug") or t.get("name") or t.get("id") or "image-catalyst"
        # LLM-optimized description for function tool schema (NOT the user-facing DB description).
        # Must be unambiguous so the LLM always picks this for visual/image requests.
        description = (
            "Create, generate, or make an IMAGE, picture, photo, thumbnail, banner, or any "
            "VISUAL content using AI. Use this tool whenever the user wants any kind of visual "
            "or graphic created — including thumbnails, promotional images, photos, artwork, "
            "illustrations, or designs. NEVER use content_catalyst for visual content."
        )

        raw_schema = {
            "name": slug,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "trigger_widget": {
                        "type": "boolean",
                        "default": True,
                        "description": "Set to true to trigger the Image Catalyst widget UI",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Description of the image to generate",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["general", "thumbnail"],
                        "description": "Image generation mode: 'general' for creative imagery, 'thumbnail' for polished marketing images",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

        async def _invoke_raw(**kwargs: Any) -> str:
            try:
                self._logger.info("🖼️ Image Catalyst widget trigger invoked", extra={"args": kwargs})
            except Exception:
                pass

            prompt = kwargs.get("prompt", "")
            mode = kwargs.get("mode", "general")

            widget_trigger = {
                "widget_type": "image_catalyst",
                "prompt": prompt,
                "mode": mode,
                "message": "Opening Image Catalyst...",
            }

            self._emit_tool_result(
                slug=slug,
                tool_type="image_catalyst",
                success=True,
                output="Widget triggered",
                raw_output=widget_trigger,
            )

            return f"WIDGET_TRIGGER:image_catalyst:{prompt}"

        return lk_function_tool(raw_schema=raw_schema)(_invoke_raw)
