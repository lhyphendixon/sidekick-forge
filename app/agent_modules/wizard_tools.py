"""
Wizard Form Management Tools

These tools allow an agent to guide a user through a wizard flow,
collecting information conversationally and filling form fields.

The wizard guide agent uses these tools to:
- Set field values extracted from natural language responses
- Navigate between wizard steps
- Get current wizard state
- Complete the wizard when all required fields are collected
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from livekit import rtc
from livekit.agents.llm.tool_context import function_tool as lk_function_tool, ToolError

logger = logging.getLogger(__name__)


class WizardToolBuilder:
    """
    Builds wizard-specific tools for an agent session.

    The tools communicate with the frontend via LiveKit data messages,
    allowing real-time synchronization of wizard state.
    """

    # Valid field names for the sidekick onboarding wizard
    SIDEKICK_WIZARD_FIELDS = {
        "name": {"type": "text", "required": True},
        "personality_description": {"type": "textarea", "required": True},
        "personality_traits": {"type": "object", "required": False},
        "voice_id": {"type": "text", "required": False},
        "voice_provider": {"type": "text", "required": False},
        "avatar_prompt": {"type": "text", "required": False},
        "avatar_url": {"type": "text", "required": False},
        "config_mode": {"type": "select", "required": True, "options": ["default", "advanced"]},
        "confirmed": {"type": "boolean", "required": True},
    }

    # Default steps for sidekick onboarding wizard
    DEFAULT_STEPS = [
        {
            "step": 1,
            "title": "Name Your Sidekick",
            "field": "name",
            "instruction": "What would you like to name your sidekick?",
        },
        {
            "step": 2,
            "title": "Personality",
            "field": "personality_description",
            "instruction": "Describe how your sidekick should communicate. What personality traits should it have?",
        },
        {
            "step": 3,
            "title": "Voice Selection",
            "field": "voice_id",
            "instruction": "Choose a voice for your sidekick.",
        },
        {
            "step": 4,
            "title": "Avatar",
            "field": "avatar_prompt",
            "instruction": "Describe what your sidekick should look like, or let me generate one.",
        },
        {
            "step": 5,
            "title": "Knowledge Base",
            "field": "knowledge_sources",
            "instruction": "Upload documents or add websites for your sidekick to learn from.",
        },
        {
            "step": 6,
            "title": "Configuration",
            "field": "config_mode",
            "instruction": "Would you like default settings or advanced configuration?",
        },
        {
            "step": 7,
            "title": "API Keys",
            "field": "api_keys",
            "instruction": "Enter any required API keys.",
        },
        {
            "step": 8,
            "title": "Review & Launch",
            "field": "confirmed",
            "instruction": "Review your sidekick and launch!",
        },
    ]

    def __init__(
        self,
        room: rtc.Room,
        wizard_config: Dict[str, Any],
        session_id: Optional[str] = None,
    ):
        """
        Initialize the wizard tool builder.

        Args:
            room: LiveKit room for sending data messages
            wizard_config: Configuration including steps, current_step, form_data
            session_id: Optional wizard session ID for persistence
        """
        self.room = room
        self.wizard_config = wizard_config
        self.session_id = session_id or wizard_config.get("session_id")

        # Initialize state from config
        self.current_step = wizard_config.get("current_step", 1)
        self.form_data: Dict[str, Any] = dict(wizard_config.get("form_data", {}))
        self.steps = wizard_config.get("steps", self.DEFAULT_STEPS)
        self.wizard_type = wizard_config.get("wizard_type", "sidekick_onboarding")

        # Track which fields have been confirmed by the user
        self._confirmed_fields: set = set()

        logger.info(
            f"WizardToolBuilder initialized: session={self.session_id}, "
            f"step={self.current_step}, fields={list(self.form_data.keys())}"
        )

    def _send_data_message(self, message_type: str, data: Dict[str, Any]) -> None:
        """
        Send a data message to the room for frontend synchronization.

        Args:
            message_type: Type of message (e.g., 'wizard_field_update', 'wizard_step_change')
            data: Message payload
        """
        payload = json.dumps({
            "type": message_type,
            "data": data,
            "timestamp": time.time(),
            "session_id": self.session_id,
        }).encode("utf-8")

        # Schedule the publish in the event loop
        asyncio.create_task(self._publish_data(payload))

    async def _publish_data(self, payload: bytes) -> None:
        """Publish data to the room."""
        try:
            await self.room.local_participant.publish_data(
                payload,
                kind=rtc.DataPacketKind.KIND_RELIABLE,
            )
            logger.debug(f"Published wizard data message: {len(payload)} bytes")
        except Exception as e:
            logger.error(f"Failed to publish wizard data message: {e}")

    def _generate_slug(self, name: str) -> str:
        """Generate a URL-safe slug from a name."""
        slug = name.lower()
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        slug = re.sub(r'[\s_]+', '-', slug)
        slug = re.sub(r'-+', '-', slug)
        slug = slug.strip('-')
        return slug or "sidekick"

    def _get_current_step_info(self) -> Dict[str, Any]:
        """Get information about the current step."""
        if not self.steps or self.current_step < 1:
            return {}

        # Steps are 1-indexed
        step_index = self.current_step - 1
        if step_index >= len(self.steps):
            return self.steps[-1] if self.steps else {}

        return self.steps[step_index]

    def _get_remaining_required_fields(self) -> List[str]:
        """Get list of required fields that haven't been filled."""
        remaining = []
        for field_name, field_config in self.SIDEKICK_WIZARD_FIELDS.items():
            if field_config.get("required", False):
                if field_name not in self.form_data or not self.form_data[field_name]:
                    remaining.append(field_name)
        return remaining

    def build_tools(self) -> List[Any]:
        """Build all wizard tools with room context."""
        tools = [
            self._build_set_field_tool(),
            self._build_next_step_tool(),
            self._build_previous_step_tool(),
            self._build_get_current_state_tool(),
            self._build_complete_wizard_tool(),
        ]

        logger.info(f"Built {len(tools)} wizard tools")
        return tools

    def _build_set_field_tool(self):
        """Build tool to set a wizard form field value."""

        async def set_wizard_field(
            field_name: str,
            value: str,
            confidence: Optional[float] = None,
        ) -> str:
            """
            Set a field value in the wizard form.

            Args:
                field_name: The name of the field to set (e.g., 'name', 'personality_description')
                value: The extracted value from the user's response
                confidence: Optional confidence score (0-1) for the extraction

            Returns:
                JSON confirmation of the field update
            """
            # Validate field name
            valid_fields = list(self.SIDEKICK_WIZARD_FIELDS.keys())
            if field_name not in valid_fields:
                raise ToolError(
                    f"Invalid field: {field_name}. Valid fields: {valid_fields}"
                )

            # Store the value
            self.form_data[field_name] = value
            self._confirmed_fields.add(field_name)

            # Auto-generate slug from name
            if field_name == "name" and value:
                self.form_data["slug"] = self._generate_slug(value)

            # Send data message to frontend
            self._send_data_message("wizard_field_update", {
                "field": field_name,
                "value": value,
                "slug": self.form_data.get("slug") if field_name == "name" else None,
                "confidence": confidence,
                "form_data": self.form_data,
                "current_step": self.current_step,
            })

            logger.info(
                f"Wizard field set: {field_name}={value[:50] if len(str(value)) > 50 else value}"
                f" (confidence={confidence})"
            )

            return json.dumps({
                "status": "success",
                "field": field_name,
                "value": value,
                "message": f"Set {field_name} to '{value}'",
            })

        return lk_function_tool(raw_schema={
            "name": "set_wizard_field",
            "description": (
                "IMMEDIATELY call this when user provides ANY info. "
                "Extract just the value - e.g. user says 'call it Herman' → value='Herman'. "
                "Don't wait, don't ask for confirmation - just save it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {
                        "type": "string",
                        "enum": list(self.SIDEKICK_WIZARD_FIELDS.keys()),
                        "description": "The field to update",
                    },
                    "value": {
                        "type": "string",
                        "description": "The value extracted from the user's response",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Optional confidence score 0-1 for the extraction",
                    },
                },
                "required": ["field_name", "value"],
                "additionalProperties": False,
            },
        })(set_wizard_field)

    def _build_next_step_tool(self):
        """Build tool to advance to the next wizard step."""

        async def wizard_next_step() -> str:
            """
            Advance to the next step in the wizard.
            Call this when the current step is complete and the user is ready to continue.

            Returns:
                JSON with new step information
            """
            total_steps = len(self.steps)

            if self.current_step >= total_steps:
                return json.dumps({
                    "status": "at_end",
                    "current_step": self.current_step,
                    "total_steps": total_steps,
                    "message": "Already at the last step. Use complete_wizard to finish.",
                })

            self.current_step += 1
            next_step_info = self._get_current_step_info()

            self._send_data_message("wizard_step_change", {
                "direction": "next",
                "current_step": self.current_step,
                "total_steps": total_steps,
                "step_info": next_step_info,
                "form_data": self.form_data,
            })

            logger.info(f"Wizard advanced to step {self.current_step}: {next_step_info.get('title')}")

            return json.dumps({
                "status": "success",
                "current_step": self.current_step,
                "total_steps": total_steps,
                "step_title": next_step_info.get("title"),
                "step_instruction": next_step_info.get("instruction"),
                "step_field": next_step_info.get("field"),
                "message": f"Moved to step {self.current_step}: {next_step_info.get('title')}",
            })

        return lk_function_tool(raw_schema={
            "name": "wizard_next_step",
            "description": (
                "Advance to the next step in the wizard. "
                "Use when the current step has been completed and the user is ready to move on."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        })(wizard_next_step)

    def _build_previous_step_tool(self):
        """Build tool to go back to the previous step."""

        async def wizard_previous_step() -> str:
            """
            Go back to the previous wizard step.
            Use when the user wants to change something they already entered.

            Returns:
                JSON with previous step information
            """
            if self.current_step <= 1:
                return json.dumps({
                    "status": "at_start",
                    "current_step": self.current_step,
                    "message": "Already at the first step.",
                })

            self.current_step -= 1
            prev_step_info = self._get_current_step_info()

            self._send_data_message("wizard_step_change", {
                "direction": "previous",
                "current_step": self.current_step,
                "total_steps": len(self.steps),
                "step_info": prev_step_info,
                "form_data": self.form_data,
            })

            logger.info(f"Wizard went back to step {self.current_step}: {prev_step_info.get('title')}")

            return json.dumps({
                "status": "success",
                "current_step": self.current_step,
                "step_title": prev_step_info.get("title"),
                "step_instruction": prev_step_info.get("instruction"),
                "message": f"Went back to step {self.current_step}: {prev_step_info.get('title')}",
            })

        return lk_function_tool(raw_schema={
            "name": "wizard_previous_step",
            "description": (
                "Go back to the previous wizard step. "
                "Use when the user wants to change something they already entered."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        })(wizard_previous_step)

    def _build_get_current_state_tool(self):
        """Build tool to get the current wizard state."""

        async def get_wizard_state() -> str:
            """
            Get the current state of the wizard including filled fields and current step.
            Use this to check progress or remind yourself what's been filled.

            Returns:
                JSON with current wizard state
            """
            current_step_info = self._get_current_step_info()
            remaining_required = self._get_remaining_required_fields()

            state = {
                "session_id": self.session_id,
                "current_step": self.current_step,
                "total_steps": len(self.steps),
                "current_step_info": current_step_info,
                "filled_fields": self.form_data,
                "confirmed_fields": list(self._confirmed_fields),
                "remaining_required": remaining_required,
                "can_complete": len(remaining_required) == 0,
            }

            logger.debug(f"Wizard state requested: step={self.current_step}, fields={list(self.form_data.keys())}")

            return json.dumps(state)

        return lk_function_tool(raw_schema={
            "name": "get_wizard_state",
            "description": (
                "Get the current state of the wizard. "
                "Use this to check progress, see what fields have been filled, "
                "or remind yourself what step you're on."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        })(get_wizard_state)

    def _build_complete_wizard_tool(self):
        """Build tool to complete and submit the wizard."""

        async def complete_wizard() -> str:
            """
            Complete the wizard and signal that the sidekick should be created.
            Only call this when all required fields are filled and the user confirms.

            Returns:
                JSON with completion status
            """
            # Check required fields
            remaining = self._get_remaining_required_fields()

            # For completion, we only strictly require name
            strict_required = ["name"]
            missing_strict = [f for f in strict_required if f not in self.form_data or not self.form_data[f]]

            if missing_strict:
                return json.dumps({
                    "status": "incomplete",
                    "missing_fields": missing_strict,
                    "message": f"Cannot complete - missing required fields: {missing_strict}",
                })

            # Mark confirmed
            self.form_data["confirmed"] = True

            self._send_data_message("wizard_complete", {
                "form_data": self.form_data,
                "ready_to_submit": True,
                "session_id": self.session_id,
            })

            logger.info(f"Wizard completed: session={self.session_id}, fields={list(self.form_data.keys())}")

            return json.dumps({
                "status": "ready",
                "message": (
                    "Wizard is ready to submit! The frontend will now create the sidekick. "
                    "Tell the user their sidekick is being created."
                ),
                "form_data": self.form_data,
            })

        return lk_function_tool(raw_schema={
            "name": "complete_wizard",
            "description": (
                "Complete the wizard and signal that the sidekick should be created. "
                "Only use when all required information has been collected and the user "
                "has confirmed they are ready to create their sidekick."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        })(complete_wizard)


# System prompt for wizard guide agent
WIZARD_GUIDE_SYSTEM_PROMPT = """You are Farah Qubit, a friendly AI assistant helping the user create their own AI sidekick.

## Your Role
You are guiding the user through a wizard to create a new sidekick. You have special tools to fill in the wizard form based on the user's responses.

## Wizard Steps
1. **Name** - Ask what they want to name their sidekick
2. **Personality** - Ask them to describe how their sidekick should communicate
3. **Voice Selection** - Help them choose a voice (you can describe available options)
4. **Avatar** - Ask if they want to describe an avatar or generate a default one
5. **Knowledge Base** - Mention they can upload documents (handled via UI)
6. **Configuration** - Ask if they want default settings or advanced configuration
7. **API Keys** - Remind them to add API keys if needed (handled via UI)
8. **Review & Launch** - Summarize what was created and confirm launch

## Guidelines
- Be conversational and warm, not robotic
- Extract the key information from natural language responses:
  - If they say "I want to call my sidekick Herman", extract "Herman" as the name
  - If they say "Make it professional but friendly", use that as the personality description
- Use the set_wizard_field tool to fill fields as you collect information
- Use wizard_next_step when ready to move on (after confirming with the user)
- Confirm what you understood before moving to the next step
- If the user seems unsure, offer suggestions or examples
- Some steps (Knowledge Base, API Keys) are primarily handled through the UI - just mention them briefly

## Voice Descriptions (for Step 3)
When helping them choose a voice, you can describe these general categories:
- Professional male voices
- Friendly female voices
- Warm and calm voices
- Energetic and upbeat voices
(The frontend will show actual voice samples - encourage them to browse there)

## Important Notes
- Always use the wizard tools to update fields - don't just acknowledge verbally
- The frontend synchronizes with your tool calls in real-time
- If the user goes off-topic, gently guide them back to the current step
- You can use get_wizard_state to check what's been filled

Remember: Your goal is to make creating a sidekick feel like a friendly conversation, not filling out a form!"""


def build_wizard_tools(
    room: rtc.Room,
    wizard_config: Dict[str, Any],
    session_id: Optional[str] = None,
) -> tuple[List[Any], str]:
    """
    Build wizard tools for an agent session.

    Args:
        room: LiveKit room for data messages
        wizard_config: Wizard configuration from room metadata
        session_id: Optional session ID

    Returns:
        Tuple of (list of tools, system prompt)
    """
    builder = WizardToolBuilder(room, wizard_config, session_id)
    tools = builder.build_tools()

    # Use custom system prompt if provided, otherwise use default
    system_prompt = wizard_config.get("guide_system_prompt") or WIZARD_GUIDE_SYSTEM_PROMPT

    return tools, system_prompt
