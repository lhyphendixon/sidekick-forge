"""
Lore Depth Grader — LLM-driven quality rubric for Lore nodes.

Each "node" (one of the 10 lore categories, birth_chart, human_design, mbti,
big5) is graded 0-3 against a consistent rubric. Scores are cached in
`lore_depth_scores` on the user's home Supabase via the Lore MCP
admin-api layer, so grading only runs when content actually changes.

The grader is intentionally lenient on volume and strict on specificity —
300 characters of filler should score lower than 80 characters of dense,
concrete personal context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


LEVEL_FROM_SCORE = {
    0: "not_captured",
    1: "emerging",
    2: "growing",
    3: "strong",
}


DEPTH_RUBRIC_SYSTEM_PROMPT = """You grade one node of a user's personal-context profile (Lore) on a strict 0-3 rubric. Return JSON only.

Rubric:
- 0 (not_captured): Empty, placeholder, or purely template/filler with no real information about the person.
- 1 (emerging): Some content, but generic, vague, or thin. Could describe almost anyone. A stranger couldn't act on this.
- 2 (growing): Specific, authentic details that distinguish this person — but narrow in scope or missing important dimensions. A stranger could make some decisions confidently.
- 3 (strong): Dense, specific, multi-dimensional, and actionable. Reveals how this person actually thinks/works/lives. A stranger could operate on their behalf with confidence.

