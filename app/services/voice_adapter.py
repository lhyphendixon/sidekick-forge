"""Utility helpers for one-shot STT/TTS outside of LiveKit sessions.

These helpers intentionally stay lightweight and reuse the same provider
choices (and API keys) already configured on the agent/client.
"""
from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from typing import Any, Optional

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)


def _pick_api_key(client: Any, key_name: str) -> Optional[str]:
    """Pick an API key from client settings or fallback to global env."""
    try:
        api_keys = getattr(getattr(client, "settings", None), "api_keys", None)
        if api_keys and getattr(api_keys, key_name, None):
            return getattr(api_keys, key_name)
    except Exception:
        logger.debug("Failed to pull %s from client settings", key_name, exc_info=True)

    env_key = getattr(settings, key_name, None)
    if env_key:
        return env_key
    return None


async def transcribe_audio(
    audio_bytes: bytes,
    agent: Any = None,
    client: Any = None,
    filename: str = "telegram.ogg",
) -> Optional[str]:
    """Transcribe audio to text using OpenAI Whisper (best-effort)."""
    if not audio_bytes:
        return None

    api_key = _pick_api_key(client, "openai_api_key")
    if not api_key:
        logger.warning("No OpenAI API key available for transcription")
        return None

    def _do_transcribe() -> Optional[str]:
        audio_file = BytesIO(audio_bytes)
        audio_file.name = filename
        client_openai = OpenAI(api_key=api_key)
        try:
            result = client_openai.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )
            return getattr(result, "text", None)
        except Exception as exc:  # noqa: BLE001
            logger.error("Transcription failed: %s", exc)
            return None

    return await asyncio.to_thread(_do_transcribe)


async def synthesize_voice(
    text: str,
    agent: Any = None,
    client: Any = None,
    response_format: str = "ogg_opus",
) -> Optional[bytes]:
    """Synthesize speech using OpenAI TTS. Returns audio bytes or None."""
    if not text or not text.strip():
        return None

    api_key = _pick_api_key(client, "openai_api_key")
    if not api_key:
        logger.warning("No OpenAI API key available for TTS")
        return None

    voice_settings = getattr(agent, "voice_settings", None)
    voice_id = getattr(voice_settings, "voice_id", None) or "alloy"
    tts_model = None
    if voice_settings:
        tts_model = (
            getattr(voice_settings, "model", None)
            or getattr(voice_settings, "tts_model", None)
            or getattr(voice_settings, "provider_config", {}).get("tts_model")
        )
    if not tts_model:
        tts_model = "gpt-4o-mini-tts"

    def _do_speech() -> Optional[bytes]:
        client_openai = OpenAI(api_key=api_key)
        try:
            response = client_openai.audio.speech.create(
                model=tts_model,
                voice=voice_id,
                input=text,
                response_format=response_format,
            )
            try:
                return response.read()
            except Exception:
                # Fallback if response exposes bytes directly
                return bytes(response)
        except Exception as exc:  # noqa: BLE001
            logger.error("TTS failed: %s", exc)
            if tts_model != "gpt-4o-mini-tts":
                try:
                    fallback = client_openai.audio.speech.create(
                        model="gpt-4o-mini-tts",
                        voice=voice_id,
                        input=text,
                        response_format=response_format,
                    )
                    return fallback.read()
                except Exception:
                    logger.debug("TTS fallback also failed", exc_info=True)
            return None

    return await asyncio.to_thread(_do_speech)
