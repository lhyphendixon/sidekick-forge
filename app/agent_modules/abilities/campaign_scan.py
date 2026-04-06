"""
Campaign Scan Ability

Builds a LiveKit function tool that triggers the Campaign Scan widget
for email/newsletter proofreading and visual review.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def build_campaign_scan_tool(
    tool_def: Dict[str, Any],
    config: Dict[str, Any],
    *,
    api_keys: Optional[Dict[str, str]] = None,
) -> Any:
    """Build the Campaign Scan LiveKit function tool.

    This tool triggers the Campaign Scan widget in the frontend.
    The user forwards an email to the sidekick's address, and the
    system analyses it for errors, typos, and visual issues.
    """
    from livekit.agents import function_tool as lk_function_tool

    slug = tool_def.get("slug") or "campaign_scan"
    description = tool_def.get("description") or (
        "Trigger the Campaign Scan email review widget. "
        "When the user wants to check an email, newsletter, or campaign for "
        "typos, errors, visual issues, or quality, use this tool. "
        "Also use this tool when the user mentions 'proofread', 'review my email', "
        "'check my newsletter', 'campaign review', or 'email check'. "
        "The user will forward the email to the sidekick's address "
        "and receive a detailed analysis. "
        "Call this tool immediately when email review is mentioned — do not ask clarifying questions."
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
                    "description": "Set to true to trigger the Campaign Scan widget UI",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    }

    async def _invoke(**kwargs: Any) -> str:
        logger.info(f"Campaign Scan widget trigger invoked: {kwargs}")
        return "WIDGET_TRIGGER:campaign_scan"

    return lk_function_tool(raw_schema=raw_schema)(_invoke)