Hard rules:
- Volume is NOT quality. 80 concrete words beats 500 words of filler.
- Penalize templates, bullet headers with no content, vague corporate-speak, and "placeholder" patterns like "- **Key:**".
- Reward concrete names, numbers, specific tools/processes, and explicit preferences.
- Return JSON exactly: {"score": <int 0-3>, "detail": "<one short sentence explaining the grade>"}
- No markdown, no prose outside the JSON.
"""


NODE_DESCRIPTIONS = {
    "identity": "Identity — name, role, org, philosophy, personal context.",
    "roles_and_responsibilities": "Roles & responsibilities — day-to-day work, outputs, decisions, who this person serves.",
    "current_projects": "Current projects — active workstreams, status, priority, KPIs, definition of done.",
    "team_and_relationships": "Team & relationships — key people, roles, what each relationship requires.",
    "tools_and_systems": "Tools & systems — stack, architecture patterns, constraints, design systems.",
    "communication_style": "Communication style — tone, formatting, editing preferences, voice-matching notes.",
    "goals_and_priorities": "Goals & priorities — week/quarter/year/career optimization targets.",
    "preferences_and_constraints": "Preferences & constraints — always/never rules, tool preferences, hard constraints.",
    "domain_knowledge": "Domain knowledge — expertise areas, frameworks used, what NOT to explain.",
    "decision_log": "Decision log — past decisions and the reasoning behind them.",
    "birth_chart": "Birth chart — western tropical natal chart summary and analysis.",
    "human_design": "Human Design — bodygraph type, strategy, authority, profile, and reading.",
    "mbti": "Myers-Briggs — 4-letter type and a short supporting summary.",
    "big5": "Big Five (OCEAN) — 0-100 scores plus narrative summary.",
}


async def grade_node(
    node_key: str,
    content: str,
    *,
    llm_provider: Any,
) -> Tuple[int, str]:
    """Run the LLM rubric on a single node. Returns (score, detail).

    Raises on empty response — we never fall back to a heuristic silently.
    The caller decides whether to suppress errors (e.g. background tasks
    just log and continue).
    """
    if llm_provider is None:
        raise RuntimeError("No LLM provider available for depth grading")
    if not content or not content.strip():
        return 0, "No content to grade"

    node_desc = NODE_DESCRIPTIONS.get(node_key, node_key)
    user_content = (
        DEPTH_RUBRIC_SYSTEM_PROMPT
        + f"\n\n## Node being graded\n{node_desc}"
        + f"\n\n## Content\n{content[:6000]}"
        + "\n\nReturn JSON only."
    )

    for attempt in range(2):
        try:
            response = await llm_provider.chat(
                messages=[{"role": "user", "content": user_content}],
                max_tokens=200,
            )
            text = (response or "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            parsed = json.loads(text)
            score = int(parsed.get("score", 0))
            score = max(0, min(3, score))
            detail = (parsed.get("detail") or "").strip() or f"Graded {LEVEL_FROM_SCORE[score]}"
            return score, detail
        except json.JSONDecodeError:
            if attempt == 0:
                continue
            logger.warning(f"grade_node({node_key}): unparseable LLM output")
            raise RuntimeError(f"LLM returned unparseable grade for {node_key}")
        except Exception as exc:
            if attempt == 0:
                continue
            raise RuntimeError(f"LLM grade failed for {node_key}: {exc}") from exc

    raise RuntimeError(f"LLM grade failed for {node_key} after retries")


async def fetch_node_content(
    mcp_base: str,
    internal_headers: Dict[str, str],
    target_params: Dict[str, str],
    node_key: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Ask the Lore MCP for the current raw content + hash for one node."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{mcp_base}/admin-api/depth-score/content/{node_key}",
            params=target_params,
            headers=internal_headers,
        )
        if resp.status_code != 200:
            return None, None
        data = resp.json()
        return data.get("content"), data.get("content_hash")


async def write_node_grade(
    mcp_base: str,
    internal_headers: Dict[str, str],
    target_params: Dict[str, str],
    node_key: str,
    score: int,
    detail: str,
    content_hash: Optional[str],
) -> bool:
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.put(
            f"{mcp_base}/admin-api/depth-score",
            params=target_params,
            headers=internal_headers,
            json={
                "node_key": node_key,
                "score": score,
                "level": LEVEL_FROM_SCORE.get(score, "not_captured"),
                "detail": detail,
                "content_hash": content_hash,
            },
        )
        return resp.status_code == 200


async def grade_and_cache_node(
    mcp_base: str,
    internal_headers: Dict[str, str],
    target_params: Dict[str, str],
    node_key: str,
    *,
    llm_provider: Any,
) -> Optional[Tuple[int, str]]:
    """Fetch → grade → write, all via the Lore MCP admin-api.

    Returns (score, detail) on success, None if there was nothing to grade
    (empty content). Re-raises any grading error so the caller can decide.
    """
    content, content_hash = await fetch_node_content(
        mcp_base, internal_headers, target_params, node_key
    )
    if not content or not content.strip():
        # Nothing to grade — clear any stale grade by writing score=0
        await write_node_grade(
            mcp_base, internal_headers, target_params,
            node_key, 0, "Not captured yet", "",
        )
        return None

    score, detail = await grade_node(node_key, content, llm_provider=llm_provider)
    await write_node_grade(
        mcp_base, internal_headers, target_params,
        node_key, score, detail, content_hash,
    )
    return score, detail


async def grade_nodes_parallel(
    mcp_base: str,
    internal_headers: Dict[str, str],
    target_params: Dict[str, str],
    node_keys: List[str],
    *,
    llm_provider: Any,
    concurrency: int = 4,
) -> Dict[str, Any]:
    """Grade several nodes concurrently (bounded). Never raises — failures
    are logged and reported per-node in the returned dict."""
    results: Dict[str, Any] = {}
    semaphore = asyncio.Semaphore(concurrency)

    async def one(node_key: str):
        async with semaphore:
            try:
                result = await grade_and_cache_node(
                    mcp_base, internal_headers, target_params,
                    node_key, llm_provider=llm_provider,
                )
                results[node_key] = (
                    {"score": result[0], "detail": result[1]} if result else {"score": 0, "detail": "empty"}
                )
            except Exception as exc:
                logger.warning(f"grade_nodes_parallel: {node_key} failed: {exc}")
                results[node_key] = {"error": str(exc)}

    await asyncio.gather(*[one(n) for n in node_keys])
    return results
