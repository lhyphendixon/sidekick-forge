"""
LINGUA Service

Audio transcription and subtitle translation service using AssemblyAI.
Supports multiple output formats (SRT, VTT, plain text) and LLM-based translation.
"""

import logging
import json
import asyncio
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

ASSEMBLYAI_BASE_URL = "https://api.assemblyai.com/v2"

# Supported languages for transcription (AssemblyAI)
TRANSCRIPTION_LANGUAGES = {
    "auto": "Auto-detect",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "hi": "Hindi",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "pl": "Polish",
    "tr": "Turkish",
    "ru": "Russian",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
}

# Supported languages for translation output
TRANSLATION_LANGUAGES = {
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "ru": "Russian",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "ar": "Arabic",
    "hi": "Hindi",
}


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class TranscriptSegment:
    """A single subtitle segment with timing."""
    start: int  # milliseconds
    end: int  # milliseconds
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptionResult:
    """Result from AssemblyAI transcription."""
    transcript_id: str
    text: str
    segments: List[TranscriptSegment]
    language_code: str
    duration_ms: int
    word_count: int
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transcript_id": self.transcript_id,
            "text": self.text,
            "segments": [s.to_dict() for s in self.segments],
            "language_code": self.language_code,
            "duration_ms": self.duration_ms,
            "word_count": self.word_count,
            "confidence": self.confidence,
        }


@dataclass
class TranslationResult:
    """Result from subtitle translation."""
    language_code: str
    language_name: str
    segments: List[TranscriptSegment]
    text: str  # Full translated text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language_code": self.language_code,
            "language_name": self.language_name,
            "segments": [s.to_dict() for s in self.segments],
            "text": self.text,
        }


@dataclass
class LinguaResult:
    """Complete LINGUA processing result."""
    run_id: str
    status: str  # pending, transcribing, translating, complete, failed
    original_transcript: Optional[TranscriptionResult] = None
    translations: Dict[str, TranslationResult] = field(default_factory=dict)
    srt_urls: Dict[str, str] = field(default_factory=dict)
    vtt_urls: Dict[str, str] = field(default_factory=dict)
    txt_urls: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "original_transcript": self.original_transcript.to_dict() if self.original_transcript else None,
            "translations": {k: v.to_dict() for k, v in self.translations.items()},
            "srt_urls": self.srt_urls,
            "vtt_urls": self.vtt_urls,
            "txt_urls": self.txt_urls,
            "error": self.error,
        }


# ============================================================================
# Format Generators
# ============================================================================

def generate_srt(segments: List[TranscriptSegment]) -> str:
    """Generate SRT format subtitles from segments."""
    srt_lines = []
    for i, segment in enumerate(segments, 1):
        start_time = _ms_to_srt_time(segment.start)
        end_time = _ms_to_srt_time(segment.end)
        srt_lines.append(f"{i}")
        srt_lines.append(f"{start_time} --> {end_time}")
        srt_lines.append(segment.text)
        srt_lines.append("")  # Blank line between entries
    return "\n".join(srt_lines)


def generate_vtt(segments: List[TranscriptSegment]) -> str:
    """Generate WebVTT format subtitles from segments."""
    vtt_lines = ["WEBVTT", ""]
    for segment in segments:
        start_time = _ms_to_vtt_time(segment.start)
        end_time = _ms_to_vtt_time(segment.end)
        vtt_lines.append(f"{start_time} --> {end_time}")
        vtt_lines.append(segment.text)
        vtt_lines.append("")
    return "\n".join(vtt_lines)


def generate_txt(segments: List[TranscriptSegment]) -> str:
    """Generate plain text transcript from segments."""
    return " ".join(segment.text for segment in segments)


def _ms_to_srt_time(ms: int) -> str:
    """Convert milliseconds to SRT time format (HH:MM:SS,mmm)."""
    hours = ms // 3600000
    minutes = (ms % 3600000) // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _ms_to_vtt_time(ms: int) -> str:
    """Convert milliseconds to VTT time format (HH:MM:SS.mmm)."""
    hours = ms // 3600000
    minutes = (ms % 3600000) // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


# ============================================================================
# LLM Provider for Translation
# ============================================================================

