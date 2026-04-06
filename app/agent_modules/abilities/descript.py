"""
Descript Connect Ability

Builds a LiveKit function tool that triggers the Descript Connect widget
for AI-powered video editing via the Descript API.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class DescriptAbilityConfigError(Exception):
    """Raised when Descript Connect is misconfigured."""
    pass


def build_descript_tool(
    tool_def: Dict[str, Any],
    config: Dict[str, Any],
    *,
    api_keys: Optional[Dict[str, str]] = None,
) -> Any:
    """
    Build the Descript Connect LiveKit function tool.

    This tool triggers the Descript Connect widget in the frontend.
    The actual Descript API calls are made from the widget via REST endpoints.
    """
    from livekit.agents import function_tool as lk_function_tool

    slug = tool_def.get("slug") or "descript_connect"
    description = tool_def.get("description") or (
        "Trigger the Descript Connect video editing widget. "
        "When the user wants to edit a video, remove filler words, remove silences, "
        "enhance audio, create highlight clips, or apply any video/audio edits, use this tool. "
        "Also use this tool when the user mentions 'Descript' by name. "
        "The user will upload their video and configure editing preferences "
        "directly in the widget interface. "
        "Call this tool immediately when video editing is mentioned — do not ask clarifying questions."
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
                    "description": "Set to true to trigger the Descript Connect widget UI",
                },
                "suggested_instructions": {
                    "type": "string",
                    "description": "Optional editing instructions inferred from the conversation",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    }

    async def _invoke(**kwargs: Any) -> str:
        logger.info(f"Descript Connect widget trigger invoked: {kwargs}")
        suggested_instructions = kwargs.get("suggested_instructions", "")

        return f"WIDGET_TRIGGER:descript:{suggested_instructions}"

    return lk_function_tool(raw_schema=raw_schema)(_invoke)
