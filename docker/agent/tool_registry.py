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

# Import ability modules separately from OAuth services so abilities can work
# even if OAuth services fail to import (e.g., due to Settings validation in container)

# Asana ability module
try:
    from app.agent_modules.abilities.asana import (  # type: ignore
        AsanaAbilityConfigError,
        build_asana_tool,
    )
except Exception as exc:  # pragma: no cover - agent runtime runs standalone
    logging.getLogger(__name__).warning(
        "Failed to import Asana ability module: %s", exc,
    )
    build_asana_tool = None
    AsanaAbilityConfigError = None  # type: ignore

# Asana OAuth service (optional - may fail due to Settings validation)
try:
    from app.services.asana_oauth_service import AsanaOAuthService  # type: ignore
except Exception as exc:  # pragma: no cover
    logging.getLogger(__name__).debug(
        "Asana OAuth service not available (expected in container): %s", exc,
    )
    AsanaOAuthService = None  # type: ignore

# HelpScout ability module
try:
    from app.agent_modules.abilities.helpscout import (  # type: ignore
        HelpScoutAbilityConfigError,
        build_helpscout_tool,
    )
except Exception as exc:  # pragma: no cover - agent runtime runs standalone
    logging.getLogger(__name__).warning(
        "Failed to import HelpScout ability module: %s", exc,
    )
    build_helpscout_tool = None
    HelpScoutAbilityConfigError = None  # type: ignore

# HelpScout OAuth service (optional - may fail due to Settings validation)
try:
    from app.services.helpscout_oauth_service import HelpScoutOAuthService  # type: ignore
except Exception as exc:  # pragma: no cover
    logging.getLogger(__name__).debug(
        "HelpScout OAuth service not available (expected in container): %s", exc,
    )
    HelpScoutOAuthService = None  # type: ignore


