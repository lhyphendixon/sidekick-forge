"""
Astrology Service — birth chart + Human Design via astrology-api.io.

Single vendor (astrology-api.io) provides both the natal chart
(`POST /api/v3/charts/natal`) and the Human Design bodygraph
(`POST /api/v3/human-design/bodygraph`). Both endpoints accept the same
`subject.birth_data` shape so one form feeds both calls.

The raw upstream JSON is persisted via the Lore MCP; this module only
handles the outbound HTTP calls and the LLM narrative analysis of the HD
chart. Storage routing to each user's home-client Supabase is handled by
the Lore MCP admin-api layer.
"""

import json
import logging
import os
import re
from datetime import date, time as dt_time
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

ASTROLOGY_API_BASE = "https://api.astrology-api.io"
NATAL_CHART_PATH = "/api/v3/charts/natal"
HUMAN_DESIGN_PATH = "/api/v3/human-design/bodygraph"


class AstrologyAPIError(RuntimeError):
    """Raised when astrology-api.io returns a non-2xx response."""


def _get_api_key() -> str:
    key = os.getenv("ASTROLOGY_API_IO_KEY", "").strip()
    if not key:
        raise AstrologyAPIError(
            "ASTROLOGY_API_IO_KEY is not configured. Add it to .env and restart fastapi."
        )
    return key


# ---------------------------------------------------------------------------
# Birth data parsing
# ---------------------------------------------------------------------------

