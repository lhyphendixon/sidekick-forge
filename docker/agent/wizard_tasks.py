"""
Wizard Tasks using LiveKit TaskGroup

This module implements the sidekick onboarding wizard as a series of AgentTask
classes, leveraging LiveKit's TaskGroup for ordered multi-step flows with
built-in regression support.

Each wizard step is a separate task with focused tools, making the LLM's
job simpler and the flow more predictable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from livekit import rtc
from livekit.agents import AgentTask, function_tool, RunContext
from livekit.agents.beta.workflows import TaskGroup

# For LLM-based personality assessment
try:
    from groq import AsyncGroq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    AsyncGroq = None

logger = logging.getLogger(__name__)


# =============================================================================
# LLM-Based Personality Assessment
# =============================================================================

async def assess_personality_with_llm(
    collected_traits: Dict[str, str],
    sidekick_name: str
) -> Dict[str, int]:
    """
    Use an LLM to assess Big Five personality trait scores (0-100) based on
    user descriptions collected during the wizard.

    Args:
        collected_traits: Dictionary with trait descriptions (openness, conscientiousness, etc.)
        sidekick_name: Name of the sidekick being created

    Returns:
        Dictionary with trait scores from 0-100:
        - openness: 0 (practical) to 100 (creative)
        - conscientiousness: 0 (flexible) to 100 (organized)
        - extraversion: 0 (reserved) to 100 (outgoing)
        - agreeableness: 0 (direct) to 100 (warm)
        - neuroticism: 0 (calm/stable) to 100 (sensitive/reactive)
    """
    # Default scores if LLM call fails
    default_scores = {
        "openness": 50,
        "conscientiousness": 50,
        "extraversion": 50,
        "agreeableness": 50,
        "neuroticism": 50,
    }

    if not GROQ_AVAILABLE:
        logger.warning("Groq SDK not available, using keyword-based scoring")
        return _keyword_based_scoring(collected_traits)

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        logger.warning("GROQ_API_KEY not set, using keyword-based scoring")
        return _keyword_based_scoring(collected_traits)

    # Build the assessment prompt
    trait_summary = []
    if collected_traits.get("openness"):
        trait_summary.append(f"- Openness: User described as '{collected_traits['openness']}'")
    if collected_traits.get("conscientiousness"):
        trait_summary.append(f"- Conscientiousness: User described as '{collected_traits['conscientiousness']}'")
    if collected_traits.get("extraversion"):
        trait_summary.append(f"- Extraversion: User described as '{collected_traits['extraversion']}'")
    if collected_traits.get("agreeableness"):
        trait_summary.append(f"- Agreeableness: User described as '{collected_traits['agreeableness']}'")
    if collected_traits.get("emotional_stability"):
        trait_summary.append(f"- Emotional Stability: User described as '{collected_traits['emotional_stability']}'")
    if collected_traits.get("communication_style"):
        trait_summary.append(f"- Communication Style: '{collected_traits['communication_style']}'")
    if collected_traits.get("expertise_focus"):
        trait_summary.append(f"- Expertise: '{collected_traits['expertise_focus']}'")
    if collected_traits.get("humor_style"):
        trait_summary.append(f"- Humor: '{collected_traits['humor_style']}'")

    traits_text = "\n".join(trait_summary) if trait_summary else "No specific traits provided."

    system_prompt = """You are a personality psychologist expert in the Big Five (OCEAN) personality model.
Your task is to convert natural language personality descriptions into precise numerical scores.

SCORING SCALE (0-100):
- Openness: 0 = practical/conventional, 100 = creative/imaginative
- Conscientiousness: 0 = flexible/spontaneous, 100 = organized/detail-oriented
- Extraversion: 0 = reserved/calm, 100 = outgoing/energetic
- Agreeableness: 0 = direct/objective, 100 = warm/nurturing
- Neuroticism: 0 = calm/emotionally stable, 100 = sensitive/emotionally reactive

NOTE: "Emotional Stability" is the INVERSE of Neuroticism. If user says "calm" or "stable", that means LOW neuroticism (closer to 0). If user says "expressive" or "passionate", that means HIGHER neuroticism.

Respond ONLY with valid JSON in this exact format:
{"openness": N, "conscientiousness": N, "extraversion": N, "agreeableness": N, "neuroticism": N}

Where N is an integer from 0-100. No explanation, just the JSON."""

    user_prompt = f"""Assess the Big Five personality traits for an AI sidekick named "{sidekick_name}" based on these user descriptions:

{traits_text}