def _is_glm_reasoning_model(model_name: str) -> bool:
    """
    Check if the model is a GLM model that supports the reasoning toggle.

    GLM-4.7 (and potentially future versions) support a `disable_reasoning` parameter
    that can be passed to the Cerebras API to control whether the model uses
    extended reasoning capabilities.

    Args:
        model_name: The model name/identifier to check

    Returns:
        True if the model supports reasoning toggle, False otherwise
    """
    if not model_name:
        return False
    model_lower = model_name.lower()
    # Match various naming patterns for GLM-4.7
    return any(pattern in model_lower for pattern in [
        "glm-4.7", "glm-4-7", "glm4.7", "glm47",
        "zai-glm", "z-ai/glm", "zai/glm"
    ])


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

    def build(
        self,
        tool_defs: List[Dict[str, Any]],
        model_name: Optional[str] = None,
        agent_ref: Optional[Any] = None
    ) -> List[Any]:
        """
        Build all tools for the agent session.

        Args:
            tool_defs: List of tool definitions from metadata
            model_name: The LLM model name (for model-specific system tools)
            agent_ref: Reference to the agent instance (for system tools that need state access)

        Returns:
            List of built LiveKit function tools
        """
        try:
            self._logger.info(f"🔧 ToolRegistry.build: received {len(tool_defs or [])} tool defs, model={model_name}")
        except Exception:
            pass
        self._tools.clear()
        out: List[Any] = []

        # Add default built-in tools that are always available
        # Pass model_name and agent_ref for model-specific system tools (e.g., GLM reasoning toggle)
        default_tools = self._build_default_tools(model_name=model_name, agent_ref=agent_ref)
        out.extend(default_tools)

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
                elif ttype == "helpscout":
                    ft = self._build_helpscout_tool(t)
                elif ttype == "user_overview":
                    ft = self._build_user_overview_tool(t)
                elif ttype == "documentsense":
                    ft = self._build_documentsense_tool(t)
                elif ttype == "content_catalyst":
                    ft = self._build_content_catalyst_tool(t)
                elif ttype == "lingua":
                    ft = self._build_lingua_tool(t)
                elif ttype == "scrape_url":
                    ft = self._build_scrape_url_tool(t)
                elif ttype == "builtin":
                    # Handle built-in tools by mapping slug to appropriate builder
                    ft = self._build_builtin_tool(t)
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

    def _build_default_tools(
        self,
        model_name: Optional[str] = None,
        agent_ref: Optional[Any] = None
    ) -> List[Any]:
        """
        Build default tools that are always available to all agents.
        These tools don't need to be configured in the database.

        Args:
            model_name: The LLM model name (used for model-specific tools like GLM reasoning toggle)
            agent_ref: Reference to the agent instance (for state manipulation by system tools)
        """
        default_tools = []

        # scrape_url - Always available for fetching web page content
        try:
            scrape_url_tool = self._build_scrape_url_tool({
                "id": "default_scrape_url",
                "slug": "scrape_url",
                "type": "scrape_url",
                "name": "Scrape URL",
                "description": (
                    "Scrape a URL and extract its main content as clean markdown. "
                    "Use this when the user shares a URL and wants you to read, summarize, "
                    "or answer questions about the page content. Returns the page title, "
                    "main content (as markdown), and metadata. Works with articles, blog posts, "
                    "documentation, and most web pages."
                ),
                "config": {},
            })
            if scrape_url_tool:
                default_tools.append(scrape_url_tool)
                self._tools["default_scrape_url"] = scrape_url_tool
                self._logger.info("✅ Built default tool: scrape_url")
        except Exception as e:
            self._logger.warning(f"⚠️ Failed to build default scrape_url tool: {e}")

        # GLM reasoning toggle - Only available for GLM models (system-level, not user-configurable)
        if model_name and agent_ref and _is_glm_reasoning_model(model_name):
            try:
                reasoning_tool = self._build_reasoning_toggle_tool(agent_ref, model_name)
                if reasoning_tool:
                    default_tools.append(reasoning_tool)
                    self._tools["_system_toggle_reasoning"] = reasoning_tool
                    self._logger.info(f"✅ Built system tool: _system_toggle_reasoning (GLM-4.7 reasoning toggle for model {model_name})")
            except Exception as e:
                self._logger.warning(f"⚠️ Failed to build reasoning toggle tool: {e}")

        return default_tools

    def _build_reasoning_toggle_tool(self, agent_ref: Any, model_name: str) -> Any:
        """
        Build the system-level reasoning toggle tool for GLM models.

        This tool allows the agent to dynamically enable/disable reasoning mode
        when using GLM-4.7 on Cerebras. It is a SYSTEM-LEVEL tool that is:
        - NOT visible to users as a configurable ability
        - Automatically added when GLM model is detected
        - Used by the agent to optimize response speed vs. depth

        Args:
            agent_ref: Either a direct reference to the SidekickAgent instance,
                      or a mutable dict container {"agent": <agent>} that will be
                      populated after agent creation (for deferred binding)
            model_name: The model name for logging

        Returns:
            A LiveKit function_tool that toggles reasoning mode
        """
        slug = "_system_toggle_reasoning"
        description = """Toggle reasoning mode for complex tasks.

Use enable=true when you need to think harder about:
- Complex multi-step analysis or calculations
- Ambiguous questions requiring careful interpretation
- Content creation that benefits from structured thinking
- Problem-solving that requires exploring multiple approaches

Use enable=false (default) when:
- Answering simple factual questions
- Casual conversation
- Quick responses are more important than deep analysis
- The task is straightforward

Note: Reasoning mode increases response quality but takes longer. Only enable it when the task truly benefits from deeper thinking."""

        raw_schema = {
            "name": slug,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "enable": {
                        "type": "boolean",
                        "description": "true to enable extended reasoning (slower, deeper), false to disable (faster, direct)"
                    }
                },
                "required": ["enable"],
                "additionalProperties": False,
            },
        }

        async def _toggle_reasoning(enable: bool) -> str:
            """Toggle reasoning mode on the agent."""
            # Support both direct agent reference and container pattern
            # Container pattern: {"agent": <agent>} - used when tool is built before agent exists
            actual_agent = agent_ref.get("agent") if isinstance(agent_ref, dict) else agent_ref

            if actual_agent is None:
                self._logger.warning("_system_toggle_reasoning called but agent reference not yet set")
                return "Reasoning toggle not available yet - agent initializing."

            if hasattr(actual_agent, '_reasoning_enabled'):
                previous_state = actual_agent._reasoning_enabled
                actual_agent._reasoning_enabled = enable
                mode = "ENABLED" if enable else "DISABLED"

                # Only log if state actually changed
                if previous_state != enable:
                    self._logger.info(f"🧠 GLM reasoning {mode} (was {'enabled' if previous_state else 'disabled'})")

                if enable:
                    return f"Reasoning mode enabled. Take your time to think through the problem carefully."
                else:
                    return f"Reasoning mode disabled. Responding quickly and directly."
            else:
                self._logger.warning("_system_toggle_reasoning called but agent has no _reasoning_enabled attribute")
                return "Reasoning toggle not available for this session."

        return lk_function_tool(raw_schema=raw_schema)(_toggle_reasoning)

    def _build_builtin_tool(self, t: Dict[str, Any]) -> Any:
        """
        Handle built-in tools by mapping known slugs to appropriate builders.
        This allows tools to be configured with type='builtin' and a slug that
        maps to known functionality.
        """
        slug = t.get("slug") or t.get("name") or t.get("id") or "builtin_tool"

        # Map known slugs to their builders
        if slug in ("usersense", "user_sense", "user_overview", "update_user_overview"):
            # UserSense is the user overview tool
            self._logger.info(f"🔧 Mapping builtin '{slug}' to user_overview tool")
            return self._build_user_overview_tool(t)
        elif slug in ("documentsense", "document_sense", "query_document_intelligence"):
            self._logger.info(f"🔧 Mapping builtin '{slug}' to documentsense tool")
            return self._build_documentsense_tool(t)
        elif slug in ("scrape_url", "scrape", "web_scrape"):
            self._logger.info(f"🔧 Mapping builtin '{slug}' to scrape_url tool")
            return self._build_scrape_url_tool(t)
        elif slug in ("content_catalyst", "article_writer"):
            self._logger.info(f"🔧 Mapping builtin '{slug}' to content_catalyst tool")
            return self._build_content_catalyst_tool(t)
        elif slug in ("lingua", "transcribe", "subtitles"):
            self._logger.info(f"🔧 Mapping builtin '{slug}' to lingua tool")
            return self._build_lingua_tool(t)
        else:
            self._logger.warning(f"⚠️ Unknown builtin tool slug '{slug}'; skipping")
            return None

    def _emit_tool_result(
        self,
        *,
        slug: Optional[str],
        tool_type: Optional[str],
        success: bool,
        output: Any = None,
        raw_output: Any = None,
        error: Optional[str] = None,
        citations: Optional[List[Dict[str, Any]]] = None,
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
        if citations:
            entry["citations"] = citations
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
            self._logger.info(f"🌐 n8n tool: initial user_inquiry from kwargs = '{user_inquiry}'")
            if not isinstance(user_inquiry, str) or not user_inquiry.strip():
                candidate = None
                candidate_source = None
                # Try metadata payload first
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
                # Try runtime context
                if not candidate and isinstance(runtime_ctx, dict):
                    self._logger.info(f"🌐 n8n tool: checking runtime_ctx keys={list(runtime_ctx.keys())}")
                    user_text = runtime_ctx.get("latest_user_text")
                    if isinstance(user_text, str) and user_text.strip():
                        candidate = user_text.strip()
                        candidate_source = "runtime.latest_user_text"
                # Try dynamic_context which includes context_payload
                if not candidate and isinstance(dynamic_context, dict):
                    self._logger.info(f"🌐 n8n tool: checking dynamic_context keys={list(dynamic_context.keys())}")
                    for key in ("latest_user_text", "latestUserText", "user_text", "query"):
                        val = dynamic_context.get(key)
                        if isinstance(val, str) and val.strip():
                            candidate = val.strip()
                            candidate_source = f"dynamic_context.{key}"
                            break
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
                    self._logger.error(f"🌐 n8n tool: no user_inquiry found! runtime_ctx={runtime_ctx}, dynamic_context keys={list(dynamic_context.keys()) if dynamic_context else 'None'}")
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

        # Extract the tool info from the original decorated function
        original_tool_info = getattr(original_tool, "__livekit_tool_info", None)

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
                # Extract underlying callable from original_tool (which is a RawFunctionTool)
                # RawFunctionTool stores the wrapped function in _func attribute (not _callable!)
                inner_callable = getattr(original_tool, '_func', None)
                if inner_callable is None:
                    inner_callable = original_tool

                import asyncio as _asyncio
                if _asyncio.iscoroutinefunction(inner_callable):
                    result = await inner_callable(**kwargs)
                else:
                    result = inner_callable(**kwargs)
                    # Handle case where sync function returns a coroutine
                    if _asyncio.iscoroutine(result):
                        result = await result
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

        # CRITICAL: Re-wrap with lk_function_tool to create a proper FunctionTool
        # The LiveKit SDK requires FunctionTool instances, not plain functions
        # Always wrap the tool to inject runtime context (client_id, etc.)
        description = t.get("description") or f"Asana task management for {slug}"
        return lk_function_tool(
            raw_schema={
                "name": slug,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_inquiry": {
                            "type": "string",
                            "description": "Pass the COMPLETE user request VERBATIM including the action verb.",
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
        )(_invoke_with_context)

    def _build_helpscout_tool(self, t: Dict[str, Any]) -> Any:
        """Build the HelpScout tool for managing support tickets."""
        slug = t.get("slug") or t.get("name") or t.get("id") or "helpscout_tickets"
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
        key_name = merged_cfg.get("access_token_key") or merged_cfg.get("api_key_name") or "helpscout_access_token"
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
        if not build_helpscout_tool:
            self._logger.warning("HelpScout ability not available in agent runtime; skipping.")
            return None

        oauth_service = None
        client_service = None
        if HelpScoutOAuthService is not None:
            try:  # pragma: no cover - best-effort in agent runtime
                from app.core.dependencies import get_client_service
                client_service = get_client_service()
                platform_client = self._platform_supabase or getattr(client_service, "supabase", None)
                oauth_service = HelpScoutOAuthService(
                    client_service,
                    primary_supabase=self._primary_supabase,
                    platform_supabase=platform_client,
                )
            except Exception:
                oauth_service = None
                client_service = None

        if oauth_service is None and HelpScoutOAuthService is not None:
            platform_client = self._platform_supabase or getattr(client_service, "supabase", None) if client_service else self._platform_supabase
            if platform_client is not None or self._primary_supabase is not None:
                try:
                    stub_service = SimpleNamespace(supabase=platform_client)
                    oauth_service = HelpScoutOAuthService(
                        stub_service,
                        primary_supabase=self._primary_supabase,
                        platform_supabase=platform_client,
                    )
                    self._logger.info(
                        "HelpScout OAuth fallback initialized via Supabase clients: platform=%s primary=%s",
                        bool(platform_client),
                        bool(self._primary_supabase),
                    )
                except Exception as exc:  # pragma: no cover - logging path
                    self._logger.warning("Failed to initialize HelpScout OAuth fallback: %s", exc)
                    oauth_service = None

        try:
            original_tool = build_helpscout_tool(t, merged_cfg, oauth_service=oauth_service)
        except Exception as exc:
            if not HelpScoutAbilityConfigError or not isinstance(exc, HelpScoutAbilityConfigError):
                raise
            message = f"HelpScout ability is not ready: {exc}"
            try:
                self._logger.warning(
                    "HelpScout ability could not be built",
                    extra={"slug": slug, "reason": str(exc)},
                )
            except Exception:
                pass

            async def _misconfigured_tool(**_: Any) -> Dict[str, str]:
                return {"error": message, "slug": slug}

            description = t.get("description") or "HelpScout integration is not configured yet."
            return lk_function_tool(
                raw_schema={
                    "name": slug,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_inquiry": {
                                "type": "string",
                                "description": "Latest user request describing the desired HelpScout action.",
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

        # Extract the tool info from the original decorated function
        original_tool_info = getattr(original_tool, "__livekit_tool_info", None)

        async def _invoke_with_context(**kwargs: Any) -> Any:
            runtime_ctx = self._runtime_context.get(slug) or {}

            # DEBUG: Log runtime context lookup for HelpScout
            self._logger.info(
                f"🔍 HelpScout _invoke_with_context: slug={slug}, "
                f"runtime_ctx_keys={list(runtime_ctx.keys()) if runtime_ctx else 'EMPTY'}, "
                f"client_id_in_ctx={runtime_ctx.get('client_id', 'MISSING')}"
            )

            incoming_metadata = kwargs.get("metadata")
            merged_metadata: Dict[str, Any] = {}
            if isinstance(runtime_ctx, dict):
                merged_metadata.update(runtime_ctx)
            if isinstance(incoming_metadata, dict):
                merged_metadata.update(incoming_metadata)

            if merged_metadata:
                kwargs["metadata"] = merged_metadata

            # DEBUG: Log merged metadata for HelpScout
            self._logger.info(
                f"🔍 HelpScout merged_metadata: client_id={merged_metadata.get('client_id', 'MISSING')}, "
                f"keys={list(merged_metadata.keys())}"
            )

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
                # Extract underlying callable from original_tool (which is a RawFunctionTool)
                # RawFunctionTool stores the wrapped function in _func attribute (not _callable!)
                inner_callable = getattr(original_tool, '_func', None)
                if inner_callable is None:
                    inner_callable = original_tool

                import asyncio as _asyncio
                if _asyncio.iscoroutinefunction(inner_callable):
                    result = await inner_callable(**kwargs)
                else:
                    result = inner_callable(**kwargs)
                    # Handle case where sync function returns a coroutine
                    if _asyncio.iscoroutine(result):
                        result = await result
            except Exception as exc:
                self._emit_tool_result(
                    slug=slug,
                    tool_type="helpscout",
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
                tool_type="helpscout",
                success=True,
                output=output_value,
                raw_output=result,
            )
            return result

        # CRITICAL: Re-wrap with lk_function_tool to create a proper FunctionTool
        # The LiveKit SDK requires FunctionTool instances, not plain functions
        # Always wrap the tool to inject runtime context (client_id, etc.)
        description = t.get("description") or f"HelpScout ticket management for {slug}"
        return lk_function_tool(
            raw_schema={
                "name": slug,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_inquiry": {
                            "type": "string",
                            "description": "Pass the COMPLETE user request VERBATIM including the action verb.",
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
        )(_invoke_with_context)

    def _build_user_overview_tool(self, t: Dict[str, Any]) -> Any:
        """
        Build the update_user_overview tool for maintaining persistent user summaries.

        This tool allows agents to update a shared User Overview - persistent notes
        about each user that all sidekicks within a client can access.
        """
        slug = t.get("slug") or "update_user_overview"
        description = t.get("description") or """Update the persistent User Overview - your shared notes about this user.
All sidekicks for this client share this overview, so updates help maintain consistent context.

Use this when the user shares ENDURING, IMPORTANT information about:
- Who they are (identity, role, background, team)
- What they're trying to achieve (goals, priorities, blockers)
- How they work best (communication preferences, decision style, notes)
- Critical context (sensitivities, relationships, constraints)

Do NOT update for:
- Transient tasks or routine questions
- Information that's only relevant today
- Things already captured in the overview
- Speculation or assumptions - only facts the user has shared"""

        raw_schema = {
            "name": slug,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "enum": ["identity", "goals", "working_style", "important_context", "relationship_history"],
                        "description": "Which section of the overview to update: identity (role, background), goals (objectives, priorities), working_style (preferences, communication), important_context (sensitivities, constraints), relationship_history (milestones, wins)."
                    },
                    "action": {
                        "type": "string",
                        "enum": ["set", "append", "remove"],
                        "description": "set=replace a value, append=add to list or notes, remove=delete specific item."
                    },
                    "key": {
                        "type": "string",
                        "description": "The field within the section to update. For identity: role, background, team. For goals: primary, secondary, blockers. For working_style: communication, decision_making, notes. For relationship_history: key_wins, ongoing_threads. For important_context: omit key to append to the list."
                    },
                    "value": {
                        "type": "string",
                        "description": "The new value to set, item to append, or content to remove. Be concise - the overview should be scannable."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why this update matters (for audit trail)."
                    }
                },
                "required": ["section", "action", "value", "reason"],
                "additionalProperties": False,
            },
        }

        async def _invoke_update_overview(**kwargs: Any) -> str:
            """Execute the user overview update via Supabase RPC."""
            section = kwargs.get("section")
            action = kwargs.get("action")
            key = kwargs.get("key")  # Can be None for important_context
            value = kwargs.get("value")
            reason = kwargs.get("reason")

            # Validate required fields - return soft error to prevent LLM retry loops
            missing = []
            if not section:
                missing.append("section")
            if not action:
                missing.append("action")
            if not value:
                missing.append("value")
            if not reason:
                missing.append("reason")

            if missing:
                # Return error message instead of raising - prevents infinite retry loops
                self._logger.warning(f"update_user_overview called with missing fields: {missing}")
                return f"[Tool skipped - missing required fields: {', '.join(missing)}. Respond to the user instead.]"

            if section not in ["identity", "goals", "working_style", "important_context", "relationship_history"]:
                raise ToolError(f"Invalid section '{section}'. Must be one of: identity, goals, working_style, important_context, relationship_history.")

            if action not in ["set", "append", "remove"]:
                raise ToolError(f"Invalid action '{action}'. Must be one of: set, append, remove.")

            # Get user_id and client_id from runtime context
            runtime_ctx = self._runtime_context.get(slug) or {}
            user_id = runtime_ctx.get("user_id")
            client_id = runtime_ctx.get("client_id")
            agent_id = runtime_ctx.get("agent_id")

            if not user_id or not client_id:
                raise ToolError("Cannot update user overview: missing user_id or client_id in context.")

            try:
                self._logger.info(
                    f"📝 Updating user overview: user={user_id[:8]}..., section={section}, action={action}, key={key}"
                )
            except Exception:
                pass

            # Call the Supabase RPC function
            if not self._primary_supabase:
                raise ToolError("User overview update failed: no database connection available.")

            try:
                result = await asyncio.to_thread(
                    lambda: self._primary_supabase.rpc(
                        "update_user_overview",
                        {
                            "p_user_id": user_id,
                            "p_client_id": client_id,
                            "p_section": section,
                            "p_action": action,
                            "p_key": key,
                            "p_value": value,
                            "p_agent_id": agent_id,
                            "p_reason": reason,
                        }
                    ).execute()
                )

                if result.data:
                    response_data = result.data
                    if isinstance(response_data, dict):
                        if response_data.get("success"):
                            output_msg = f"Updated user overview: {section}/{key or 'list'} ({action}). Reason: {reason}"
                            self._emit_tool_result(
                                slug=slug,
                                tool_type="user_overview",
                                success=True,
                                output=output_msg,
                                raw_output=response_data,
                            )
                            try:
                                self._logger.info(f"✅ User overview updated: {response_data}")
                            except Exception:
                                pass
                            return output_msg
                        else:
                            error_msg = response_data.get("message", "Unknown error")
                            raise ToolError(f"User overview update failed: {error_msg}")
                    else:
                        # Unexpected response format
                        output_msg = f"Updated user overview: {section}/{key or 'list'} ({action})"
                        self._emit_tool_result(
                            slug=slug,
                            tool_type="user_overview",
                            success=True,
                            output=output_msg,
                            raw_output=response_data,
                        )
                        return output_msg
                else:
                    raise ToolError("User overview update returned no data.")

            except ToolError:
                raise
            except Exception as exc:
                error_msg = f"User overview update failed: {str(exc)}"
                self._emit_tool_result(
                    slug=slug,
                    tool_type="user_overview",
                    success=False,
                    error=error_msg,
                )
                try:
                    self._logger.error(f"❌ User overview update error: {exc}", exc_info=True)
                except Exception:
                    pass
                raise ToolError(error_msg)

        return lk_function_tool(raw_schema=raw_schema)(_invoke_update_overview)

    def _build_documentsense_tool(self, t: Dict[str, Any]) -> Any:
        """
        Build the query_document_intelligence tool for document-specific queries.

        This tool allows agents to query extracted intelligence about specific documents,
        enabling questions like "What are the best quotes from Recording 239?"
        """
        slug = t.get("slug") or "query_document_intelligence"
        description = t.get("description") or """Query extracted intelligence about a specific document.

Use this when the user asks about a SPECIFIC document by name or title, such as:
- "What are the best quotes from Recording 239?"
- "Summarize the Divine Plan document"
- "What themes are discussed in the interview with John?"
- "What questions does the marketing report answer?"

This tool searches documents by title and returns:
- Summary of the document
- Key quotes (exact text from the document)
- Main themes discussed
- Named entities (people, organizations, locations, etc.)
- Questions the document helps answer

Do NOT use this for general knowledge questions - only for document-specific queries."""

        raw_schema = {
            "name": slug,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "document_query": {
                        "type": "string",
                        "description": "Document name or identifier to search for. Use the title or partial title mentioned by the user."
                    },
                    "info_type": {
                        "type": "string",
                        "enum": ["summary", "quotes", "themes", "entities", "questions", "all"],
                        "default": "all",
                        "description": "What type of information to retrieve. Use 'quotes' for quote requests, 'summary' for summaries, 'all' for comprehensive info."
                    }
                },
                "required": ["document_query"],
                "additionalProperties": False
            }
        }

        async def _invoke_query_document_intelligence(**kwargs: Any) -> str:
            document_query = kwargs.get("document_query", "").strip()
            info_type = kwargs.get("info_type", "all").lower()

            if not document_query:
                self._logger.warning("query_document_intelligence called without document_query")
                return "[Tool skipped - document_query is required. Ask the user which document they want to know about.]"

            # Get client_id from runtime context
            runtime_ctx = self._runtime_context.get(slug) or {}
            client_id = runtime_ctx.get("client_id")

            if not client_id:
                raise ToolError("Cannot query document intelligence: missing client_id in context.")

            if not self._primary_supabase:
                raise ToolError("Document intelligence query failed: no database connection available.")

            try:
                self._logger.info(
                    f"📄 Querying document intelligence: query='{document_query}', type={info_type}"
                )

                # Search for documents matching the query
                result = await asyncio.to_thread(
                    lambda: self._primary_supabase.rpc(
                        "search_document_intelligence",
                        {
                            "p_client_id": client_id,
                            "p_query": document_query,
                            "p_limit": 5
                        }
                    ).execute()
                )

                if not result.data:
                    return f"No documents found matching '{document_query}'. The document may not have been processed yet or the title might be different."

                # Format the results based on info_type
                output_parts = []

                for doc in result.data:
                    doc_title = doc.get("document_title", "Untitled")
                    summary = doc.get("summary", "")
                    key_quotes = doc.get("key_quotes", [])
                    themes = doc.get("themes", [])

                    doc_section = f"## {doc_title}\n"

                    if info_type in ["summary", "all"]:
                        if summary:
                            doc_section += f"\n**Summary:** {summary}\n"

                    if info_type in ["quotes", "all"]:
                        if key_quotes and isinstance(key_quotes, list):
                            doc_section += "\n**Key Quotes:**\n"
                            for i, quote in enumerate(key_quotes[:10], 1):
                                doc_section += f'{i}. "{quote}"\n'

                    if info_type in ["themes", "all"]:
                        if themes and isinstance(themes, list):
                            doc_section += f"\n**Themes:** {', '.join(themes)}\n"

                    # For full intelligence, also get entities and questions
                    if info_type in ["entities", "questions", "all"]:
                        # Fetch full intelligence for this document
                        full_intel = await asyncio.to_thread(
                            lambda doc_id=doc.get("document_id"): self._primary_supabase.rpc(
                                "get_document_intelligence",
                                {
                                    "p_document_id": doc_id,
                                    "p_client_id": client_id
                                }
                            ).execute()
                        )

                        if full_intel.data and full_intel.data.get("exists"):
                            intel = full_intel.data.get("intelligence", {})

                            if info_type in ["entities", "all"]:
                                entities = intel.get("entities", {})
                                if entities:
                                    entity_parts = []
                                    for etype, elist in entities.items():
                                        if elist:
                                            entity_parts.append(f"{etype}: {', '.join(elist[:5])}")
                                    if entity_parts:
                                        doc_section += f"\n**Entities:** {'; '.join(entity_parts)}\n"

                            if info_type in ["questions", "all"]:
                                questions = intel.get("questions_answered", [])
                                if questions:
                                    doc_section += "\n**Questions Answered:**\n"
                                    for q in questions[:5]:
                                        doc_section += f"- {q}\n"

                    output_parts.append(doc_section)

                output_msg = "\n---\n".join(output_parts)

                # Build citations from the found documents for UI display
                documentsense_citations = []
                for doc in result.data:
                    doc_id = doc.get("document_id")
                    doc_title = doc.get("document_title", "Untitled")
                    summary = doc.get("summary", "")
                    # Create a citation entry that matches the expected format
                    documentsense_citations.append({
                        "id": doc_id,
                        "document_id": doc_id,
                        "title": doc_title,
                        "content": summary[:500] if summary else f"Document: {doc_title}",
                        "similarity": 1.0,  # Perfect match since user asked for this doc
                        "source": "documentsense",
                    })

                self._emit_tool_result(
                    slug=slug,
                    tool_type="documentsense",
                    success=True,
                    output=f"Found {len(result.data)} document(s) matching '{document_query}'",
                    raw_output=result.data,
                    citations=documentsense_citations,  # Include citations for UI
                )

                self._logger.info(f"✅ Document intelligence retrieved for query '{document_query}'")

                return output_msg

            except ToolError:
                raise
            except Exception as exc:
                error_msg = f"Document intelligence query failed: {str(exc)}"
                self._emit_tool_result(
                    slug=slug,
                    tool_type="documentsense",
                    success=False,
                    error=error_msg,
                )
                self._logger.error(f"❌ Document intelligence query error: {exc}", exc_info=True)
                raise ToolError(error_msg)

        return lk_function_tool(raw_schema=raw_schema)(_invoke_query_document_intelligence)

    def _build_content_catalyst_tool(self, t: Dict[str, Any]) -> Any:
        """Build the Content Catalyst tool for multi-phase article generation."""
        slug = t.get("slug") or t.get("name") or t.get("id") or "content_catalyst"
        cfg = dict(t.get("config") or {})
        description = t.get("description") or (
            "Trigger the Content Catalyst article generation widget. "
            "When the user asks you to write an article, blog post, or generate content, "
            "use this tool to open the Content Catalyst configuration widget. "
            "The user will configure their preferences (topic, source type, word count, style) "
            "directly in the widget interface and submit from there. "
            "Call this tool with trigger_widget=true and an optional suggested_topic from the conversation."
        )

        # Get client_id from runtime context or config
        client_id = cfg.get("client_id")

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

            # Extract all possible parameters from LLM tool call
            suggested_topic = kwargs.get("suggested_topic", "") or kwargs.get("source_content", "")
            source_type = kwargs.get("source_type", "topic")
            source_content = kwargs.get("source_content", "")
            target_word_count = kwargs.get("target_word_count")
            style_prompt = kwargs.get("style_prompt", "")

            # This tool now just returns a signal that the widget should be shown
            # The actual API call will be made by the frontend widget when user submits
            widget_trigger = {
                "widget_type": "content_catalyst",
                "suggested_topic": suggested_topic,
                "source_type": source_type,
                "source_content": source_content,
                "target_word_count": target_word_count,
                "style_prompt": style_prompt,
                "message": "Opening Content Catalyst configuration...",
            }

            # Emit the widget trigger through tool result callback
            self._emit_tool_result(
                slug=slug,
                tool_type="content_catalyst",
                success=True,
                output="Widget triggered",
                raw_output=widget_trigger,
            )

            return f"WIDGET_TRIGGER:content_catalyst:{suggested_topic}"

        return lk_function_tool(raw_schema=raw_schema)(_invoke_raw)

    def _build_lingua_tool(self, t: Dict[str, Any]) -> Any:
        """Build the LINGUA tool for audio transcription and subtitle translation."""
        slug = t.get("slug") or t.get("name") or t.get("id") or "lingua"
        cfg = dict(t.get("config") or {})
        description = t.get("description") or (
            "Trigger the LINGUA audio transcription and subtitle translation widget. "
            "When the user wants to transcribe audio, generate subtitles, or translate subtitles, "
            "use this tool to open the LINGUA configuration widget. "
            "The user will upload their audio file and select translation languages "
            "directly in the widget interface. "
            "Call this tool with trigger_widget=true when the user mentions transcription, subtitles, "
            "captions, or audio-to-text conversion."
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
                        "description": "Set to true to trigger the LINGUA widget UI",
                    },
                    "suggested_context": {
                        "type": "string",
                        "description": "Optional context from conversation (e.g., language preferences)",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

        async def _invoke_raw(**kwargs: Any) -> str:
            """Trigger the LINGUA widget UI for audio transcription."""
            try:
                self._logger.info("🌐 LINGUA widget trigger invoked", extra={"args": kwargs})
            except Exception:
                pass

            suggested_context = kwargs.get("suggested_context", "")

            widget_trigger = {
                "widget_type": "lingua",
                "suggested_context": suggested_context,
                "message": "Opening LINGUA transcription widget...",
            }

            # Emit the widget trigger through tool result callback
            self._emit_tool_result(
                slug=slug,
                tool_type="lingua",
                success=True,
                output="Widget triggered",
                raw_output=widget_trigger,
            )

            return f"WIDGET_TRIGGER:lingua:{suggested_context}"

        return lk_function_tool(raw_schema=raw_schema)(_invoke_raw)

    def _build_scrape_url_tool(self, t: Dict[str, Any]) -> Any:
        """
        Build the scrape_url tool for fetching and extracting content from URLs.

        Uses self-hosted Firecrawl service for web scraping when available.
        Falls back to built-in BeautifulSoup + html2text scraping if Firecrawl
        is unavailable or returns an error.
        """
        slug = t.get("slug") or t.get("name") or t.get("id") or "scrape_url"
        cfg = dict(t.get("config") or {})
        description = t.get("description") or (
            "Scrape a URL and extract its main content as clean markdown. "
            "Use this when the user shares a URL and wants you to read, summarize, or answer questions about the page content. "
            "Returns the page title, main content (as markdown), and metadata. "
            "Works with articles, blog posts, documentation, and most web pages."
        )

        # Get Firecrawl URL from environment or config
        firecrawl_url = cfg.get("firecrawl_url") or os.getenv("FIRECRAWL_URL", "http://firecrawl:3002")

        # Timeout for scraping (some pages take longer)
        try:
            timeout_seconds = float(cfg.get("timeout") or 30)
        except (TypeError, ValueError):
            timeout_seconds = 30.0

        raw_schema = {
            "name": slug,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to scrape. Must be a valid http:// or https:// URL.",
                    },
                    "include_links": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, include a list of links found on the page.",
                    },
                    "wait_for_js": {
                        "type": "integer",
                        "description": "Milliseconds to wait for JavaScript to render (for dynamic pages). Default: 0 (no wait). Note: Only works with Firecrawl; built-in fallback does not support JS rendering.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        }

        async def _scrape_with_fallback(url: str, include_links: bool = False) -> dict:
            """
            Built-in scraping fallback using BeautifulSoup + html2text.
            Returns dict with: title, description, markdown, links, source_url
            """
            try:
                from bs4 import BeautifulSoup
                import html2text
            except ImportError:
                raise ToolError("Web scraping libraries not available. Please contact support.")

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }

            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        raise ToolError(f"HTTP {resp.status} error fetching URL")

                    content_type = resp.headers.get("Content-Type", "")
                    if "text/html" not in content_type.lower() and "application/xhtml" not in content_type.lower():
                        raise ToolError(f"URL returned non-HTML content type: {content_type}")

                    html_content = await resp.text()
                    final_url = str(resp.url)

            # Parse HTML
            soup = BeautifulSoup(html_content, "lxml")

            # Extract title
            title = "Untitled"
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
            elif soup.find("h1"):
                title = soup.find("h1").get_text(strip=True)

            # Extract description from meta tags
            description = ""
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc and meta_desc.get("content"):
                description = meta_desc["content"].strip()
            elif soup.find("meta", attrs={"property": "og:description"}):
                og_desc = soup.find("meta", attrs={"property": "og:description"})
                if og_desc and og_desc.get("content"):
                    description = og_desc["content"].strip()

            # Remove unwanted elements before conversion
            for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript", "iframe"]):
                tag.decompose()

            # Try to find main content area
            main_content = None
            for selector in ["main", "article", "[role='main']", ".content", "#content", ".post", ".article"]:
                main_content = soup.select_one(selector)
                if main_content:
                    break

            if not main_content:
                main_content = soup.find("body") or soup

            # Convert to markdown using html2text
            h2t = html2text.HTML2Text()
            h2t.ignore_links = False
            h2t.ignore_images = True
            h2t.ignore_emphasis = False
            h2t.body_width = 0  # No wrapping
            h2t.skip_internal_links = True

            markdown_content = h2t.handle(str(main_content))

            # Clean up excessive whitespace
            import re
            markdown_content = re.sub(r'\n{3,}', '\n\n', markdown_content)
            markdown_content = markdown_content.strip()

            # Extract links if requested
            links = []
            if include_links:
                for a_tag in soup.find_all("a", href=True):
                    href = a_tag["href"]
                    if href.startswith(("http://", "https://")):
                        links.append(href)
                links = list(dict.fromkeys(links))  # Remove duplicates while preserving order

            return {
                "title": title,
                "description": description,
                "markdown": markdown_content,
                "links": links,
                "source_url": final_url,
            }

        async def _invoke_scrape(**kwargs: Any) -> str:
            """Scrape a URL and return markdown content."""
            url = kwargs.get("url", "").strip()
            include_links = kwargs.get("include_links", False)
            wait_for_js = kwargs.get("wait_for_js")

            if not url:
                raise ToolError("URL is required. Please provide a valid web address to scrape.")

            # Validate URL format
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"

            use_fallback = False
            firecrawl_error = None

            # Try Firecrawl first
            try:
                self._logger.info(f"🌐 Scraping URL: {url} via Firecrawl at {firecrawl_url}")

                scrape_endpoint = f"{firecrawl_url.rstrip('/')}/v1/scrape"
                payload = {
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                }

                if include_links:
                    payload["formats"].append("links")

                if wait_for_js:
                    payload["waitFor"] = int(wait_for_js)

                timeout = aiohttp.ClientTimeout(total=timeout_seconds)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        scrape_endpoint,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    ) as resp:
                        response_text = await resp.text()

                        if resp.status >= 400:
                            firecrawl_error = f"Firecrawl HTTP {resp.status}"
                            use_fallback = True
                        else:
                            try:
                                data = json.loads(response_text)
                            except json.JSONDecodeError:
                                firecrawl_error = "Invalid JSON from Firecrawl"
                                use_fallback = True

                            if not use_fallback:
                                if not data.get("success"):
                                    firecrawl_error = data.get("error", "Firecrawl error")
                                    use_fallback = True
                                else:
                                    result_data = data.get("data", {})
                                    markdown_content = result_data.get("markdown", "")
                                    if not markdown_content:
                                        firecrawl_error = "Firecrawl returned empty content"
                                        use_fallback = True
                                    else:
                                        # Firecrawl success
                                        metadata = result_data.get("metadata", {})
                                        links = result_data.get("links", [])
                                        title = metadata.get("title", "Untitled")
                                        description = metadata.get("description", "")
                                        source_url = metadata.get("sourceURL", url)

                                        return self._format_scrape_output(
                                            slug, title, description, source_url,
                                            markdown_content, links if include_links else [], "firecrawl"
                                        )

            except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as e:
                firecrawl_error = f"Firecrawl unavailable: {type(e).__name__}"
                use_fallback = True

            # Fallback to built-in scraping
            if use_fallback:
                try:
                    self._logger.info(f"🔄 Firecrawl failed ({firecrawl_error}), using built-in scraper for: {url}")
                except Exception:
                    pass

                try:
                    result = await _scrape_with_fallback(url, include_links)
                    return self._format_scrape_output(
                        slug,
                        result["title"],
                        result["description"],
                        result["source_url"],
                        result["markdown"],
                        result["links"] if include_links else [],
                        "builtin"
                    )
                except ToolError:
                    raise
                except Exception as e:
                    error_msg = f"Failed to scrape {url}: {str(e)}"
                    try:
                        self._logger.error(f"❌ {error_msg}", exc_info=True)
                    except Exception:
                        pass
                    self._emit_tool_result(
                        slug=slug,
                        tool_type="scrape_url",
                        success=False,
                        error=error_msg,
                    )
                    raise ToolError(error_msg)

            # Should not reach here, but just in case
            raise ToolError(f"Failed to scrape URL: {firecrawl_error or 'Unknown error'}")

        return lk_function_tool(raw_schema=raw_schema)(_invoke_scrape)

    def _format_scrape_output(
        self, slug: str, title: str, description: str, source_url: str,
        markdown_content: str, links: list, method: str
    ) -> str:
        """Format scraped content into a clean markdown response."""
        output_parts = [
            f"# {title}",
            f"**Source:** [{source_url}]({source_url})",
        ]

        if description:
            output_parts.append(f"**Description:** {description}")

        output_parts.append("")
        output_parts.append("## Content")
        output_parts.append("")
        output_parts.append(markdown_content[:8000])

        if links:
            output_parts.append("")
            output_parts.append("## Links Found")
            for link in links[:20]:
                output_parts.append(f"- [{link}]({link})")

        output = "\n".join(output_parts)

        try:
            self._logger.info(
                f"✅ Successfully scraped {source_url} via {method}: {len(markdown_content)} chars",
                extra={"title": title, "url": source_url, "method": method},
            )
        except Exception:
            pass

        self._emit_tool_result(
            slug=slug,
            tool_type="scrape_url",
            success=True,
            output=f"Scraped: {title} ({len(markdown_content)} chars) via {method}",
            raw_output={"url": source_url, "title": title, "content_length": len(markdown_content), "method": method},
        )

        return output
