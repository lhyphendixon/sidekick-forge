#!/usr/bin/env python3
"""
Voice AI Agent for Sidekick Forge Platform
Handles session-agent-rag jobs with full STT/LLM/TTS capabilities

Supports special modes:
- wizard_guide: Agent guides user through wizard with form-filling tools
"""

import os
import sys
import asyncio
import logging
import json
from typing import Any, Dict, Optional, List

from livekit import agents, rtc
from livekit.agents import JobContext, WorkerOptions, cli, AutoSubscribe
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.plugins import openai, groq, elevenlabs, deepgram, cartesia

# Add app path for importing wizard tools
APP_PATH = os.getenv("APP_PATH", "/app")
if APP_PATH not in sys.path:
    sys.path.insert(0, APP_PATH)

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Agent configuration from environment
AGENT_NAME = os.getenv("AGENT_NAME", "session-agent-rag")
AGENT_SLUG = os.getenv("AGENT_SLUG", "session-agent-rag")
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

def _as_dict(value: Any) -> Dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return {}
        try:
            return json.loads(value)
        except Exception:
            logger.warning(f"[{AGENT_NAME}] Failed to parse JSON metadata", exc_info=True)
            return {}
    if hasattr(value, "dict"):
        try:
            return value.dict()  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        return dict(value)
    except Exception:
        return {}


def _normalize_cartesia_encoding(value: Optional[str]) -> str:
    if not value:
        return "pcm_s16le"
    mapping = {
        "pcm": "pcm_s16le",
        "pcm_s16le": "pcm_s16le",
        "wav": "pcm_s16le",
        "mp3": "mp3",
        "ogg": "ogg_vorbis",
        "ogg_vorbis": "ogg_vorbis",
    }
    normalized = value.strip().lower()
    return mapping.get(normalized, normalized)