Return the JSON scores:"""

    try:
        client = AsyncGroq(api_key=groq_api_key)
        response = await client.chat.completions.create(
            model="llama-3.1-8b-instant",  # Fast model for quick assessment
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,  # Low temperature for consistent scoring
            max_tokens=100,
            response_format={"type": "json_object"}
        )

        result_text = response.choices[0].message.content.strip()
        logger.info(f"LLM personality assessment response: {result_text}")

        # Parse the JSON response
        scores = json.loads(result_text)

        # Validate and clamp scores to 0-100
        validated_scores = {}
        for trait in ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]:
            value = scores.get(trait, 50)
            if isinstance(value, (int, float)):
                validated_scores[trait] = max(0, min(100, int(value)))
            else:
                validated_scores[trait] = 50

        logger.info(f"Wizard: LLM assessed personality scores for {sidekick_name}: {validated_scores}")
        return validated_scores

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM response as JSON: {e}, using keyword-based scoring")
        return _keyword_based_scoring(collected_traits)
    except Exception as e:
        logger.warning(f"LLM personality assessment failed: {e}, using keyword-based scoring")
        return _keyword_based_scoring(collected_traits)


def _keyword_based_scoring(collected_traits: Dict[str, str]) -> Dict[str, int]:
    """
    Fallback keyword-based scoring when LLM is unavailable.
    Returns scores on 0-100 scale.
    """
    scores = {
        "openness": 50,
        "conscientiousness": 50,
        "extraversion": 50,
        "agreeableness": 50,
        "neuroticism": 50,
    }

    # Openness scoring
    openness_desc = (collected_traits.get("openness") or "").lower()
    high_openness = ["creative", "imaginative", "curious", "innovative", "artistic", "adventurous"]
    low_openness = ["practical", "down-to-earth", "conventional", "traditional", "realistic"]
    if any(w in openness_desc for w in high_openness):
        scores["openness"] = 80
    elif any(w in openness_desc for w in low_openness):
        scores["openness"] = 30

    # Conscientiousness scoring
    consc_desc = (collected_traits.get("conscientiousness") or "").lower()
    high_consc = ["organized", "thorough", "detail-oriented", "meticulous", "disciplined", "structured"]
    low_consc = ["relaxed", "flexible", "easygoing", "spontaneous", "casual", "laid-back"]
    if any(w in consc_desc for w in high_consc):
        scores["conscientiousness"] = 80
    elif any(w in consc_desc for w in low_consc):
        scores["conscientiousness"] = 30

    # Extraversion scoring
    extra_desc = (collected_traits.get("extraversion") or "").lower()
    high_extra = ["outgoing", "energetic", "enthusiastic", "social", "talkative", "expressive"]
    low_extra = ["reserved", "calm", "quiet", "introverted", "thoughtful", "reflective"]
    if any(w in extra_desc for w in high_extra):
        scores["extraversion"] = 80
    elif any(w in extra_desc for w in low_extra):
        scores["extraversion"] = 30

    # Agreeableness scoring
    agree_desc = (collected_traits.get("agreeableness") or "").lower()
    high_agree = ["warm", "friendly", "nurturing", "caring", "compassionate", "empathetic", "kind"]
    low_agree = ["professional", "neutral", "objective", "direct", "straightforward", "matter-of-fact"]
    if any(w in agree_desc for w in high_agree):
        scores["agreeableness"] = 80
    elif any(w in agree_desc for w in low_agree):
        scores["agreeableness"] = 30

    # Neuroticism scoring (from emotional_stability - inverted)
    emo_desc = (collected_traits.get("emotional_stability") or "").lower()
    high_stability = ["calm", "composed", "steady", "stable", "collected", "unflappable"]
    low_stability = ["expressive", "reactive", "passionate", "emotional", "intense", "sensitive"]
    if any(w in emo_desc for w in high_stability):
        scores["neuroticism"] = 20  # Low neuroticism = high stability
    elif any(w in emo_desc for w in low_stability):
        scores["neuroticism"] = 70  # High neuroticism = more reactive

    return scores


def _score_single_trait(field: str, value: str) -> Optional[Dict[str, int]]:
    """Convert a single trait text description to a numeric slider update dict.

    Returns a dict like {"openness": 80} suitable for merging into personality_traits,
    or None if the field isn't a Big 5 trait.
    """
    val = (value or "").lower()
    if not val:
        return None

    keywords: Dict[str, tuple] = {
        "openness": (
            ["creative", "imaginative", "curious", "innovative", "artistic", "adventurous", "open"],
            ["practical", "down-to-earth", "conventional", "traditional", "realistic"],
        ),
        "conscientiousness": (
            ["organized", "thorough", "detail-oriented", "meticulous", "disciplined", "structured", "diligent"],
            ["relaxed", "flexible", "easygoing", "spontaneous", "casual", "laid-back"],
        ),
        "extraversion": (
            ["outgoing", "energetic", "enthusiastic", "social", "talkative", "expressive", "lively"],
            ["reserved", "calm", "quiet", "introverted", "thoughtful", "reflective"],
        ),
        "agreeableness": (
            ["warm", "friendly", "nurturing", "caring", "compassionate", "empathetic", "kind"],
            ["professional", "neutral", "objective", "direct", "straightforward", "matter-of-fact"],
        ),
        "emotional_stability": (
            ["calm", "composed", "steady", "stable", "collected", "unflappable"],
            ["expressive", "reactive", "passionate", "emotional", "intense", "sensitive"],
        ),
    }

    if field not in keywords:
        return None

    high_words, low_words = keywords[field]
    if any(w in val for w in high_words):
        score = 80
    elif any(w in val for w in low_words):
        score = 30
    else:
        score = 60  # Mild positive for unrecognized descriptors

    # Map emotional_stability -> neuroticism (inverted) for frontend slider
    if field == "emotional_stability":
        return {"neuroticism": 100 - score}
    return {field: score}


# =============================================================================
# UI Event Manager - Handles bidirectional sync with frontend
# =============================================================================

class WizardUIEventManager:
    """
    Manages UI events from the frontend to sync wizard state.

    When a user clicks Continue/Skip in the wizard UI instead of speaking,
    the frontend sends a data message. This manager receives those messages
    and signals the appropriate task to complete.
    """

    def __init__(self, room: rtc.Room):
        self.room = room
        self._step_completion_events: Dict[int, asyncio.Event] = {}
        self._step_data: Dict[int, Dict[str, Any]] = {}
        self._data_callback_registered = False

    def setup_listener(self) -> None:
        """Register the data received callback on the room."""
        if self._data_callback_registered:
            return

        @self.room.on("data_received")
        def on_data_received(data: rtc.DataPacket):
            try:
                message = json.loads(data.data.decode("utf-8"))
                if message.get("type") == "wizard_ui_action":
                    self._handle_ui_action(message.get("data", {}))
            except Exception as e:
                logger.warning(f"Failed to parse UI data message: {e}")

        self._data_callback_registered = True
        logger.info("🔗 Wizard UI event listener registered")

    def _handle_ui_action(self, data: Dict[str, Any]) -> None:
        """Handle a UI action message from the frontend."""
        action = data.get("action")
        step = data.get("step")

        logger.info(f"🖱️ UI action received: action={action}, step={step}, data={data}")

        if action == "step_completed" and step is not None:
            # Store any data from the UI (e.g., voice_id, avatar_prompt)
            if data.get("field_data"):
                self._step_data[step] = data["field_data"]
                logger.info(f"📦 Stored step {step} data from UI: {data['field_data']}")

            # Signal the step completion event
            if step in self._step_completion_events:
                logger.info(f"✅ Signaling step {step} completion from UI click")
                self._step_completion_events[step].set()
            else:
                # Pre-create the event and set it so if the task checks later, it's ready
                event = asyncio.Event()
                event.set()
                self._step_completion_events[step] = event
                logger.info(f"✅ Pre-created and set completion event for step {step}")

    def get_step_event(self, step: int) -> asyncio.Event:
        """Get or create the completion event for a step."""
        if step not in self._step_completion_events:
            self._step_completion_events[step] = asyncio.Event()
        return self._step_completion_events[step]

    def get_step_data(self, step: int) -> Optional[Dict[str, Any]]:
        """Get any data passed from the UI for this step."""
        return self._step_data.get(step)

    def clear_step(self, step: int) -> None:
        """Clear the event and data for a step (for reuse if going back)."""
        if step in self._step_completion_events:
            self._step_completion_events[step].clear()
        if step in self._step_data:
            del self._step_data[step]


# =============================================================================
# Helper Functions
# =============================================================================

async def wait_for_speech_completion(session, timeout: float = 10.0) -> None:
    """
    Wait for any active speech to complete before proceeding.
    This prevents double-speaking when transitioning between wizard steps.

    Args:
        session: The AgentSession instance
        timeout: Maximum seconds to wait for speech completion
    """
    try:
        # Check if there's active speech
        current_speech = getattr(session, 'current_speech', None)
        if current_speech is not None:
            logger.info("🔇 Waiting for current speech to complete before transition...")
            try:
                await asyncio.wait_for(current_speech.wait_for_playout(), timeout=timeout)
                logger.info("✅ Speech completed, proceeding with transition")
                # Extra buffer after speech completes to ensure audio is fully done
                await asyncio.sleep(0.5)
            except asyncio.TimeoutError:
                logger.warning(f"⚠️ Speech wait timed out after {timeout}s, proceeding anyway")
        else:
            # Longer delay when no speech detected - there may be audio in flight
            await asyncio.sleep(1.0)
            logger.info("🔇 No active speech detected, waited 1s buffer before proceeding")
    except Exception as e:
        logger.warning(f"Error waiting for speech completion: {e}")
        await asyncio.sleep(1.0)


# =============================================================================
# Result Dataclasses
# =============================================================================

@dataclass
class NameResult:
    """Result from the name collection step."""
    name: str
    slug: str


@dataclass
class PersonalityResult:
    """Result from the personality collection step."""
    description: str  # Generated summary of personality
    # Big Five personality traits (0-100 scale, matches UI sliders)
    # Keys: openness, conscientiousness, extraversion, agreeableness, neuroticism
    traits: Optional[Dict[str, int]] = None
    # Big Five trait descriptions (from user answers)
    openness: Optional[str] = None  # creative/curious vs practical/conventional
    conscientiousness: Optional[str] = None  # organized/thorough vs relaxed/flexible
    extraversion: Optional[str] = None  # outgoing/energetic vs reserved/calm
    agreeableness: Optional[str] = None  # warm/friendly vs professional/neutral
    emotional_stability: Optional[str] = None  # calm/composed vs expressive/reactive
    # Communication style
    communication_style: Optional[str] = None  # formal/casual, verbose/concise
    # Optional extras
    expertise_focus: Optional[str] = None  # what topics/areas they specialize in
    humor_style: Optional[str] = None  # witty/serious, playful/straightforward
    additional_notes: Optional[str] = None  # anything else the user wants to add


@dataclass
class VoiceResult:
    """Result from the voice selection step."""
    voice_id: str
    voice_provider: str = "cartesia"


@dataclass
class AvatarResult:
    """Result from the avatar generation step."""
    avatar_url: Optional[str] = None
    avatar_prompt: Optional[str] = None
    skipped: bool = False


@dataclass
class KnowledgeResult:
    """Result from the knowledge base step."""
    document_count: int = 0
    website_count: int = 0
    skipped: bool = False


@dataclass
class AbilitiesResult:
    """Result from the abilities selection step."""
    skipped: bool = False


@dataclass
class ConfigResult:
    """Result from the configuration step."""
    config_mode: str = "default"  # "default" or "advanced"
    # Advanced settings (only if config_mode == "advanced")
    stt_provider: Optional[str] = None
    tts_provider: Optional[str] = None
    llm_provider: Optional[str] = None


@dataclass
class WizardResults:
    """Combined results from all wizard steps."""
    name: str
    slug: str
    personality_description: str
    personality_traits: Optional[Dict[str, float]] = None
    voice_id: str = ""
    voice_provider: str = "cartesia"
    avatar_url: Optional[str] = None
    avatar_prompt: Optional[str] = None
    config_mode: str = "default"
    stt_provider: Optional[str] = None
    tts_provider: Optional[str] = None
    llm_provider: Optional[str] = None
    document_count: int = 0
    website_count: int = 0


# =============================================================================
# Frontend Communication Helper
# =============================================================================

class WizardDataPublisher:
    """Publishes wizard state updates to the frontend via LiveKit data channel
    and persists step/field data to Supabase for resume support."""

    def __init__(self, room: rtc.Room, session_id: Optional[str] = None,
                 supabase_url: Optional[str] = None, supabase_service_key: Optional[str] = None):
        self.room = room
        self.session_id = session_id
        self._supabase = None
        if supabase_url and supabase_service_key:
            try:
                from supabase import create_client
                self._supabase = create_client(supabase_url, supabase_service_key)
                logger.info(f"WizardDataPublisher: Supabase persistence enabled for session {session_id}")
            except Exception as e:
                logger.warning(f"WizardDataPublisher: Supabase persistence unavailable: {e}")

    async def publish(self, message_type: str, data: Dict[str, Any]) -> None:
        """Send a data message to the room."""
        payload = json.dumps({
            "type": message_type,
            "data": data,
            "timestamp": time.time(),
            "session_id": self.session_id,
        }).encode("utf-8")

        try:
            # Use reliable=True for ordered delivery (SDK v1.x API)
            await self.room.local_participant.publish_data(
                payload,
                reliable=True,
            )
            logger.debug(f"Published wizard data: {message_type}")
        except Exception as e:
            logger.error(f"Failed to publish wizard data: {e}")

    async def field_update(self, field: str, value: Any, step: int) -> None:
        """Notify frontend of a field update and persist to DB."""
        await self.publish("wizard_field_update", {
            "field": field,
            "value": value,
            "current_step": step,
        })
        # Persist field data to Supabase for resume support
        if self._supabase and self.session_id:
            try:
                from datetime import datetime, timezone
                # Fetch current step_data, merge in the new field
                result = self._supabase.table("sidekick_wizard_sessions").select("step_data").eq("id", self.session_id).execute()
                step_data = (result.data[0]["step_data"] if result.data and result.data[0].get("step_data") else {}) or {}
                step_data[field] = value
                self._supabase.table("sidekick_wizard_sessions").update({
                    "step_data": step_data,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", self.session_id).execute()
            except Exception as e:
                logger.warning(f"WizardDataPublisher: Failed to persist field {field}: {e}")

    async def step_change(self, step: int, total: int, direction: str = "next") -> None:
        """Notify frontend of a step change and persist to DB."""
        await self.publish("wizard_step_change", {
            "direction": direction,
            "current_step": step,
            "total_steps": total,
        })
        # Persist current_step to Supabase for resume support
        if self._supabase and self.session_id:
            try:
                from datetime import datetime, timezone
                self._supabase.table("sidekick_wizard_sessions").update({
                    "current_step": step,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", self.session_id).execute()
                logger.info(f"WizardDataPublisher: Persisted current_step={step} to DB")
            except Exception as e:
                logger.warning(f"WizardDataPublisher: Failed to persist step: {e}")

    async def complete(self, results: WizardResults) -> None:
        """Notify frontend that the wizard is complete."""
        await self.publish("wizard_complete", {
            "ready_to_submit": True,
            "form_data": {
                "name": results.name,
                "slug": results.slug,
                "personality_description": results.personality_description,
                "personality_traits": results.personality_traits,
                "voice_id": results.voice_id,
                "voice_provider": results.voice_provider,
                "avatar_url": results.avatar_url,
                "config_mode": results.config_mode,
            },
        })


# =============================================================================
# Utility Functions
# =============================================================================

def generate_slug(name: str) -> str:
    """Generate a URL-safe slug from a name."""
    slug = name.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip('-')
    return slug or "sidekick"


# =============================================================================
# Wizard Tasks
# =============================================================================

class NameTask(AgentTask[NameResult]):
    """Step 1: Collect the sidekick's name."""

    def __init__(self, publisher: WizardDataPublisher, chat_ctx=None):
        super().__init__(
            instructions="""
            You are collecting the name for the user's AI sidekick.

            CRITICAL RULES:
            1. NEVER call confirm_and_continue in the same response as set_name
            2. You need TWO separate user messages: one with the name, one with confirmation
            3. confirm_and_continue requires the EXACT words the user said to confirm

            FLOW (two separate turns):
            Turn 1: User says a name (e.g., "Herman") -> call set_name -> you ask "Is Herman correct?"
            Turn 2: User says yes/correct -> call confirm_and_continue with their exact words

            Do NOT speak after confirm_and_continue completes.
            """,
            chat_ctx=chat_ctx,
        )
        self.publisher = publisher
        self._pending_name: Optional[str] = None
        self._pending_slug: Optional[str] = None
        self._last_set_time: float = 0

    async def on_enter(self) -> None:
        await wait_for_speech_completion(self.session, timeout=3.0)
        # Use say() instead of generate_reply() to prevent LLM from calling tools
        await self.session.say("What would you like to name your sidekick?", allow_interruptions=False)

    @function_tool
    async def set_name(self, context: RunContext, name: str) -> str:
        """
        Record the name the user said. Only call this when you hear the user say a name.

        Args:
            name: The name for the sidekick that the USER said (e.g., "Herman", "Luna", "Max")
        """
        slug = generate_slug(name)
        logger.info(f"Wizard: Name set to '{name}' (slug: {slug})")

        self._pending_name = name
        self._pending_slug = slug
        self._last_set_time = time.time()

        await self.publisher.field_update("name", name, step=1)

        return f"Ask: Is {name} correct? ONE short sentence only."

    @function_tool
    async def confirm_and_continue(self, context: RunContext, user_said: str) -> Optional[str]:
        """
        ONLY call this after you asked the confirmation question AND the user replied with 'yes', 'correct', etc.
        Do NOT call this in the same turn as set_name. Returns None to suppress speech.

        Args:
            user_said: The EXACT words the user spoke to confirm (e.g., "yes", "that's right", "yep")
        """
        if not self._pending_name:
            return "Error: No name set yet. Call set_name first, then ask user to confirm."

        # CRITICAL: Reject if called too soon after set_name (same turn detection)
        time_since_set = time.time() - self._last_set_time
        if time_since_set < 3.0:  # Must wait at least 3 seconds
            logger.warning(f"Wizard: confirm_and_continue called too soon ({time_since_set:.1f}s after set_name) - rejecting")
            return (
                "ERROR: You called confirm_and_continue too quickly. "
                "You MUST wait for the user to respond after asking your confirmation question. "
                f"Ask the user: 'Is {self._pending_name} correct?' and WAIT for them to say yes."
            )

        # Check if user_said sounds like confirmation
        # Extended list includes common affirmative responses and variations
        confirmation_words = [
            # Basic affirmatives
            "yes", "yeah", "yep", "yup", "yea", "ya",
            # Sounds/utterances
            "uh-huh", "uh huh", "mm-hmm", "mm hmm", "mhm", "mmm",
            # Correctness
            "correct", "right", "exactly", "precisely",
            # Agreement
            "sure", "ok", "okay", "k", "alright", "all right",
            # Positive evaluation
            "good", "great", "perfect", "fine", "nice", "awesome", "cool",
            # Phrases (checked via 'in' so partial matches work)
            "sounds good", "that's right", "that's correct", "that's it",
            "it is", "that is", "she is", "he is",  # "It is!" type confirmations
            "you got it", "you got her", "you got him",
            "that's the one", "bingo", "nailed it",
            # Formal
            "confirm", "confirmed", "affirmative", "absolutely",
            "definitely", "certainly", "indeed",
            # Casual
            "for sure", "totally", "works for me", "love it",
            # Action
            "go ahead", "proceed", "continue", "next", "let's go", "move on"
        ]
        user_lower = user_said.lower().strip()
        is_confirmation = any(word in user_lower for word in confirmation_words)

        logger.info(f"Wizard: confirm_and_continue called with user_said='{user_said}', is_confirmation={is_confirmation}")

        if not is_confirmation:
            logger.warning(f"Wizard: '{user_said}' not recognized as confirmation, asking again")
            return f"I didn't quite catch that. Is {self._pending_name} correct? Just say yes or no."

        logger.info(f"Wizard: Name '{self._pending_name}' confirmed with '{user_said}', completing step")
        self.complete(NameResult(name=self._pending_name, slug=self._pending_slug))
        return None  # Return None to suppress LLM response


