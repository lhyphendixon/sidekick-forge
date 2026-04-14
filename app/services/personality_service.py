"""
Lore personality analysis — Myers-Briggs + Big Five from existing lore.

Two analysis entry points, both invoked from the admin routes:

    analyze_mbti_from_lore(categories, llm_provider)   -> {type, summary, model}
    analyze_big5_from_lore(categories, llm_provider)   -> {openness, conscientiousness,
                                                            extraversion, agreeableness,
                                                            neuroticism, summary, model}

They're kept separate so the user can refresh one without re-running the other
(e.g. after editing their `identity` category). Both prompts are JSON-mode
style — the LLM is asked for a strict JSON object, which is parsed and
validated server-side. Invalid output raises PersonalityAnalysisError so the
caller returns a clean 4xx to the UI rather than persisting garbage.

No fallbacks: if the configured LLM provider fails, we surface the error.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

MBTI_LETTERS = [("E", "I"), ("S", "N"), ("T", "F"), ("J", "P")]
BIG5_KEYS = ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism")
MAX_CATEGORY_CHARS = 3000  # hard cap per category to keep the prompt bounded


class PersonalityAnalysisError(Exception):
    pass


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def _compact_lore(categories: Dict[str, str]) -> str:
    """Flatten the user's lore categories into a single prompt-friendly block.
    Empty categories are omitted so the LLM doesn't waste attention on them.
    Each category is capped at MAX_CATEGORY_CHARS to keep token usage bounded."""
    parts: List[str] = []
    for cat, content in categories.items():
        text = (content or "").strip()
        if not text:
            continue
        if len(text) > MAX_CATEGORY_CHARS:
            text = text[:MAX_CATEGORY_CHARS] + "\n… [truncated]"
        parts.append(f"## {cat}\n{text}")
    if not parts:
        raise PersonalityAnalysisError(
            "No lore content to analyze. Fill in at least one category "
            "(identity, communication_style, goals_and_priorities, etc.) first."
        )
    return "\n\n".join(parts)


MBTI_SYSTEM = """You are a personality analyst. Based ONLY on the lore provided, infer
the user's most likely Myers-Briggs Type Indicator (MBTI) code and write a
short summary (3–5 sentences, ~70–120 words) explaining the inference in
terms of concrete signals from the lore.

Output strict JSON — nothing else, no markdown fences, no commentary:

{
  "type": "XXXX",
  "summary": "..."
}

where type is exactly 4 uppercase letters, one from each pair:
(E or I)(S or N)(T or F)(J or P).

If the lore is too thin to be confident, still pick the most-supported type
but say so clearly in the summary."""


BIG5_SYSTEM = """You are a personality analyst. Based ONLY on the lore provided, infer the
user's Big Five (OCEAN) personality trait scores on a 0–100 percentile-ish
scale, plus a short summary (3–5 sentences, ~80–130 words) tying the scores
to concrete signals from the lore.

Output strict JSON — nothing else, no markdown fences, no commentary:

{
  "openness": 0-100,
  "conscientiousness": 0-100,
  "extraversion": 0-100,
  "agreeableness": 0-100,
  "neuroticism": 0-100,
  "summary": "..."
}

Integers only. If the lore is too thin to be confident, still produce your
best estimate and say so clearly in the summary."""


# ---------------------------------------------------------------------------
# LLM JSON parsing
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_llm_json(text: str) -> Dict[str, Any]:
    """Salvage a JSON object from an LLM response that may include fences or
    preamble. Raises PersonalityAnalysisError on unrecoverable output."""
    if not text:
        raise PersonalityAnalysisError("LLM returned empty response")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```\w*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(stripped)
        if not match:
            raise PersonalityAnalysisError(f"LLM output was not valid JSON: {stripped[:200]}")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise PersonalityAnalysisError(f"LLM JSON parse failed: {exc}")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def normalize_mbti_type(raw: str) -> str:
    """Validate and normalize an MBTI 4-letter code. Raises on invalid input."""
    if not isinstance(raw, str):
        raise PersonalityAnalysisError(f"MBTI type must be a string, got {type(raw).__name__}")
    code = raw.strip().upper()
    if len(code) != 4:
        raise PersonalityAnalysisError(f"MBTI type must be exactly 4 letters, got '{code}'")
    for i, (a, b) in enumerate(MBTI_LETTERS):
        if code[i] not in (a, b):
            raise PersonalityAnalysisError(
                f"MBTI letter {i+1} must be {a} or {b}, got '{code[i]}' in '{code}'"
            )
    return code


def validate_big5_score(value: Any, name: str) -> int:
    """Coerce a Big5 value to a 0–100 integer. Raises on bad input."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        raise PersonalityAnalysisError(f"Big5 '{name}' must be numeric, got {value!r}")
    if not (0 <= n <= 100):
        raise PersonalityAnalysisError(f"Big5 '{name}' must be 0–100, got {n}")
    return n


# ---------------------------------------------------------------------------
# Public analyze entry points
# ---------------------------------------------------------------------------

async def analyze_mbti_from_lore(
    categories: Dict[str, str],
    *,
    llm_provider: Any,
) -> Dict[str, Any]:
    """Infer MBTI type + summary from existing lore. Returns a dict ready to
    upsert into lore_personality (mbti_type, mbti_summary, analysis_model)."""
    if llm_provider is None:
        raise PersonalityAnalysisError("No LLM provider available.")

    lore_block = _compact_lore(categories)
    messages = [
        {"role": "system", "content": MBTI_SYSTEM},
        {"role": "user", "content": f"Lore:\n\n{lore_block}"},
    ]
    try:
        response = await llm_provider.chat(messages=messages, max_tokens=600)
    except Exception as exc:
        raise PersonalityAnalysisError(f"LLM call failed: {exc}")

    parsed = _parse_llm_json(response)
    mbti_type = normalize_mbti_type(parsed.get("type") or "")
    summary = (parsed.get("summary") or "").strip()
    if not summary:
        raise PersonalityAnalysisError("LLM did not return an MBTI summary")

    model_name = getattr(llm_provider, "model", None) or type(llm_provider).__name__
    return {
        "mbti_type": mbti_type,
        "mbti_summary": summary,
        "analysis_model": str(model_name),
    }


async def analyze_big5_from_lore(
    categories: Dict[str, str],
    *,
    llm_provider: Any,
) -> Dict[str, Any]:
    """Infer Big Five (OCEAN) scores + summary from existing lore."""
    if llm_provider is None:
        raise PersonalityAnalysisError("No LLM provider available.")

    lore_block = _compact_lore(categories)
    messages = [
        {"role": "system", "content": BIG5_SYSTEM},
        {"role": "user", "content": f"Lore:\n\n{lore_block}"},
    ]
    try:
        response = await llm_provider.chat(messages=messages, max_tokens=700)
    except Exception as exc:
        raise PersonalityAnalysisError(f"LLM call failed: {exc}")

    parsed = _parse_llm_json(response)
    scores: Dict[str, int] = {}
    for key in BIG5_KEYS:
        scores[f"big5_{key}"] = validate_big5_score(parsed.get(key), key)
    summary = (parsed.get("summary") or "").strip()
    if not summary:
        raise PersonalityAnalysisError("LLM did not return a Big5 summary")

    model_name = getattr(llm_provider, "model", None) or type(llm_provider).__name__
    return {
        **scores,
        "big5_summary": summary,
        "analysis_model": str(model_name),
    }
