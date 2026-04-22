"""Patches for the Fish Audio TTS plugin.

Two orthogonal behaviors, both toggled by env vars and installed via
``install()`` from ``entrypoint.py`` at worker startup:

1. ``TTS_CHUNK_TIMING_LOG=1`` — per-segment timing instrumentation. Emits
   ``🎛️ fish_tts`` INFO logs for ``segment_start``, each ``text_in`` pushed
   to the Fish Audio WebSocket, each ``audio_out`` received (with TTFA,
   inter-chunk gap stats), and a ``segment_end`` summary.

2. ``TTS_CHUNK_COALESCE=1`` — coalesces the LLM's token stream into
   larger text units before pushing to the Fish Audio WebSocket. Fixes
   the observed silent-stall when fast LLMs (e.g. Cerebras GLM-4.7)
   flood Fish Audio's WebSocket with dozens of 1–5 character frames per
   second. Boundaries: sentence-ending punctuation, or a word boundary
   once the buffer reaches ``TTS_CHUNK_COALESCE_MIN_CHARS``, or an idle
   flush after ``TTS_CHUNK_COALESCE_FLUSH_MS`` with no new input.

Both flags are independent — timing can run without coalescing and vice
versa. When both are on, the ``text_in`` log lines reflect the
post-coalesce chunks actually delivered to Fish Audio, which is what you
want for verification.

The single ``install()`` call applies whichever behaviors the env vars
request, and is idempotent per process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import AsyncIterator

logger = logging.getLogger(__name__)

_installed = False

_SENTENCE_ENDERS = ".!?\n"
_DEFAULT_MIN_CHARS = 20
_DEFAULT_FLUSH_MS = 150.0
_DEFAULT_MIN_INTERVAL_MS = 0.0  # 0 = no pacing; set >0 to rate-limit chunks


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _preview(text: str, limit: int = 60) -> str:
    sanitized = text.replace("\n", "\\n").replace("\r", "\\r")
    if len(sanitized) <= limit:
        return sanitized
    return sanitized[:limit] + "…"


def _try_emit(buf: str, min_chars: int) -> tuple[str | None, str]:
    """Decide whether to release a prefix of ``buf`` as a TTS chunk.

    Returns ``(emit, remaining)``. ``emit`` is ``None`` when nothing is
    ready to be pushed yet — the caller should wait for more input or a
    flush timeout before trying again.

    Boundaries, in priority order:
      1. Sentence-ending punctuation (``.!?\\n``) — emit through the end
         of that character so Fish Audio sees a complete sentence.
      2. Word boundary (last space) once the buffer reaches
         ``min_chars`` — emit up to and including that space.
    """
    if not buf:
        return None, buf
    for i, ch in enumerate(buf):
        if ch in _SENTENCE_ENDERS:
            return buf[: i + 1], buf[i + 1 :]
    if len(buf) >= min_chars:
        sp = buf.rfind(" ")
        if sp > 0:
            return buf[: sp + 1], buf[sp + 1 :]
    return None, buf


def install() -> None:
    """Apply Fish Audio plugin patches per env vars. Idempotent."""
    global _installed
    if _installed:
        return

    timing_on = _truthy(os.getenv("TTS_CHUNK_TIMING_LOG"))
    coalesce_on = _truthy(os.getenv("TTS_CHUNK_COALESCE"))
    if not (timing_on or coalesce_on):
        return

    try:
        from livekit.plugins.fishaudio import tts as fish_tts  # type: ignore
        from fish_audio_sdk import TTSRequest  # type: ignore
        from fish_audio_sdk.exceptions import WebSocketErr  # type: ignore
        from livekit.agents import APIConnectionError  # type: ignore
        from livekit.agents.utils import is_given  # type: ignore
    except ImportError as exc:
        logger.info("🎛️ fish_tts patch disabled (missing dependency: %s)", exc)
        return

    try:
        min_chars = max(1, int(os.getenv("TTS_CHUNK_COALESCE_MIN_CHARS", str(_DEFAULT_MIN_CHARS))))
    except ValueError:
        min_chars = _DEFAULT_MIN_CHARS
    try:
        flush_ms = float(os.getenv("TTS_CHUNK_COALESCE_FLUSH_MS", str(_DEFAULT_FLUSH_MS)))
    except ValueError:
        flush_ms = _DEFAULT_FLUSH_MS
    flush_s = max(0.01, flush_ms / 1000.0)
    try:
        min_interval_ms = max(
            0.0, float(os.getenv("TTS_CHUNK_COALESCE_MIN_INTERVAL_MS", str(_DEFAULT_MIN_INTERVAL_MS)))
        )
    except ValueError:
        min_interval_ms = _DEFAULT_MIN_INTERVAL_MS
    min_interval_s = min_interval_ms / 1000.0

    async def _patched_stream_audio(self, ws_session, output_emitter):
        req_id = getattr(self, "_request_id", "?")
        opts = self._opts
        t0 = time.perf_counter()

        text_chunks = 0
        text_chars = 0
        audio_chunks = 0
        audio_bytes = 0
        ttfa_ms: float | None = None
        last_audio_t: float | None = None
        max_gap_ms = 0.0
        gap_sum_ms = 0.0
        gap_samples = 0

        if timing_on:
            logger.info(
                "🎛️ fish_tts segment_start request_id=%s model=%s latency_mode=%s coalesce=%s",
                req_id,
                opts.model,
                opts.latency_mode,
                "on" if coalesce_on else "off",
            )

        request = TTSRequest(
            text="",
            reference_id=opts.reference_id if is_given(opts.reference_id) else None,
            format=opts.output_format,
            sample_rate=opts.sample_rate,
            latency=opts.latency_mode,
        )

        def _log_text_out(chunk: str) -> None:
            nonlocal text_chunks, text_chars
            text_chunks += 1
            text_chars += len(chunk)
            if timing_on:
                logger.info(
                    "🎛️ fish_tts text_in request_id=%s chunk=%d chars=%d elapsed_ms=%.1f preview=%r",
                    req_id,
                    text_chunks,
                    len(chunk),
                    (time.perf_counter() - t0) * 1000.0,
                    _preview(chunk),
                )

        async def _passthrough_text_gen() -> AsyncIterator[str]:
            async for data in self._input_ch:
                if isinstance(data, self._FlushSentinel):
                    continue
                _log_text_out(data)
                yield data

        async def _coalesced_text_gen() -> AsyncIterator[str]:
            """Coalesce the upstream token stream into word/sentence chunks.

            Uses a background reader task so we can apply a per-iteration
            flush timeout: if nothing new has arrived for ``flush_s``
            seconds and we have buffered text, we flush it regardless of
            boundary. This bounds worst-case added latency at ``flush_ms``
            (default 150ms) per flush.
            """
            queue: asyncio.Queue = asyncio.Queue()
            _EOF = object()

            async def reader() -> None:
                try:
                    async for item in self._input_ch:
                        await queue.put(item)
                finally:
                    await queue.put(_EOF)

            reader_task = asyncio.create_task(reader())
            buf = ""
            last_emit_t: float | None = None

            async def _pace() -> None:
                """Sleep so this emission is at least ``min_interval_s``
                after the previous one. First emission is never delayed."""
                if min_interval_s <= 0 or last_emit_t is None:
                    return
                elapsed = time.perf_counter() - last_emit_t
                if elapsed < min_interval_s:
                    await asyncio.sleep(min_interval_s - elapsed)

            try:
                while True:
                    while True:
                        emit, buf = _try_emit(buf, min_chars)
                        if emit is None:
                            break
                        await _pace()
                        _log_text_out(emit)
                        yield emit
                        last_emit_t = time.perf_counter()

                    try:
                        timeout = flush_s if buf else None
                        item = await asyncio.wait_for(queue.get(), timeout=timeout)
                    except asyncio.TimeoutError:
                        if buf:
                            await _pace()
                            _log_text_out(buf)
                            yield buf
                            last_emit_t = time.perf_counter()
                            buf = ""
                        continue

                    if item is _EOF:
                        break
                    if isinstance(item, self._FlushSentinel):
                        if buf:
                            await _pace()
                            _log_text_out(buf)
                            yield buf
                            last_emit_t = time.perf_counter()
                            buf = ""
                        continue
                    buf += item

                if buf:
                    await _pace()
                    _log_text_out(buf)
                    yield buf
            finally:
                reader_task.cancel()
                try:
                    await reader_task
                except (asyncio.CancelledError, Exception):
                    pass

        text_stream = _coalesced_text_gen() if coalesce_on else _passthrough_text_gen()

        try:
            audio_iterator = ws_session.tts(
                request=request, text_stream=text_stream, backend=opts.model
            )

            async for audio_chunk in audio_iterator:
                if not audio_chunk:
                    continue

                now = time.perf_counter()
                audio_chunks += 1
                audio_bytes += len(audio_chunk)

                if ttfa_ms is None:
                    ttfa_ms = (now - t0) * 1000.0
                    if timing_on:
                        logger.info(
                            "🎛️ fish_tts audio_out request_id=%s chunk=%d bytes=%d ttfa_ms=%.1f",
                            req_id,
                            audio_chunks,
                            len(audio_chunk),
                            ttfa_ms,
                        )
                else:
                    gap_ms = (now - (last_audio_t or now)) * 1000.0
                    gap_sum_ms += gap_ms
                    gap_samples += 1
                    if gap_ms > max_gap_ms:
                        max_gap_ms = gap_ms
                    if timing_on and (audio_chunks % 5 == 0 or gap_ms > 150.0):
                        logger.info(
                            "🎛️ fish_tts audio_out request_id=%s chunk=%d bytes=%d since_prev_ms=%.1f elapsed_ms=%.1f",
                            req_id,
                            audio_chunks,
                            len(audio_chunk),
                            gap_ms,
                            (now - t0) * 1000.0,
                        )
                last_audio_t = now

                output_emitter.push(audio_chunk)
                self._mark_started()

        except WebSocketErr as e:
            logger.error(
                "🎛️ fish_tts websocket_err request_id=%s err=%s",
                req_id,
                e,
                exc_info=e,
            )
            raise APIConnectionError(f"Fish Audio WebSocket error: {e}") from e

        finally:
            if timing_on:
                total_ms = (time.perf_counter() - t0) * 1000.0
                avg_gap_ms = (gap_sum_ms / gap_samples) if gap_samples else 0.0
                logger.info(
                    "🎛️ fish_tts segment_end request_id=%s text_chunks=%d text_chars=%d "
                    "audio_chunks=%d audio_bytes=%d ttfa_ms=%s total_ms=%.1f "
                    "max_gap_ms=%.1f avg_gap_ms=%.1f",
                    req_id,
                    text_chunks,
                    text_chars,
                    audio_chunks,
                    audio_bytes,
                    f"{ttfa_ms:.1f}" if ttfa_ms is not None else "none",
                    total_ms,
                    max_gap_ms,
                    avg_gap_ms,
                )

    fish_tts.SynthesizeStream._stream_audio = _patched_stream_audio
    _installed = True
    logger.info(
        "🎛️ fish_tts patch installed (timing=%s coalesce=%s min_chars=%d flush_ms=%.0f min_interval_ms=%.0f)",
        "on" if timing_on else "off",
        "on" if coalesce_on else "off",
        min_chars,
        flush_ms,
        min_interval_ms,
    )