def build_subject(
    full_name: Optional[str],
    birth_date: date,
    birth_time: dt_time,
    birth_place: str,
    city: Optional[str] = None,
    country_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the `subject` payload shared by the natal + HD endpoints.

    If explicit `city` and `country_code` are provided (e.g. from the
    Nominatim-backed city autosuggest), they're used as-is. Otherwise
    `birth_place` is split on commas as a best-effort fallback.
    """
    if not city:
        parts = [p.strip() for p in (birth_place or "").split(",") if p.strip()]
        city = parts[0] if parts else (birth_place or "").strip()
        if not country_code and len(parts) >= 2:
            tail = parts[-1]
            if len(tail) == 2 and tail.isalpha():
                country_code = tail.upper()

    birth_data: Dict[str, Any] = {
        "year": birth_date.year,
        "month": birth_date.month,
        "day": birth_date.day,
        "hour": birth_time.hour,
        "minute": birth_time.minute,
        "second": 0,
        "city": city,
    }
    if country_code:
        birth_data["country_code"] = country_code.upper()

    return {
        "name": (full_name or "Anonymous").strip() or "Anonymous",
        "birth_data": birth_data,
    }


# ---------------------------------------------------------------------------
# City autosuggest (Nominatim / OpenStreetMap)
# ---------------------------------------------------------------------------

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_UA = "SidekickForge-Lore/1.0 (astrology-birthplace-autosuggest)"


async def search_cities(query: str, limit: int = 8) -> list[Dict[str, Any]]:
    """Return up to `limit` city suggestions for `query`.

    Each suggestion is `{label, city, country_code}` — `country_code` is a
    2-letter ISO code matching what astrology-api.io expects.
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []
    params = {
        "q": q,
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": max(1, min(limit, 15)),
        "featuretype": "city",
        "accept-language": "en",
    }
    headers = {"User-Agent": _NOMINATIM_UA}
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(NOMINATIM_URL, params=params, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"Nominatim {resp.status_code}: {resp.text[:200]}")
                return []
            raw = resp.json()
    except Exception as exc:
        logger.warning(f"Nominatim search failed: {exc}")
        return []

    suggestions: list[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in raw or []:
        address = row.get("address") or {}
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("hamlet")
            or address.get("municipality")
            or row.get("name")
            or ""
        ).strip()
        country_code = (address.get("country_code") or "").strip().upper()
        if not city or not country_code:
            continue
        key = (city.lower(), country_code)
        if key in seen:
            continue
        seen.add(key)
        state = (address.get("state") or address.get("region") or "").strip()
        country = (address.get("country") or "").strip()
        label_bits = [city]
        if state and state != city:
            label_bits.append(state)
        if country:
            label_bits.append(country)
        suggestions.append({
            "label": ", ".join(label_bits),
            "city": city,
            "country_code": country_code,
        })
        if len(suggestions) >= limit:
            break
    return suggestions


# ---------------------------------------------------------------------------
# Upstream API calls
# ---------------------------------------------------------------------------

async def _post(client: httpx.AsyncClient, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    resp = await client.post(path, json=payload)
    if resp.status_code >= 400:
        body_preview = resp.text[:500]
        raise AstrologyAPIError(
            f"astrology-api.io {path} returned {resp.status_code}: {body_preview}"
        )
    try:
        return resp.json()
    except Exception as exc:
        raise AstrologyAPIError(f"astrology-api.io {path} returned non-JSON: {exc}")


async def fetch_birth_chart_and_hd(subject: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Call both natal chart and Human Design bodygraph concurrently."""
    import asyncio

    api_key = _get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    natal_body = {
        "subject": subject,
        "options": {
            "house_system": "P",
            "zodiac_type": "Tropic",
            "include_interpretations": True,
        },
    }
    hd_body = {
        "subject": subject,
        "options": {
            "include_interpretations": True,
            "language": "en",
        },
        "hd_options": {
            "include_channels": True,
            "include_design_chart": True,
            "include_interpretations": True,
        },
    }

    async with httpx.AsyncClient(base_url=ASTROLOGY_API_BASE, headers=headers, timeout=60.0) as client:
        chart, hd = await asyncio.gather(
            _post(client, NATAL_CHART_PATH, natal_body),
            _post(client, HUMAN_DESIGN_PATH, hd_body),
        )
    return chart, hd


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------

# astrology-api.io returns 3-letter sign abbreviations in planetary_positions.
SIGN_ABBR_MAP = {
    "ari": "Aries",
    "tau": "Taurus",
    "gem": "Gemini",
    "can": "Cancer",
    "leo": "Leo",
    "vir": "Virgo",
    "lib": "Libra",
    "sco": "Scorpio",
    "sag": "Sagittarius",
    "cap": "Capricorn",
    "aqu": "Aquarius",
    "pis": "Pisces",
}


def normalize_sign(sign: Optional[str]) -> Optional[str]:
    """Map 'Can' -> 'Cancer'. Passes full names through unchanged."""
    if not sign:
        return None
    s = str(sign).strip()
    key = s[:3].lower()
    return SIGN_ABBR_MAP.get(key, s.title())


def _titleize(raw: Any) -> Optional[str]:
    """Format snake_case / lowercase API values as human-readable Title Case."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raw = str(raw)
    raw = raw.strip()
    if not raw:
        return None
    return raw.replace("_", " ").replace("-", " ").strip().title()


def extract_sun_sign(chart_json: Dict[str, Any]) -> Optional[str]:
    """Extract the Sun's zodiac sign from astrology-api.io `/charts/natal`.

    Shape: `chart_json.chart_data.planetary_positions` is a list of planets
    each with `{name, sign, ...}`. The `sign` value is a 3-letter abbreviation
    ("Can", "Leo", etc.) that we normalize to a full name.
    """
    if not isinstance(chart_json, dict):
        return None
    chart_data = chart_json.get("chart_data") or chart_json
    planets = chart_data.get("planetary_positions") if isinstance(chart_data, dict) else None
    if isinstance(planets, list):
        for p in planets:
            if isinstance(p, dict) and str(p.get("name", "")).lower() == "sun":
                return normalize_sign(p.get("sign"))
    return None


def extract_hd_summary(hd_json: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Pull the top-level HD fields from astrology-api.io `/human-design/bodygraph`.

    Shape: `hd_json.data.bodygraph` contains `{type, strategy, authority,
    profile, definition, centers, channels, ...}`. Type/authority come through
    as snake_case raw values ('manifesting_generator', 'sacral') — we title-case
    them for display.
    """
    bg: Dict[str, Any] = {}
    if isinstance(hd_json, dict):
        data = hd_json.get("data") or {}
        bg = (data.get("bodygraph") if isinstance(data, dict) else None) or {}
        # Fallback — some endpoints may return bodygraph fields at the top
        if not bg and "type" in hd_json:
            bg = hd_json

    return {
        "type": _titleize(bg.get("type")),
        "strategy": _titleize(bg.get("strategy")),
        "authority": _titleize(bg.get("authority")),
        "profile": str(bg.get("profile")).strip() if bg.get("profile") not in (None, "") else None,
    }


# ---------------------------------------------------------------------------
# LLM analysis of Human Design bodygraph
# ---------------------------------------------------------------------------

BIRTH_CHART_ANALYSIS_SYSTEM_PROMPT = """You are a western tropical astrologer writing a personalized natal chart reading for {subject_full_name}, based on their chart data.

CRITICAL: Write in the THIRD PERSON. NEVER use "you", "your", "yours". This reading will be stored as personal context that an AI assistant reads later — second-person would make the assistant think the reading is addressed to it.

Naming convention:
- The VERY FIRST sentence must use the full name "{subject_full_name}" exactly once as the subject.
- After that first sentence, refer to the subject only by their FIRST NAME ("{subject_first_name}") or third-person pronouns (he/she/they — default to they if unclear).
- Do NOT repeat the full name anywhere else in the reading.

Write a grounded, warm, 3-5 paragraph analysis that covers:

1. The overall signature — Sun, Moon, and Ascendant together, and what that combination means for how {subject_first_name} leads, feels, and shows up.
2. The standout placements — one or two planets in particularly significant signs or houses (e.g. Mars in its own sign, stelliums, angular planets, strong aspects).
3. The dominant aspects — pick the tightest 2-3 aspects from the data and explain what they create or tense in daily life.
4. A practical closing paragraph on what this chart asks of {subject_first_name} right now (decision-making, energy, relationships, work).

Requirements:
- Ground every claim in the chart data provided. Never invent placements.
- No fortune-telling, no disclaimers.
- Write in flowing prose. No bullet lists, no headers.
- Do not restate the raw data — interpret it.
"""


HD_ANALYSIS_SYSTEM_PROMPT = """You are a Human Design expert writing a personalized reading for {subject_full_name}, based on their bodygraph data.

CRITICAL: Write in the THIRD PERSON. NEVER use "you", "your", "yours". This reading will be stored as personal context that an AI assistant reads later — second-person would make the assistant think the reading is addressed to it.

Naming convention:
- The VERY FIRST sentence must use the full name "{subject_full_name}" exactly once as the subject.
- After that first sentence, refer to the subject only by their FIRST NAME ("{subject_first_name}") or third-person pronouns (he/she/they — default to they if unclear).
- Do NOT repeat the full name anywhere else in the reading.

Write a grounded, practical, 4-8 paragraph analysis covering, in order:

1. Type and Strategy — how {subject_first_name} is designed to interact with the world.
2. Inner Authority — how {subject_first_name} should make decisions correctly.
3. Profile — the lines and their lived expression in {subject_first_name}'s life.
4. Defined vs undefined Centers — where {subject_first_name} has consistent energy vs where they are open/conditioned.
5. Key Channels and Gates — the most meaningful active channels and what they unlock for {subject_first_name}.
6. Decision-making guidance and daily-life alignment — concrete suggestions for {subject_first_name} living as themselves.

Requirements:
- Ground every claim in the bodygraph JSON provided. Never invent fields.
- No astrology, no disclaimers, no "this is not professional advice" hedging.
- Write in confident, warm prose. Avoid bullet lists unless summarizing at the end.
- Do not restate the JSON; interpret it.
"""


def _truncate_json(obj: Any, limit: int = 12000) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    if len(text) > limit:
        return text[:limit] + "\n...[truncated]"
    return text


async def analyze_birth_chart(
    chart_json: Dict[str, Any],
    *,
    llm_provider: Any,
    subject_name: Optional[str] = None,
) -> Tuple[str, str]:
    """Run the natal chart narrative analysis through the platform default LLM.

    Returns (analysis_text, model_name). Raises on empty response. The
    analysis is written in third person using `subject_name` so AI agents
    reading it later don't confuse "you" for themselves.
    """
    if llm_provider is None:
        raise AstrologyAPIError("No LLM provider available for birth chart analysis.")
    full_name = (subject_name or "").strip() or "the subject"
    first_name = full_name.split()[0] if full_name != "the subject" else "they"

    chart_data = chart_json.get("chart_data", {}) if isinstance(chart_json, dict) else {}
    planets = chart_data.get("planetary_positions") or []
    houses = chart_data.get("house_cusps") or []
    aspects = chart_data.get("aspects") or []

    # Angles (Ascendant / MC) appear in planetary_positions but aren't
    # planets — pull them aside so the LLM can't confuse them for the subject.
    PLANET_POINTS = {"sun", "moon", "mercury", "venus", "mars", "jupiter",
                     "saturn", "uranus", "neptune", "pluto", "chiron"}
    ascendant_sign = None
    midheaven_sign = None
    placement_lines: list[str] = []
    for p in planets:
        if not isinstance(p, dict):
            continue
        raw_name = str(p.get("name", ""))
        key = raw_name.lower().replace("_", "")
        sign = normalize_sign(p.get("sign"))
        if key in ("ascendant", "asc"):
            ascendant_sign = sign
            continue
        if key in ("mediumcoeli", "mc", "midheaven"):
            midheaven_sign = sign
            continue
        if key not in PLANET_POINTS:
            continue
        display_name = raw_name.replace("_", " ")
        house = p.get("house")
        degree = p.get("degree")
        retro = " retrograde" if p.get("is_retrograde") else ""
        deg_str = f"{degree:.2f}°" if isinstance(degree, (int, float)) else ""
        house_str = f" in house {house}" if house not in (None, "") else ""
        placement_lines.append(f"- {display_name}: {sign} {deg_str}{house_str}{retro}")

    house_lines: list[str] = []
    for h in houses:
        if not isinstance(h, dict):
            continue
        house_lines.append(f"- House {h.get('house')}: {normalize_sign(h.get('sign'))}")

    aspect_lines: list[str] = []
    for a in aspects[:30]:
        if not isinstance(a, dict):
            continue
        p1 = str(a.get("point1", "")).replace("_", " ")
        p2 = str(a.get("point2", "")).replace("_", " ")
        kind = a.get("aspect_type", "")
        orb = a.get("orb")
        orb_str = f" (orb {orb:+.1f}°)" if isinstance(orb, (int, float)) else ""
        aspect_lines.append(f"- {p1} {kind} {p2}{orb_str}")

    angles_block = []
    if ascendant_sign:
        angles_block.append(f"- Ascendant (Rising): {ascendant_sign}")
    if midheaven_sign:
        angles_block.append(f"- Midheaven (MC): {midheaven_sign}")

    user_content = (
        BIRTH_CHART_ANALYSIS_SYSTEM_PROMPT.format(
            subject_full_name=full_name,
            subject_first_name=first_name,
        )
        + f"\n\n# Natal Chart Reading for: {full_name}\n"
        + f"(The subject is {full_name}. Open the first sentence with '{full_name}'. After that, use only the first name '{first_name}' or third-person pronouns. Never use 'you' or 'your'.)\n"
        + "\n## Angles\n" + ("\n".join(angles_block) or "(none)")
        + "\n\n## Planetary placements\n" + ("\n".join(placement_lines) or "(none)")
        + "\n\n## House cusps\n" + ("\n".join(house_lines) or "(none)")
        + "\n\n## Aspects\n" + ("\n".join(aspect_lines) or "(none)")
        + f"\n\nReminder: Begin the reading with '{full_name}'. After that first sentence, use only '{first_name}' or 'they/them/their'. Do not repeat the full name. Never use 'you', 'your', or 'yours'."
    )

    response = await llm_provider.chat(
        messages=[{"role": "user", "content": user_content}],
        max_tokens=1500,
    )
    text = (response or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    if not text:
        raise AstrologyAPIError("LLM returned empty birth chart analysis.")

    model_name = getattr(llm_provider, "model", None) or type(llm_provider).__name__
    return text, str(model_name)


async def analyze_human_design(
    hd_json: Dict[str, Any],
    *,
    llm_provider: Any,
    subject_name: Optional[str] = None,
) -> Tuple[str, str]:
    """Run the Human Design narrative analysis through the platform default LLM.

    Returns (analysis_text, model_name). Raises on empty response — per
    CLAUDE.md, fail fast rather than silently falling back. The reading is
    written in third person using `subject_name` so AI agents reading it
    later don't confuse "you" for themselves.
    """
    if llm_provider is None:
        raise AstrologyAPIError("No LLM provider available for Human Design analysis.")
    full_name = (subject_name or "").strip() or "the subject"
    first_name = full_name.split()[0] if full_name != "the subject" else "they"

    summary_fields = extract_hd_summary(hd_json)
    header_lines = [
        f"# Human Design Reading for: {full_name}",
        f"(The subject is {full_name}. Open the first sentence with '{full_name}'. After that, use only the first name '{first_name}' or third-person pronouns. Never use 'you' or 'your'.)",
        "",
        f"## Bodygraph fields",
        f"- Type: {summary_fields.get('type') or 'unknown'}",
        f"- Strategy: {summary_fields.get('strategy') or 'unknown'}",
        f"- Authority: {summary_fields.get('authority') or 'unknown'}",
        f"- Profile: {summary_fields.get('profile') or 'unknown'}",
        "",
        "## Full bodygraph JSON (for centers, channels, gates, definition, interpretations):",
    ]
    user_content = (
        HD_ANALYSIS_SYSTEM_PROMPT.format(
            subject_full_name=full_name,
            subject_first_name=first_name,
        )
        + "\n\n"
        + "\n".join(header_lines)
        + "\n"
        + _truncate_json(hd_json)
        + f"\n\nReminder: Begin the reading with '{full_name}'. After that first sentence, use only '{first_name}' or 'they/them/their'. Do not repeat the full name. Never use 'you', 'your', or 'yours'."
    )

    response = await llm_provider.chat(
        messages=[{"role": "user", "content": user_content}],
        max_tokens=3000,
    )
    text = (response or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    if not text:
        raise AstrologyAPIError("LLM returned empty Human Design analysis.")

    model_name = getattr(llm_provider, "model", None) or type(llm_provider).__name__
    return text, str(model_name)