class PersonalityTask(AgentTask[PersonalityResult]):
    """
    Step 2: Collect the sidekick's personality using structured Big 5 questions.

    This task gathers personality through 7 guided questions:
    1. Openness - creative/curious vs practical/conventional
    2. Conscientiousness - organized/thorough vs relaxed/flexible
    3. Extraversion - outgoing/energetic vs reserved/calm
    4. Agreeableness - warm/friendly vs professional/neutral
    5. Emotional Stability - calm/composed vs expressive/reactive
    6. Communication Style - formal/casual, verbose/concise
    7. Anything Else? - optional extras (expertise, humor, etc.)

    Uses the Unordered Collection pattern from LiveKit - user can answer in any order,
    but Farah guides them through the questions systematically.
    """

    STEP_NUMBER = 2  # Used for UI event sync

    # Big 5 traits + communication style are required
    REQUIRED_FIELDS = {
        "openness",
        "conscientiousness",
        "extraversion",
        "agreeableness",
        "emotional_stability",
        "communication_style",
    }
    # Optional extras from "anything else?" question
    OPTIONAL_FIELDS = {"expertise_focus", "humor_style", "additional_notes"}

    # Question order for Farah to follow
    QUESTION_ORDER = [
        "openness",
        "conscientiousness",
        "extraversion",
        "agreeableness",
        "emotional_stability",
        "communication_style",
        "anything_else",  # Special - handles optional fields
    ]

    def __init__(
        self,
        publisher: WizardDataPublisher,
        sidekick_name: str,
        chat_ctx=None,
        ui_event_manager: Optional[WizardUIEventManager] = None,
    ):
        super().__init__(
            instructions=f"""
            You are collecting personality details for {sidekick_name} through guided questions about 6 traits.

            TRAITS TO COLLECT:
            1. OPENNESS: creative/imaginative vs practical/down-to-earth
            2. CONSCIENTIOUSNESS: organized/detail-oriented vs relaxed/flexible
            3. EXTRAVERSION: outgoing/energetic vs reserved/calm
            4. AGREEABLENESS: warm/nurturing vs professional/neutral
            5. EMOTIONAL STABILITY: calm under pressure vs emotionally expressive
            6. COMMUNICATION STYLE: formal/casual, detailed/concise
            7. ANYTHING ELSE: optional extras (expertise, humor, etc.)

            PRE-COLLECTION FROM INITIAL DESCRIPTION:
            - When the user gives a rich initial description (e.g., "creative, outgoing, warm, casual"),
              call analyze_description FIRST to extract any traits already mentioned.
            - This will record matching traits automatically. Then only ask about the REMAINING uncollected traits.
            - If the user gives a short/vague answer, skip analyze_description and ask questions one by one.

            QUESTION FLOW (for remaining uncollected traits):
            - Ask ONE question at a time for each trait not yet collected
            - After EACH user response, call ONLY the ONE matching record_* tool
            - Then SPEAK the next question - do NOT silently call another record tool
            - NEVER call multiple record_* tools in a single turn
            - The tool response tells you what to ask next - SAY that question to the user
            - After all 6 required traits are collected, ask the "anything else?" question
            - If user says "no" or "that's it" for anything else, call skip_anything_else

            MANDATORY CONFIRMATION STEP:
            - After ALL 6 traits are collected AND the "anything else?" question is handled,
              you MUST verbally summarize ALL collected traits to the user.
            - Say something like: "Here's what I have for [name]'s personality: [list all traits]. Does that sound right?"
            - WAIT for the user to confirm (e.g., "yes", "sounds good", "perfect")
            - Only call confirm_personality AFTER the user verbally confirms
            - If the user wants changes, update the relevant trait(s) and re-confirm
            - NEVER call confirm_personality without the user's explicit verbal confirmation

            STYLE:
            - Keep questions conversational and brief
            - Acknowledge each answer briefly before asking the next question
            - If traits were pre-collected, acknowledge what you understood and ask about what's missing
            """,
            chat_ctx=chat_ctx,
        )
        self.publisher = publisher
        self.sidekick_name = sidekick_name
        self._collected: Dict[str, str] = {}
        self._asked_anything_else = False
        self._user_confirmed = False
        self._ui_event_manager = ui_event_manager
        self._ui_watcher_task: Optional[asyncio.Task] = None

    async def on_enter(self) -> None:
        await wait_for_speech_completion(self.session, timeout=3.0)

        # Start watching for UI completion in the background
        if self._ui_event_manager:
            self._ui_watcher_task = asyncio.create_task(self._watch_ui_completion())

        # Start with openness question
        await self.session.say(
            f"Now let's shape {self.sidekick_name}'s personality. Should they be creative and imaginative, or more practical and down-to-earth?",
            allow_interruptions=False
        )

    async def _watch_ui_completion(self) -> None:
        """Background task that watches for UI-triggered step completion."""
        try:
            ui_event = self._ui_event_manager.get_step_event(self.STEP_NUMBER)
            await ui_event.wait()

            # Stop any ongoing speech immediately when user clicks Continue
            if self.session:
                self.session.interrupt()

            # UI submitted personality data directly
            ui_data = self._ui_event_manager.get_step_data(self.STEP_NUMBER) or {}
            description = ui_data.get("personality_description", "A helpful AI assistant")

            logger.info(f"🖱️ PersonalityTask completing via UI: description={description[:50]}...")

            self.complete(PersonalityResult(
                description=description,
                openness=ui_data.get("openness"),
                conscientiousness=ui_data.get("conscientiousness"),
                extraversion=ui_data.get("extraversion"),
                agreeableness=ui_data.get("agreeableness"),
                emotional_stability=ui_data.get("emotional_stability"),
                communication_style=ui_data.get("communication_style"),
                expertise_focus=ui_data.get("expertise_focus"),
                humor_style=ui_data.get("humor_style"),
                additional_notes=ui_data.get("additional_notes"),
            ))

        except asyncio.CancelledError:
            logger.debug("UI watcher task cancelled (voice completion happened first)")
        except Exception as e:
            logger.warning(f"Error in UI watcher: {e}")

    async def on_exit(self) -> None:
        """Cancel the UI watcher when exiting the task."""
        if self._ui_watcher_task and not self._ui_watcher_task.done():
            self._ui_watcher_task.cancel()
            try:
                await self._ui_watcher_task
            except asyncio.CancelledError:
                pass

    def _get_missing_required(self) -> set:
        """Return the set of required fields not yet collected."""
        return self.REQUIRED_FIELDS - set(self._collected.keys())

    def _get_next_question(self) -> str:
        """Get the next question to ask based on what's been collected."""
        for field in self.QUESTION_ORDER:
            if field == "anything_else":
                # Check if we're ready for the "anything else" question
                if not self._get_missing_required() and not self._asked_anything_else:
                    return "anything_else"
            elif field not in self._collected:
                return field
        return "done"

    def _build_next_prompt(self) -> str:
        """Build the prompt for the next question based on what's missing."""
        next_q = self._get_next_question()

        prompts = {
            "openness": f"Ask about {self.sidekick_name}'s openness - creative/imaginative or practical/down-to-earth?",
            "conscientiousness": f"Ask about {self.sidekick_name}'s work style - organized/detail-oriented or relaxed/flexible?",
            "extraversion": f"Ask about {self.sidekick_name}'s energy - outgoing/energetic or reserved/calm?",
            "agreeableness": f"Ask about {self.sidekick_name}'s warmth - warm/nurturing or professional/neutral?",
            "emotional_stability": f"Ask about {self.sidekick_name}'s emotional style - calm under pressure or more expressive?",
            "communication_style": f"Ask about {self.sidekick_name}'s speaking style - formal or casual? Detailed or concise?",
            "anything_else": "Ask: 'Anything else? Like expertise areas, humor style, or other preferences?' Keep it brief.",
            "done": "All traits collected! Summarize ALL 6 traits to the user and ask 'Does that sound right?' WAIT for their confirmation before calling confirm_personality.",
        }

        return prompts.get(next_q, prompts["done"])

    async def _update_and_continue(self, field: str, value: str) -> str:
        """Store a collected field and return instructions for next step."""
        self._collected[field] = value
        logger.info(f"Wizard: Recorded {field}='{value}' for {self.sidekick_name}")

        # Publish partial update to frontend (text description)
        await self.publisher.field_update(f"personality_{field}", value, step=2)

        # Publish numeric slider score so the personality engine sliders update in real time
        trait_score = _score_single_trait(field, value)
        if trait_score:
            await self.publisher.field_update("personality_traits", trait_score, step=2)

        # Build response with progress info
        collected_count = len([k for k in self._collected if k in self.REQUIRED_FIELDS])
        total_required = len(self.REQUIRED_FIELDS)

        return f"Recorded! ({collected_count}/{total_required} traits collected). {self._build_next_prompt()}"

    @function_tool
    async def analyze_description(
        self,
        context: RunContext,
        openness: str = "",
        conscientiousness: str = "",
        extraversion: str = "",
        agreeableness: str = "",
        emotional_stability: str = "",
        communication_style: str = "",
    ) -> str:
        """
        Analyze a rich user description and extract any Big 5 traits mentioned.
        Call this when the user provides a detailed initial description that covers multiple traits.
        Only fill in parameters for traits you can clearly identify from what the user said.
        Leave parameters empty for traits not mentioned.

        Args:
            openness: If mentioned - e.g., "creative", "imaginative", "practical" (leave empty if not mentioned)
            conscientiousness: If mentioned - e.g., "organized", "detail-oriented", "relaxed" (leave empty if not mentioned)
            extraversion: If mentioned - e.g., "outgoing", "energetic", "reserved" (leave empty if not mentioned)
            agreeableness: If mentioned - e.g., "warm", "friendly", "professional" (leave empty if not mentioned)
            emotional_stability: If mentioned - e.g., "calm", "composed", "expressive" (leave empty if not mentioned)
            communication_style: If mentioned - e.g., "casual", "formal", "concise" (leave empty if not mentioned)
        """
        extracted = []
        trait_map = {
            "openness": openness,
            "conscientiousness": conscientiousness,
            "extraversion": extraversion,
            "agreeableness": agreeableness,
            "emotional_stability": emotional_stability,
            "communication_style": communication_style,
        }

        for trait, value in trait_map.items():
            if value and value.strip():
                self._collected[trait] = value.strip()
                await self.publisher.field_update(f"personality_{trait}", value.strip(), step=2)
                # Update slider with numeric score
                trait_score = _score_single_trait(trait, value.strip())
                if trait_score:
                    await self.publisher.field_update("personality_traits", trait_score, step=2)
                extracted.append(trait)
                logger.info(f"Wizard: Pre-collected {trait}='{value.strip()}' from initial description")

        collected_count = len([k for k in self._collected if k in self.REQUIRED_FIELDS])
        total_required = len(self.REQUIRED_FIELDS)
        missing = self._get_missing_required()

        if not missing:
            return f"All {total_required} traits extracted! Acknowledge what you understood, then ask the 'anything else?' question."

        return (
            f"Extracted {len(extracted)} traits ({collected_count}/{total_required} collected). "
            f"Still need: {', '.join(sorted(missing))}. "
            f"Acknowledge what you understood, then {self._build_next_prompt()}"
        )

    @function_tool
    async def record_openness(
        self,
        context: RunContext,
        openness: str
    ) -> str:
        """
        Record the sidekick's openness trait (creative vs practical).

        Args:
            openness: Description like "creative", "imaginative", "curious", "practical", "down-to-earth", "conventional"
        """
        return await self._update_and_continue("openness", openness)

    @function_tool
    async def record_conscientiousness(
        self,
        context: RunContext,
        conscientiousness: str
    ) -> str:
        """
        Record the sidekick's conscientiousness trait (organized vs relaxed).

        Args:
            conscientiousness: Description like "organized", "thorough", "detail-oriented", "relaxed", "flexible", "easygoing"
        """
        return await self._update_and_continue("conscientiousness", conscientiousness)

    @function_tool
    async def record_extraversion(
        self,
        context: RunContext,
        extraversion: str
    ) -> str:
        """
        Record the sidekick's extraversion trait (outgoing vs reserved).

        Args:
            extraversion: Description like "outgoing", "energetic", "enthusiastic", "reserved", "calm", "quiet"
        """
        return await self._update_and_continue("extraversion", extraversion)

    @function_tool
    async def record_agreeableness(
        self,
        context: RunContext,
        agreeableness: str
    ) -> str:
        """
        Record the sidekick's agreeableness trait (warm vs professional).

        Args:
            agreeableness: Description like "warm", "friendly", "nurturing", "caring", "professional", "neutral", "objective"
        """
        return await self._update_and_continue("agreeableness", agreeableness)

    @function_tool
    async def record_emotional_stability(
        self,
        context: RunContext,
        emotional_stability: str
    ) -> str:
        """
        Record the sidekick's emotional stability trait (calm vs expressive).

        Args:
            emotional_stability: Description like "calm", "composed", "steady", "stable", "expressive", "reactive", "passionate"
        """
        return await self._update_and_continue("emotional_stability", emotional_stability)

    @function_tool
    async def record_communication_style(
        self,
        context: RunContext,
        style: str,
        detail_level: str = ""
    ) -> str:
        """
        Record how the sidekick communicates.

        Args:
            style: Communication tone - e.g., "formal", "casual", "professional", "friendly"
            detail_level: How verbose - e.g., "detailed", "concise", "thorough", "brief" (optional)
        """
        combined = style
        if detail_level:
            combined = f"{style}, {detail_level}"
        return await self._update_and_continue("communication_style", combined)

    @function_tool
    async def record_anything_else(
        self,
        context: RunContext,
        expertise: str = "",
        humor: str = "",
        notes: str = ""
    ) -> str:
        """
        Record optional extras from the "anything else?" question.
        Call this after user responds to the final optional question.

        Args:
            expertise: Areas of knowledge (e.g., "technology", "cooking", "fitness") - optional
            humor: Humor style (e.g., "witty", "playful", "serious") - optional
            notes: Any other preferences the user mentioned - optional
        """
        self._asked_anything_else = True

        if expertise:
            self._collected["expertise_focus"] = expertise
            await self.publisher.field_update("personality_expertise_focus", expertise, step=2)
            logger.info(f"Wizard: Recorded expertise_focus='{expertise}' for {self.sidekick_name}")

        if humor:
            self._collected["humor_style"] = humor
            await self.publisher.field_update("personality_humor_style", humor, step=2)
            logger.info(f"Wizard: Recorded humor_style='{humor}' for {self.sidekick_name}")

        if notes:
            self._collected["additional_notes"] = notes
            await self.publisher.field_update("personality_additional_notes", notes, step=2)
            logger.info(f"Wizard: Recorded additional_notes='{notes}' for {self.sidekick_name}")

        extras_recorded = sum(1 for x in [expertise, humor, notes] if x)
        if extras_recorded > 0:
            return f"Recorded {extras_recorded} extra preference(s)! Now summarize ALL collected traits to the user and ask 'Does that sound right?' Wait for their confirmation."
        else:
            return "Got it, no extras. Now summarize ALL collected traits to the user and ask 'Does that sound right?' Wait for their confirmation."

    @function_tool
    async def skip_anything_else(self, context: RunContext, skip: bool = True) -> str:
        """
        Skip the "anything else?" question if user says no/that's it/done.

        Args:
            skip: Always True when called
        """
        self._asked_anything_else = True
        return "Understood! Now summarize ALL collected traits to the user and ask 'Does that sound right?' Wait for their confirmation before calling confirm_personality."

    @function_tool
    async def confirm_personality(self, context: RunContext, user_said_yes: bool = True) -> Optional[str]:
        """
        Complete the personality step. ONLY call this AFTER you have:
        1. Collected ALL 6 required traits
        2. Asked the "anything else?" question
        3. Verbally summarized all traits to the user
        4. The user has explicitly confirmed (said "yes", "sounds good", "perfect", etc.)

        DO NOT call this until the user has verbally confirmed the summary.

        Args:
            user_said_yes: True if the user confirmed the personality summary
        """
        missing_required = self._get_missing_required()
        if missing_required:
            return f"Cannot confirm yet. Still need: {', '.join(sorted(missing_required))}. Ask about each missing trait one by one."

        if not self._asked_anything_else:
            return "You haven't asked the 'anything else?' question yet. Ask it before confirming."

        # Build description from collected traits
        parts = []
        if self._collected.get("openness"):
            parts.append(f"is {self._collected['openness']}")
        if self._collected.get("conscientiousness"):
            parts.append(f"{self._collected['conscientiousness']}")
        if self._collected.get("extraversion"):
            parts.append(f"{self._collected['extraversion']}")
        if self._collected.get("agreeableness"):
            parts.append(f"{self._collected['agreeableness']}")
        if self._collected.get("emotional_stability"):
            parts.append(f"emotionally {self._collected['emotional_stability']}")
        if self._collected.get("communication_style"):
            parts.append(f"communicates in a {self._collected['communication_style']} style")
        if self._collected.get("expertise_focus"):
            parts.append(f"specializes in {self._collected['expertise_focus']}")
        if self._collected.get("humor_style"):
            parts.append(f"uses {self._collected['humor_style']} humor")

        description = f"{self.sidekick_name} " + ", ".join(parts) + "."

        # Use LLM to assess Big Five trait scores (0-100 scale) from collected descriptions
        traits = await assess_personality_with_llm(self._collected, self.sidekick_name)

        logger.info(f"Wizard: Personality confirmed: {description}")
        logger.info(f"Wizard: Assessed personality traits (0-100): {traits}")

        # Publish description and trait scores to frontend
        await self.publisher.field_update("personality_description", description, step=2)
        await self.publisher.field_update("personality_traits", traits, step=2)

        self.complete(PersonalityResult(
            description=description,
            traits=traits,
            openness=self._collected.get("openness"),
            conscientiousness=self._collected.get("conscientiousness"),
            extraversion=self._collected.get("extraversion"),
            agreeableness=self._collected.get("agreeableness"),
            emotional_stability=self._collected.get("emotional_stability"),
            communication_style=self._collected.get("communication_style"),
            expertise_focus=self._collected.get("expertise_focus"),
            humor_style=self._collected.get("humor_style"),
            additional_notes=self._collected.get("additional_notes"),
        ))
        return None  # Suppress LLM speech after completion


