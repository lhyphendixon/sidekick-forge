"""
Internal HTTP helpers for calling the Lore MCP admin API.

Wraps httpx with the X-Lore-Internal header automatically set from the
platform service role key. Used by admin routes, import pipeline, etc.
"""

import os
from typing import Any, Dict, Optional


def internal_headers() -> Dict[str, str]:
    """Return the header dict that authenticates this process as an internal
    caller of the Lore MCP admin API."""
    return {"X-Lore-Internal": os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")}


def internal_params(
    user_id: str,
    target_url: Optional[str] = None,
    target_key: Optional[str] = None,
) -> Dict[str, str]:
    """Build the query param dict for an internal Lore admin API call."""
    params: Dict[str, str] = {"user_id": user_id}
    if target_url and target_key:
        params["target_url"] = target_url
        params["target_key"] = target_key
    return params
