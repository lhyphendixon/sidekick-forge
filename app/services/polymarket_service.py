"""Polymarket Gamma API client for prediction market data."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
DEFAULT_TIMEOUT = 15.0


class PolymarketService:
    """Async client for the Polymarket Gamma API (read-only, no auth required).

    CLOB API credentials (api_key, api_secret, passphrase) are stored for
    future authenticated endpoints but are not needed for Gamma API reads.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def search_markets(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search for prediction markets matching a query string."""
        if not query or not query.strip():
            return []

        params = {"q": query.strip(), "limit": min(limit, 20)}
        url = f"{GAMMA_BASE_URL}/public-search"

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning("Polymarket search failed: %s %s", resp.status, text[:200])
                    return []
                data = await resp.json()

        # The /public-search endpoint returns {"events": [...]}
        # Each event contains markets with outcome data
        results: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            events = data.get("events") or []
        elif isinstance(data, list):
            events = data
        else:
            events = []

        for event in events:
            if not isinstance(event, dict):
                continue

            markets = event.get("markets") or []
            for market in markets:
                if not isinstance(market, dict):
                    continue

                result = self._format_market(market, event)
                if result:
                    results.append(result)

                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        return results

    async def get_event(self, event_slug: str) -> Optional[Dict[str, Any]]:
        """Get a specific event by slug."""
        if not event_slug:
            return None

        url = f"{GAMMA_BASE_URL}/events"
        params = {"slug": event_slug}

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        events = data if isinstance(data, list) else []
        if not events:
            return None

        event = events[0]
        markets = event.get("markets") or []
        formatted_markets = []
        for market in markets:
            result = self._format_market(market, event)
            if result:
                formatted_markets.append(result)

        return {
            "title": event.get("title") or "",
            "description": event.get("description") or "",
            "slug": event.get("slug") or "",
            "end_date": event.get("endDate") or event.get("end_date") or "",
            "markets": formatted_markets,
        }

    async def get_market(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific market by condition ID."""
        if not condition_id:
            return None

        url = f"{GAMMA_BASE_URL}/markets"
        params = {"condition_id": condition_id}

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        markets = data if isinstance(data, list) else []
        if not markets:
            return None

        return self._format_market(markets[0])

    @staticmethod
    def _format_market(
        market: Dict[str, Any],
        event: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Format a raw market dict into a clean result."""
        question = market.get("question") or market.get("title") or ""
        if not question:
            return None

        # Extract outcome prices
        outcomes = market.get("outcomes") or []
        outcome_prices = market.get("outcomePrices") or market.get("outcome_prices") or []

        # Both fields can be JSON strings like '["Yes","No"]' or '["0.65","0.35"]'
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except Exception:
                outcomes = []

        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except Exception:
                outcome_prices = []

        probabilities = {}
        if isinstance(outcomes, list) and isinstance(outcome_prices, list):
            for i, outcome in enumerate(outcomes):
                if i < len(outcome_prices):
                    try:
                        price = float(outcome_prices[i])
                        probabilities[str(outcome)] = round(price * 100, 1)
                    except (ValueError, TypeError):
                        pass

        volume = market.get("volume") or market.get("volumeNum") or 0
        try:
            volume = float(volume)
        except (ValueError, TypeError):
            volume = 0

        # Skip inactive markets with no prices and no volume (placeholder candidates)
        if not probabilities and not market.get("active", True) and volume == 0:
            return None

        return {
            "question": question,
            "description": market.get("description") or "",
            "probabilities": probabilities,
            "volume_usd": round(volume, 2),
            "end_date": market.get("endDate") or market.get("end_date") or "",
            "active": market.get("active", True),
            "condition_id": market.get("conditionId") or market.get("condition_id") or "",
            "event_title": (event.get("title") or "") if event else "",
        }