class VoiceTask(AgentTask[VoiceResult]):
    """Step 3: Select the sidekick's voice."""

    STEP_NUMBER = 3  # Used for UI event sync

    def __init__(
        self,
        publisher: WizardDataPublisher,
        sidekick_name: str,
        chat_ctx=None,
        ui_event_manager: Optional[WizardUIEventManager] = None,
    ):
        super().__init__(
            instructions=f"""
            You are helping choose a voice for {sidekick_name}. The frontend shows voice samples.

            CRITICAL RULES:
            1. NEVER call confirm_and_continue in the same response as set_voice
            2. You need TWO separate user messages: one with the selection, one with confirmation
            3. confirm_and_continue requires the EXACT words the user said to confirm
            4. If user says "skip" -> call use_default_voice (no confirmation needed)

            FLOW (two separate turns):
            Turn 1: User selects a voice -> call set_voice -> you ask "Is this voice good?"
            Turn 2: User says yes/correct -> call confirm_and_continue with their exact words

            Do NOT speak after confirm_and_continue or use_default_voice completes.
            """,
            chat_ctx=chat_ctx,
        )
        self.publisher = publisher
        self.sidekick_name = sidekick_name
        self._pending_voice_id: Optional[str] = None
        self._pending_voice_provider: str = "cartesia"
        self._last_set_time: float = 0
        self._ui_event_manager = ui_event_manager
        self._ui_watcher_task: Optional[asyncio.Task] = None

    async def on_enter(self) -> None:
        await wait_for_speech_completion(self.session, timeout=3.0)

        # Start watching for UI completion in the background
        if self._ui_event_manager:
            self._ui_watcher_task = asyncio.create_task(self._watch_ui_completion())

        # Use say() instead of generate_reply() to prevent LLM from calling tools
        await self.session.say("Pick a voice from the options on screen, or say skip.", allow_interruptions=False)

    async def _watch_ui_completion(self) -> None:
        """Background task that watches for UI-triggered step completion."""
        try:
            ui_event = self._ui_event_manager.get_step_event(self.STEP_NUMBER)
            await ui_event.wait()

            # Stop any ongoing speech immediately when user clicks Continue
            if self.session:
                self.session.interrupt()

            # UI clicked Continue - get data from UI
            ui_data = self._ui_event_manager.get_step_data(self.STEP_NUMBER) or {}
            voice_id = ui_data.get("voice_id", "a0e99841-438c-4a64-b679-ae501e7d6091")  # Default voice
            voice_provider = ui_data.get("voice_provider", "cartesia")

            logger.info(f"🖱️ VoiceTask completing via UI click: voice_id={voice_id}")

            # Complete the task silently (no voice response needed)
            self.complete(VoiceResult(voice_id=voice_id, voice_provider=voice_provider))

        except asyncio.CancelledError:
            logger.debug("UI watcher task cancelled (voice completion happened first)")
        except Exception as e:
            logger.warning(f"Error in UI watcher: {e}")

    async def on_exit(self) -> None:
        """Cancel the UI watcher when exiting the task."""
        if self._ui_watcher_task and not self._ui_watcher_task.done():
            self._ui_watcher_task.cancel()
            try:
                await self._ui_watcher_task
            except asyncio.CancelledError:
                pass

    @function_tool
    async def set_voice(self, context: RunContext, voice_id: str, voice_provider: str = "cartesia") -> str:
        """
        Record the voice selection. Returns a confirmation question.

        Args:
            voice_id: The ID of the selected voice
            voice_provider: The voice provider (default: cartesia)
        """
        logger.info(f"Wizard: Voice set to '{voice_id}' ({voice_provider})")

        self._pending_voice_id = voice_id
        self._pending_voice_provider = voice_provider
        self._last_set_time = time.time()
        await self.publisher.field_update("voice_id", voice_id, step=3)

        return "Ask: Is this voice good? ONE short sentence."

    @function_tool
    async def confirm_and_continue(self, context: RunContext, user_said: str) -> Optional[str]:
        """
        ONLY call this after you asked the confirmation question AND the user replied with 'yes', 'correct', etc.
        Do NOT call this in the same turn as set_voice. Returns None to suppress speech.

        Args:
            user_said: The EXACT words the user spoke to confirm (e.g., "yes", "that's right", "yep")
        """
        if not self._pending_voice_id:
            return "Error: No voice selected yet. Call set_voice first, then ask user to confirm."

        # CRITICAL: Reject if called too soon after set_voice (same turn detection)
        time_since_set = time.time() - self._last_set_time
        if time_since_set < 3.0:
            logger.warning(f"Wizard: confirm_and_continue called too soon ({time_since_set:.1f}s after set_voice) - rejecting")
            return (
                "ERROR: You called confirm_and_continue too quickly. "
                "You MUST wait for the user to respond after asking your confirmation question. "
                "Ask the user: 'Is this voice good?' and WAIT for them to say yes."
            )

        # Check if user_said sounds like confirmation
        # Extended list includes common affirmative responses and variations
        confirmation_words = [
            # Basic affirmatives
            "yes", "yeah", "yep", "yup", "yea", "ya",
            # Sounds/utterances
            "uh-huh", "uh huh", "mm-hmm", "mm hmm", "mhm", "mmm",
            # Correctness
            "correct", "right", "exactly", "precisely",
            # Agreement
            "sure", "ok", "okay", "k", "alright", "all right",
            # Positive evaluation
            "good", "great", "perfect", "fine", "nice", "awesome", "cool",
            # Phrases (checked via 'in' so partial matches work)
            "sounds good", "that's right", "that's correct", "that's it",
            "it is", "that is", "it does", "that does",  # "It is!" / "That does!" type confirmations
            "you got it", "nailed it", "bingo", "love it", "like it",
            # Formal
            "confirm", "confirmed", "affirmative", "absolutely",
            "definitely", "certainly", "indeed",
            # Casual
            "for sure", "totally", "works for me",
            # Action
            "go ahead", "proceed", "continue", "next", "let's go", "move on"
        ]
        user_lower = user_said.lower().strip()
        is_confirmation = any(word in user_lower for word in confirmation_words)

        logger.info(f"Wizard: confirm_and_continue called with user_said='{user_said}', is_confirmation={is_confirmation}")

        if not is_confirmation:
            logger.warning(f"Wizard: '{user_said}' not recognized as confirmation, asking again")
            return "I didn't quite catch that. Is this voice good? Just say yes or no."

        logger.info(f"Wizard: Voice confirmed with '{user_said}', completing step")
        self.complete(VoiceResult(voice_id=self._pending_voice_id, voice_provider=self._pending_voice_provider))
        return None  # Return None to suppress LLM response

    @function_tool
    async def use_default_voice(self, context: RunContext, skip: bool = True) -> None:
        """
        Skip voice selection. Complete silently - do NOT speak after this.

        Args:
            skip: Always True when called (required for Groq schema compatibility)
        """
        logger.info(f"Wizard: Using default voice, completing step")

        default_voice_id = "a0e99841-438c-4a64-b679-ae501e7d6091"
        await self.publisher.field_update("voice_id", default_voice_id, step=3)

        self.complete(VoiceResult(voice_id=default_voice_id, voice_provider="cartesia"))


