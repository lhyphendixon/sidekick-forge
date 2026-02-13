"""
Ken Burns Image Generation Tools

These tools allow an agent to generate contextual images during conversations
using RunWare.ai. Images are sent to the frontend via LiveKit data channel
for display with Ken Burns (pan/zoom) effects.

The agent uses these tools to:
- Generate scene images that visualize what's being discussed
- Push images to the frontend in real-time
- Enhance storytelling with AI-generated visuals

Auto-generation feature:
- Automatically generates new images at configurable intervals
- Uses recent conversation context to create relevant visuals
- Keeps the visual experience fresh without relying solely on LLM tool calls
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional, Callable, Awaitable

from livekit import rtc
from livekit.agents.llm.tool_context import function_tool as lk_function_tool, ToolError

logger = logging.getLogger(__name__)


# Conversational filler phrases to strip from context before generating image prompts
CONVERSATIONAL_FILLER = [
    "well ", "so ", "you know ", "i mean ", "actually ", "basically ",
    "that's a great question", "great question", "good question",
    "let me tell you", "let me explain", "i'd say ", "i think ",
    "you see ", "the thing is ", "here's the thing ", "to be honest ",
    "absolutely ", "definitely ", "of course ", "sure ", "right ",
]


class KenBurnsToolBuilder:
    """
    Builds Ken Burns image generation tools for an agent session.

    The tools communicate with the frontend via LiveKit data messages,
    allowing real-time image updates during conversations.
    """

    # Style presets for image generation
    STYLE_PRESETS = {
        "cinematic": "cinematic lighting, dramatic composition, film still, professional photography, 8k",
        "futuristic": "futuristic, sci-fi, advanced technology, sleek design, neon accents, cyberpunk aesthetic",
        "realistic": "photorealistic, high detail, natural lighting, professional photograph, sharp focus",
        "artistic": "artistic interpretation, painterly, vibrant colors, creative composition, digital art",
        "documentary": "documentary style, realistic, informative, clear visualization, journalistic",
        "fantasy": "fantasy art, magical, ethereal lighting, mystical atmosphere, detailed illustration",
        "minimalist": "minimalist, clean design, simple composition, modern aesthetic, elegant",
    }

    # Default negative prompt for quality
    DEFAULT_NEGATIVE_PROMPT = (
        "blurry, low quality, low resolution, pixelated, watermark, text, logo, "
        "signature, username, deformed, distorted, disfigured, bad anatomy, "
        "poorly drawn, amateur, ugly, duplicate, morbid, mutilated"
    )

    def __init__(
        self,
        room: rtc.Room,
        kenburns_config: Dict[str, Any],
        runware_service: Any = None,
    ):
        """
        Initialize the Ken Burns tool builder.

        Args:
            room: LiveKit room for sending data messages
            kenburns_config: Configuration including style preferences
            runware_service: Optional RunWare service instance (will create if not provided)
        """
        self.room = room
        self.config = kenburns_config

        # Get RunWare service
        if runware_service:
            self.runware = runware_service
        else:
            from app.services.runware_service import get_runware_service
            self.runware = get_runware_service()

        # Configuration
        self.default_style = kenburns_config.get("style_preset", "cinematic")
        self.animation_duration = kenburns_config.get("animation_duration", 20)
        self.image_width = kenburns_config.get("width", 576)
        self.image_height = kenburns_config.get("height", 1024)  # 9:16 portrait aspect ratio

        # Auto-generation settings
        self.auto_interval = kenburns_config.get("auto_interval", 15)  # seconds
        self._auto_generation_task: Optional[asyncio.Task] = None
        self._auto_generation_running = False

        # Track generation state
        self._current_image_url: Optional[str] = None
        self._generation_in_progress = False
        self._generation_count = 0
        self._last_generation_time: float = 0

        # Context tracking for auto-generation
        self._recent_context: List[str] = []  # Recent assistant speech
        self._recent_user_context: List[str] = []  # Recent user speech/questions
        self._max_context_items = 5

        logger.info(
            f"KenBurnsToolBuilder initialized: style={self.default_style}, "
            f"dimensions={self.image_width}x{self.image_height}, "
            f"auto_interval={self.auto_interval}s"
        )

    async def _send_image_to_frontend(
        self,
        image_url: str,
        prompt: str,
        generation_time_ms: float,
    ) -> None:
        """
        Send a generated image to the frontend via data channel.

        Args:
            image_url: URL of the generated image
            prompt: The prompt used to generate the image
            generation_time_ms: Time taken to generate the image
        """
        payload = json.dumps({
            "type": "kenburns_image",
            "data": {
                "image_url": image_url,
                "prompt": prompt,
                "generation_time_ms": generation_time_ms,
                "animation_duration": self.animation_duration,
                "timestamp": time.time(),
            },
        }).encode("utf-8")

        try:
            await self.room.local_participant.publish_data(
                payload,
                reliable=True,
            )
            logger.info(f"Published Ken Burns image to frontend: {image_url[:60]}...")
        except Exception as e:
            logger.error(f"Failed to publish Ken Burns image: {e}")

    def _enhance_prompt(self, base_prompt: str, style: Optional[str] = None) -> str:
        """
        Enhance a prompt with style modifiers for better image quality.

        Args:
            base_prompt: The user's scene description
            style: Style preset name (uses default if not provided)

        Returns:
            Enhanced prompt with style modifiers
        """
        style_key = style or self.default_style
        style_modifier = self.STYLE_PRESETS.get(
            style_key,
            self.STYLE_PRESETS["cinematic"]
        )

        # Build enhanced prompt
        enhanced = f"{base_prompt}, {style_modifier}"
        return enhanced

    def update_context(self, text: str) -> None:
        """
        Update the recent context with new assistant speech/text.

        Args:
            text: Recent speech text from the agent
        """
        if text and len(text.strip()) > 10:
            self._recent_context.append(text.strip())
            if len(self._recent_context) > self._max_context_items:
                self._recent_context.pop(0)

    def update_user_context(self, text: str) -> None:
        """
        Update context with user speech. User questions/topics are critical
        for generating relevant images since they define the conversation subject.

        Args:
            text: Recent speech text from the user
        """
        if text and len(text.strip()) > 5:
            self._recent_user_context.append(text.strip())
            if len(self._recent_user_context) > self._max_context_items:
                self._recent_user_context.pop(0)

    @staticmethod
    def _strip_filler(text: str) -> str:
        """Strip conversational filler from text to extract the core subject."""
        cleaned = text.lower().strip()
        for filler in CONVERSATIONAL_FILLER:
            if cleaned.startswith(filler):
                cleaned = cleaned[len(filler):].strip()
        # Remove leading punctuation/connectors after stripping
        cleaned = cleaned.lstrip(",;:-– ")
        # Restore original casing by finding the cleaned portion in original
        if cleaned and len(cleaned) > 5:
            return cleaned
        return text.strip()

    def _extract_topic(self, text: str) -> str:
        """
        Extract the core visual topic from speech text.
        Strips filler, takes the most substantive sentences.
        """
        cleaned = self._strip_filler(text)

        # Split into sentences and pick the most substantive ones
        sentences = [s.strip() for s in cleaned.replace("!", ".").replace("?", ".").split(".") if s.strip()]
        if not sentences:
            return cleaned

        # Filter out very short/filler sentences (< 15 chars)
        substantive = [s for s in sentences if len(s) >= 15]
        if not substantive:
            substantive = sentences

        # Use last 1-2 substantive sentences
        topic = ". ".join(substantive[-2:]) if len(substantive) > 1 else substantive[-1]

        # Truncate on word boundary
        if len(topic) > 250:
            topic = topic[:250].rsplit(" ", 1)[0]

        return topic

    def _generate_auto_prompt(self) -> str:
        """
        Generate an image prompt based on recent conversation context.
        Prioritizes the combination of user question + agent response
        for maximum topic relevance.

        Returns:
            A scene description prompt for image generation
        """
        # Best case: we have both user question and agent response
        if self._recent_user_context and self._recent_context:
            user_topic = self._extract_topic(self._recent_user_context[-1])
            agent_topic = self._extract_topic(self._recent_context[-1])

            # Combine both for a richer prompt — user defines the subject,
            # agent provides the detail
            combined = f"{user_topic}. {agent_topic}"
            if len(combined) > 300:
                combined = combined[:300].rsplit(" ", 1)[0]

            return f"A photorealistic scene showing: {combined}"

        # Have agent speech only
        if self._recent_context:
            topic = self._extract_topic(self._recent_context[-1])
            return f"A photorealistic scene showing: {topic}"

        # Have user speech only — use their question as the topic
        if self._recent_user_context:
            topic = self._extract_topic(self._recent_user_context[-1])
            return f"A photorealistic scene related to: {topic}"

        # No context at all — generic fallback
        fallback_prompts = [
            "An abstract digital landscape with flowing energy and light",
            "A serene moment captured in vibrant colors and soft light",
            "A creative visualization of ideas and connections",
            "An atmospheric scene with depth and movement",
            "A modern artistic interpretation of technology and nature",
        ]
        return random.choice(fallback_prompts)

    async def _auto_generate_image(self) -> None:
        """
        Automatically generate an image using recent context.
        Called by the auto-generation background task.
        """
        if self._generation_in_progress:
            logger.debug("Auto-generation skipped - generation already in progress")
            return

        self._generation_in_progress = True
        try:
            # Generate prompt from context
            auto_prompt = self._generate_auto_prompt()
            enhanced_prompt = self._enhance_prompt(auto_prompt)

            logger.info(f"Auto-generating Ken Burns image: {auto_prompt[:50]}...")

            # Generate the image
            result = await self.runware.generate_image(
                prompt=enhanced_prompt,
                width=self.image_width,
                height=self.image_height,
                negative_prompt=self.DEFAULT_NEGATIVE_PROMPT,
            )

            # Update state
            self._current_image_url = result.image_url
            self._last_generation_time = time.time()
            self._generation_count += 1

            # Send to frontend
            await self._send_image_to_frontend(
                image_url=result.image_url,
                prompt=auto_prompt,
                generation_time_ms=result.generation_time_ms,
            )

            logger.info(
                f"Auto-generated Ken Burns image in {result.generation_time_ms:.0f}ms"
            )

        except Exception as e:
            logger.error(f"Auto-generation failed: {e}", exc_info=True)
        finally:
            self._generation_in_progress = False

    async def _auto_generation_loop(self) -> None:
        """
        Background loop that auto-generates images at the configured interval.
        """
        logger.info(f"Starting Ken Burns auto-generation loop (interval: {self.auto_interval}s)")

        while self._auto_generation_running:
            try:
                # Wait for the interval
                await asyncio.sleep(self.auto_interval)

                if not self._auto_generation_running:
                    break

                # Room disconnection is handled by the disconnect event handler
                # which calls stop_auto_generation(). No need to check state here -
                # if the room disconnects, the loop will be cancelled.

                # Check if enough time has passed since last generation
                # (manual generations should also reset the timer)
                time_since_last = time.time() - self._last_generation_time
                if time_since_last >= self.auto_interval:
                    await self._auto_generate_image()

            except asyncio.CancelledError:
                logger.info("Auto-generation loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in auto-generation loop: {e}", exc_info=True)
                await asyncio.sleep(5)  # Brief pause before retrying

        logger.info("Ken Burns auto-generation loop stopped")

    def start_auto_generation(self) -> None:
        """
        Start the auto-generation background task.
        Call this when the agent session begins.
        """
        if self.auto_interval <= 0:
            logger.info("Auto-generation disabled (interval <= 0)")
            return

        if self._auto_generation_running:
            logger.warning("Auto-generation already running")
            return

        self._auto_generation_running = True
        self._last_generation_time = time.time()  # Start the timer
        self._auto_generation_task = asyncio.create_task(self._auto_generation_loop())
        logger.info(f"Started Ken Burns auto-generation (interval: {self.auto_interval}s)")

    def stop_auto_generation(self) -> None:
        """
        Stop the auto-generation background task.
        Call this when the agent session ends.
        """
        self._auto_generation_running = False
        if self._auto_generation_task:
            self._auto_generation_task.cancel()
            self._auto_generation_task = None
        logger.info("Stopped Ken Burns auto-generation")

    def build_tools(self) -> List[Any]:
        """Build all Ken Burns tools with room context."""
        tools = [
            self._build_generate_scene_image_tool(),
        ]
        return tools

    def _build_generate_scene_image_tool(self) -> Any:
        """Build the main image generation tool."""

        # Capture self for closure
        builder = self

        @lk_function_tool(
            name="generate_scene_image",
            description=(
                "Generate an AI image to visualize the current topic being discussed. "
                "Use this tool when describing visual concepts, scenarios, the future, "
                "or any topic that would benefit from an illustrative image. "
                "The image will be displayed to the user with a cinematic Ken Burns effect. "
                "Provide a detailed, vivid description of the scene to generate."
            ),
        )
        async def generate_scene_image(
            scene_description: str,
            style: Optional[str] = None,
        ) -> str:
            """
            Generate an image to visualize the current topic.

            Args:
                scene_description: Detailed description of the scene to generate.
                    Be specific and vivid - describe visual elements, lighting,
                    atmosphere, and composition.
                style: Optional style preset. Options: cinematic, futuristic,
                    realistic, artistic, documentary, fantasy, minimalist.
                    Defaults to the agent's configured style.

            Returns:
                Confirmation message about the image generation.
            """
            if builder._generation_in_progress:
                return "An image is already being generated. Please wait for it to complete."

            if not scene_description or len(scene_description.strip()) < 10:
                raise ToolError("Please provide a more detailed scene description (at least 10 characters).")

            builder._generation_in_progress = True
            builder._generation_count += 1

            try:
                # Enhance the prompt with style
                enhanced_prompt = builder._enhance_prompt(
                    scene_description.strip(),
                    style
                )

                logger.info(f"Generating Ken Burns image: {scene_description[:50]}...")

                # Generate the image (this runs async)
                result = await builder.runware.generate_image(
                    prompt=enhanced_prompt,
                    width=builder.image_width,
                    height=builder.image_height,
                    negative_prompt=builder.DEFAULT_NEGATIVE_PROMPT,
                )

                # Update state
                builder._current_image_url = result.image_url
                builder._last_generation_time = time.time()  # Reset auto-gen timer

                # Send to frontend immediately
                await builder._send_image_to_frontend(
                    image_url=result.image_url,
                    prompt=scene_description,
                    generation_time_ms=result.generation_time_ms,
                )

                logger.info(
                    f"Ken Burns image generated in {result.generation_time_ms:.0f}ms: "
                    f"{result.image_url[:60]}..."
                )

                return f"Image generated successfully and is now being displayed."

            except Exception as e:
                logger.error(f"Failed to generate Ken Burns image: {e}", exc_info=True)
                raise ToolError(f"Failed to generate image: {str(e)}")
            finally:
                builder._generation_in_progress = False

        return generate_scene_image


def build_kenburns_tools(
    room: rtc.Room,
    kenburns_config: Dict[str, Any],
    runware_service: Any = None,
    return_builder: bool = False,
) -> List[Any] | tuple[List[Any], KenBurnsToolBuilder]:
    """
    Build Ken Burns tools for an agent session.

    Args:
        room: LiveKit room for data messages
        kenburns_config: Configuration for Ken Burns mode
        runware_service: Optional RunWare service instance
        return_builder: If True, also return the builder instance for
                       calling start_auto_generation() and update_context()

    Returns:
        List of function tools for the agent, or tuple of (tools, builder)
        if return_builder=True
    """
    builder = KenBurnsToolBuilder(
        room=room,
        kenburns_config=kenburns_config,
        runware_service=runware_service,
    )
    tools = builder.build_tools()

    if return_builder:
        return tools, builder
    return tools


# System prompt addition for agents with Ken Burns enabled
KENBURNS_SYSTEM_PROMPT_ADDITION = """