class TranslationLLM:
    """LLM provider for subtitle translation."""

    def __init__(self, api_key: str, provider: str = "groq", model: Optional[str] = None):
        self.api_key = api_key
        self.provider = provider

        # Default models per provider
        default_models = {
            "groq": "llama-3.3-70b-versatile",
            "openai": "gpt-4o-mini",
            "anthropic": "claude-3-5-haiku-latest",
            "deepinfra": "meta-llama/Llama-3.3-70B-Instruct",
        }
        self.model = model or default_models.get(provider, "llama-3.3-70b-versatile")

        # API endpoints
        self.endpoints = {
            "groq": "https://api.groq.com/openai/v1/chat/completions",
            "openai": "https://api.openai.com/v1/chat/completions",
            "deepinfra": "https://api.deepinfra.com/v1/openai/chat/completions",
        }

    async def translate_text(self, text: str, target_language: str) -> str:
        """Translate text to target language."""
        language_name = TRANSLATION_LANGUAGES.get(target_language, target_language)

        messages = [
            {
                "role": "system",
                "content": f"You are a professional subtitle translator. Translate the following text to {language_name}. "
                           "Keep the translation natural and suitable for subtitles (concise, readable). "
                           "Only output the translated text, nothing else. Do not add explanations or notes."
            },
            {
                "role": "user",
                "content": text
            }
        ]

        endpoint = self.endpoints.get(self.provider, self.endpoints["groq"])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": 1000,
                    "temperature": 0.3,  # Lower temperature for more consistent translations
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()


# ============================================================================
# AssemblyAI Client
# ============================================================================

class AssemblyAIClient:
    """Client for AssemblyAI transcription API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }

    async def submit_transcription(
        self,
        audio_url: str,
        language_code: Optional[str] = None,
    ) -> str:
        """Submit audio for transcription. Returns transcript ID."""
        payload = {
            "audio_url": audio_url,
            "punctuate": True,
            "format_text": True,
        }

        if language_code and language_code != "auto":
            payload["language_code"] = language_code
        else:
            payload["language_detection"] = True

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{ASSEMBLYAI_BASE_URL}/transcript",
                headers=self.headers,
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["id"]

    async def get_transcription_status(self, transcript_id: str) -> Dict[str, Any]:
        """Get transcription status and result."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{ASSEMBLYAI_BASE_URL}/transcript/{transcript_id}",
                headers=self.headers,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def wait_for_transcription(
        self,
        transcript_id: str,
        poll_interval: float = 3.0,
        max_wait: float = 600.0,
    ) -> Dict[str, Any]:
        """Poll until transcription is complete or failed."""
        elapsed = 0.0
        while elapsed < max_wait:
            result = await self.get_transcription_status(transcript_id)
            status = result.get("status")

            if status == "completed":
                return result
            elif status == "error":
                raise Exception(f"Transcription failed: {result.get('error', 'Unknown error')}")

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise Exception(f"Transcription timed out after {max_wait} seconds")

    async def get_srt(self, transcript_id: str) -> str:
        """Get SRT format directly from AssemblyAI."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{ASSEMBLYAI_BASE_URL}/transcript/{transcript_id}/srt",
                headers=self.headers,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.text

    async def get_vtt(self, transcript_id: str) -> str:
        """Get VTT format directly from AssemblyAI."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{ASSEMBLYAI_BASE_URL}/transcript/{transcript_id}/vtt",
                headers=self.headers,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.text


# ============================================================================
# LINGUA Service
# ============================================================================