class AvatarTask(AgentTask[AvatarResult]):
    """Step 4: Generate or skip the sidekick's avatar."""

    STEP_NUMBER = 4  # Used for UI event sync

    def __init__(
        self,
        publisher: WizardDataPublisher,
        sidekick_name: str,
        personality: str,
        chat_ctx=None,
        ui_event_manager: Optional[WizardUIEventManager] = None,
    ):
        super().__init__(
            instructions=f"""
            You are helping create an avatar for {sidekick_name}.

            FLOW:
            1. User describes the avatar -> call generate_avatar (this auto-generates images on screen)
            2. Tell user avatars are being generated. They can pick by number or ask for more.
            3. WAIT for user response. They will either:
               a. Pick a number ("number 3", "the second one", "I like 2") -> call select_avatar_by_number
               b. Ask for more ("generate another", "try again", "more", "different ones", "another") -> call regenerate_avatars
               c. Refine ("make it more blue", "try something darker") -> call regenerate_avatars with the new description
               d. Say "skip" -> call skip_avatar
            4. Repeat steps 2-3 until user picks one or skips

            CRITICAL RULES:
            - NEVER call select_avatar_by_number in the same turn as generate_avatar or regenerate_avatars
            - When user says "another", "more", "again", "different", "try again", "new ones", "generate more" -> call regenerate_avatars
            - Do NOT call confirm_and_continue or select_avatar_by_number when user asks for more avatars
            - Only complete the step when user picks a specific number or says skip
            - Do NOT speak after select_avatar_by_number or skip_avatar completes
            """,
            chat_ctx=chat_ctx,
        )
        self.publisher = publisher
        self.sidekick_name = sidekick_name
        self._pending_prompt: Optional[str] = None
        self._last_set_time: float = 0
        self._ui_event_manager = ui_event_manager
        self._ui_watcher_task: Optional[asyncio.Task] = None

    async def on_enter(self) -> None:
        await wait_for_speech_completion(self.session, timeout=3.0)

        # Start watching for UI completion in the background
        if self._ui_event_manager:
            self._ui_watcher_task = asyncio.create_task(self._watch_ui_completion())

        # Use say() instead of generate_reply() to prevent LLM from calling tools
        await self.session.say(f"Describe an avatar for {self.sidekick_name}, or say skip.", allow_interruptions=False)

    async def _watch_ui_completion(self) -> None:
        """Background task that watches for UI-triggered step completion."""
        try:
            ui_event = self._ui_event_manager.get_step_event(self.STEP_NUMBER)
            await ui_event.wait()

            # Stop any ongoing speech immediately when user clicks Continue
            if self.session:
                self.session.interrupt()

            # UI clicked Continue/Skip - get data from UI
            ui_data = self._ui_event_manager.get_step_data(self.STEP_NUMBER) or {}
            avatar_prompt = ui_data.get("avatar_prompt")
            skipped = ui_data.get("skipped", avatar_prompt is None)

            logger.info(f"🖱️ AvatarTask completing via UI click: skipped={skipped}, prompt={avatar_prompt}")

            # Complete the task silently (no voice response needed)
            self.complete(AvatarResult(avatar_prompt=avatar_prompt, skipped=skipped))

        except asyncio.CancelledError:
            logger.debug("UI watcher task cancelled (voice completion happened first)")
        except Exception as e:
            logger.warning(f"Error in UI watcher: {e}")

    async def on_exit(self) -> None:
        """Cancel the UI watcher when exiting the task."""
        if self._ui_watcher_task and not self._ui_watcher_task.done():
            self._ui_watcher_task.cancel()
            try:
                await self._ui_watcher_task
            except asyncio.CancelledError:
                pass

    @function_tool
    async def generate_avatar(self, context: RunContext, description: str) -> str:
        """
        Record the avatar description, populate the frontend text field, and trigger avatar generation.
        After calling this, tell the user you're generating avatars and ask them to pick one they like.

        Args:
            description: Visual description for the avatar (e.g., "friendly robot with blue eyes")
        """
        logger.info(f"Wizard: Avatar prompt set to '{description[:50]}...'")

        self._pending_prompt = description
        self._last_set_time = time.time()

        # Populate the frontend text field
        await self.publisher.field_update("avatar_prompt", description, step=4)

        # Trigger auto-generation on the frontend
        await self.publisher.publish("wizard_avatar_generate", {
            "prompt": description,
            "current_step": 4,
        })

        return (
            "Avatar generation triggered! Tell the user: 'I'm generating some avatars based on that description. "
            "Once they appear, you can pick one by saying its number, like \"I like number 3\", "
            "or ask me to generate more.' Keep it brief and conversational."
        )

    @function_tool
    async def select_avatar_by_number(self, context: RunContext, number: int) -> Optional[str]:
        """
        Select an avatar by its number in the grid (1-based). Call this when the user says
        something like "I like number 3" or "let's go with 2".

        Args:
            number: The avatar number (1-based index in the grid)
        """
        logger.info(f"Wizard: User selected avatar number {number}")

        # Tell frontend to select the avatar at this index
        await self.publisher.publish("wizard_avatar_select", {
            "index": number - 1,  # Convert to 0-based
            "current_step": 4,
        })

        self._pending_prompt = self._pending_prompt or ""
        self.complete(AvatarResult(avatar_prompt=self._pending_prompt))
        return None  # Suppress speech - frontend handles selection

    @function_tool
    async def regenerate_avatars(self, context: RunContext, new_description: str = "") -> str:
        """
        Generate a new batch of avatars. Call when the user wants to try again or refine.
        Optionally update the description if the user provided a new one.

        Args:
            new_description: Updated description if user wants to change it (leave empty to reuse current)
        """
        if new_description and new_description.strip():
            self._pending_prompt = new_description.strip()
            await self.publisher.field_update("avatar_prompt", self._pending_prompt, step=4)

        prompt = self._pending_prompt or ""
        logger.info(f"Wizard: Regenerating avatars with prompt '{prompt[:50]}...'")

        await self.publisher.publish("wizard_avatar_generate", {
            "prompt": prompt,
            "current_step": 4,
        })

        return "Generating new avatars! Tell the user new avatars are on the way. They can pick by number or ask for more."

    @function_tool
    async def confirm_and_continue(self, context: RunContext, user_said: str) -> Optional[str]:
        """
        ONLY call this after the user confirms they're happy with their avatar selection.
        Do NOT call this in the same turn as generate_avatar. Returns None to suppress speech.

        Args:
            user_said: The EXACT words the user spoke to confirm (e.g., "yes", "that's right", "yep")
        """
        if not self._pending_prompt:
            return "Error: No avatar description yet. Call generate_avatar first, then ask user to confirm."

        # CRITICAL: Reject if called too soon after generate_avatar (same turn detection)
        time_since_set = time.time() - self._last_set_time
        if time_since_set < 3.0:
            logger.warning(f"Wizard: confirm_and_continue called too soon ({time_since_set:.1f}s after generate_avatar) - rejecting")
            return (
                "ERROR: You called confirm_and_continue too quickly. "
                "You MUST wait for the user to respond after asking your confirmation question. "
                "Ask the user: 'Does that sound right?' and WAIT for them to say yes."
            )

        # Check if user_said sounds like confirmation
        confirmation_words = [
            "yes", "yeah", "yep", "yup", "yea", "ya",
            "uh-huh", "uh huh", "mm-hmm", "mm hmm", "mhm", "mmm",
            "correct", "right", "exactly", "precisely",
            "sure", "ok", "okay", "k", "alright", "all right",
            "good", "great", "perfect", "fine", "nice", "awesome", "cool",
            "sounds good", "that's right", "that's correct", "that's it",
            "it is", "that is", "it does", "that does",
            "you got it", "nailed it", "bingo", "love it", "like it",
            "confirm", "confirmed", "affirmative", "absolutely",
            "definitely", "certainly", "indeed",
            "for sure", "totally", "works for me",
            "go ahead", "proceed", "continue", "next", "let's go", "move on"
        ]
        user_lower = user_said.lower().strip()
        is_confirmation = any(word in user_lower for word in confirmation_words)

        logger.info(f"Wizard: confirm_and_continue called with user_said='{user_said}', is_confirmation={is_confirmation}")

        if not is_confirmation:
            logger.warning(f"Wizard: '{user_said}' not recognized as confirmation, asking again")
            return "I didn't quite catch that. Does this sound right? Just say yes or no."

        logger.info(f"Wizard: Avatar confirmed with '{user_said}', completing step")
        self.complete(AvatarResult(avatar_prompt=self._pending_prompt))
        return None  # Return None to suppress LLM response

    @function_tool
    async def skip_avatar(self, context: RunContext, skip: bool = True) -> None:
        """
        Skip avatar generation. Complete silently - do NOT speak after this.

        Args:
            skip: Always True when called (required for Groq schema compatibility)
        """
        logger.info(f"Wizard: Avatar generation skipped, completing step")
        self.complete(AvatarResult(skipped=True))


