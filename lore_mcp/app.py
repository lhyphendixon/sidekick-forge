"""
Lore — Personal Context MCP Server (Supabase-backed, multi-tenant).

Stores per-user personal context across ten categories. Data lives in the
user's home client Supabase instance (platform shared DB for Adventurer-tier
users, dedicated instance for Champion/Paragon).

External MCP clients authenticate with static Bearer tokens issued by the
Sidekick Forge admin UI. Internal callers (admin routes, voice interview,
agent worker) authenticate with the platform service role key in the
X-Lore-Internal header plus explicit user_id/target query params.

The MCP tools themselves do NOT accept user_id or target credentials as
parameters — these are injected server-side from the authenticated context,
so external clients cannot impersonate other users or point the MCP at
arbitrary Supabase instances.

Tables expected on every target instance:
    lore_files       (user_id, category, content, updated_at)
    lore_summary     (user_id, content, updated_at)
    lore_categories  (category, description, sort_order)

Port 8082, mounted at /mcp/sse for MCP protocol, admin REST at /admin-api/*.
"""

import contextvars
import hashlib
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import Context, FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from supabase import Client as SupabaseClient, create_client

from auth import (
    LoreContext,
    hash_token,
    optional_lore_context,
    require_lore_context,
    resolve_token,
    resolve_user_home_target,
    token_prefix,
)
from oauth import router as oauth_router
from external_endpoints import (
    get_external_endpoint,
    remote_list_categories,
    remote_read_category,
    remote_read_summary,
    remote_write_category,
)

LOGGER = logging.getLogger("lore_mcp")

DEFAULT_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
DEFAULT_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

VALID_CATEGORIES = {
    "identity": "Name, role, org, philosophy, personal context",
    "roles_and_responsibilities": "Day-to-day work, outputs, decisions, who you serve",
    "current_projects": "Active workstreams, status, priority, KPIs, definition of done",
    "team_and_relationships": "Key people, roles, what each relationship requires",
    "tools_and_systems": "Stack, architecture patterns, constraints, design systems",
    "communication_style": "Tone, formatting, editing preferences, voice matching notes",
    "goals_and_priorities": "Week / quarter / year / career optimization targets",
    "preferences_and_constraints": "Always/never rules, tool preferences, hard constraints",
    "domain_knowledge": "Expertise areas, frameworks used, what NOT to explain",
    "decision_log": "Past decisions and reasoning — how you think",
}

SUMMARY_PRIORITY = [
    "identity",
    "goals_and_priorities",
    "communication_style",
    "preferences_and_constraints",
    "current_projects",
]
SUMMARY_SECTION_CHAR_LIMIT = 400


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO)
    LOGGER.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Per-MCP-session LoreContext. The SSE middleware validates the Bearer token
# on connection and stores the resolved context in a ContextVar so all tools
# called during that session automatically pick it up.
# ---------------------------------------------------------------------------

_current_context: contextvars.ContextVar[Optional[LoreContext]] = contextvars.ContextVar(
    "lore_context", default=None
)


def get_current_context() -> LoreContext:
    """Retrieve the LoreContext for the current MCP tool call.
    Raises if not authenticated — tools should never be called without one."""
    ctx = _current_context.get()
    if ctx is None:
        raise RuntimeError(
            "Lore MCP tool called without authentication. "
            "External clients must provide Authorization: Bearer slf_lore_<token>."
        )
    return ctx


# ---------------------------------------------------------------------------
# Connection cache — key on (url, sha256(key)[:12]), TTL 10 minutes
# ---------------------------------------------------------------------------

_connection_cache: Dict[str, Tuple[SupabaseClient, float]] = {}
_CACHE_TTL_SECONDS = 600


def _cache_key(url: str, key: str) -> str:
    h = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"{url}:{h}"


def _get_client(url: Optional[str] = None, key: Optional[str] = None) -> SupabaseClient:
    resolved_url = url or DEFAULT_SUPABASE_URL
    resolved_key = key or DEFAULT_SUPABASE_KEY
    if not resolved_url or not resolved_key:
        raise RuntimeError("No Supabase credentials available.")
    ck = _cache_key(resolved_url, resolved_key)
    now = time.time()
    if ck in _connection_cache:
        client, created_at = _connection_cache[ck]
        if now - created_at < _CACHE_TTL_SECONDS:
            return client
    client = create_client(resolved_url, resolved_key)
    _connection_cache[ck] = (client, now)
    return client


# ---------------------------------------------------------------------------
# Storage operations — take credentials explicitly (used by all tools via
# the current LoreContext)
# ---------------------------------------------------------------------------

def _read_category(user_id: str, category: str, url: Optional[str] = None, key: Optional[str] = None) -> Optional[str]:
    # 1. Self-host override — if the user registered an external endpoint,
    #    fetch from there instead of the platform Supabase.
    ext = get_external_endpoint(user_id)
    if ext is not None:
        return remote_read_category(ext[0], ext[1], category)

    client = _get_client(url, key)
    try:
        resp = client.table("lore_files").select("content").eq("user_id", user_id).eq("category", category).maybe_single().execute()
        if resp and resp.data:
            return resp.data.get("content", "") or ""
    except Exception as exc:
        LOGGER.warning(f"_read_category({category}, user={user_id[:8]}): {exc}")
    return None


def _write_category(user_id: str, category: str, content: str, url: Optional[str] = None, key: Optional[str] = None) -> bool:
    ext = get_external_endpoint(user_id)
    if ext is not None:
        return remote_write_category(ext[0], ext[1], category, content)

    client = _get_client(url, key)
    try:
        client.table("lore_files").upsert(
            {"user_id": user_id, "category": category, "content": content},
            on_conflict="user_id,category",
        ).execute()
        return True
    except Exception as exc:
        LOGGER.error(f"_write_category({category}, user={user_id[:8]}): {exc}")
        return False


