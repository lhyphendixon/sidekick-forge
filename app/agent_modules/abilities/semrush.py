"""
Semrush SEO Ability

Builds a LiveKit function tool that proxies requests to the semrush-mcp
npm package running as a stdio subprocess. Provides 60+ SEO analytics tools
via a single meta-tool interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SemrushAbilityConfigError(Exception):
    pass


class SemrushMCPClient:
    """Manages a long-lived semrush-mcp subprocess communicating over JSON-RPC/stdio."""

    def __init__(self, api_key: str, *, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._lock = asyncio.Lock()
        self._initialized = False

    async def start(self) -> None:
        async with self._lock:
            if self._process and self._process.returncode is None:
                return

            # Find the semrush-mcp binary
            binary = shutil.which("semrush-mcp")
            if binary:
                cmd = [binary]
            else:
                npx = shutil.which("npx")
                if not npx:
                    raise SemrushAbilityConfigError(
                        "Neither semrush-mcp nor npx found. "
                        "Install Node.js and run: npm install -g semrush-mcp"
                    )
                cmd = [npx, "-y", "semrush-mcp"]

            env = {**os.environ, "SEMRUSH_API_KEY": self._api_key}

            logger.info("Starting semrush-mcp subprocess: %s", cmd)
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._reader_task = asyncio.create_task(self._read_loop())

            # MCP initialize handshake
            await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "sidekick-forge", "version": "1.0.0"},
            })
            # Send initialized notification (no id, no response expected)
            await self._send_notification("notifications/initialized", {})
            self._initialized = True
            logger.info("semrush-mcp subprocess initialized successfully")

    async def _read_loop(self) -> None:
        assert self._process and self._process.stdout
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.debug("semrush-mcp non-JSON output: %s", line_str[:200])
                    continue

                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if not future.done():
                        if "error" in msg:
                            future.set_exception(
                                SemrushAbilityConfigError(
                                    f"MCP error: {msg['error'].get('message', msg['error'])}"
                                )
                            )
                        else:
                            future.set_result(msg.get("result"))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("semrush-mcp reader loop error: %s", exc)
        finally:
            # Resolve any pending futures with errors
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(SemrushAbilityConfigError("MCP process exited"))
            self._pending.clear()

    async def _send_request(self, method: str, params: Dict[str, Any]) -> Any:
        assert self._process and self._process.stdin
        req_id = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        data = json.dumps(msg) + "\n"
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future
        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise SemrushAbilityConfigError(
                f"Semrush MCP request timed out after {self._timeout}s"
            )

    async def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        assert self._process and self._process.stdin
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        data = json.dumps(msg) + "\n"
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

    async def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Call a Semrush MCP tool. Lazily starts the subprocess if needed."""
        if not self._process or self._process.returncode is not None:
            self._initialized = False
            await self.start()

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
            # Try to parse as JSON for structured data
            try:
                return json.loads(combined)
            except (json.JSONDecodeError, TypeError):
                return {"raw_text": combined}
        return result or {}

    async def stop(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception:
                self._process.kill()
        self._initialized = False


# Complete tool catalog for system prompt
SEMRUSH_TOOL_CATALOG = """
## Domain Analytics
- semrush_domain_overview: {domain, database?, limit?} — Traffic, keywords, backlinks overview
- semrush_domain_rank: {domain, database?, limit?} — Domain ranking data
- semrush_domain_rank_history: {domain, database?, limit?} — Historical ranking
- semrush_rank_difference: {database?, limit?} — Biggest ranking changes

## Domain Keyword Reports
- semrush_domain_organic_keywords: {domain, database?, limit?} — Organic keyword positions
- semrush_domain_paid_keywords: {domain, database?, limit?} — Paid keyword positions
- semrush_competitors: {domain, database?, limit?} — Organic competitors
- semrush_paid_competitors: {domain, database?, limit?} — Paid competitors
- semrush_domain_ads_history: {domain, database?, limit?} — Ad history
- semrush_domain_organic_unique: {domain, database?, limit?} — Unique organic keywords
- semrush_domain_adwords_unique: {domain, database?, limit?} — Unique paid keywords
- semrush_domain_shopping: {domain, database?, limit?} — Shopping campaigns
- semrush_domain_shopping_unique: {domain, database?, limit?} — Unique shopping keywords

## Keyword Research
- semrush_keyword_overview: {keyword, database?} — Volume, difficulty, CPC, trend
- semrush_keyword_overview_single_db: {keyword, database} — Single database overview
- semrush_batch_keyword_overview: {keywords (array, max 100), database?} — Batch overview
- semrush_related_keywords: {keyword, database?, limit?} — Related keywords
- semrush_broad_match_keywords: {keyword, database, limit?} — Broad match variations
- semrush_phrase_questions: {keyword, database, limit?} — Question-form keywords
- semrush_keyword_difficulty: {keywords (array), database?} — Difficulty scores
- semrush_keyword_organic_results: {keyword, database, limit?} — Organic SERP results
- semrush_keyword_paid_results: {keyword, database, limit?} — Paid SERP results
- semrush_keyword_ads_history: {keyword, database, limit?} — Keyword ad history

## URL Analysis
- semrush_url_organic: {url, database?, limit?} — URL organic keywords
- semrush_url_adwords: {url, database?, limit?} — URL paid keywords
- semrush_url_rank: {url, database?, limit?} — URL ranking
- semrush_url_rank_history: {url, database?, limit?} — URL rank history
- semrush_url_ranks: {url, limit?} — URL ranks across all databases

## Subdomain Analysis
- semrush_subdomain_rank: {subdomain, database?, limit?}
- semrush_subdomain_ranks: {subdomain, limit?}
- semrush_subdomain_rank_history: {subdomain, database?, limit?}
- semrush_subdomain_organic: {subdomain, database?, limit?}

## Subfolder Analysis
- semrush_subfolder_organic: {subfolder, database?, limit?}
- semrush_subfolder_adwords: {subfolder, database?, limit?}
- semrush_subfolder_rank: {subfolder, database?, limit?}
- semrush_subfolder_ranks: {subfolder, limit?}
- semrush_subfolder_rank_history: {subfolder, database?, limit?}
- semrush_subfolder_organic_unique: {subfolder, database?, limit?}
- semrush_subfolder_adwords_unique: {subfolder, database?, limit?}

## Backlinks
- semrush_backlinks: {target, limit?} — Backlink list
- semrush_backlinks_domains: {target, limit?} — Referring domains
- semrush_backlinks_overview: {target, target_type? (root_domain|domain|url), limit?}
- semrush_backlinks_pages: {target, target_type?, limit?} — Indexed pages
- semrush_backlinks_anchors: {target, target_type?, limit?} — Anchor texts
- semrush_backlinks_tld: {target, target_type?, limit?} — TLD distribution
- semrush_backlinks_categories: {target, target_type?, limit?} — Category distribution

## Traffic Analytics (requires .Trends subscription)
- semrush_traffic_summary: {domains (array), country?}
- semrush_traffic_sources: {domain, country?}
- semrush_traffic_destinations: {target, country?, device_type?, display_date?, limit?}
- semrush_traffic_geo: {target, country?, device_type?, display_date?, limit?}
- semrush_traffic_subdomains: {target, country?, device_type?, display_date?, limit?}
- semrush_traffic_subfolders: {target, country?, device_type?, display_date?, limit?}
- semrush_traffic_top_pages: {target, country?, device_type?, display_date?, limit?}
- semrush_traffic_rank: {target, country?, device_type?, display_date?, limit?}
- semrush_traffic_social_media: {target, country?, device_type?, display_date?, limit?}
- semrush_audience_insights: {targets (array), selected_targets (array), limit?}
- semrush_purchase_conversion: {target, country?, display_date?, limit?}
- semrush_household_distribution: {target, country?, device_type?, display_date?, limit?}
- semrush_income_distribution: {target, country?, device_type?, display_date?, limit?}
- semrush_education_distribution: {target, country?, device_type?, display_date?, limit?}
- semrush_occupation_distribution: {target, country?, device_type?, display_date?, limit?}
- semrush_audience_interests: {target, country?, device_type?, display_date?, limit?}
- semrush_traffic_accuracy: {target, display_date?, limit?}

## Projects
- semrush_list_projects: {} — List all projects
- semrush_get_project: {project_id} — Get project details
- semrush_create_project: {url, project_name?}
- semrush_update_project: {project_id, project_name}
- semrush_delete_project: {project_id}

## Site Audit
- semrush_site_audit_info: {project_id}
- semrush_site_audit_snapshots: {project_id}
- semrush_site_audit_snapshot_detail: {project_id, snapshot_id}
- semrush_site_audit_issues: {project_id}
- semrush_site_audit_pages: {project_id, url, limit?, page?}
- semrush_site_audit_page_detail: {project_id, page_id}
- semrush_site_audit_history: {project_id, limit?, offset?}
- semrush_site_audit_launch: {project_id}

## Utility
- semrush_api_units_balance: {} — Check remaining API units

Notes: "database" defaults to "us". Available: us, uk, ca, au, de, fr, es, it, br, ru, jp, etc.
"""


def build_semrush_tool(
    tool_def: Dict[str, Any],
    config: Dict[str, Any],
    *,
    api_keys: Optional[Dict[str, str]] = None,
) -> Any:
    """Build the Semrush SEO LiveKit function tool.

    Uses a single meta-tool that accepts a Semrush tool name + params,
    proxying requests to the semrush-mcp subprocess.
    """
    from livekit.agents.llm.tool_context import function_tool as lk_function_tool

    slug = tool_def.get("slug") or "semrush"

    # Resolve API key: config > api_keys > env
    api_key = config.get("semrush_api_key")
    if not api_key and api_keys:
        api_key = api_keys.get("semrush_api_key")
    if not api_key:
        api_key = os.getenv("SEMRUSH_API_KEY")
    if not api_key:
        raise SemrushAbilityConfigError(
            "Semrush API key not configured. Set semrush_api_key in client settings "
            "or SEMRUSH_API_KEY environment variable."
        )

    timeout = float(config.get("timeout", 30))
    mcp_client = SemrushMCPClient(api_key=api_key, timeout=timeout)

    description = (
        "Query Semrush for SEO and content marketing intelligence. "
        "Pass a Semrush tool name and its parameters to get keyword research, "
        "domain analytics, competitor analysis, backlink data, traffic insights, and more.\n\n"
        "Common examples:\n"
        '- Keyword research: {"tool": "semrush_keyword_overview", "params": {"keyword": "content marketing", "database": "us"}}\n'
        '- Domain overview: {"tool": "semrush_domain_overview", "params": {"domain": "example.com"}}\n'
        '- Competitors: {"tool": "semrush_competitors", "params": {"domain": "example.com"}}\n'
        '- Keyword difficulty: {"tool": "semrush_keyword_difficulty", "params": {"keywords": ["seo", "content strategy"]}}\n'
        '- Related keywords: {"tool": "semrush_related_keywords", "params": {"keyword": "email marketing", "database": "us", "limit": 20}}\n'
        '- Questions: {"tool": "semrush_phrase_questions", "params": {"keyword": "seo", "database": "us"}}\n'
        '- Backlinks: {"tool": "semrush_backlinks_overview", "params": {"target": "example.com"}}\n'
        '- API balance: {"tool": "semrush_api_units_balance", "params": {}}\n\n'
        "The 'database' parameter defaults to 'us'. Available: us, uk, ca, au, de, fr, es, it, br, etc.\n"
        "For the full list of 60+ available tools, consult the Semrush tool catalog."
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
                        "The Semrush MCP tool name to call (e.g. semrush_keyword_overview, "
                        "semrush_domain_overview, semrush_competitors, semrush_backlinks_overview)"
                    ),
                },
                "params": {
                    "type": "object",
                    "description": "Tool-specific parameters (e.g. {\"domain\": \"example.com\", \"database\": \"us\"})",
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
            return json.dumps({"error": "Missing 'tool' parameter. Specify which Semrush tool to call."})

        logger.info("Semrush tool call: %s with params: %s", tool_name, params)

        try:
            result = await mcp_client.call_tool(tool_name, params)
            # Wrap result with metadata for citation injection
            output = {
                "tool": tool_name,
                "params": params,
                "data": result,
                "source": "Semrush",
            }
            return json.dumps(output, default=str)
        except SemrushAbilityConfigError as exc:
            logger.error("Semrush MCP error: %s", exc)
            return json.dumps({"error": str(exc), "tool": tool_name})
        except Exception as exc:
            logger.error("Semrush tool call failed: %s", exc, exc_info=True)
            return json.dumps({"error": f"Semrush request failed: {exc}", "tool": tool_name})

    return lk_function_tool(raw_schema=raw_schema)(_invoke)