class AbilitiesTask(AgentTask[AbilitiesResult]):
    """Step 5: Abilities selection (primarily UI-driven)."""

    STEP_NUMBER = 5  # Used for UI event sync

    def __init__(
        self,
        publisher: WizardDataPublisher,
        sidekick_name: str,
        chat_ctx=None,
        ui_event_manager: Optional[WizardUIEventManager] = None,
    ):
        super().__init__(
            instructions=f"""
            The user can enable built-in abilities for {sidekick_name} on screen.

            CRITICAL: You must WAIT for the user to actually speak before calling any tools.
            Do NOT call tools until you hear the user say continue/skip/next/done.

            FLOW:
            1. Tell them they can toggle abilities on screen, then say continue when ready
            2. When user ACTUALLY says continue/skip/next/done -> call continue_to_next or skip_abilities

            Do NOT speak after calling the tool.
            """,
            chat_ctx=chat_ctx,
        )
        self.publisher = publisher
        self._ui_event_manager = ui_event_manager
        self._ui_watcher_task: Optional[asyncio.Task] = None

    async def on_enter(self) -> None:
        await wait_for_speech_completion(self.session, timeout=3.0)

        if self._ui_event_manager:
            self._ui_watcher_task = asyncio.create_task(self._watch_ui_completion())

        # Use generate_reply to engage the LLM properly for tool-calling on subsequent turns.
        # say() bypasses the LLM, so it won't be primed to call tools when user says "continue".
        self.session.chat_ctx.append(
            role="user",
            text="[System: The abilities/superpowers page is now showing. Tell the user to toggle abilities on screen and say continue when ready.]"
        )
        await self.session.generate_reply()

    async def _watch_ui_completion(self) -> None:
        """Background task that watches for UI-triggered step completion."""
        try:
            ui_event = self._ui_event_manager.get_step_event(self.STEP_NUMBER)
            await ui_event.wait()

            # Stop any ongoing speech immediately when user clicks Continue
            if self.session:
                self.session.interrupt()

            ui_data = self._ui_event_manager.get_step_data(self.STEP_NUMBER) or {}
            skipped = ui_data.get("skipped", False)
            logger.info(f"🖱️ AbilitiesTask completing via UI click: skipped={skipped}")
            self.complete(AbilitiesResult(skipped=skipped))
        except asyncio.CancelledError:
            logger.debug("UI watcher task cancelled (voice completion happened first)")
        except Exception as e:
            logger.warning(f"Error in UI watcher: {e}")

    async def on_exit(self) -> None:
        if self._ui_watcher_task and not self._ui_watcher_task.done():
            self._ui_watcher_task.cancel()
            try:
                await self._ui_watcher_task
            except asyncio.CancelledError:
                pass

    @function_tool
    async def continue_to_next(self, context: RunContext, proceed: bool = True) -> str:
        """
        Call this when the user says continue, next, done, move on, or any similar phrase.
        Do NOT speak after calling this.

        Args:
            proceed: Always True when called (required for Groq schema compatibility)
        """
        logger.info(f"Wizard: Abilities step complete, moving on")
        self.complete(AbilitiesResult())
        return ""

    @function_tool
    async def skip_abilities(self, context: RunContext, skip: bool = True) -> str:
        """
        Call this when the user says skip or wants to skip abilities.
        Do NOT speak after calling this.

        Args:
            skip: Always True when called (required for Groq schema compatibility)
        """
        logger.info(f"Wizard: Abilities skipped")
        self.complete(AbilitiesResult(skipped=True))
        return ""


class KnowledgeTask(AgentTask[KnowledgeResult]):
    """Step 6: Knowledge base setup (primarily UI-driven)."""

    STEP_NUMBER = 6  # Used for UI event sync

    def __init__(
        self,
        publisher: WizardDataPublisher,
        sidekick_name: str,
        chat_ctx=None,
        ui_event_manager: Optional[WizardUIEventManager] = None,
    ):
        super().__init__(
            instructions="""
            User can add documents/URLs via UI.

            CRITICAL: You must WAIT for the user to actually speak before calling any tools.
            Do NOT call tools until you hear the user say continue/skip/next/done.

            FLOW:
            1. Ask the question and WAIT
            2. When user ACTUALLY says continue/skip/next/done -> call continue_to_next or skip_knowledge

            Do NOT speak after calling the tool.
            """,
            chat_ctx=chat_ctx,
        )
        self.publisher = publisher
        self._ui_event_manager = ui_event_manager
        self._ui_watcher_task: Optional[asyncio.Task] = None

    async def on_enter(self) -> None:
        await wait_for_speech_completion(self.session, timeout=3.0)

        if self._ui_event_manager:
            self._ui_watcher_task = asyncio.create_task(self._watch_ui_completion())

        # Use generate_reply to engage the LLM for tool-calling on subsequent turns
        self.session.chat_ctx.append(
            role="user",
            text="[System: The knowledge base page is now showing. Tell the user they can add documents or URLs on screen, then say continue or skip when ready.]"
        )
        await self.session.generate_reply()

    async def _watch_ui_completion(self) -> None:
        """Background task that watches for UI-triggered step completion."""
        try:
            ui_event = self._ui_event_manager.get_step_event(self.STEP_NUMBER)
            await ui_event.wait()

            # Stop any ongoing speech immediately when user clicks Continue
            if self.session:
                self.session.interrupt()

            ui_data = self._ui_event_manager.get_step_data(self.STEP_NUMBER) or {}
            skipped = ui_data.get("skipped", False)
            logger.info(f"🖱️ KnowledgeTask completing via UI click: skipped={skipped}")
            self.complete(KnowledgeResult(skipped=skipped))
        except asyncio.CancelledError:
            logger.debug("UI watcher task cancelled (voice completion happened first)")
        except Exception as e:
            logger.warning(f"Error in UI watcher: {e}")

    async def on_exit(self) -> None:
        if self._ui_watcher_task and not self._ui_watcher_task.done():
            self._ui_watcher_task.cancel()
            try:
                await self._ui_watcher_task
            except asyncio.CancelledError:
                pass

    @function_tool
    async def continue_to_next(self, context: RunContext, proceed: bool = True) -> str:
        """
        Call this when the user says continue, next, done, move on, or any similar phrase.
        Do NOT speak after calling this.

        Args:
            proceed: Always True when called (required for Groq schema compatibility)
        """
        logger.info(f"Wizard: Knowledge step complete, moving on")
        self.complete(KnowledgeResult(document_count=0, website_count=0))
        return ""

    @function_tool
    async def skip_knowledge(self, context: RunContext, skip: bool = True) -> str:
        """
        Call this when the user says skip or wants to skip knowledge.
        Do NOT speak after calling this.

        Args:
            skip: Always True when called (required for Groq schema compatibility)
        """
        logger.info(f"Wizard: Knowledge base skipped")
        self.complete(KnowledgeResult(skipped=True))
        return ""