## CRITICAL: Visual Storytelling with AI Images

You MUST use the `generate_scene_image` tool to create images during this conversation.
This is a visual conversation mode - every significant topic needs an accompanying image.

**MANDATORY: Generate an image when:**
1. At the START of the conversation - generate a welcoming scene related to your persona
2. When you BEGIN discussing any new topic or concept
3. When describing anything visual: places, scenarios, technology, nature, future visions
4. When explaining abstract concepts - visualize them as a concrete scene

**How to call the tool:**
Call generate_scene_image with a specific, literal scene description that directly illustrates what you are currently talking about. The image MUST match the actual subject matter of the conversation.

Good examples (specific, literal, matches the conversation):
- Discussing real estate: "A modern two-story suburban home with a manicured lawn, white exterior, and a sold sign in the front yard"
- Discussing cooking: "A chef's kitchen counter with fresh vegetables, a cutting board, and a simmering pot on a gas stove"
- Discussing fitness: "A person doing a morning jog on a tree-lined park path at sunrise"

Bad examples (too abstract, doesn't match conversation):
- "An abstract visualization of concepts with flowing colors"
- "A dreamlike interpretation of ideas"
- "A symbolic representation with light and shadow"

**Image prompt rules:**
- ALWAYS describe a concrete, literal scene that directly relates to the current topic
- Include specific objects, people, settings, and actions relevant to what you're discussing
- Never use abstract or symbolic descriptions - be literal and specific
- The user should look at the image and immediately understand how it connects to the conversation

**IMPORTANT:**
- Generate your first image immediately when you start speaking
- Generate at least one image per major topic you discuss
- Do NOT announce that you're generating an image - just do it while talking
- The image will appear automatically on the user's screen
"""