def _read_astrology(user_id: str, url: Optional[str] = None, key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Read the user's stored astrology row (birth chart + Human Design).

    Returns None if no row exists. Self-host endpoints are not supported for
    astrology data yet — if a user is self-hosting, this returns None.
    """
    if get_external_endpoint(user_id) is not None:
        return None

    client = _get_client(url, key)
    try:
        resp = (
            client.table("lore_astrology")
            .select("*")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if resp and resp.data:
            return resp.data
    except Exception as exc:
        LOGGER.warning(f"_read_astrology(user={user_id[:8]}): {exc}")
    return None


# ---------------------------------------------------------------------------
# MCP visibility flags — per-user toggles controlling which nodes are exposed
# to the MCP. Missing row / missing key = enabled (opt-out default).
# ---------------------------------------------------------------------------

VISIBILITY_NODES = ("birth_chart", "human_design", "mbti", "big5")


def _read_visibility(
    user_id: str,
    url: Optional[str] = None,
    key: Optional[str] = None,
) -> Dict[str, bool]:
    """Return the user's visibility flags. Defaults to all-enabled if no row."""
    if get_external_endpoint(user_id) is not None:
        return {k: True for k in VISIBILITY_NODES}
    client = _get_client(url, key)
    try:
        resp = (
            client.table("lore_mcp_visibility")
            .select("flags")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        raw = (resp.data or {}).get("flags") if resp else {}
        raw = raw or {}
    except Exception as exc:
        LOGGER.warning(f"_read_visibility(user={user_id[:8]}): {exc}")
        raw = {}
    return {k: bool(raw.get(k, True)) for k in VISIBILITY_NODES}


def _write_visibility(
    user_id: str,
    flags: Dict[str, bool],
    url: Optional[str] = None,
    key: Optional[str] = None,
) -> bool:
    if get_external_endpoint(user_id) is not None:
        return True
    client = _get_client(url, key)
    # Merge over existing flags so partial updates don't wipe other keys
    existing = _read_visibility(user_id, url, key)
    merged = {**existing, **{k: bool(v) for k, v in flags.items() if k in VISIBILITY_NODES}}
    try:
        client.table("lore_mcp_visibility").upsert(
            {"user_id": user_id, "flags": merged}, on_conflict="user_id"
        ).execute()
        return True
    except Exception as exc:
        LOGGER.error(f"_write_visibility(user={user_id[:8]}): {exc}")
        return False


# ---------------------------------------------------------------------------
# Depth scores — cached LLM rubric grades per node
# ---------------------------------------------------------------------------

ALL_DEPTH_NODES: Tuple[str, ...] = tuple(VALID_CATEGORIES.keys()) + (
    "birth_chart", "human_design", "mbti", "big5",
)

DEPTH_LEVEL_FROM_SCORE = {
    0: ("not_captured", "Not captured"),
    1: ("emerging", "Emerging"),
    2: ("growing", "Growing"),
    3: ("strong", "Strong"),
}


def _read_depth_scores(
    user_id: str,
    url: Optional[str] = None,
    key: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return cached depth scores keyed by node_key."""
    if get_external_endpoint(user_id) is not None:
        return {}
    client = _get_client(url, key)
    try:
        resp = (
            client.table("lore_depth_scores")
            .select("node_key,score,level,detail,content_hash,graded_at")
            .eq("user_id", user_id)
            .execute()
        )
        out: Dict[str, Dict[str, Any]] = {}
        for row in resp.data or []:
            out[row["node_key"]] = row
        return out
    except Exception as exc:
        LOGGER.warning(f"_read_depth_scores(user={user_id[:8]}): {exc}")
        return {}


def _write_depth_score(
    user_id: str,
    node_key: str,
    score: int,
    level: str,
    detail: str,
    content_hash: Optional[str],
    url: Optional[str] = None,
    key: Optional[str] = None,
) -> bool:
    if get_external_endpoint(user_id) is not None:
        return True
    client = _get_client(url, key)
    try:
        from datetime import datetime, timezone
        client.table("lore_depth_scores").upsert({
            "user_id": user_id,
            "node_key": node_key,
            "score": int(max(0, min(3, score))),
            "level": level,
            "detail": detail,
            "content_hash": content_hash,
            "graded_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="user_id,node_key").execute()
        return True
    except Exception as exc:
        LOGGER.error(f"_write_depth_score({node_key}, user={user_id[:8]}): {exc}")
        return False


def _content_hash(content: Any) -> str:
    """Stable SHA-1 hash of a normalized content representation."""
    import json as _json
    if content is None:
        return ""
    if isinstance(content, str):
        material = content.strip()
    else:
        try:
            material = _json.dumps(content, sort_keys=True, default=str)
        except Exception:
            material = str(content)
    return hashlib.sha1(material.encode("utf-8", errors="ignore")).hexdigest()


def _node_content_for_grading(
    user_id: str,
    node_key: str,
    url: Optional[str] = None,
    key: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Return (content_string_for_grader, content_hash) for a given node.

    content_string_for_grader is None when there's nothing to grade yet
    (empty category, no astrology row, no personality field).
    """
    if node_key in VALID_CATEGORIES:
        content = _read_category(user_id, node_key, url, key)
        if not content or not content.strip():
            return None, ""
        return content, _content_hash(content)

    if node_key in ("birth_chart", "human_design"):
        astro = _read_astrology(user_id, url, key)
        if not astro:
            return None, ""
        if node_key == "birth_chart":
            parts = [
                f"Sun sign: {astro.get('sun_sign') or ''}",
                f"Birth place: {astro.get('birth_place') or ''}",
                f"Analysis: {astro.get('birth_chart_analysis') or ''}",
            ]
            content = "\n".join(p for p in parts if p.split(': ', 1)[-1].strip())
        else:
            parts = [
                f"Type: {astro.get('hd_type') or ''}",
                f"Strategy: {astro.get('hd_strategy') or ''}",
                f"Authority: {astro.get('hd_authority') or ''}",
                f"Profile: {astro.get('hd_profile') or ''}",
                f"Analysis: {astro.get('human_design_analysis') or ''}",
            ]
            content = "\n".join(p for p in parts if p.split(': ', 1)[-1].strip())
        if not content.strip():
            return None, ""
        return content, _content_hash(content)

    if node_key == "mbti":
        pers = _read_personality(user_id, url, key)
        if not pers or not pers.get("mbti_type"):
            return None, ""
        content = f"MBTI: {pers['mbti_type']}\n{pers.get('mbti_summary') or ''}"
        return content, _content_hash(content)

    if node_key == "big5":
        pers = _read_personality(user_id, url, key)
        if not pers or pers.get("big5_openness") is None:
            return None, ""
        content = (
            f"Openness: {pers.get('big5_openness')}\n"
            f"Conscientiousness: {pers.get('big5_conscientiousness')}\n"
            f"Extraversion: {pers.get('big5_extraversion')}\n"
            f"Agreeableness: {pers.get('big5_agreeableness')}\n"
            f"Neuroticism: {pers.get('big5_neuroticism')}\n"
            f"{pers.get('big5_summary') or ''}"
        )
        return content, _content_hash(content)

    return None, ""


def _node_enabled(
    user_id: str,
    node: str,
    url: Optional[str] = None,
    key: Optional[str] = None,
) -> bool:
    return _read_visibility(user_id, url, key).get(node, True)


def _read_personality(
    user_id: str,
    url: Optional[str] = None,
    key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the user's lore_personality row or None. Self-host users do not
    have personality data mirrored to their server — it stays on platform."""
    if get_external_endpoint(user_id) is not None:
        return None

    client = _get_client(url, key)
    try:
        resp = (
            client.table("lore_personality")
            .select("*")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if resp and resp.data:
            return resp.data
    except Exception as exc:
        LOGGER.warning(f"_read_personality(user={user_id[:8]}): {exc}")
    return None


def _write_personality(
    user_id: str,
    payload: Dict[str, Any],
    url: Optional[str] = None,
    key: Optional[str] = None,
) -> bool:
    """Partial upsert — only the keys present in `payload` are updated. This
    keeps MBTI and Big5 independent: analyzing Big5 doesn't wipe an existing
    MBTI value the user already typed in, and vice versa."""
    if get_external_endpoint(user_id) is not None:
        LOGGER.info(f"_write_personality: user {user_id[:8]} is self-hosting — skipping")
        return True

    client = _get_client(url, key)
    row = {"user_id": user_id, **payload, "updated_at": "now()"}
    try:
        # Supabase-py doesn't parse "now()" — use an ISO timestamp
        from datetime import datetime, timezone
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        client.table("lore_personality").upsert(row, on_conflict="user_id").execute()
        return True
    except Exception as exc:
        LOGGER.error(f"_write_personality(user={user_id[:8]}): {exc}")
        return False


def _write_astrology(
    user_id: str,
    payload: Dict[str, Any],
    url: Optional[str] = None,
    key: Optional[str] = None,
) -> bool:
    """Upsert the astrology row. `payload` must NOT include user_id — added here."""
    if get_external_endpoint(user_id) is not None:
        LOGGER.info(f"_write_astrology: user {user_id[:8]} is self-hosting — skipping")
        return True

    client = _get_client(url, key)
    row = {"user_id": user_id, **payload}
    try:
        client.table("lore_astrology").upsert(row, on_conflict="user_id").execute()
        return True
    except Exception as exc:
        LOGGER.error(f"_write_astrology(user={user_id[:8]}): {exc}")
        return False


def _read_summary(user_id: str, url: Optional[str] = None, key: Optional[str] = None) -> Optional[str]:
    ext = get_external_endpoint(user_id)
    if ext is not None:
        return remote_read_summary(ext[0], ext[1])

    client = _get_client(url, key)
    try:
        resp = client.table("lore_summary").select("content").eq("user_id", user_id).maybe_single().execute()
        if resp and resp.data:
            return resp.data.get("content", "") or ""
    except Exception as exc:
        LOGGER.warning(f"_read_summary(user={user_id[:8]}): {exc}")
    return None


def _write_summary(user_id: str, content: str, url: Optional[str] = None, key: Optional[str] = None) -> bool:
    # When a user is self-hosting, the remote server regenerates its own
    # summary on every update_lore_category write. We no-op here so we don't
    # round-trip a stale copy of the summary back to their server.
    if get_external_endpoint(user_id) is not None:
        return True

    client = _get_client(url, key)
    try:
        client.table("lore_summary").upsert(
            {"user_id": user_id, "content": content},
            on_conflict="user_id",
        ).execute()
        return True
    except Exception as exc:
        LOGGER.error(f"_write_summary(user={user_id[:8]}): {exc}")
        return False


def _list_user_categories(user_id: str, url: Optional[str] = None, key: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {
        cat: {"has_content": False, "updated_at": None} for cat in VALID_CATEGORIES
    }

    ext = get_external_endpoint(user_id)
    if ext is not None:
        remote = remote_list_categories(ext[0], ext[1])
        if remote is None:
            return result
        for item in remote:
            cat = item.get("key")
            if cat in result:
                result[cat] = {
                    "has_content": bool(item.get("has_content")),
                    "updated_at": item.get("updated_at"),
                }
        return result

    client = _get_client(url, key)
    try:
        resp = client.table("lore_files").select("category,content,updated_at").eq("user_id", user_id).execute()
        for row in resp.data or []:
            cat = row["category"]
            content = row.get("content") or ""
            if cat in result:
                result[cat] = {
                    "has_content": bool(content.strip()),
                    "updated_at": row.get("updated_at"),
                }
    except Exception as exc:
        LOGGER.warning(f"_list_user_categories(user={user_id[:8]}): {exc}")
    return result


def _regenerate_summary(user_id: str, url: Optional[str] = None, key: Optional[str] = None) -> str:
    sections = []
    flags = _read_visibility(user_id, url, key)

    # Astrology + Human Design — each gated by its own visibility flag.
    astro = _read_astrology(user_id, url, key)
    if astro:
        astro_lines = []
        if flags.get("birth_chart", True):
            sun_sign = astro.get("sun_sign")
            if sun_sign:
                astro_lines.append(f"- Sun sign: {sun_sign}")
        if flags.get("human_design", True):
            hd_type = astro.get("hd_type")
            if hd_type:
                astro_lines.append(f"- Human Design type: {hd_type}")
            if astro.get("hd_strategy"):
                astro_lines.append(f"- Strategy: {astro['hd_strategy']}")
            if astro.get("hd_authority"):
                astro_lines.append(f"- Authority: {astro['hd_authority']}")
            if astro.get("hd_profile"):
                astro_lines.append(f"- Profile: {astro['hd_profile']}")
        if astro_lines:
            sections.append("### Astrology & Human Design\n" + "\n".join(astro_lines))

    # Personality — MBTI and Big Five, each gated independently.
    personality = _read_personality(user_id, url, key)
    if personality:
        pers_lines = []
        if flags.get("mbti", True) and personality.get("mbti_type"):
            pers_lines.append(f"- MBTI: {personality['mbti_type']}")
            if personality.get("mbti_summary"):
                pers_lines.append(f"  {personality['mbti_summary'][:SUMMARY_SECTION_CHAR_LIMIT]}")
        if flags.get("big5", True) and personality.get("big5_openness") is not None:
            pers_lines.append(
                f"- Big Five: O={personality.get('big5_openness')} "
                f"C={personality.get('big5_conscientiousness')} "
                f"E={personality.get('big5_extraversion')} "
                f"A={personality.get('big5_agreeableness')} "
                f"N={personality.get('big5_neuroticism')}"
            )
            if personality.get("big5_summary"):
                pers_lines.append(f"  {personality['big5_summary'][:SUMMARY_SECTION_CHAR_LIMIT]}")
        if pers_lines:
            sections.append("### Personality\n" + "\n".join(pers_lines))

    for category in SUMMARY_PRIORITY:
        content = _read_category(user_id, category, url, key)
        if not content or not content.strip():
            continue
        lines = content.strip().splitlines()
        if lines and lines[0].startswith("# "):
            lines = lines[1:]
        preview = "\n".join(lines).strip()[:SUMMARY_SECTION_CHAR_LIMIT]
        if preview:
            label = category.replace("_", " ").title()
            sections.append(f"### {label}\n{preview}")
    if sections:
        summary = (
            "# Lore Summary\n"
            "Compressed personal context for system prompt injection.\n\n"
            + "\n\n".join(sections)
        )
    else:
        summary = ""
    _write_summary(user_id, summary, url, key)
    return summary


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

_CATEGORY_SIGNALS = {
    "identity": ["name", "role", "org", "philosophy", "context"],
    "roles_and_responsibilities": ["work", "output", "decision", "serve"],
    "current_projects": ["project", "status", "priority", "kpi", "done"],
    "team_and_relationships": ["person", "role", "relationship"],
    "tools_and_systems": ["stack", "architecture", "constraint", "design"],
    "communication_style": ["tone", "format", "editing", "voice"],
    "goals_and_priorities": ["week", "quarter", "year", "career"],
    "preferences_and_constraints": ["always", "never", "preference", "constraint"],
    "domain_knowledge": ["expertise", "framework", "explain"],
    "decision_log": ["date", "decision", "reasoning", "outcome"],
}


def _score_category_content(category: str, content: Optional[str]) -> dict:
    if not content or not content.strip():
        return {"level": "not_captured", "score": 0, "detail": "No content yet"}
    text = content.strip()
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    non_header_content = []
    for line in lines:
        stripped = line.strip().rstrip("|").strip()
        if re.match(r"^[\-\|:\s]+$", stripped):
            continue
        if stripped in ("-", "|", "- ", "| |"):
            continue
        if re.match(r"^-\s+\*\*[^*]+\*\*:\s*$", stripped):
            continue
        if re.match(r"^\|(\s*\|)+\s*$", stripped):
            continue
        non_header_content.append(stripped)
    if not non_header_content:
        return {"level": "not_captured", "score": 0, "detail": "Template only, no content filled in"}
    content_length = len(text)
    line_count = len(non_header_content)
    lower_text = text.lower()
    signals = _CATEGORY_SIGNALS.get(category, [])
    signals_found = sum(1 for s in signals if s in lower_text) if signals else 0
    signal_ratio = signals_found / max(len(signals), 1)
    if content_length >= 300 and line_count >= 4 and signal_ratio >= 0.5:
        return {"level": "strong", "score": 3, "detail": f"{line_count} entries, {signals_found}/{len(signals)} signals"}
    elif content_length >= 100 and line_count >= 2:
        return {"level": "growing", "score": 2, "detail": f"{line_count} entries, room to deepen"}
    elif line_count >= 1:
        return {"level": "emerging", "score": 1, "detail": f"{line_count} entries, just getting started"}
    else:
        return {"level": "not_captured", "score": 0, "detail": "No meaningful content"}


# ---------------------------------------------------------------------------
# MCP Server — tools take ZERO user-facing parameters for user_id/target.
# The current context is read from the ContextVar set by the SSE middleware.
# ---------------------------------------------------------------------------

server = FastMCP(
    name="Lore — Personal Context MCP",
    instructions=(
        "Lore is the portable personal context layer for Sidekick Forge. "
        "It stores a structured profile across ten categorical files and "
        "exposes that context to MCP-compatible tools. Authentication via "
        "Bearer token — user identity is resolved server-side."
    ),
    sse_path="/sse",
    message_path="/messages/",
)


@server.tool(
    name="get_lore_summary",
    title="Get Lore Summary",
    description="Returns the compressed personal context summary for the authenticated user.",
)
async def get_lore_summary(ctx: Optional[Context] = None) -> str:
    lc = get_current_context()
    content = _read_summary(lc.user_id, lc.target_url, lc.target_key)
    if content:
        return content
    return _regenerate_summary(lc.user_id, lc.target_url, lc.target_key) or "No Lore content yet."


@server.tool(
    name="get_lore_category",
    title="Get Lore Category",
    description=(
        "Returns the full content of a specific Lore category. "
        "Categories: identity, roles_and_responsibilities, current_projects, "
        "team_and_relationships, tools_and_systems, communication_style, "
        "goals_and_priorities, preferences_and_constraints, domain_knowledge, decision_log."
    ),
)
async def get_lore_category(category: str, ctx: Optional[Context] = None) -> str:
    lc = get_current_context()
    category = category.strip().lower()
    if category not in VALID_CATEGORIES:
        available = ", ".join(sorted(VALID_CATEGORIES.keys()))
        raise RuntimeError(f"Unknown category '{category}'. Valid categories: {available}")
    content = _read_category(lc.user_id, category, lc.target_url, lc.target_key)
    if content is None or not content.strip():
        return f"Category '{category}' exists but has no content yet."
    return content


@server.tool(
    name="update_lore_category",
    title="Update Lore Category",
    description=(
        "Writes updated content to a specific Lore category. "
        "Triggers regeneration of the summary. Use when new context surfaces."
    ),
)
async def update_lore_category(category: str, content: str, ctx: Optional[Context] = None) -> str:
    lc = get_current_context()
    category = category.strip().lower()
    if category not in VALID_CATEGORIES:
        available = ", ".join(sorted(VALID_CATEGORIES.keys()))
        raise RuntimeError(f"Unknown category '{category}'. Valid categories: {available}")
    if content is None:
        raise RuntimeError("Content cannot be None.")
    ok = _write_category(lc.user_id, category, content.strip() if content else "", lc.target_url, lc.target_key)
    if not ok:
        raise RuntimeError(f"Failed to write category '{category}'")
    _regenerate_summary(lc.user_id, lc.target_url, lc.target_key)
    if ctx is not None:
        await ctx.info(f"Updated Lore '{category}' for user {lc.user_id[:8]}.")
    return f"Successfully updated '{category}'. Summary regenerated."


@server.tool(
    name="list_lore_categories",
    title="List Lore Categories",
    description="Returns all Lore categories with their descriptions and population status.",
)
async def list_lore_categories(ctx: Optional[Context] = None) -> str:
    lc = get_current_context()
    status = _list_user_categories(lc.user_id, lc.target_url, lc.target_key)
    lines = []
    for category, description in VALID_CATEGORIES.items():
        has_content = status.get(category, {}).get("has_content", False)
        state = "has content" if has_content else "empty"
        lines.append(f"- **{category}** ({state}): {description}")
    return "\n".join(lines)


@server.tool(
    name="get_birth_chart",
    title="Get Birth Chart",
    description=(
        "Returns the user's western tropical natal chart (Sun sign, planetary "
        "positions, houses, aspects) as stored from astrology-api.io. Use when "
        "the user asks about their birth chart or astrological placements."
    ),
)
async def get_birth_chart(ctx: Optional[Context] = None) -> str:
    lc = get_current_context()
    if not _node_enabled(lc.user_id, "birth_chart", lc.target_url, lc.target_key):
        return "The user has disabled birth chart access for MCP tools. Do not reference astrological placements for this user."
    row = _read_astrology(lc.user_id, lc.target_url, lc.target_key)
    if not row:
        return "No birth chart on file. The user can add one from the Lore page → Add Lore → Birth Chart."
    lines = ["# Birth Chart"]
    if row.get("full_name"):
        lines.append(f"Name: {row['full_name']}")
    if row.get("birth_date"):
        when = str(row["birth_date"])
        if row.get("birth_time"):
            when += f" {row['birth_time']}"
        lines.append(f"Born: {when}")
    if row.get("birth_place"):
        lines.append(f"Place: {row['birth_place']}")
    if row.get("sun_sign"):
        lines.append(f"Sun sign: {row['sun_sign']}")

    analysis = row.get("birth_chart_analysis")
    if analysis:
        lines.append("")
        lines.append("## Reading")
        lines.append(str(analysis).strip())

    chart = row.get("chart_json")
    if chart:
        import json as _json
        lines.append("")
        lines.append("## Raw chart data")
        lines.append("```json")
        lines.append(_json.dumps(chart, indent=2, default=str)[:6000])
        lines.append("```")
    return "\n".join(lines)


@server.tool(
    name="get_human_design",
    title="Get Human Design",
    description=(
        "Returns the user's Human Design bodygraph reading: a narrative analysis "
        "(Type, Strategy, Authority, Profile, defined/undefined Centers, Channels, "
        "decision-making guidance) followed by the structured bodygraph fields. "
        "Use when the user asks about their Human Design, type, strategy, or "
        "authority."
    ),
)
async def get_human_design(ctx: Optional[Context] = None) -> str:
    lc = get_current_context()
    if not _node_enabled(lc.user_id, "human_design", lc.target_url, lc.target_key):
        return "The user has disabled Human Design access for MCP tools. Do not reference Type/Strategy/Authority for this user."
    row = _read_astrology(lc.user_id, lc.target_url, lc.target_key)
    if not row:
        return "No Human Design chart on file. The user can add one from the Lore page → Add Lore → Human Design."

    parts: List[str] = []
    analysis = row.get("human_design_analysis")
    if analysis:
        parts.append("# Human Design Reading")
        parts.append(analysis.strip())
        parts.append("")
    parts.append("## Bodygraph fields")
    for label, key in [
        ("Type", "hd_type"),
        ("Strategy", "hd_strategy"),
        ("Authority", "hd_authority"),
        ("Profile", "hd_profile"),
    ]:
        val = row.get(key)
        if val:
            parts.append(f"- **{label}**: {val}")
    return "\n".join(parts)


@server.tool(
    name="get_mbti",
    title="Get Myers-Briggs Type",
    description=(
        "Returns the user's Myers-Briggs (MBTI) type (e.g. INTJ) and a short "
        "summary of what that type typically looks like. Use when the user "
        "asks about their personality type or how they think/decide."
    ),
)
async def get_mbti(ctx: Optional[Context] = None) -> str:
    lc = get_current_context()
    if not _node_enabled(lc.user_id, "mbti", lc.target_url, lc.target_key):
        return "The user has disabled MBTI access for MCP tools. Do not reference their Myers-Briggs type."
    row = _read_personality(lc.user_id, lc.target_url, lc.target_key)
    if not row or not row.get("mbti_type"):
        return "No MBTI type on file. The user can add one from the Lore page → Personality → Myers-Briggs."
    lines = [f"# Myers-Briggs: {row['mbti_type']}"]
    if row.get("mbti_source"):
        lines.append(f"Source: {row['mbti_source']}")
    if row.get("mbti_summary"):
        lines.append("")
        lines.append(row["mbti_summary"].strip())
    return "\n".join(lines)


@server.tool(
    name="get_big_five",
    title="Get Big Five (OCEAN) Profile",
    description=(
        "Returns the user's Big Five personality scores (Openness, "
        "Conscientiousness, Extraversion, Agreeableness, Neuroticism) on a "
        "0-100 scale plus a short narrative summary. Use when the user asks "
        "about their personality traits or behavioral tendencies."
    ),
)
async def get_big_five(ctx: Optional[Context] = None) -> str:
    lc = get_current_context()
    if not _node_enabled(lc.user_id, "big5", lc.target_url, lc.target_key):
        return "The user has disabled Big Five access for MCP tools. Do not reference their OCEAN traits."
    row = _read_personality(lc.user_id, lc.target_url, lc.target_key)
    if not row or row.get("big5_openness") is None:
        return "No Big Five scores on file. The user can add them from the Lore page → Personality → Big Five."
    lines = [
        "# Big Five (OCEAN)",
        f"- Openness: {row.get('big5_openness')}",
        f"- Conscientiousness: {row.get('big5_conscientiousness')}",
        f"- Extraversion: {row.get('big5_extraversion')}",
        f"- Agreeableness: {row.get('big5_agreeableness')}",
        f"- Neuroticism: {row.get('big5_neuroticism')}",
    ]
    if row.get("big5_source"):
        lines.append(f"Source: {row['big5_source']}")
    if row.get("big5_summary"):
        lines.append("")
        lines.append(row["big5_summary"].strip())
    return "\n".join(lines)


@server.tool(
    name="search_lore",
    title="Search Lore",
    description="Keyword search across all Lore files. Fallback for cross-categorical lookups.",
)
async def search_lore(query: str, ctx: Optional[Context] = None) -> str:
    lc = get_current_context()
    query = query.strip()
    if not query:
        raise RuntimeError("Search query cannot be empty.")
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    results = []
    for category in VALID_CATEGORIES:
        content = _read_category(lc.user_id, category, lc.target_url, lc.target_key)
        if not content:
            continue
        for line_num, line in enumerate(content.splitlines(), 1):
            if pattern.search(line):
                results.append(f"[{category}:{line_num}] {line.strip()}")
    if not results:
        return f"No results found for '{query}'."
    header = f"Found {len(results)} match(es) for '{query}':\n"
    return header + "\n".join(results)


PLUGIN_METADATA = {
    "schema_version": "v1",
    "name_for_human": "Lore — Personal Context",
    "name_for_model": "lore",
    "description_for_human": (
        "Your portable personal context layer. Stores identity, projects, "
        "preferences, and decisions — accessible from any MCP-compatible tool."
    ),
    "description_for_model": (
        "Use Lore tools to retrieve and update the user's personal context. "
        "Authentication is via Bearer token; user identity is resolved server-side."
    ),
    "auth": {"type": "bearer"},
    "api": {"type": "sse", "url": "/mcp/sse"},
    "contact_email": "support@sidekickforge.com",
    "legal_info_url": "https://sidekickforge.com/legal",
}


# ---------------------------------------------------------------------------
# SSE auth middleware — validates Bearer tokens on SSE connection and stores
# the resolved LoreContext in the ContextVar so all tool invocations during
# that session see it.
# ---------------------------------------------------------------------------

class MCPBearerAuthMiddleware(BaseHTTPMiddleware):
    """Enforces Bearer token auth on /mcp/* paths and stores the resolved
    LoreContext in the ContextVar. Applied to the Starlette app mounted at /mcp."""

    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("authorization") or ""
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                {"error": "unauthorized", "detail": "Missing Bearer token. Provide Authorization: Bearer slf_lore_<token>."},
                status_code=401,
            )
        raw = auth_header[7:].strip()
        resolved = resolve_token(raw)
        if resolved is None:
            return JSONResponse(
                {"error": "unauthorized", "detail": "Invalid or revoked API key."},
                status_code=401,
            )
        user_id, api_key_id = resolved
        target_url, target_key = resolve_user_home_target(user_id)
        lc = LoreContext(
            user_id=user_id,
            target_url=target_url,
            target_key=target_key,
            source="bearer",
            api_key_id=api_key_id,
        )
        token = _current_context.set(lc)
        try:
            LOGGER.info(f"MCP auth: user={user_id[:8]} target={'dedicated' if target_url else 'platform'}")
            response = await call_next(request)
            return response
        finally:
            _current_context.reset(token)


# ---------------------------------------------------------------------------
# FastAPI app — admin REST endpoints + mounted MCP SSE subapp
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    _configure_logging()
    app = FastAPI(title="Lore MCP", version="0.4.0")

    # OAuth 2.1 shim — discovery, dynamic client registration, token exchange.
    # The /authorize + consent flow lives on the main Sidekick Forge app where
    # the login session cookie exists.
    app.include_router(oauth_router)

    @app.get("/.well-known/ai-plugin.json", response_model=dict)
    async def well_known_manifest() -> JSONResponse:
        return JSONResponse(PLUGIN_METADATA)

    @app.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {
            "service": "lore-mcp",
            "version": "0.4.0",
            "status": "ready",
            "storage": "supabase",
            "auth": "bearer-token",
            "default_supabase_configured": bool(DEFAULT_SUPABASE_URL and DEFAULT_SUPABASE_KEY),
            "connection_cache_size": len(_connection_cache),
        }

    # -----------------------------------------------------------------------
    # Admin REST API — every endpoint requires a LoreContext (bearer or internal)
    # -----------------------------------------------------------------------

    @app.get("/admin-api/categories")
    async def admin_list_categories(lc: LoreContext = Depends(require_lore_context)):
        status = _list_user_categories(lc.user_id, lc.target_url, lc.target_key)
        return [
            {
                "key": cat,
                "description": desc,
                "has_content": status.get(cat, {}).get("has_content", False),
                "updated_at": status.get(cat, {}).get("updated_at"),
            }
            for cat, desc in VALID_CATEGORIES.items()
        ]

    @app.get("/admin-api/category/{category}")
    async def admin_get_category(category: str, lc: LoreContext = Depends(require_lore_context)):
        category = category.strip().lower()
        if category not in VALID_CATEGORIES:
            return JSONResponse(status_code=400, content={"error": f"Unknown category: {category}"})
        content = _read_category(lc.user_id, category, lc.target_url, lc.target_key) or ""
        return {"category": category, "content": content}

    @app.put("/admin-api/category/{category}")
    async def admin_update_category(
        category: str,
        request: Request,
        lc: LoreContext = Depends(require_lore_context),
    ):
        category = category.strip().lower()
        if category not in VALID_CATEGORIES:
            return JSONResponse(status_code=400, content={"error": f"Unknown category: {category}"})
        body = await request.json()
        content = body.get("content", "")
        ok = _write_category(lc.user_id, category, content.strip() if content else "", lc.target_url, lc.target_key)
        if not ok:
            return JSONResponse(status_code=500, content={"error": "Failed to write category"})
        _regenerate_summary(lc.user_id, lc.target_url, lc.target_key)
        return {"status": "ok", "category": category}

    @app.get("/admin-api/depth-score")
    async def admin_depth_score(lc: LoreContext = Depends(require_lore_context)):
        """Return the Lore depth breakdown across all 14 graded nodes.

        Cached LLM-rubric grades are used where available; nodes without a
        cached grade fall back to a cheap heuristic (categories) or 0
        (unfilled astrology/personality). Each layer also reports whether
        its cached grade is stale (content changed since grading).
        """
        cached = _read_depth_scores(lc.user_id, lc.target_url, lc.target_key)
        layers: List[Dict[str, Any]] = []
        total_score = 0
        active_count = 0
        stale_nodes: List[str] = []

        for node_key in ALL_DEPTH_NODES:
            content_preview, content_hash = _node_content_for_grading(
                lc.user_id, node_key, lc.target_url, lc.target_key
            )
            has_content = bool(content_preview)
            cached_entry = cached.get(node_key)

            if cached_entry and cached_entry.get("content_hash") == content_hash and has_content:
                score = int(cached_entry.get("score") or 0)
                level = cached_entry.get("level") or DEPTH_LEVEL_FROM_SCORE[score][0]
                detail = cached_entry.get("detail") or ""
                graded = True
            elif not has_content:
                score = 0
                level = DEPTH_LEVEL_FROM_SCORE[0][0]
                detail = "Not captured yet"
                graded = False
            else:
                # Content exists but grade is missing or stale. Use a cheap
                # fallback for categories (so the bar isn't zero) and
                # flag the node for regrading.
                if node_key in VALID_CATEGORIES:
                    score_data = _score_category_content(node_key, content_preview)
                    score = int(score_data["score"])
                    level = score_data["level"]
                    detail = score_data["detail"] + " (heuristic, awaiting LLM grade)"
                else:
                    # For astrology/personality nodes assume 'growing' as a
                    # placeholder so their bars don't look empty while waiting.
                    score = 2
                    level = "growing"
                    detail = "Pending LLM grade"
                graded = False
                stale_nodes.append(node_key)

            layers.append({
                "key": node_key,
                "score": score,
                "level": level,
                "detail": detail,
                "has_content": has_content,
                "graded": graded,
            })
            total_score += score
            if score > 0:
                active_count += 1

        max_score = len(ALL_DEPTH_NODES) * 3

        # Derive a qualitative band from the overall percentage
        pct = (total_score / max_score) if max_score else 0
        if pct >= 0.75:
            band, band_label = "deep", "Deep"
        elif pct >= 0.50:
            band, band_label = "strong", "Strong"
        elif pct >= 0.25:
            band, band_label = "growing", "Growing"
        elif total_score > 0:
            band, band_label = "emerging", "Emerging"
        else:
            band, band_label = "not_captured", "Not captured"

        return {
            "layers": layers,
            "active_count": active_count,
            "total_nodes": len(ALL_DEPTH_NODES),
            "total_score": total_score,
            "max_score": max_score,
            "percentage": round(pct * 100),
            "band": band,
            "band_label": band_label,
            "stale_nodes": stale_nodes,
            # Legacy fields kept for any callers that may still expect them
            "total_categories": len(VALID_CATEGORIES),
        }

    @app.get("/admin-api/depth-score/content/{node_key}")
    async def admin_depth_score_content(
        node_key: str,
        lc: LoreContext = Depends(require_lore_context),
    ):
        """Return the raw content the LLM should grade for one node,
        plus its current content_hash. Used by the FastAPI grader worker
        so it can submit fresh grades back via PUT /depth-score."""
        if node_key not in ALL_DEPTH_NODES:
            return JSONResponse(status_code=400, content={"error": f"Unknown node: {node_key}"})
        content, content_hash = _node_content_for_grading(
            lc.user_id, node_key, lc.target_url, lc.target_key
        )
        return {
            "node_key": node_key,
            "content": content,
            "content_hash": content_hash,
        }

    @app.put("/admin-api/depth-score")
    async def admin_depth_score_put(
        request: Request,
        lc: LoreContext = Depends(require_lore_context),
    ):
        """Write an LLM-graded score for one node.

        Body: `{node_key, score, level, detail, content_hash}`.
        """
        body = await request.json()
        node_key = body.get("node_key")
        if node_key not in ALL_DEPTH_NODES:
            return JSONResponse(status_code=400, content={"error": f"Unknown node: {node_key}"})
        try:
            score = int(body.get("score", 0))
        except Exception:
            return JSONResponse(status_code=400, content={"error": "score must be an int"})
        score = max(0, min(3, score))
        level = body.get("level") or DEPTH_LEVEL_FROM_SCORE[score][0]
        detail = body.get("detail") or ""
        content_hash = body.get("content_hash")
        ok = _write_depth_score(
            lc.user_id, node_key, score, level, detail, content_hash,
            lc.target_url, lc.target_key,
        )
        if not ok:
            return JSONResponse(status_code=500, content={"error": "Failed to write depth score"})
        return {"status": "ok"}

    @app.get("/admin-api/summary")
    async def admin_get_summary(lc: LoreContext = Depends(require_lore_context)):
        content = _read_summary(lc.user_id, lc.target_url, lc.target_key) or ""
        return {"user_id": lc.user_id, "content": content}

    # -----------------------------------------------------------------------
    # Astrology — birth chart + Human Design row
    # -----------------------------------------------------------------------

    @app.get("/admin-api/astrology")
    async def admin_get_astrology(lc: LoreContext = Depends(require_lore_context)):
        row = _read_astrology(lc.user_id, lc.target_url, lc.target_key)
        if not row:
            return {"connected": False}
        return {
            "connected": True,
            "full_name": row.get("full_name"),
            "birth_date": str(row.get("birth_date")) if row.get("birth_date") else None,
            "birth_time": str(row.get("birth_time")) if row.get("birth_time") else None,
            "birth_place": row.get("birth_place"),
            "sun_sign": row.get("sun_sign"),
            "hd_type": row.get("hd_type"),
            "hd_strategy": row.get("hd_strategy"),
            "hd_authority": row.get("hd_authority"),
            "hd_profile": row.get("hd_profile"),
            "updated_at": row.get("updated_at"),
        }

    @app.get("/admin-api/astrology/full")
    async def admin_get_astrology_full(lc: LoreContext = Depends(require_lore_context)):
        row = _read_astrology(lc.user_id, lc.target_url, lc.target_key)
        if not row:
            return JSONResponse(status_code=404, content={"error": "No astrology data on file"})
        return {
            "full_name": row.get("full_name"),
            "birth_date": str(row.get("birth_date")) if row.get("birth_date") else None,
            "birth_time": str(row.get("birth_time")) if row.get("birth_time") else None,
            "birth_place": row.get("birth_place"),
            "sun_sign": row.get("sun_sign"),
            "hd_type": row.get("hd_type"),
            "hd_strategy": row.get("hd_strategy"),
            "hd_authority": row.get("hd_authority"),
            "hd_profile": row.get("hd_profile"),
            "chart_json": row.get("chart_json"),
            "human_design_json": row.get("human_design_json"),
            "birth_chart_analysis": row.get("birth_chart_analysis"),
            "human_design_analysis": row.get("human_design_analysis"),
            "analysis_model": row.get("analysis_model"),
            "updated_at": row.get("updated_at"),
        }

    @app.put("/admin-api/astrology")
    async def admin_put_astrology(
        request: Request,
        lc: LoreContext = Depends(require_lore_context),
    ):
        body = await request.json()
        allowed_keys = {
            "full_name",
            "birth_date",
            "birth_time",
            "birth_place",
            "city",
            "country_code",
            "sun_sign",
            "hd_type",
            "hd_strategy",
            "hd_authority",
            "hd_profile",
            "chart_json",
            "human_design_json",
            "birth_chart_analysis",
            "human_design_analysis",
            "analysis_model",
        }
        payload = {k: v for k, v in body.items() if k in allowed_keys}
        if not payload.get("birth_date") or not payload.get("birth_time") or not payload.get("birth_place"):
            return JSONResponse(
                status_code=400,
                content={"error": "birth_date, birth_time, and birth_place are required"},
            )
        ok = _write_astrology(lc.user_id, payload, lc.target_url, lc.target_key)
        if not ok:
            return JSONResponse(status_code=500, content={"error": "Failed to write astrology row"})
        _regenerate_summary(lc.user_id, lc.target_url, lc.target_key)
        return {"status": "ok"}

    # -----------------------------------------------------------------------
    # MCP Visibility toggles
    # -----------------------------------------------------------------------

    @app.get("/admin-api/mcp-visibility")
    async def admin_get_mcp_visibility(lc: LoreContext = Depends(require_lore_context)):
        return _read_visibility(lc.user_id, lc.target_url, lc.target_key)

    @app.put("/admin-api/mcp-visibility")
    async def admin_put_mcp_visibility(
        request: Request,
        lc: LoreContext = Depends(require_lore_context),
    ):
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "Body must be an object"})
        ok = _write_visibility(lc.user_id, body, lc.target_url, lc.target_key)
        if not ok:
            return JSONResponse(status_code=500, content={"error": "Failed to write visibility"})
        _regenerate_summary(lc.user_id, lc.target_url, lc.target_key)
        return _read_visibility(lc.user_id, lc.target_url, lc.target_key)

    # -----------------------------------------------------------------------
    # Personality — MBTI + Big Five
    # -----------------------------------------------------------------------

    @app.get("/admin-api/personality")
    async def admin_get_personality(lc: LoreContext = Depends(require_lore_context)):
        row = _read_personality(lc.user_id, lc.target_url, lc.target_key)
        if not row:
            return {"mbti": None, "big5": None}
        mbti = None
        if row.get("mbti_type"):
            mbti = {
                "type": row.get("mbti_type"),
                "summary": row.get("mbti_summary"),
                "source": row.get("mbti_source"),
                "updated_at": row.get("mbti_updated_at"),
            }
        big5 = None
        if row.get("big5_openness") is not None:
            big5 = {
                "openness": row.get("big5_openness"),
                "conscientiousness": row.get("big5_conscientiousness"),
                "extraversion": row.get("big5_extraversion"),
                "agreeableness": row.get("big5_agreeableness"),
                "neuroticism": row.get("big5_neuroticism"),
                "summary": row.get("big5_summary"),
                "source": row.get("big5_source"),
                "updated_at": row.get("big5_updated_at"),
            }
        return {
            "mbti": mbti,
            "big5": big5,
            "analysis_model": row.get("analysis_model"),
        }

    @app.put("/admin-api/personality")
    async def admin_put_personality(
        request: Request,
        lc: LoreContext = Depends(require_lore_context),
    ):
        """Partial upsert. Pass `mbti` and/or `big5` sub-dicts — unspecified
        halves are left alone on the existing row."""
        body = await request.json()
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {}

        mbti = body.get("mbti")
        if isinstance(mbti, dict):
            type_val = (mbti.get("type") or "").strip().upper() or None
            payload["mbti_type"] = type_val
            payload["mbti_summary"] = (mbti.get("summary") or "").strip() or None
            payload["mbti_source"] = mbti.get("source") if mbti.get("source") in ("manual", "ai_analysis") else "manual"
            payload["mbti_updated_at"] = now_iso

        big5 = body.get("big5")
        if isinstance(big5, dict):
            for k in ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"):
                v = big5.get(k)
                if v is None:
                    continue
                try:
                    n = int(round(float(v)))
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"error": f"big5.{k} must be numeric"})
                if not (0 <= n <= 100):
                    return JSONResponse(status_code=400, content={"error": f"big5.{k} must be 0–100"})
                payload[f"big5_{k}"] = n
            payload["big5_summary"] = (big5.get("summary") or "").strip() or None
            payload["big5_source"] = big5.get("source") if big5.get("source") in ("manual", "ai_analysis") else "manual"
            payload["big5_updated_at"] = now_iso

        analysis_model = body.get("analysis_model")
        if analysis_model:
            payload["analysis_model"] = str(analysis_model)

        if not payload:
            return JSONResponse(status_code=400, content={"error": "No personality fields provided"})

        ok = _write_personality(lc.user_id, payload, lc.target_url, lc.target_key)
        if not ok:
            return JSONResponse(status_code=500, content={"error": "Failed to write personality row"})
        _regenerate_summary(lc.user_id, lc.target_url, lc.target_key)
        return {"status": "ok"}

    @app.post("/admin-api/external-endpoint/invalidate")
    async def admin_invalidate_external_endpoint_cache(lc: LoreContext = Depends(require_lore_context)):
        """Called by the main app after a user updates/disables their self-host
        endpoint so the 60s in-process cache drops the stale row immediately."""
        from external_endpoints import invalidate_cache
        invalidate_cache(lc.user_id)
        return {"status": "ok"}

    # -----------------------------------------------------------------------
    # Export — markdown, JSON, self-host ZIP. See exports.py for the zip
    # builders; this handler just resolves the user's categories + summary
    # from the authenticated context and hands them to the builder.
    # -----------------------------------------------------------------------
    @app.get("/admin-api/export/{fmt}")
    async def admin_export(fmt: str, lc: LoreContext = Depends(require_lore_context)):
        from fastapi.responses import Response
        from exports import (
            build_markdown_zip,
            build_json_export,
            build_selfhost_zip,
        )

        # Load full user state from storage — shared across formats
        categories: Dict[str, str] = {}
        for cat in VALID_CATEGORIES:
            content = _read_category(lc.user_id, cat, lc.target_url, lc.target_key) or ""
            categories[cat] = content
        summary = _read_summary(lc.user_id, lc.target_url, lc.target_key) or ""

        payload = {
            "user_id": lc.user_id,
            "summary": summary,
            "categories": categories,
            "category_descriptions": VALID_CATEGORIES,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "schema_version": "lore/1",
        }

        if fmt == "markdown":
            blob, filename = build_markdown_zip(payload)
            media = "application/zip"
        elif fmt == "json":
            blob, filename = build_json_export(payload)
            media = "application/json"
        elif fmt == "self-host":
            blob, filename = build_selfhost_zip(payload)
            media = "application/zip"
        else:
            raise HTTPException(status_code=400, detail=f"Unknown export format '{fmt}'")

        return Response(
            content=blob,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # -----------------------------------------------------------------------
    # Mount the MCP SSE subapp with Bearer auth middleware wrapping it
    # -----------------------------------------------------------------------
    sse_app = server.sse_app()
    sse_app.add_middleware(MCPBearerAuthMiddleware)
    app.mount("/mcp", sse_app)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8082)