class ConfigTask(AgentTask[ConfigResult]):
    """Step 7: Configuration mode selection."""

    STEP_NUMBER = 7  # Used for UI event sync

    def __init__(
        self,
        publisher: WizardDataPublisher,
        sidekick_name: str,
        chat_ctx=None,
        ui_event_manager: Optional[WizardUIEventManager] = None,
    ):
        super().__init__(
            instructions="""
            CRITICAL: You must WAIT for the user to actually speak before calling any tools.
            Do NOT call tools until you hear the user say default or advanced.

            FLOW:
            1. Ask the question and WAIT
            2. When user ACTUALLY says default or advanced -> call the matching tool

            Do NOT speak after calling. Do NOT recommend one over the other.
            """,
            chat_ctx=chat_ctx,
        )
        self.publisher = publisher
        self._ui_event_manager = ui_event_manager
        self._ui_watcher_task: Optional[asyncio.Task] = None

    async def on_enter(self) -> None:
        await wait_for_speech_completion(self.session, timeout=3.0)

        # Start watching for UI completion in the background
        if self._ui_event_manager:
            self._ui_watcher_task = asyncio.create_task(self._watch_ui_completion())

        # Use say() instead of generate_reply() to prevent LLM from calling tools
        await self.session.say("Default settings or advanced?", allow_interruptions=False)

    async def _watch_ui_completion(self) -> None:
        """Background task that watches for UI-triggered step completion."""
        try:
            ui_event = self._ui_event_manager.get_step_event(self.STEP_NUMBER)
            await ui_event.wait()

            # Stop any ongoing speech immediately when user clicks Continue
            if self.session:
                self.session.interrupt()

            # UI clicked Default or Advanced
            ui_data = self._ui_event_manager.get_step_data(self.STEP_NUMBER) or {}
            config_mode = ui_data.get("config_mode", "default")

            logger.info(f"🖱️ ConfigTask completing via UI click: config_mode={config_mode}")

            # Update the field and complete
            await self.publisher.field_update("config_mode", config_mode, step=7)
            self.complete(ConfigResult(config_mode=config_mode))

        except asyncio.CancelledError:
            logger.debug("UI watcher task cancelled (voice completion happened first)")
        except Exception as e:
            logger.warning(f"Error in UI watcher: {e}")

    async def on_exit(self) -> None:
        """Cancel the UI watcher when exiting the task."""
        if self._ui_watcher_task and not self._ui_watcher_task.done():
            self._ui_watcher_task.cancel()
            try:
                await self._ui_watcher_task
            except asyncio.CancelledError:
                pass

    @function_tool
    async def use_default_config(self, context: RunContext, use_default: bool = True) -> None:
        """
        Use default configuration. Do NOT speak after calling this.

        Args:
            use_default: Always True when called (required for Groq schema compatibility)
        """
        logger.info(f"Wizard: Using default configuration")
        await self.publisher.field_update("config_mode", "default", step=7)
        self.complete(ConfigResult(config_mode="default"))

    @function_tool
    async def use_advanced_config(self, context: RunContext, use_advanced: bool = True) -> None:
        """
        Use advanced configuration. Do NOT speak after calling this.

        Args:
            use_advanced: Always True when called (required for Groq schema compatibility)
        """
        logger.info(f"Wizard: Using advanced configuration")
        await self.publisher.field_update("config_mode", "advanced", step=7)
        self.complete(ConfigResult(config_mode="advanced"))


class ReviewTask(AgentTask[bool]):
    """Step 9: Review and confirm creation (UI step 9 - Launch)."""

    STEP_NUMBER = 9  # Used for UI event sync

    def __init__(
        self,
        publisher: WizardDataPublisher,
        wizard_data: Dict[str, Any],
        chat_ctx=None,
        ui_event_manager: Optional[WizardUIEventManager] = None,
    ):
        self.wizard_data = wizard_data
        name = wizard_data.get('name', 'your sidekick')
        super().__init__(
            instructions=f"""
            Final step for creating {name}.

            CRITICAL: You must WAIT for the user to actually speak before calling any tools.
            Do NOT call tools until you hear the user say yes/create/done.

            FLOW:
            1. Ask the question and WAIT
            2. When user ACTUALLY says yes/create/done -> call confirm_creation

            Do NOT speak after calling.
            """,
            chat_ctx=chat_ctx,
        )
        self.publisher = publisher
        self._ui_event_manager = ui_event_manager
        self._ui_watcher_task: Optional[asyncio.Task] = None

    async def on_enter(self) -> None:
        await wait_for_speech_completion(self.session, timeout=3.0)

        # Start watching for UI completion in the background
        if self._ui_event_manager:
            self._ui_watcher_task = asyncio.create_task(self._watch_ui_completion())

        name = self.wizard_data.get('name', 'your sidekick')
        # Use say() instead of generate_reply() to prevent LLM from calling tools
        await self.session.say(f"Ready to create {name}?", allow_interruptions=False)

    async def _watch_ui_completion(self) -> None:
        """Background task that watches for UI-triggered step completion."""
        try:
            ui_event = self._ui_event_manager.get_step_event(self.STEP_NUMBER)
            await ui_event.wait()

            # Stop any ongoing speech immediately when user clicks Continue
            if self.session:
                self.session.interrupt()

            logger.info(f"🖱️ ReviewTask completing via UI click")

            # Complete the task silently
            self.complete(True)

        except asyncio.CancelledError:
            logger.debug("UI watcher task cancelled (voice completion happened first)")
        except Exception as e:
            logger.warning(f"Error in UI watcher: {e}")

    async def on_exit(self) -> None:
        """Cancel the UI watcher when exiting the task."""
        if self._ui_watcher_task and not self._ui_watcher_task.done():
            self._ui_watcher_task.cancel()
            try:
                await self._ui_watcher_task
            except asyncio.CancelledError:
                pass

    @function_tool
    async def confirm_creation(self, context: RunContext, create: bool = True) -> None:
        """
        Create the sidekick. Do NOT speak after calling this.

        Args:
            create: Always True when called (required for Groq schema compatibility)
        """
        logger.info(f"Wizard: Creation confirmed!")
        self.complete(True)

    @function_tool
    async def go_back(self, context: RunContext, go_back: bool = True) -> None:
        """
        Go back to make changes.

        Args:
            go_back: Always True when called (required for Groq schema compatibility)
        """
        logger.info(f"Wizard: User wants to go back")
        await self.session.generate_reply(
            instructions="Say: OK, what would you like to change? ONE sentence."
        )


# =============================================================================
# Wizard Flow Orchestrator
# =============================================================================

async def run_wizard_flow(
    room: rtc.Room,
    session_id: Optional[str] = None,
    initial_chat_ctx=None,
) -> WizardResults:
    """
    Run the complete wizard flow using TaskGroup.

    Args:
        room: LiveKit room for data channel communication
        session_id: Optional wizard session ID for persistence
        initial_chat_ctx: Initial chat context (if resuming)

    Returns:
        WizardResults with all collected data
    """
    publisher = WizardDataPublisher(room, session_id)

    # Collected data to pass between tasks
    collected_data: Dict[str, Any] = {}

    # Create the TaskGroup
    task_group = TaskGroup(
        chat_ctx=initial_chat_ctx,
        summarize_chat_ctx=True,
    )

    # Add tasks in order
    # Note: We use lambdas to defer task creation so we can pass collected data

    task_group.add(
        lambda: NameTask(publisher, chat_ctx=initial_chat_ctx),
        id="name",
        description="Collects the sidekick's name",
    )

    # For subsequent tasks, we need to access results from previous tasks
    # TaskGroup handles this via the results dictionary

    # Execute the task group
    results = await task_group
    task_results = results.task_results

    # Extract results
    name_result: NameResult = task_results.get("name")

    # Continue with remaining tasks that depend on name
    # We need to run these sequentially since they depend on previous results

    # Actually, TaskGroup doesn't support dynamic task addition after start.
    # We need a different approach - use a WizardAgent that runs tasks sequentially.

    # For now, return partial results
    return WizardResults(
        name=name_result.name if name_result else "",
        slug=name_result.slug if name_result else "",
        personality_description="",
    )


# =============================================================================
# Wizard Agent (Uses Agent with sequential tasks)
# =============================================================================

from livekit.agents import Agent, get_job_context