class LinguaService:
    """Main LINGUA service for transcription and translation."""

    def __init__(
        self,
        assemblyai_api_key: str,
        llm_api_key: str,
        llm_provider: str = "groq",
        llm_model: Optional[str] = None,
    ):
        self.assemblyai = AssemblyAIClient(assemblyai_api_key)
        self.translator = TranslationLLM(llm_api_key, llm_provider, llm_model)

    async def transcribe(
        self,
        audio_url: str,
        source_language: Optional[str] = None,
    ) -> TranscriptionResult:
        """Transcribe audio and return structured result."""
        logger.info(f"Starting transcription for audio: {audio_url[:50]}...")

        # Submit transcription job
        transcript_id = await self.assemblyai.submit_transcription(
            audio_url=audio_url,
            language_code=source_language,
        )
        logger.info(f"Transcription job submitted: {transcript_id}")

        # Wait for completion
        result = await self.assemblyai.wait_for_transcription(transcript_id)

        # Parse segments from words
        segments = self._parse_segments(result)

        return TranscriptionResult(
            transcript_id=transcript_id,
            text=result.get("text", ""),
            segments=segments,
            language_code=result.get("language_code", source_language or "en"),
            duration_ms=result.get("audio_duration", 0) * 1000 if result.get("audio_duration") else 0,
            word_count=len(result.get("text", "").split()),
            confidence=result.get("confidence", 0.0),
        )

    def _parse_segments(self, result: Dict[str, Any]) -> List[TranscriptSegment]:
        """Parse transcript result into timed segments suitable for subtitles."""
        segments = []

        # AssemblyAI returns utterances (speaker-based) or we can use words
        utterances = result.get("utterances", [])

        if utterances:
            # Use utterances if available (better for conversations)
            for utt in utterances:
                segments.append(TranscriptSegment(
                    start=utt["start"],
                    end=utt["end"],
                    text=utt["text"],
                ))
        else:
            # Fall back to sentence-based segmentation from words
            words = result.get("words", [])
            if words:
                segments = self._words_to_segments(words)
            elif result.get("text"):
                # Last resort: single segment for entire transcript
                segments = [TranscriptSegment(
                    start=0,
                    end=int(result.get("audio_duration", 0) * 1000),
                    text=result["text"],
                )]

        return segments

    def _words_to_segments(
        self,
        words: List[Dict[str, Any]],
        max_segment_duration_ms: int = 5000,
        max_chars_per_segment: int = 80,
    ) -> List[TranscriptSegment]:
        """Convert word-level timing to subtitle segments."""
        segments = []
        current_words = []
        current_start = None
        current_text = ""

        for word in words:
            word_text = word.get("text", "")
            word_start = word.get("start", 0)
            word_end = word.get("end", 0)

            if current_start is None:
                current_start = word_start

            # Check if we should start a new segment
            potential_text = (current_text + " " + word_text).strip()
            duration = word_end - current_start

            should_split = (
                duration > max_segment_duration_ms or
                len(potential_text) > max_chars_per_segment or
                word_text.endswith(('.', '!', '?'))
            )

            if should_split and current_words:
                # Save current segment
                segments.append(TranscriptSegment(
                    start=current_start,
                    end=current_words[-1].get("end", word_start),
                    text=current_text.strip(),
                ))
                # Start new segment
                current_words = [word]
                current_start = word_start
                current_text = word_text
            else:
                current_words.append(word)
                current_text = potential_text

        # Don't forget the last segment
        if current_words:
            segments.append(TranscriptSegment(
                start=current_start,
                end=current_words[-1].get("end", current_start + 1000),
                text=current_text.strip(),
            ))

        return segments

    async def translate_segments(
        self,
        segments: List[TranscriptSegment],
        target_language: str,
        batch_size: int = 10,
    ) -> TranslationResult:
        """Translate segments to target language, preserving timing."""
        logger.info(f"Translating {len(segments)} segments to {target_language}")

        translated_segments = []
        full_text_parts = []

        # Process in batches to avoid rate limits and timeouts
        for i in range(0, len(segments), batch_size):
            batch = segments[i:i + batch_size]

            # Translate batch in parallel
            tasks = [
                self.translator.translate_text(seg.text, target_language)
                for seg in batch
            ]
            translations = await asyncio.gather(*tasks, return_exceptions=True)

            for seg, translation in zip(batch, translations):
                if isinstance(translation, Exception):
                    logger.warning(f"Translation failed for segment: {translation}")
                    # Fall back to original text on failure
                    translation = seg.text

                translated_segments.append(TranscriptSegment(
                    start=seg.start,
                    end=seg.end,
                    text=translation,
                ))
                full_text_parts.append(translation)

            # Small delay between batches to avoid rate limiting
            if i + batch_size < len(segments):
                await asyncio.sleep(0.5)

        return TranslationResult(
            language_code=target_language,
            language_name=TRANSLATION_LANGUAGES.get(target_language, target_language),
            segments=translated_segments,
            text=" ".join(full_text_parts),
        )

    async def process_full(
        self,
        audio_url: str,
        source_language: Optional[str] = None,
        target_languages: Optional[List[str]] = None,
    ) -> LinguaResult:
        """Full LINGUA pipeline: transcribe, translate, generate formats."""
        run_id = str(uuid.uuid4())
        result = LinguaResult(run_id=run_id, status="transcribing")

        try:
            # Step 1: Transcribe
            transcript = await self.transcribe(audio_url, source_language)
            result.original_transcript = transcript
            result.status = "translating" if target_languages else "complete"

            # Step 2: Translate to each target language
            if target_languages:
                for lang in target_languages:
                    if lang in TRANSLATION_LANGUAGES:
                        translation = await self.translate_segments(
                            transcript.segments,
                            lang,
                        )
                        result.translations[lang] = translation

            result.status = "complete"
            logger.info(f"LINGUA processing complete: {run_id}")

        except Exception as e:
            logger.error(f"LINGUA processing failed: {e}", exc_info=True)
            result.status = "failed"
            result.error = str(e)

        return result


# ============================================================================
# Helper Functions
# ============================================================================

def get_available_transcription_languages() -> Dict[str, str]:
    """Return available transcription languages."""
    return TRANSCRIPTION_LANGUAGES.copy()


def get_available_translation_languages() -> Dict[str, str]:
    """Return available translation languages."""
    return TRANSLATION_LANGUAGES.copy()