async def entrypoint(ctx: JobContext):
    """Main entry point for LiveKit agent jobs with voice AI capabilities"""
    
    # Log job start
    logger.info(f"[{AGENT_NAME}] Starting voice AI job for room: {ctx.room.name}")
    
    # Extract metadata from room and job
    room_metadata = _as_dict(getattr(ctx.room, "metadata", None))
    job_metadata = {}
    try:
        job_metadata = _as_dict(getattr(ctx.job, "metadata", None))
    except Exception:
        logger.warning(f"[{AGENT_NAME}] Failed to parse job metadata", exc_info=True)

    combined_metadata: Dict[str, Any] = {}
    combined_metadata.update(room_metadata)
    combined_metadata.update(job_metadata)

    for key in ("agent_config", "agent_context"):
        nested = combined_metadata.get(key)
        if isinstance(nested, (dict, str)):
            combined_metadata.update(_as_dict(nested))

    api_keys = _as_dict(combined_metadata.get("api_keys"))
    voice_settings = _as_dict(combined_metadata.get("voice_settings"))
    provider_config = _as_dict(voice_settings.get("provider_config"))
    if provider_config:
        voice_settings["provider_config"] = provider_config

    agent_config: Dict[str, Any] = dict(combined_metadata)
    agent_config.update(api_keys)
    agent_config.update(voice_settings)
    agent_config["api_keys"] = api_keys
    agent_config["voice_settings"] = voice_settings

    if "cartesia_voice_id" not in agent_config:
        cartesia_voice = voice_settings.get("cartesia_voice_id") or provider_config.get("cartesia_voice_id")
        if not cartesia_voice:
            cartesia_voice = voice_settings.get("voice_id") or provider_config.get("voice_id")
        if cartesia_voice:
            agent_config["cartesia_voice_id"] = cartesia_voice

    cartesia_voice_val = agent_config.get("cartesia_voice_id")
    if cartesia_voice_val and len(str(cartesia_voice_val)) < 8:
        message = (
            f"[{AGENT_NAME}] Cartesia voice id '{cartesia_voice_val}' looks invalid;"
            " refusing to continue per no-fallback policy"
        )
        logger.error(message)
        raise ValueError("Invalid Cartesia voice_id supplied")

    if "elevenlabs_voice_id" not in agent_config:
        eleven_voice = voice_settings.get("elevenlabs_voice_id") or provider_config.get("elevenlabs_voice_id")
        if eleven_voice:
            agent_config["elevenlabs_voice_id"] = eleven_voice

    if "voice_id" not in agent_config and voice_settings.get("voice_id"):
        agent_config["voice_id"] = voice_settings["voice_id"]

    if (
        (agent_config.get("tts_provider") or voice_settings.get("tts_provider")) == "cartesia"
        and len(str(agent_config.get("voice_id") or "")) < 8
    ):
        message = (
            f"[{AGENT_NAME}] Cartesia voice_id '{agent_config.get('voice_id')}' invalid;"
            " refusing to continue per no-fallback policy"
        )
        logger.error(message)
        raise ValueError("Invalid Cartesia voice_id supplied")

    if "model" not in agent_config and voice_settings.get("model"):
        agent_config["model"] = voice_settings["model"]

    if "tts_model" not in agent_config and voice_settings.get("tts_model"):
        agent_config["tts_model"] = voice_settings["tts_model"]

    if "cartesia_format" not in agent_config and provider_config.get("cartesia_format"):
        agent_config["cartesia_format"] = provider_config["cartesia_format"]

    if "temperature" not in agent_config and voice_settings.get("temperature") is not None:
        agent_config["temperature"] = voice_settings["temperature"]

    if not agent_config.get("api_keys"):
        logger.error(f"[{AGENT_NAME}] No API keys available in metadata; cannot start voice assistant")
        return

    if not agent_config.get("voice_settings"):
        logger.error(f"[{AGENT_NAME}] No voice settings supplied; cannot start voice assistant")
        return

    client_id = agent_config.get("client_id", "unknown")
    logger.info(f"[{AGENT_NAME}] Job metadata - Client: {client_id}")
    logger.info(
        f"[{AGENT_NAME}] Providers: llm={agent_config.get('llm_provider')} stt={agent_config.get('stt_provider')} tts={agent_config.get('tts_provider') or agent_config.get('provider')}"
    )
    
    # Connect to room first
    logger.info(f"[{AGENT_NAME}] Connecting to room: {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    logger.info(f"[{AGENT_NAME}] Voice AI agent successfully joined room: {ctx.room.name}")

    # Check for special modes
    room_type = combined_metadata.get("type", "")
    is_wizard_mode = room_type == "wizard_guide"

    if is_wizard_mode:
        logger.info(f"[{AGENT_NAME}] 🧙 Wizard mode detected - loading wizard tools")

    try:
        # Create voice AI components
        stt = create_stt(agent_config)
        tts = create_tts(agent_config)
        llm = create_llm(agent_config)

        # Create voice assistant with optional tools
        interrupt_duration = float(os.getenv("VOICE_INTERRUPT_DURATION", "0.9"))
        interrupt_min_words = int(os.getenv("VOICE_INTERRUPT_MIN_WORDS", "4"))

        # Build assistant options
        assistant_kwargs = {
            "stt": stt,
            "tts": tts,
            "llm": llm,
            "interrupt_speech_duration": interrupt_duration,
            "interrupt_min_words": interrupt_min_words,
        }

        # For wizard mode, load wizard tools and set up chat context
        if is_wizard_mode:
            wizard_tools, wizard_system_prompt = await setup_wizard_mode(
                ctx.room,
                combined_metadata
            )

            if wizard_tools:
                assistant_kwargs["fnc_ctx"] = wizard_tools
                logger.info(f"[{AGENT_NAME}] Loaded {len(wizard_tools)} wizard tools")

            # Build the initial greeting based on wizard state
            wizard_config = _as_dict(combined_metadata.get("wizard_config", {}))
            current_step = wizard_config.get("current_step", 1)
            form_data = wizard_config.get("form_data", {})

            if current_step == 1 and not form_data.get("name"):
                initial_greeting = (
                    "Hi! I'm Farah, and I'll help you create your AI sidekick today. "
                    "Let's start with the basics. What would you like to name your sidekick?"
                )
            elif current_step > 1:
                name = form_data.get("name", "your sidekick")
                initial_greeting = (
                    f"Welcome back! We were working on creating {name}. "
                    f"Let me check where we left off and continue from there."
                )
            else:
                initial_greeting = "Hi! I'm here to help you create your sidekick. What would you like to name them?"

            # Set up initial chat context with wizard system prompt AND the greeting
            # Adding the greeting as an assistant message prevents the LLM from generating
            # an unwanted initial response (which would read the system prompt)
            chat_ctx = ChatContext()
            chat_ctx.append(
                role="system",
                text=wizard_system_prompt
            )
            # Add greeting as assistant message so LLM knows it already spoke
            chat_ctx.append(
                role="assistant",
                text=initial_greeting
            )
            assistant_kwargs["chat_ctx"] = chat_ctx
            logger.info(f"[{AGENT_NAME}] Set wizard system prompt ({len(wizard_system_prompt)} chars) with initial greeting")

        assistant = agents.VoiceAssistant(**assistant_kwargs)

        # Start the assistant
        assistant.start(ctx.room)
        logger.info(f"[{AGENT_NAME}] Voice assistant started successfully")

        # For wizard mode, speak the greeting immediately (already in chat context)
        if is_wizard_mode:
            # Use the same greeting we added to chat context
            asyncio.create_task(_speak_wizard_greeting(assistant, initial_greeting))
        
        # Set up event handlers
        @ctx.room.on("participant_connected")
        def on_participant_connected(participant: rtc.RemoteParticipant):
            logger.info(f"[{AGENT_NAME}] Participant connected: {participant.identity}")
        
        @ctx.room.on("participant_disconnected") 
        def on_participant_disconnected(participant: rtc.RemoteParticipant):
            logger.info(f"[{AGENT_NAME}] Participant disconnected: {participant.identity}")
            
        @ctx.room.on("track_subscribed")
        def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                logger.info(f"[{AGENT_NAME}] Audio track subscribed from {participant.identity}")
        
        # Wait for the room to close instead of managing participants manually
        logger.info(f"[{AGENT_NAME}] Voice assistant ready - waiting for interactions...")
        await ctx.wait_for_close()
        
    except Exception as e:
        logger.error(f"[{AGENT_NAME}] Voice assistant error: {e}", exc_info=True)
        # Still wait briefly to allow debugging
        await asyncio.sleep(5)
    
    logger.info(f"[{AGENT_NAME}] Job completed for room: {ctx.room.name}")

async def setup_wizard_mode(
    room: rtc.Room,
    metadata: Dict[str, Any]
) -> tuple[Optional[List], str]:
    """
    Set up wizard mode with form-filling tools.

    Args:
        room: LiveKit room for data messages
        metadata: Room metadata containing wizard configuration

    Returns:
        Tuple of (list of tools, system prompt)
    """
    try:
        # Try to import wizard tools from the app
        from app.agent_modules.wizard_tools import build_wizard_tools, WIZARD_GUIDE_SYSTEM_PROMPT

        wizard_config = _as_dict(metadata.get("wizard_config", {}))
        session_id = wizard_config.get("session_id") or metadata.get("session_id")

        # Build tools with room context for data messages
        tools, system_prompt = build_wizard_tools(
            room=room,
            wizard_config=wizard_config,
            session_id=session_id
        )

        # Use custom system prompt if provided, otherwise use default
        custom_prompt = metadata.get("system_prompt") or wizard_config.get("guide_system_prompt")
        if custom_prompt:
            system_prompt = custom_prompt

        logger.info(f"[{AGENT_NAME}] Wizard tools built: {len(tools)} tools, session={session_id}")
        return tools, system_prompt

    except ImportError as e:
        logger.warning(f"[{AGENT_NAME}] Could not import wizard tools: {e}")
        # Return a basic system prompt without tools
        default_prompt = metadata.get("system_prompt", "You are a helpful assistant guiding the user through a wizard.")
        return None, default_prompt
    except Exception as e:
        logger.error(f"[{AGENT_NAME}] Error setting up wizard mode: {e}", exc_info=True)
        default_prompt = metadata.get("system_prompt", "You are a helpful assistant guiding the user through a wizard.")
        return None, default_prompt


async def _speak_wizard_greeting(assistant, greeting: str):
    """
    Speak the wizard greeting after a brief delay for audio setup.
    The greeting text is passed in (already determined during setup).
    """
    try:
        # Brief delay for audio to be ready (reduced from 2s since user is already connected)
        await asyncio.sleep(0.5)
        logger.info(f"[{AGENT_NAME}] Speaking wizard greeting: {greeting[:50]}...")
        await assistant.say(greeting)
    except Exception as e:
        logger.warning(f"[{AGENT_NAME}] Could not speak wizard greeting: {e}")


def create_stt(config: Dict[str, Any]):
    """Create STT provider based on configuration"""
    provider = str(config.get("stt_provider") or config.get("provider") or "").lower()
    language = config.get("stt_language") or "en"
    model_preferences = {
        "groq": config.get("stt_model") or "whisper-large-v3",
        "deepgram": config.get("stt_model") or "nova-2",
        "openai": config.get("stt_model") or "whisper-1",
        "cartesia": config.get("stt_model") or "ink-whisper",
    }

    stt_options = [
        ("groq", "groq_api_key", lambda key: groq.STT(api_key=key, model=model_preferences["groq"])),
        (
            "deepgram",
            "deepgram_api_key",
            lambda key: deepgram.STT(api_key=key, model=model_preferences["deepgram"], language=language),
        ),
        ("openai", "openai_api_key", lambda key: openai.STT(api_key=key, model=model_preferences["openai"])),
        (
            "cartesia",
            "cartesia_api_key",
            lambda key: cartesia.STT(
                api_key=key,
                model=model_preferences["cartesia"],
                language=language,
                encoding=_normalize_cartesia_encoding(
                    config.get("stt_format") or config.get("output_format")
                ),
            ),
        ),
    ]

    for name, key, factory in stt_options:
        if provider == name and config.get(key):
            logger.info(f"[{AGENT_NAME}] Using {name.title()} STT")
            return factory(config[key])

    for name, key, factory in stt_options:
        if config.get(key):
            logger.info(f"[{AGENT_NAME}] Using {name.title()} STT (fallback)")
            return factory(config[key])

    raise ValueError("No valid STT API key found")

def create_tts(config: Dict[str, Any]):
    """Create TTS provider based on configuration"""
    elevenlabs_key = config.get("elevenlabs_api_key")
    cartesia_key = config.get("cartesia_api_key")
    openai_key = config.get("openai_api_key")
    tts_provider = str(config.get("tts_provider") or config.get("provider") or "").lower()
    voice_id = (
        config.get("voice_id")
        or config.get("openai_voice")
        or config.get("elevenlabs_voice_id")
        or "alloy"
    )
    cartesia_voice = config.get("cartesia_voice_id") or voice_id or "sonic-english"
    cartesia_model = config.get("model") or config.get("tts_model") or "sonic-2"
    cartesia_encoding = _normalize_cartesia_encoding(config.get("output_format") or config.get("cartesia_format"))

    if tts_provider == "elevenlabs" and elevenlabs_key:
        logger.info(f"[{AGENT_NAME}] Using ElevenLabs TTS with voice: {voice_id}")
        return elevenlabs.TTS(api_key=elevenlabs_key, voice_id=config.get("elevenlabs_voice_id") or voice_id, model=config.get("tts_model") or "eleven_turbo_v2_5")
    if tts_provider == "cartesia" and cartesia_key:
        logger.info(f"[{AGENT_NAME}] Using Cartesia TTS with voice: {cartesia_voice} and model: {cartesia_model}")
        return cartesia.TTS(api_key=cartesia_key, model=cartesia_model, voice=cartesia_voice, encoding=cartesia_encoding)
    if tts_provider == "openai" and openai_key:
        logger.info(f"[{AGENT_NAME}] Using OpenAI TTS with voice: {voice_id}")
        return openai.TTS(api_key=openai_key, model=config.get("tts_model") or "gpt-4o-mini-tts", voice=voice_id)

    if elevenlabs_key:
        logger.info(f"[{AGENT_NAME}] Using ElevenLabs TTS with voice: {voice_id} (fallback)")
        return elevenlabs.TTS(api_key=elevenlabs_key, voice_id=config.get("elevenlabs_voice_id") or voice_id, model=config.get("tts_model") or "eleven_turbo_v2_5")
    if cartesia_key:
        logger.info(f"[{AGENT_NAME}] Using Cartesia TTS with voice: {cartesia_voice} and model: {cartesia_model} (fallback)")
        return cartesia.TTS(api_key=cartesia_key, model=cartesia_model, voice=cartesia_voice, encoding=cartesia_encoding)
    if openai_key:
        logger.info(f"[{AGENT_NAME}] Using OpenAI TTS with voice: {voice_id} (fallback)")
        return openai.TTS(api_key=openai_key, model=config.get("tts_model") or "gpt-4o-mini-tts", voice=voice_id)

    raise ValueError("No valid TTS API key found")

def create_llm(config: Dict[str, Any]):
    """Create LLM provider based on configuration (no silent fallbacks)."""

    groq_key = config.get("groq_api_key")
    openai_key = config.get("openai_api_key")
    cerebras_key = config.get("cerebras_api_key")

    llm_provider = str(config.get("llm_provider") or "").strip().lower()

    available_providers = {
        "cerebras": bool(cerebras_key),
        "groq": bool(groq_key),
        "openai": bool(openai_key),
    }

    if not llm_provider:
        candidates = [name for name, present in available_providers.items() if present]
        if len(candidates) == 1:
            llm_provider = candidates[0]
        else:
            raise ValueError(
                "llm_provider must be specified explicitly when multiple or no provider keys are present"
            )

    if llm_provider not in available_providers:
        raise ValueError(f"Unsupported llm_provider '{llm_provider}'.")

    if not available_providers[llm_provider]:
        raise ValueError(
            f"API key for provider '{llm_provider}' is required but was not found."
        )

    llm_model = config.get("llm_model") or config.get("model")
    if llm_model:
        llm_model = str(llm_model)

    if not llm_model or llm_model.lower() in {"sonic-2", "sonic", "voice"}:
        if llm_provider == "cerebras":
            llm_model = "llama3.1-8b"
        elif llm_provider == "groq":
            llm_model = "llama-3.3-70b-versatile"
        elif llm_provider == "openai":
            llm_model = "gpt-4o"

    logger.info(f"[{AGENT_NAME}] Using {llm_provider.title()} LLM with model: {llm_model}")

    if llm_provider == "cerebras":
        return openai.LLM.with_cerebras(
            api_key=cerebras_key,
            model=llm_model,
        )

    if llm_provider == "groq":
        return groq.LLM(
            api_key=groq_key,
            model=llm_model,
            temperature=0.7,
        )

    if llm_provider == "openai":
        return openai.LLM(
            api_key=openai_key,
            model=llm_model,
            temperature=0.7,
        )

    raise ValueError(f"Unhandled llm_provider '{llm_provider}'")

def main():
    """Main function to start the agent worker using CLI"""
    
    logger.info(f"Starting Generic LiveKit Agent Worker: {AGENT_NAME}")
    logger.info(f"LiveKit URL: {LIVEKIT_URL}")
    logger.info(f"Agent will accept jobs for: {AGENT_NAME}")
    
    # Validate configuration
    if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
        logger.error("Missing required LiveKit configuration!")
        logger.error(f"URL: {'SET' if LIVEKIT_URL else 'MISSING'}")
        logger.error(f"API Key: {'SET' if LIVEKIT_API_KEY else 'MISSING'}")
        logger.error(f"API Secret: {'SET' if LIVEKIT_API_SECRET else 'MISSING'}")
        return
    
    # Use the CLI to run the agent worker
    import sys
    sys.argv = ["generic_agent.py", "start"]
    
    # Configure worker options as module-level variable for CLI
    global worker_options
    worker_options = WorkerOptions(
        entrypoint_fnc=entrypoint,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
        ws_url=LIVEKIT_URL,
        worker_type=agents.WorkerType.ROOM,
    )
    
    logger.info(f"[{AGENT_NAME}] Worker starting...")
    cli.run_app(worker_options)

if __name__ == "__main__":
    main()