class WizardGuideAgent(Agent):
    """
    Farah Qubit - The wizard guide agent.

    Runs through wizard steps sequentially using AgentTask for each step.
    Collects data and notifies the frontend of progress.

    Supports bidirectional sync with the frontend UI - when users click
    Continue/Skip buttons instead of speaking, the UI sends a data message
    that signals the current task to complete.
    """

    def __init__(self, session_id: Optional[str] = None, current_step: int = 1,
                 form_data: Optional[Dict[str, Any]] = None,
                 supabase_url: Optional[str] = None, supabase_service_key: Optional[str] = None):
        super().__init__(
            instructions="""
            You are Farah Qubit, a friendly guide helping users create their AI sidekick.
            You will walk them through each step of the wizard, collecting information
            and helping them make choices. Be warm, conversational, and helpful.
            """,
        )
        self.session_id = session_id
        self.current_step = current_step
        self.form_data = form_data or {}
        self._supabase_url = supabase_url
        self._supabase_service_key = supabase_service_key
        self.publisher: Optional[WizardDataPublisher] = None
        self.ui_event_manager: Optional[WizardUIEventManager] = None
        self.collected_data: Dict[str, Any] = {}

    def _restore_from_form_data(self) -> None:
        """Populate collected_data from saved form_data for steps already completed."""
        fd = self.form_data
        if not fd:
            return

        # Step 1: Name
        if fd.get("name"):
            self.collected_data["name"] = fd["name"]
            self.collected_data["slug"] = fd.get("slug", generate_slug(fd["name"]))

        # Step 2: Personality
        if fd.get("personality_description") or fd.get("personality"):
            self.collected_data["personality"] = fd.get("personality_description") or fd.get("personality", "")
            self.collected_data["personality_traits"] = fd.get("personality_traits")
            for trait in ["openness", "conscientiousness", "extraversion", "agreeableness", "emotional_stability", "communication_style"]:
                self.collected_data[f"personality_{trait}"] = fd.get(f"personality_{trait}") or fd.get(trait)
            self.collected_data["personality_expertise"] = fd.get("personality_expertise") or fd.get("expertise_focus")
            self.collected_data["personality_humor"] = fd.get("personality_humor") or fd.get("humor_style")
            self.collected_data["personality_additional_notes"] = fd.get("personality_additional_notes") or fd.get("additional_notes")

        # Step 3: Voice
        if fd.get("voice_id"):
            self.collected_data["voice_id"] = fd["voice_id"]
            self.collected_data["voice_provider"] = fd.get("voice_provider", "cartesia")

        # Step 4: Avatar
        if fd.get("avatar_prompt") or fd.get("avatar_url"):
            self.collected_data["avatar_prompt"] = fd.get("avatar_prompt")
            self.collected_data["avatar_url"] = fd.get("avatar_url")

        # Step 5: Knowledge
        if fd.get("document_count") is not None or fd.get("website_count") is not None:
            self.collected_data["document_count"] = fd.get("document_count", 0)
            self.collected_data["website_count"] = fd.get("website_count", 0)

        # Step 6: Config
        if fd.get("config_mode"):
            self.collected_data["config_mode"] = fd["config_mode"]
            self.collected_data["stt_provider"] = fd.get("stt_provider")
            self.collected_data["tts_provider"] = fd.get("tts_provider")
            self.collected_data["llm_provider"] = fd.get("llm_provider")

        logger.info(f"🧙 Restored {len(self.collected_data)} fields from saved form_data for resume at step {self.current_step}")

    async def on_enter(self) -> None:
        """Start the wizard flow, resuming from current_step if > 1."""
        # Get room from job context for data publishing
        try:
            job_ctx = get_job_context()
            room = job_ctx.room
        except Exception as e:
            logger.warning(f"Could not get job context for room: {e}")
            room = None

        # Initialize publisher with room and Supabase for persistence
        self.publisher = WizardDataPublisher(
            room, self.session_id,
            supabase_url=self._supabase_url,
            supabase_service_key=self._supabase_service_key,
        )

        # Initialize UI event manager for bidirectional sync with frontend
        if room:
            self.ui_event_manager = WizardUIEventManager(room)
            self.ui_event_manager.setup_listener()
            logger.info("🔗 Wizard UI event manager initialized")

        # Wait for at least one participant to join before starting
        if room:
            max_wait = 30  # seconds
            waited = 0
            while len(room.remote_participants) == 0 and waited < max_wait:
                logger.info(f"🧙 Wizard: waiting for participant to join... ({waited}s)")
                await asyncio.sleep(1)
                waited += 1

            if len(room.remote_participants) == 0:
                logger.warning("🧙 Wizard: no participant joined after 30s, starting anyway")
            else:
                # Brief pause after participant joins to ensure audio is ready
                await asyncio.sleep(0.5)
                logger.info(f"🧙 Wizard: participant joined, starting wizard flow")

        # UI has 9 steps: 1-Name, 2-Personality, 3-Voice, 4-Avatar, 5-Abilities, 6-Knowledge, 7-Config, 8-API Keys, 9-Launch
        TOTAL_STEPS = 9

        # If resuming, restore previously collected data from form_data
        if self.current_step > 1:
            self._restore_from_form_data()
            logger.info(f"🧙 Wizard: resuming at step {self.current_step}, skipping completed steps")
            await self.publisher.step_change(step=self.current_step, total=TOTAL_STEPS)

        # Helper to get the sidekick name (from restored data or will be collected)
        sidekick_name = self.collected_data.get("name", "your sidekick")

        # Step 1: Collect name
        if self.current_step <= 1:
            name_result = await NameTask(self.publisher, chat_ctx=self.chat_ctx)
            self.collected_data["name"] = name_result.name
            self.collected_data["slug"] = name_result.slug
            await wait_for_speech_completion(self.session, timeout=10.0)
            await self.publisher.step_change(step=2, total=TOTAL_STEPS)
        sidekick_name = self.collected_data.get("name", "your sidekick")

        # Step 2: Collect personality
        if self.current_step <= 2:
            personality_result = await PersonalityTask(
                self.publisher,
                sidekick_name=sidekick_name,
                chat_ctx=self.chat_ctx,
                ui_event_manager=self.ui_event_manager,
            )
            self.collected_data["personality"] = personality_result.description
            self.collected_data["personality_traits"] = personality_result.traits
            self.collected_data["personality_openness"] = personality_result.openness
            self.collected_data["personality_conscientiousness"] = personality_result.conscientiousness
            self.collected_data["personality_extraversion"] = personality_result.extraversion
            self.collected_data["personality_agreeableness"] = personality_result.agreeableness
            self.collected_data["personality_emotional_stability"] = personality_result.emotional_stability
            self.collected_data["personality_communication_style"] = personality_result.communication_style
            self.collected_data["personality_expertise"] = personality_result.expertise_focus
            self.collected_data["personality_humor"] = personality_result.humor_style
            self.collected_data["personality_additional_notes"] = personality_result.additional_notes
            await wait_for_speech_completion(self.session, timeout=10.0)
            await self.publisher.step_change(step=3, total=TOTAL_STEPS)

        # Step 3: Select voice
        if self.current_step <= 3:
            voice_result = await VoiceTask(
                self.publisher,
                sidekick_name=sidekick_name,
                chat_ctx=self.chat_ctx,
                ui_event_manager=self.ui_event_manager,
            )
            self.collected_data["voice_id"] = voice_result.voice_id
            self.collected_data["voice_provider"] = voice_result.voice_provider
            await wait_for_speech_completion(self.session, timeout=10.0)
            await self.publisher.step_change(step=4, total=TOTAL_STEPS)

        # Step 4: Avatar
        if self.current_step <= 4:
            avatar_result = await AvatarTask(
                self.publisher,
                sidekick_name=sidekick_name,
                personality=self.collected_data.get("personality", ""),
                chat_ctx=self.chat_ctx,
                ui_event_manager=self.ui_event_manager,
            )
            self.collected_data["avatar_prompt"] = avatar_result.avatar_prompt
            self.collected_data["avatar_url"] = avatar_result.avatar_url
            await wait_for_speech_completion(self.session, timeout=10.0)
            await self.publisher.step_change(step=5, total=TOTAL_STEPS)

        # Step 5: Abilities (UI-driven toggle cards)
        if self.current_step <= 5:
            abilities_result = await AbilitiesTask(
                self.publisher,
                sidekick_name=sidekick_name,
                chat_ctx=self.chat_ctx,
                ui_event_manager=self.ui_event_manager,
            )
            await wait_for_speech_completion(self.session, timeout=10.0)
            await self.publisher.step_change(step=6, total=TOTAL_STEPS)

        # Step 6: Knowledge base
        if self.current_step <= 6:
            knowledge_result = await KnowledgeTask(
                self.publisher,
                sidekick_name=sidekick_name,
                chat_ctx=self.chat_ctx,
                ui_event_manager=self.ui_event_manager,
            )
            self.collected_data["document_count"] = knowledge_result.document_count
            self.collected_data["website_count"] = knowledge_result.website_count
            await wait_for_speech_completion(self.session, timeout=10.0)
            await self.publisher.step_change(step=7, total=TOTAL_STEPS)

        # Step 7: Configuration
        if self.current_step <= 7:
            config_result = await ConfigTask(
                self.publisher,
                sidekick_name=sidekick_name,
                chat_ctx=self.chat_ctx,
                ui_event_manager=self.ui_event_manager,
            )
            self.collected_data["config_mode"] = config_result.config_mode
            self.collected_data["stt_provider"] = config_result.stt_provider
            self.collected_data["tts_provider"] = config_result.tts_provider
            self.collected_data["llm_provider"] = config_result.llm_provider
            # Skip step 8 (API Keys - handled by UI only) and go to step 9
            await wait_for_speech_completion(self.session, timeout=10.0)
            await self.publisher.step_change(step=9, total=TOTAL_STEPS)

        # Step 9: Review and confirm (Launch)
        confirmed = await ReviewTask(
            self.publisher,
            wizard_data=self.collected_data,
            chat_ctx=self.chat_ctx,
            ui_event_manager=self.ui_event_manager,
        )

        if confirmed:
            # Build final results
            final_results = WizardResults(
                name=self.collected_data["name"],
                slug=self.collected_data["slug"],
                personality_description=self.collected_data["personality"],
                personality_traits=self.collected_data.get("personality_traits"),
                voice_id=self.collected_data.get("voice_id", ""),
                voice_provider=self.collected_data.get("voice_provider", "cartesia"),
                avatar_url=self.collected_data.get("avatar_url"),
                avatar_prompt=self.collected_data.get("avatar_prompt"),
                config_mode=self.collected_data.get("config_mode", "default"),
                stt_provider=self.collected_data.get("stt_provider"),
                tts_provider=self.collected_data.get("tts_provider"),
                llm_provider=self.collected_data.get("llm_provider"),
                document_count=self.collected_data.get("document_count", 0),
                website_count=self.collected_data.get("website_count", 0),
            )

            # Notify frontend
            await self.publisher.complete(final_results)

            # Generate final message
            await self.session.generate_reply(
                instructions=f"""
                Congratulate the user! {self.collected_data['name']} is being created.
                Let them know they'll be able to start chatting with their new sidekick
                in just a moment. Be enthusiastic but brief.
                Do NOT say "first sidekick" — the user may already have other sidekicks.
                """
            )


# =============================================================================
# System Prompt (for reference/backwards compatibility)
# =============================================================================

WIZARD_GUIDE_SYSTEM_PROMPT = """You are Farah Qubit, a friendly AI assistant helping the user create their own AI sidekick.

This prompt is deprecated - the wizard now uses TaskGroup with focused per-step instructions.
See WizardGuideAgent for the new implementation.
"""
