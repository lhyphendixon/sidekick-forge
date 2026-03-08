"""Prediction Market ability — searches Polymarket for real-time market probabilities."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from livekit.agents.llm.tool_context import function_tool as lk_function_tool

from app.services.polymarket_service import PolymarketService

logger = logging.getLogger(__name__)


class PredictionMarketConfigError(ValueError):
    """Raised when the prediction market ability is misconfigured."""


def build_prediction_market_tool(
    tool_def: Dict[str, Any],
    config: Dict[str, Any],
) -> Any:
    """Build a LiveKit function tool that searches Polymarket prediction markets."""
    slug = tool_def.get("slug") or tool_def.get("name") or "prediction_market"
    description = tool_def.get("description") or (
        "Search Polymarket prediction markets for real-time probability data on future events. "
        "Returns current market probabilities, trading volume, and resolution dates. "
        "Use this when users ask about the likelihood of future events, elections, policy changes, "
        "technology milestones, or any topic where prediction market data could inform the answer. "
        "Present these probabilities as crowd sentiment, not as predictions or guarantees. "
        "You should apply your own critical thinking — challenge the market's odds when warranted, "
        "note potential biases, and offer your own perspective alongside the data."
    )

    api_key = config.get("api_key") or config.get("polymarket_api_key")
    api_secret = config.get("polymarket_api_secret")
    passphrase = config.get("polymarket_passphrase")
    timeout = float(config.get("timeout") or 15)
    default_limit = int(config.get("default_limit") or 5)

    service = PolymarketService(
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
        timeout=timeout,
    )

    raw_schema = {
        "name": slug,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The topic or question to search prediction markets for. "
                        "Use specific keywords related to the event or outcome the user is asking about. "
                        "Examples: 'US presidential election 2028', 'Bitcoin price', 'AI regulation', 'Fed interest rate'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of markets to return (1-10). Default is 5.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }

    async def _invoke(**kwargs: Any) -> str:
        query = kwargs.get("query", "")
        limit = kwargs.get("limit", default_limit)

        if not query or not isinstance(query, str) or not query.strip():
            return json.dumps({
                "error": "A search query is required.",
                "markets": [],
            })

        # Clamp limit
        if not isinstance(limit, int) or limit < 1:
            limit = default_limit
        limit = min(limit, 10)

        try:
            logger.info("Searching Polymarket for: %s (limit=%d)", query, limit)
            markets = await service.search_markets(query.strip(), limit=limit)
        except Exception as exc:
            logger.error("Polymarket search failed: %s", exc)
            return json.dumps({
                "error": f"Failed to search prediction markets: {exc}",
                "markets": [],
            })

        if not markets:
            return json.dumps({
                "message": f"No prediction markets found for '{query}'. This topic may not have active markets on Polymarket.",
                "markets": [],
            })

        # Format for LLM consumption
        formatted = []
        for m in markets:
            entry: Dict[str, Any] = {
                "question": m.get("question", ""),
                "probabilities": m.get("probabilities", {}),
            }
            if m.get("volume_usd"):
                entry["volume_usd"] = m["volume_usd"]
            if m.get("end_date"):
                entry["end_date"] = m["end_date"]
            if m.get("event_title") and m["event_title"] != m.get("question"):
                entry["event"] = m["event_title"]
            formatted.append(entry)

        return json.dumps({
            "query": query,
            "market_count": len(formatted),
            "markets": formatted,
            "source": "Polymarket",
            "note": "These are prediction market probabilities from real-money trading — they reflect crowd sentiment, not certainties. Use them as one data point alongside your own reasoning. Feel free to challenge, contextualize, or disagree with the market's assessment when you have good reason to. Markets can be wrong, biased by herd behavior, or missing important context. Higher volume generally indicates a stronger signal but is still not a guarantee.",
        })

    return lk_function_tool(raw_schema=raw_schema)(_invoke)
