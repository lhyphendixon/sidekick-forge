#!/usr/bin/env python3
"""
LiveKit Agent Worker Entrypoint
Implements proper worker registration and job handling for the Autonomite agent
"""

import ast
import asyncio
import os
import json
import logging
import inspect
import time
import types
import re
import unicodedata
import aiohttp
from typing import Optional, Dict, Any, List
from datetime import datetime

# Build version - updated automatically or manually when deploying
# This helps verify which code version is actually running
AGENT_BUILD_VERSION = "2025-12-09T20:15:25Z"
AGENT_BUILD_HASH = "v1.5.9-fix-rerank-none"

from livekit import agents, rtc
from livekit import api as livekit_api
from livekit.agents import JobContext, JobRequest, WorkerOptions, cli, llm, voice
from livekit.plugins import deepgram, elevenlabs, openai, groq, silero, cartesia, bithuman
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.agents import room_io
from PIL import Image
from io import BytesIO
from api_key_loader import APIKeyLoader
from config_validator import ConfigValidator, ConfigurationError
from context import AgentContextManager
from sidekick_agent import SidekickAgent
from tool_registry import ToolRegistry
from supabase import create_client

# Enable SDK debug logging for better diagnostics
os.environ["LIVEKIT_LOG_LEVEL"] = "debug"

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Track rooms that already received a proactive greeting to avoid duplicates
_greeted_rooms = set()

def _normalize_for_compare(text: str) -> str:
    if not text:
        return ""
    try:
        t = text.lower()
        t = re.sub(r"[^\w\s]", " ", t)
        t = " ".join(t.split())
        return t
    except Exception:
        return text or ""

# Initialize shared platform Supabase client for OAuth-backed tools (best-effort)
PLATFORM_SUPABASE = None
_platform_supabase_error: Optional[str] = None
try:
    platform_url = os.getenv("SUPABASE_URL")
    platform_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    if platform_url and platform_key:
        PLATFORM_SUPABASE = create_client(platform_url, platform_key)
        logger.info("✅ Platform Supabase client initialized for agent worker")
    else:
        _platform_supabase_error = "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY"
        logger.warning(
            "⚠️ Platform Supabase credentials not configured; OAuth-backed tools may not access shared tokens"
        )
except Exception as exc:
    _platform_supabase_error = str(exc)
    logger.warning("⚠️ Failed to initialize platform Supabase client: %s", exc, exc_info=True)

# Feature toggles for transcript handling
VOICE_ITEM_COMMIT_FALLBACK = os.getenv("VOICE_ITEM_COMMIT_FALLBACK", "false").lower() == "true"

# Agent logic handled via AgentSession and SidekickAgent


def _parse_structured_output(text: Any) -> Optional[Any]:
    if not isinstance(text, str):
        return None
    snippet = text.strip()
    if not snippet or snippet[0] not in ("{", "["):
        return None
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(snippet)
        except (ValueError, SyntaxError):
            return None


def _normalize_transcript_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = text.replace("\u2019", "'")
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = " ".join(text.split())
    return text


def _should_skip_user_commit(agent: Any, text: str) -> bool:
    """Return True when the same user transcript is already pending for this turn."""
    normalized_text = _normalize_transcript_text(text)
    if not normalized_text:
        return True

    last_user = _normalize_transcript_text(getattr(agent, "_last_user_commit", ""))
    if not last_user:
        return False

    if last_user != normalized_text:
        return False

    active_turn_id = getattr(agent, "_current_turn_id", None)
    last_turn_id = getattr(agent, "_last_user_commit_turn", None)
    if getattr(agent, "_pending_user_commit", False):
        return True

    if active_turn_id and (last_turn_id is None or active_turn_id == last_turn_id):
        return True

    return False


def _safe_dump(obj: Any) -> Any:
    try:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
    except Exception:
        pass
    try:
        candidate = getattr(obj, "__dict__", obj)
    except Exception:
        candidate = str(obj)

    if isinstance(candidate, (dict, list, tuple, str, int, float, bool)) or candidate is None:
        return candidate
    return str(candidate)


def _initialize_tts_plugin(
    *,
    tts_provider: Optional[str],
    voice_settings: Dict[str, Any],
    api_keys: Dict[str, Any],
) -> Any:
    """Build and validate the configured TTS plugin (used for both voice + text modes)."""
    if not tts_provider:
        raise ConfigurationError("TTS provider required but not found (tts_provider or provider)")

    provider_config = voice_settings.get("provider_config") or {}
    if not isinstance(provider_config, dict):
        provider_config = {}

    if tts_provider == "elevenlabs":
        elevenlabs_key = api_keys.get("elevenlabs_api_key")
        if not elevenlabs_key:
            raise ConfigurationError("ElevenLabs API key required for TTS but not found")

        # Get model from provider_config or use default
        model = provider_config.get("model") or "eleven_turbo_v2_5"

        # Optimized streaming latency for faster response
        streaming_latency = provider_config.get("streaming_latency", 3)

        logger.info(f"🔊 Initializing ElevenLabs TTS with model={model}, streaming_latency={streaming_latency}")

        tts_plugin = elevenlabs.TTS(
            voice_id=voice_settings.get("voice_id", "Xb7hH8MSUJpSbSDYk0k2"),
            model=model,
            streaming_latency=streaming_latency,
            api_key=elevenlabs_key,
            enable_logging=True,
        )
    else:
        cartesia_key = api_keys.get("cartesia_api_key")
        if not cartesia_key:
            raise ConfigurationError("Cartesia API key required for TTS but not found")

        if cartesia_key.startswith("fixed_") and not cartesia_key.startswith("sk-test_"):
            raise ConfigurationError(
                "Test key 'fixed_cartesia_key' cannot be used with Cartesia API. "
                "Update the client's Cartesia API key in the admin dashboard to a valid key."
            )

        tts_model = (
            voice_settings.get("model")
            or voice_settings.get("tts_model")
            or voice_settings.get("provider_model")
            or None
        )
        if not tts_model:
            raise ConfigurationError(
                "Cartesia TTS requires an explicit 'model'. "
                "Set voice_settings.model or metadata.tts_model."
            )

        cartesia_voice_id = (
            voice_settings.get("voice_id")
            or voice_settings.get("cartesia_voice_id")
            or provider_config.get("cartesia_voice_id")
        )
        if not cartesia_voice_id:
            raise ConfigurationError(
                "Cartesia TTS requires voice_settings.voice_id (or cartesia_voice_id) to be set"
            )
        cartesia_voice_id = str(cartesia_voice_id).strip()
        if len(cartesia_voice_id) < 8:
            raise ConfigurationError(
                "Cartesia voice_id appears invalid (too short); provide the full Cartesia UUID"
            )
        voice_settings["voice_id"] = cartesia_voice_id
        tts_plugin = cartesia.TTS(
            voice=cartesia_voice_id,
            model=tts_model,
            api_key=cartesia_key,
        )
        logger.info("✅ Cartesia TTS configured with voice_id=%s model=%s", cartesia_voice_id, tts_model)

    ConfigValidator.validate_provider_initialization(f"{tts_provider} TTS", tts_plugin)
    return tts_plugin


class TextResponseCollector:
    """Capture assistant responses for text-only jobs."""

    def __init__(self) -> None:
        self._event: asyncio.Event = asyncio.Event()
        self._response_text: Optional[str] = None
        self._citations: List[Dict[str, Any]] = []
        self._tool_results: List[Dict[str, Any]] = []

    def commit_response(
        self,
        text: str,
        *,
        citations: Optional[List[Dict[str, Any]]] = None,
        tool_results: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if self._response_text is not None:
            return
        self._response_text = text or ""
        if citations:
            self._citations = citations
        if tool_results:
            self._tool_results = tool_results
        self._event.set()

    async def wait_for_response(self, timeout: float = 30.0) -> str:
        await asyncio.wait_for(self._event.wait(), timeout=timeout)
        return self._response_text or ""

    @property
    def citations(self) -> List[Dict[str, Any]]:
        return list(self._citations)

    @property
    def tool_results(self) -> List[Dict[str, Any]]:
        return list(self._tool_results)


def _extract_text_from_chat_items(items: List[Any]) -> str:
    """Best-effort extraction of assistant text from chat items."""
    for item in reversed(items or []):
        if getattr(item, "role", None) != "assistant":
            continue
        content = getattr(item, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            chunks: List[str] = []
            for part in content:
                if isinstance(part, str):
                    chunks.append(part)
                elif isinstance(part, dict):
                    text_value = part.get("text")
                    if isinstance(text_value, str):
                        chunks.append(text_value)
            if chunks:
                return "".join(chunks).strip()
        text_attr = getattr(item, "text", None)
        if isinstance(text_attr, str) and text_attr.strip():
            return text_attr.strip()
    return ""


async def _merge_and_update_room_metadata(
    *,
    room_name: str,
    payload: Dict[str, Any],
    logger: logging.Logger,
    retries: int = 2,
) -> None:
    """Persist payload into LiveKit room metadata, merging with existing metadata."""
    livekit_url = os.getenv("LIVEKIT_URL")
    livekit_key = os.getenv("LIVEKIT_API_KEY")
    livekit_secret = os.getenv("LIVEKIT_API_SECRET")
    if not all([livekit_url, livekit_key, livekit_secret]):
        logger.warning("⚠️ LiveKit credentials missing; skipping metadata update")
        return

    attempt = 0
    while attempt <= retries:
        attempt += 1
        lk_client = None
        try:
            lk_client = livekit_api.LiveKitAPI(
                url=livekit_url,
                api_key=livekit_key,
                api_secret=livekit_secret,
            )
            existing = {}
            try:
                rooms = await lk_client.room.list_rooms(
                    livekit_api.ListRoomsRequest(names=[room_name])
                )
                if rooms.rooms and rooms.rooms[0].metadata:
                    existing_raw = rooms.rooms[0].metadata
                    existing = json.loads(existing_raw) if isinstance(existing_raw, str) else dict(existing_raw)
            except Exception as read_err:
                logger.debug("Text-mode: unable to read existing metadata (attempt %s): %s", attempt, read_err)

            merged = existing or {}

            # Remove large dispatch-only fields from existing metadata to make room for response
            # These fields were needed for dispatch but aren't needed in the response
            large_dispatch_fields = ["system_prompt", "tools", "tools_config", "tool_prompt_sections",
                                     "api_keys", "embedding", "dataset_ids", "supabase_service_role_key"]
            for field in large_dispatch_fields:
                merged.pop(field, None)

            merged.update(payload)
            # Remove keys that are explicitly set to None (cleanup of streaming data)
            merged = {k: v for k, v in merged.items() if v is not None}

            # Final size check - truncate response if still too large
            merged_json = json.dumps(merged)
            if len(merged_json) > 60000:
                logger.warning(f"⚠️ Merged metadata still too large ({len(merged_json)} bytes), truncating text_response")
                text_response = merged.get("text_response", "")
                if text_response and len(text_response) > 2000:
                    merged["text_response"] = text_response[:2000] + "\n\n[... response truncated ...]"
                    merged_json = json.dumps(merged)

            await lk_client.room.update_room_metadata(
                livekit_api.UpdateRoomMetadataRequest(
                    room=room_name,
                    metadata=merged_json,
                )
            )
            logger.info("✅ Text response stored in LiveKit room metadata via API (attempt %s)", attempt)
            return
        except Exception as meta_err:
            logger.warning(
                "⚠️ Failed to update LiveKit metadata (attempt %s/%s): %s",
                attempt,
                retries + 1,
                meta_err,
                exc_info=True,
            )
            if attempt > retries:
                return
        finally:
            try:
                if lk_client:
                    await lk_client.aclose()
            except Exception:
                pass


async def _load_conversation_history(
    supabase_client,
    conversation_id: str,
    limit: int = 50,
) -> List[Dict[str, str]]:
    """Load previous messages from conversation history for context."""
    if not supabase_client or not conversation_id:
        return []

    try:
        result = supabase_client.table("conversation_transcripts").select(
            "role", "content", "created_at"
        ).eq("conversation_id", conversation_id).order(
            "created_at", desc=False
        ).limit(limit).execute()

        messages = []
        if result.data:
            for msg in result.data:
                role = msg.get("role")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        logger.info(f"📜 Loaded {len(messages)} messages from conversation history for {conversation_id}")
        return messages
    except Exception as e:
        logger.warning(f"Failed to load conversation history: {e}")
        return []


async def _run_text_mode_interaction(
    *,
    session: voice.AgentSession,
    agent: SidekickAgent,
    room: rtc.Room,
    user_message: str,
    collector: Optional[TextResponseCollector],
    conversation_id: str,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    if not user_message or not user_message.strip():
        raise ConfigurationError("Text mode requests require 'user_message' in metadata")

    logger.info("📝 Text-only mode: direct LLM path (bypass TTS pipeline)")

    # CRITICAL: Clear stale response fields from previous turns to prevent race conditions
    # This ensures the polling API doesn't see old text_response before the new one is ready
    try:
        await _merge_and_update_room_metadata(
            room_name=room.name,
            payload={
                "mode": "text",
                "conversation_id": conversation_id,
                "streaming": True,  # Indicate new response in progress
                # Explicitly clear previous response data
                "text_response": None,
                "text_response_partial": None,
                "citations": None,
                "tool_results": None,
                "widget": None,
                "generated_at": None,
            },
            logger=logger,
            retries=1,
        )
        logger.info("🔄 Cleared stale response metadata for new turn")
    except Exception as clear_err:
        logger.warning(f"⚠️ Failed to clear stale metadata: {clear_err}")
    # Proactively retrieve citations/rerank context for text mode (on_user_turn_completed may not fire)
    # NO FALLBACK POLICY: If RAG retrieval fails, we fail the request rather than hallucinating
    rag_context = ""
    if hasattr(agent, "_retrieve_with_citations") and callable(getattr(agent, "_retrieve_with_citations")):
        await agent._retrieve_with_citations(user_message)
        logger.info("📚 Text-mode: pre-fetched citations for user message (count=%s)", len(getattr(agent, "_current_citations", []) or []))
        # Get the RAG context text for injection into the prompt
        rag_context = getattr(agent, "_current_rag_context", "") or ""
        if rag_context:
            logger.info(f"📚 Text-mode: RAG context retrieved ({len(rag_context)} chars)")
        else:
            logger.error("❌ RAG context retrieval returned empty - NO FALLBACK POLICY prevents hallucination")
            raise ValueError("RAG context retrieval failed - empty context returned. Check document indexing and embeddings.")
    else:
        logger.error("❌ Agent does not have _retrieve_with_citations method - cannot proceed")
        raise ValueError("Agent not configured for RAG retrieval")

    # Call LLM directly (no TTS) to avoid LiveKit TTS failures in text-only mode
    try:
        # Build ChatContext with conversation history for context continuity
        chat_ctx = llm.ChatContext()

        # Add system prompt first if available
        # The system prompt is stored as 'instructions' in the LiveKit Agent base class
        system_prompt = getattr(agent, "instructions", None) or (
            agent._agent_config.get("system_prompt") if hasattr(agent, "_agent_config") else None
        )

        # Inject RAG context into the system prompt if available
        if rag_context:
            rag_injection = f"""

## Relevant Knowledge Base Context
Use the following information from our knowledge base to help answer the user's question.

IMPORTANT: Base your answer ONLY on the information provided below. If the context doesn't contain relevant information to answer the question, say so honestly rather than making up information. Do not invent facts, names, or details that are not in the provided context.

{rag_context}

---
"""
            system_prompt = (system_prompt or "") + rag_injection
            logger.info(f"📚 Injected RAG context into system prompt")

        if system_prompt:
            chat_ctx.add_message(role="system", content=system_prompt)
            logger.info(f"📝 Added system prompt to chat context ({len(system_prompt)} chars)")

        # Load and add conversation history for resumed conversations
        history_messages = await _load_conversation_history(
            getattr(agent, "_supabase_client", None),
            conversation_id,
            limit=50  # Limit history to avoid token overflow
        )

        for msg in history_messages:
            chat_ctx.add_message(role=msg["role"], content=msg["content"])

        if history_messages:
            logger.info(f"📜 Added {len(history_messages)} history messages to chat context")

        # Add current user message
        chat_ctx.add_message(role="user", content=user_message)

        # Get tools registered on the agent for native function calling
        # This follows LiveKit's recommended pattern for tool use
        agent_tools = list(getattr(agent, "tools", []) or [])
        logger.info(f"🧰 TEXT-MODE: Passing {len(agent_tools)} tools to LLM for native function calling")

        # Call LLM directly for text mode WITH tools for native function calling
        llm_result = agent.llm.chat(chat_ctx=chat_ctx, tools=agent_tools if agent_tools else None)

        # Streaming path: accumulate deltas and emit BATCHED partial metadata
        # to avoid per-token API calls which cause massive delays
        stream_chunks: List[str] = []
        chunk_size_env = os.getenv("TEXT_STREAM_CHUNK_SIZE")
        chunk_size = int(chunk_size_env) if chunk_size_env and chunk_size_env.isdigit() else 80
        # Batch updates: only emit metadata every N tokens to reduce API overhead
        stream_batch_size = int(os.getenv("TEXT_STREAM_BATCH_SIZE", "50"))

        assembled = ""
        response_text = ""

        # Track tool calls from native function calling
        detected_tool_calls: List[Dict[str, Any]] = []

        if hasattr(llm_result, "__aiter__"):
            chunk_index = 0
            last_update_index = 0
            async for chunk in llm_result:
                delta = None


                # Check for native tool calls in the stream
                try:
                    # LiveKit LLM stream has tool_calls on chunk.delta, not chunk directly
                    tool_calls_list = None
                    if hasattr(chunk, "delta") and chunk.delta:
                        tool_calls_list = getattr(chunk.delta, "tool_calls", None)
                    if not tool_calls_list:
                        tool_calls_list = getattr(chunk, "tool_calls", None)

                    if tool_calls_list:
                        for tc in tool_calls_list:
                            tool_name = getattr(tc, "name", None) or getattr(tc, "function", {}).get("name")
                            tool_args = getattr(tc, "arguments", {})
                            if isinstance(tool_args, str):
                                try:
                                    tool_args = json.loads(tool_args)
                                except:
                                    tool_args = {}
                            if tool_name and tool_name not in [t["name"] for t in detected_tool_calls]:
                                detected_tool_calls.append({"name": tool_name, "arguments": tool_args})
                                logger.info(f"🧰 TEXT-MODE: Detected native tool call: {tool_name} with args: {tool_args}")
                except Exception as tc_err:
                    logger.debug(f"Tool call detection error: {tc_err}")

                try:
                    if hasattr(chunk, "delta") and getattr(chunk.delta, "content", None):
                        delta = chunk.delta.content
                    elif hasattr(chunk, "message") and getattr(chunk.message, "content", None):
                        delta = chunk.message.content
                    elif hasattr(chunk, "content"):
                        delta = getattr(chunk, "content")
                    elif isinstance(chunk, str):
                        delta = chunk
                except Exception:
                    delta = None

                if not delta:
                    continue

                delta = str(delta)
                assembled += delta
                stream_chunks.append(delta)
                chunk_index += 1

                # Emit partial stream updates for UI - BATCHED to reduce API overhead
                # Only update every stream_batch_size tokens
                if chunk_index - last_update_index >= stream_batch_size:
                    last_update_index = chunk_index
                    try:
                        await _merge_and_update_room_metadata(
                            room_name=room.name,
                            payload={
                                "mode": "text",
                                "conversation_id": conversation_id,
                                "text_response_partial": assembled,
                                "text_response_stream": list(stream_chunks),
                                "text_stream_token": delta,
                                "streaming": True,
                                "stream_progress": {
                                    "current": chunk_index,
                                },
                            },
                            logger=logger,
                            retries=1,
                        )
                    except Exception as partial_err:
                        logger.debug(f"Streaming metadata update skipped: {partial_err}")

            response_text = assembled.strip()

            # After stream completes, check for final tool calls on the stream object
            try:
                if hasattr(llm_result, "tool_calls") and llm_result.tool_calls:
                    for tc in llm_result.tool_calls:
                        tool_name = getattr(tc, "name", None) or getattr(tc, "function", {}).get("name")
                        tool_args = getattr(tc, "arguments", {})
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except:
                                tool_args = {}
                        if tool_name and not any(t["name"] == tool_name for t in detected_tool_calls):
                            detected_tool_calls.append({"name": tool_name, "arguments": tool_args})
                            logger.info(f"🧰 TEXT-MODE: Detected final tool call: {tool_name}")
            except Exception as tc_err:
                logger.debug(f"Final tool call detection error: {tc_err}")
        else:
            # Non-streaming response object
            llm_response = llm_result
            text = None
            if hasattr(llm_response, "message") and getattr(llm_response.message, "content", None):
                text = llm_response.message.content
            elif hasattr(llm_response, "content"):
                text = getattr(llm_response, "content")
            elif hasattr(llm_response, "choices") and llm_response.choices:
                choice = llm_response.choices[0]
                msg = getattr(choice, "message", None)
                if msg and getattr(msg, "content", None):
                    text = msg.content
                # Check for tool calls in non-streaming response
                if msg and hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_name = getattr(tc, "name", None) or getattr(tc.function, "name", None)
                        tool_args = getattr(tc, "arguments", {}) or getattr(tc.function, "arguments", {})
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except:
                                tool_args = {}
                        if tool_name:
                            detected_tool_calls.append({"name": tool_name, "arguments": tool_args})
                            logger.info(f"🧰 TEXT-MODE: Detected tool call (non-stream): {tool_name}")
            response_text = (text or "").strip()
    except Exception as llm_err:
        logger.error(f"Direct LLM call failed in text mode: {type(llm_err).__name__}: {llm_err}")
        raise

    # Process detected tool calls (native function calling)
    tool_results: List[Dict[str, Any]] = []
    widget_trigger = None

    # Check for content_catalyst tool call from native function calling
    for tc in detected_tool_calls:
        if tc["name"] == "content_catalyst":
            logger.info(f"🎨 TEXT-MODE: Processing Content Catalyst tool call: {tc['arguments']}")
            args = tc["arguments"]
            suggested_topic = args.get("suggested_topic", "") or args.get("source_content", "")

            widget_trigger = {
                "type": "content_catalyst",
                "config": {
                    "suggested_topic": suggested_topic,
                    "source_type": args.get("source_type", "topic"),
                    "source_content": args.get("source_content", ""),
                    "target_word_count": args.get("target_word_count"),
                    "style_prompt": args.get("style_prompt", ""),
                },
                "message": "Opening Content Catalyst configuration..."
            }

            # Set a helpful response if none was generated
            if not response_text:
                response_text = "I'll help you create an article. Please configure your preferences in the Content Catalyst widget below."

            logger.info(f"🎨 TEXT-MODE: Widget trigger from native function call: {widget_trigger}")
            break

    # Fallback: Also check for JSON tool call in LLM text response (for models that don't support native function calling)
    if not widget_trigger:
        import re
        json_match = re.search(r'```json\s*(\{.*?"tool".*?\})\s*```', response_text, re.DOTALL)
        if not json_match:
            # Also try without markdown code block
            json_match = re.search(r'(\{[^{}]*"tool"\s*:\s*"content_catalyst"[^{}]*\})', response_text, re.DOTALL)

        if json_match:
            try:
                tool_call_json = json.loads(json_match.group(1))
                tool_name = tool_call_json.get("tool")
                tool_args = tool_call_json.get("args", {})

                if tool_name == "content_catalyst":
                    logger.info(f"🎨 TEXT-MODE: Detected Content Catalyst via JSON fallback: {tool_args}")

                    # Extract suggested topic from args - handle both new and old formats
                    suggested_topic = tool_args.get("suggested_topic", "") or tool_args.get("source_content", "")

                    # Create widget trigger for the UI
                    widget_trigger = {
                        "type": "content_catalyst",
                        "config": {
                            "suggested_topic": suggested_topic,
                        },
                        "message": "Opening Content Catalyst configuration..."
                    }

                    # Clean up the response text - remove the JSON block
                    response_text = re.sub(r'```json\s*\{.*?"tool".*?\}\s*```', '', response_text, flags=re.DOTALL)
                    response_text = re.sub(r'\{[^{}]*"tool"\s*:\s*"content_catalyst"[^{}]*\}', '', response_text)
                    response_text = response_text.strip()

                    # If response is now empty or just whitespace, provide a default message
                    if not response_text:
                        response_text = "I'll help you create an article. Please configure your preferences in the Content Catalyst widget below."

                    logger.info(f"🎨 TEXT-MODE: Widget trigger prepared (JSON fallback): {widget_trigger}")

            except json.JSONDecodeError as e:
                logger.debug(f"🔧 TEXT-MODE: JSON parse failed for potential tool call: {e}")

    citations = list(getattr(agent, "_current_citations", []) or [])
    logger.info(f"📚 DEBUG: Found {len(citations)} citations on agent for response payload")
    if citations:
        logger.info(f"📚 DEBUG: First citation: {citations[0].get('title', 'no title')}")
    # Log first 500 chars of response for debugging
    logger.info(f"📝 DEBUG: LLM response preview: {response_text[:500]}...")

    if collector:
        try:
            collector.commit_response(response_text, citations=citations, tool_results=tool_results)
        except Exception:
            pass

    # NOTE: Removed redundant "simulated chunking" loop that was causing massive delays
    # Real streaming updates already happen during LLM generation above

    # Truncate citations for LiveKit metadata (65KB limit)
    # Keep only top 15 citations and truncate content to 500 chars each
    # Make a deep copy to avoid modifying the original citations
    metadata_citations = []
    for citation in (citations[:15] if citations else []):
        if isinstance(citation, dict):
            citation_copy = dict(citation)
            content = citation_copy.get("content", "")
            if len(content) > 500:
                citation_copy["content"] = content[:500] + "... [truncated]"
            metadata_citations.append(citation_copy)
        else:
            metadata_citations.append(citation)

    # Log what citations we're about to send
    logger.info(f"📚 DEBUG: Sending {len(metadata_citations)} citations in final payload")
    if metadata_citations:
        logger.info(f"📚 DEBUG: Citation titles: {[c.get('title', 'no title')[:40] for c in metadata_citations[:5]]}")

    payload = {
        "mode": "text",
        "conversation_id": conversation_id,
        "text_response": response_text,
        # Don't include stream chunks in final payload - they're redundant and can exceed LiveKit's 64KB metadata limit
        # "text_response_stream": stream_chunks if 'stream_chunks' in locals() else [],
        "citations": metadata_citations,
        "tool_results": tool_results,
        "rerank": (
            getattr(agent, "_current_rerank_info", None)
            or (getattr(agent, "_agent_config", {}) or {}).get("rerank", {})
            or {}
        ),
        "streaming": False,
        "generated_at": datetime.utcnow().isoformat(),
        # Clear out streaming data from previous partial updates
        "text_response_partial": None,
        "text_response_stream": None,
        "text_stream_token": None,
        "stream_progress": None,
    }

    # Add widget trigger if present (for Content Catalyst and other widget-based abilities)
    if widget_trigger:
        payload["widget"] = widget_trigger
        logger.info(f"🎨 TEXT-MODE: Adding widget trigger to payload: {widget_trigger}")
    # Persist response via LiveKit server metadata so the API can poll it
    await _merge_and_update_room_metadata(
        room_name=room.name,
        payload=payload,
        logger=logger,
        retries=2,
    )
    return payload


def collect_tool_results_from_event(event: Any, *, log: logging.Logger) -> tuple[List[Optional[str]], List[Dict[str, Any]]]:
    function_calls = list(getattr(event, "function_calls", []) or [])
    function_call_outputs = list(getattr(event, "function_call_outputs", []) or [])

    try:
        zipped_calls = list(event.zipped())
    except Exception:
        zipped_calls = []

    if not zipped_calls:
        zipped_calls = [
            (call, function_call_outputs[idx] if idx < len(function_call_outputs) else None)
            for idx, call in enumerate(function_calls)
        ]

    calls_summary: List[Optional[str]] = []
    tool_results: List[Dict[str, Any]] = []

    for call, call_output in zipped_calls:
        try:
            log.debug("🛠️ function_call payload: %s", getattr(call, "__dict__", {}))
        except Exception:
            pass

        name = getattr(call, "name", None)
        calls_summary.append(name)

        entry: Dict[str, Any] = {
            "slug": name,
            "type": getattr(call, "tool", None) or getattr(call, "type", None),
        }

        call_id = getattr(call, "call_id", None)
        if call_id:
            entry["call_id"] = call_id

        success_flag: Optional[bool] = None
        output_value: Any = None

        if call_output is not None:
            try:
                if hasattr(call_output, "model_dump"):
                    log.debug("🛠️ function_call_output payload: %s", call_output.model_dump())
            except Exception:
                pass
            success_flag = not getattr(call_output, "is_error", False)
            output_value = getattr(call_output, "output", None)
            if not call_id:
                output_call_id = getattr(call_output, "call_id", None)
                if output_call_id:
                    entry["call_id"] = output_call_id
        else:
            success_attr = getattr(call, "success", None)
            if isinstance(success_attr, bool):
                success_flag = success_attr
            else:
                status = str(getattr(call, "status", "")).lower()
                success_flag = status not in {"error", "failed"}

        if success_flag is not None:
            entry["success"] = success_flag

        raw_call_output = _safe_dump(call_output)
        entry["raw_call_output"] = raw_call_output

        if output_value is None:
            output_value = getattr(call, "output", None)
            if output_value is None:
                output_value = getattr(call, "response", None)
            if output_value is None:
                output_value = getattr(call, "result", None)
            if output_value is None:
                payload = getattr(call, "tool_output", None)
                if payload is not None:
                    output_value = payload

        if output_value is not None and not isinstance(output_value, (str, int, float, bool)):
            try:
                entry["output"] = json.dumps(output_value, ensure_ascii=False)
            except Exception:
                entry["output"] = str(output_value)
        elif output_value is not None:
            entry["output"] = str(output_value)
        else:
            entry["output"] = None

        if entry["output"] is None and success_flag:
            call_payload = None
            if call_output is not None:
                call_payload = _safe_dump(call_output)
            log.warning(
                "Function call returned success but no output payload",
                extra={
                    "slug": name,
                    "call_id": entry.get("call_id"),
                    "call_payload": call_payload,
                    "call_data": _safe_dump(call),
                },
            )

        if isinstance(entry.get("output"), str):
            structured = _parse_structured_output(entry["output"])
            if structured is not None:
                entry["structured_output"] = structured
        else:
            log.warning(
                "⚠️ Tool %s call_id=%s returned success=%s but no output payload",
                name,
                entry.get("call_id"),
                success_flag,
            )

        error_msg = None
        if call_output is not None:
            error_msg = getattr(call_output, "error", None)
        if not error_msg:
            error_msg = getattr(call, "error", None)
        if error_msg:
            entry["error"] = error_msg

        tool_results.append(entry)

    return calls_summary, tool_results


# --- Performance Logging Helper ---
def log_perf(event: str, room_name: str, details: Dict[str, Any]):
    log_entry = {
        "event": event,
        "room_name": room_name,
        "details": details
    }
    logger.info(f"PERF: {json.dumps(log_entry)}")


async def _store_voice_turn(
    supabase_client,
    user_id: str,
    agent_id: str,
    conversation_id: str,
    user_message: str,
    agent_response: str
):
    """
    Store a voice conversation turn as TWO rows (user + assistant) in the client's Supabase,
    matching the text-mode schema for unified analytics.
    """
    try:
        ts = datetime.utcnow().isoformat()
        # User row
        user_row = {
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "role": "user",
            "content": user_message,
            "transcript": user_message,
            "created_at": ts,
            # "source": "voice",  # Column doesn't exist yet
        }
        # Assistant row
        assistant_row = {
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "role": "assistant",
            "content": agent_response,
            "transcript": agent_response,
            "created_at": ts,
            # "source": "voice",  # Column doesn't exist yet
        }
        await supabase_client.table("conversation_transcripts").insert(user_row).execute()
        await supabase_client.table("conversation_transcripts").insert(assistant_row).execute()
        logger.info(
            f"✅ Stored voice turn as two rows for conversation_id={conversation_id}"
        )
    except Exception as e:
        logger.error(f"❌ Failed to store voice turn rows: {e}")
        logger.error(
            f"Turn data that failed: user='{(user_message or '')[:50]}...', agent='{(agent_response or '')[:50]}...'"
        )


async def agent_job_handler(ctx: JobContext):
    """
    This function is called for each new agent job.
    It creates an agent, connects to the room, and handles the session.
    """
    logger.info(f"Received job for room: {ctx.room.name}")
    
    # The context provides a connected room object
    room = ctx.room

    # Attach detailed LiveKit room diagnostics for track lifecycle events
    try:
        def _on_local_track_published(publication, track):
            try:
                logger.info(
                    "📡 local_track_published kind=%s track_sid=%s muted=%s",
                    getattr(publication, "kind", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                    getattr(publication, "muted", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log local_track_published: {log_err}")

        def _on_track_published(publication, participant):
            try:
                logger.info(
                    "📡 track_published kind=%s track_sid=%s participant=%s muted=%s",
                    getattr(publication, "kind", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                    getattr(participant, "identity", None),
                    getattr(publication, "muted", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_published: {log_err}")

        def _on_track_subscribed(track, publication, participant):
            try:
                logger.info(
                    "📡 track_subscribed kind=%s track_sid=%s participant=%s muted=%s",
                    getattr(track, "kind", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                    getattr(participant, "identity", None),
                    getattr(publication, "muted", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_subscribed: {log_err}")

        def _on_track_unsubscribed(track, publication, participant):
            try:
                logger.info(
                    "📡 track_unsubscribed kind=%s track_sid=%s participant=%s",
                    getattr(track, "kind", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                    getattr(participant, "identity", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_unsubscribed: {log_err}")

        def _on_track_muted(participant, publication):
            try:
                logger.info(
                    "📡 track_muted participant=%s track_sid=%s",
                    getattr(participant, "identity", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_muted: {log_err}")

        def _on_track_unmuted(participant, publication):
            try:
                logger.info(
                    "📡 track_unmuted participant=%s track_sid=%s",
                    getattr(participant, "identity", None),
                    getattr(publication, "track_sid", None) or getattr(publication, "sid", None),
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_unmuted: {log_err}")

        def _on_track_subscription_failed(participant, track_sid, error):
            try:
                logger.warning(
                    "⚠️ track_subscription_failed participant=%s track_sid=%s error=%s",
                    getattr(participant, "identity", None),
                    track_sid,
                    error,
                )
            except Exception as log_err:
                logger.debug(f"Failed to log track_subscription_failed: {log_err}")

        room.on("local_track_published", _on_local_track_published)
        room.on("track_published", _on_track_published)
        room.on("track_subscribed", _on_track_subscribed)
        room.on("track_unsubscribed", _on_track_unsubscribed)
        room.on("track_muted", _on_track_muted)
        room.on("track_unmuted", _on_track_unmuted)
        room.on("track_subscription_failed", _on_track_subscription_failed)
        logger.info("🔍 LiveKit room diagnostics handlers attached")
    except Exception as attach_err:
        logger.warning(f"Unable to attach room diagnostics handlers: {attach_err}")

    job_received_time = time.perf_counter()
    perf_summary = {}

    try:
        # In automatic mode, we need to fetch room info to get metadata
        metadata = {}
        
        # Log room information for debugging
        logger.info(f"Room name: {ctx.room.name}")

        logger.info(f"Room sid: {getattr(ctx.room, 'sid', 'No SID')}")
        logger.info(f"Job ID: {getattr(ctx, 'job_id', 'No Job ID')}")
        logger.info(f"Room object type: {type(ctx.room)}")
        
        # Try to get room info from LiveKit API to access metadata
        try:
            # The room object from context might have limited info
            # We need to fetch full room details from LiveKit
            from livekit import api
            
            # Get LiveKit credentials from environment
            livekit_url = os.getenv("LIVEKIT_URL")
            livekit_api_key = os.getenv("LIVEKIT_API_KEY")
            livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")
            
            logger.info(f"LiveKit API credentials available: URL={bool(livekit_url)}, Key={bool(livekit_api_key)}, Secret={bool(livekit_api_secret)}")
            
            if all([livekit_url, livekit_api_key, livekit_api_secret]):
                # Create LiveKit API client
                livekit_api = None
                try:
                    livekit_api = api.LiveKitAPI(
                        url=livekit_url,
                        api_key=livekit_api_key,
                        api_secret=livekit_api_secret
                    )

                    logger.info(f"Fetching room details from LiveKit API for room: {ctx.room.name}")

                    # Fetch room details
                    rooms = await livekit_api.room.list_rooms(
                        api.ListRoomsRequest(names=[ctx.room.name])
                    )

                    logger.info(f"API response - found {len(rooms.rooms) if rooms.rooms else 0} rooms")

                    if rooms.rooms:
                        room_info = rooms.rooms[0]
                        logger.info(f"Room found - SID: {room_info.sid}, Has metadata: {bool(room_info.metadata)}")
                        
                        if room_info.metadata:
                            logger.info(f"Raw room metadata length: {len(room_info.metadata)} chars")
                            logger.info(f"Room metadata from API: {room_info.metadata[:200]}..." if len(room_info.metadata) > 200 else f"Room metadata from API: {room_info.metadata}")
                            try:
                                metadata = json.loads(room_info.metadata)
                                logger.info(f"Successfully parsed room metadata with {len(metadata)} keys")
                                logger.info(f"Metadata keys: {list(metadata.keys())}")
                                
                                # Log more details about the metadata content
                                if 'voice_settings' in metadata:
                                    logger.info(f"Voice settings found: {metadata['voice_settings']}")
                                if 'system_prompt' in metadata:
                                    logger.info(f"System prompt length: {len(metadata['system_prompt'])} chars")
                                if 'client_id' in metadata:
                                    logger.info(f"Client ID: {metadata['client_id']}")
                                
                                # Log if API keys are present
                                if 'api_keys' in metadata:
                                    available_keys = [k for k, v in metadata['api_keys'].items() if v and str(v).lower() not in ['test_key', 'test', 'dummy']]
                                    logger.info(f"API keys in metadata: {len(available_keys)} real keys found")
                            except json.JSONDecodeError as e:
                                # Strict JSON parsing - fail loudly with detailed error info
                                logger.error(f"Failed to parse room metadata as JSON: {e}")
                                logger.error(f"JSON error position: line {e.lineno}, column {e.colno}")
                                logger.error(f"Raw metadata (first 500 chars): {room_info.metadata[:500]}")
                                logger.error(f"Metadata type: {type(room_info.metadata)}, length: {len(room_info.metadata)}")
                                # Re-raise to ensure the error is visible
                                raise ValueError(f"Room metadata must be valid JSON. Parse error: {e}")
                        else:
                            logger.warning("Room has no metadata in API response")
                    else:
                        logger.warning(f"Room {ctx.room.name} not found in API response")
                finally:
                    if livekit_api:
                        try:
                            await livekit_api.aclose()
                        except Exception as close_error:
                            logger.warning(f"Failed to close LiveKitAPI session: {close_error}")
            else:
                missing = []
                if not livekit_url: missing.append("LIVEKIT_URL")
                if not livekit_api_key: missing.append("LIVEKIT_API_KEY")
                if not livekit_api_secret: missing.append("LIVEKIT_API_SECRET")
                logger.error(f"LiveKit credentials missing: {', '.join(missing)}")
                
        except Exception as e:
            logger.error(f"Failed to fetch room info from API: {e}", exc_info=True)
            
        # Check if room object has metadata attribute (backward compatibility)
        if not metadata and hasattr(ctx.room, 'metadata'):
            logger.info(f"Checking room.metadata attribute: {getattr(ctx.room, 'metadata', None)}")
            room_metadata = getattr(ctx.room, 'metadata', None)
            if room_metadata:
                if isinstance(room_metadata, str):
                    try:
                        metadata = json.loads(room_metadata)
                        logger.info(f"Room metadata from attribute: {metadata}")
                    except Exception as e:
                        logger.warning(f"Failed to parse room.metadata: {e}")
                elif isinstance(room_metadata, dict):
                    metadata = room_metadata
                    logger.info(f"Room metadata dict from attribute: {metadata}")
        
        # Merge job metadata (explicit dispatch) with room metadata, preferring job keys
        if hasattr(ctx, 'job') and ctx.job and hasattr(ctx.job, 'metadata'):
            logger.info(f"Checking job metadata: {ctx.job.metadata}")
            try:
                job_metadata = None
                if isinstance(ctx.job.metadata, str) and ctx.job.metadata:
                    job_metadata = json.loads(ctx.job.metadata)
                    logger.info(f"Successfully parsed job metadata with {len(job_metadata)} keys")
                elif isinstance(ctx.job.metadata, dict):
                    job_metadata = ctx.job.metadata
                
                if isinstance(job_metadata, dict):
                    base = metadata if isinstance(metadata, dict) else {}
                    # Merge: room/previous metadata first, then override with job metadata
                    merged = {**base, **job_metadata}
                    metadata = merged
                    logger.info("Merged room metadata with job metadata (job has priority)")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse job metadata as JSON: {e}")
                logger.error(f"Raw job metadata (first 500 chars): {ctx.job.metadata[:500]}")
                logger.error(f"Job metadata type: {type(ctx.job.metadata)}, length: {len(ctx.job.metadata)}")
                logger.warning("Continuing with room metadata if available")
        
        if not metadata:
            logger.warning("No metadata found in room or job, using defaults")
            # For rooms created by frontend without metadata, use environment variables
            metadata = {
                "system_prompt": "You are a helpful AI assistant.",
                "voice_settings": {
                    "llm_provider": "openai",
                    "stt_provider": "deepgram",
                    "tts_provider": "cartesia",
                    "voice_id": "248be419-c632-4f23-adf1-5324ed7dbf1d"
                }
            }
            
        # Detect requested interaction mode (voice default)
        raw_mode = metadata.get("mode") or metadata.get("conversation_mode")
        requested_mode = str(raw_mode or "").strip().lower()
        if not requested_mode and metadata.get("user_message"):
            requested_mode = "text"
        if requested_mode not in ("text", "voice", "video"):
            logger.warning(f"Mode not provided or unrecognized ({requested_mode!r}); defaulting to voice")
            requested_mode = "voice"
        is_text_mode = requested_mode == "text"
        is_video_mode = requested_mode == "video"
        metadata["mode"] = requested_mode
        mode_label = "TEXT" if is_text_mode else ("VIDEO" if is_video_mode else "VOICE")
        logger.info(f"🎯 Agent job running in {mode_label} mode")
        text_response_collector: Optional[TextResponseCollector] = TextResponseCollector() if is_text_mode else None

        # Load API keys using the loader (follows dynamic loading policy)
        api_keys = APIKeyLoader.load_api_keys(metadata)
        metadata['api_keys'] = api_keys
        
        # CRITICAL: Validate configuration before proceeding
        start_config = time.perf_counter()
        try:
            ConfigValidator.validate_configuration(metadata, api_keys)
        except ConfigurationError as e:
            logger.error(f"❌ Configuration validation failed: {e}")
            # Fail fast - don't try to start with invalid configuration
            raise
        perf_summary['config_validation'] = time.perf_counter() - start_config
        
        # CRITICAL: Store user_id on the context for later use in event handlers
        ctx.user_id = metadata.get("user_id", "unknown")
        logger.info(f"Stored user_id on context: {ctx.user_id}")

        # --- Migrated Agent Logic ---
        try:
            # Extract configuration from metadata
            system_prompt = metadata.get("system_prompt", "You are a helpful AI assistant.")
            voice_settings = metadata.get("voice_settings", {})
            llm_provider = voice_settings.get("llm_provider", metadata.get("llm_provider", "openai"))
            
            # Configure LLM based on provider - NO FALLBACK to environment variables
            if not llm_provider:
                raise ConfigurationError("LLM provider required but not found (llm_provider)")
            if llm_provider == "groq":
                groq_key = api_keys.get("groq_api_key")
                if not groq_key:
                    raise ConfigurationError("Groq API key required but not found")
                # Get model from voice_settings or metadata, with updated default
                model = voice_settings.get("llm_model", metadata.get("model", "llama-3.3-70b-versatile"))
                # Map old model names to new ones
                if model == "llama3-70b-8192" or model == "llama-3.1-70b-versatile":
                    model = "llama-3.3-70b-versatile"
                
                # Groq LLM with explicit tool calling configuration
                # Note: Groq may not fully support structured tool calling, which can cause
                # the LLM to generate XML-like text instead of proper function calls
                llm_plugin = groq.LLM(
                    model=model,
                    api_key=groq_key,
                    temperature=voice_settings.get("temperature", 0.8)
                )
            elif llm_provider == "cerebras":
                cerebras_key = api_keys.get("cerebras_api_key")
                if not cerebras_key:
                    raise ConfigurationError("Cerebras API key required but not found")
                # LiveKit uses openai plugin shim for Cerebras per docs
                # from livekit.plugins import openai as lk_openai  (already imported as openai)
                os.environ["CEREBRAS_API_KEY"] = cerebras_key
                # Align with Cerebras documented chat models
                # https://inference-docs.cerebras.ai/api-reference/chat-completions
                model = voice_settings.get("llm_model", metadata.get("model", "llama3.1-8b"))
                llm_plugin = openai.LLM.with_cerebras(
                    model=model
                )
            elif llm_provider == "deepinfra":
                deepinfra_key = api_keys.get("deepinfra_api_key")
                if not deepinfra_key:
                    raise ConfigurationError("DeepInfra API key required but not found")
                model = voice_settings.get("llm_model", metadata.get("model", "meta-llama/Llama-3.1-8B-Instruct"))
                # Use OpenAI-compatible base_url for DeepInfra
                llm_plugin = openai.LLM(
                    model=model,
                    api_key=deepinfra_key,
                    base_url="https://api.deepinfra.com/v1/openai"
                )
            else:
                openai_key = api_keys.get("openai_api_key")
                if not openai_key:
                    raise ConfigurationError("OpenAI API key required but not found")
                llm_plugin = openai.LLM(
                    model=metadata.get("model", "gpt-4"),
                    api_key=openai_key
                )
            
            # Validate LLM initialization
            ConfigValidator.validate_provider_initialization(f"{llm_provider} LLM", llm_plugin)
            
            stt_plugin = None
            tts_plugin = None
            vad = None
            stt_provider = voice_settings.get("stt_provider")
            tts_provider = voice_settings.get("tts_provider") or voice_settings.get("provider")

            if not is_text_mode:
                # Configure STT - NO FALLBACK to environment variables
                if not stt_provider:
                    raise ConfigurationError("STT provider required but not found (stt_provider)")
                if stt_provider == "cartesia":
                    cartesia_key = api_keys.get("cartesia_api_key")
                    if not cartesia_key:
                        raise ConfigurationError("Cartesia API key required for STT but not found")
                    stt_plugin = cartesia.STT(
                        api_key=cartesia_key,
                        model="ink-whisper"  # Cartesia's STT model
                    )
                    logger.info("📊 DIAGNOSTIC: Cartesia STT configured with model=ink-whisper")
                else:
                    deepgram_key = api_keys.get("deepgram_api_key")
                    if not deepgram_key:
                        raise ConfigurationError("Deepgram API key required for STT but not found")
                    stt_plugin = deepgram.STT(
                        api_key=deepgram_key,
                        model="nova-3",
                        language="en-US",
                        endpointing_ms=False,  # Disable Deepgram endpointing - let turn detector handle it
                        interim_results=True    # Enable interim results for turn detector
                    )
                    logger.info("📊 DIAGNOSTIC: Deepgram configured with model=nova-3, language=en-US, endpointing_ms=False (turn detector handles endpointing)")
                ConfigValidator.validate_provider_initialization(f"{stt_provider} STT", stt_plugin)

                tts_plugin = _initialize_tts_plugin(
                    tts_provider=tts_provider,
                    voice_settings=voice_settings,
                    api_keys=api_keys,
                )

                try:
                    # VAD parameters tuned to reduce false positives from ambient noise/music
                    # min_speech_duration: 0.25s - requires sustained speech, filters brief sounds
                    # min_silence_duration: 0.5s - standard pause detection
                    vad = silero.VAD.load(
                        min_speech_duration=0.25,
                        min_silence_duration=0.5,
                    )
                    logger.info("✅ VAD loaded successfully with optimized parameters")
                    logger.info(f"📊 DIAGNOSTIC: VAD type: {type(vad)}")
                    logger.info("📊 DIAGNOSTIC: VAD params: min_speech=0.25s, min_silence=0.5s")
                except Exception as e:
                    logger.error(f"❌ Failed to load VAD: {e}", exc_info=True)
                    raise
            else:
                logger.info("📝 Text-only mode: skipping STT/VAD; keeping TTS to satisfy LiveKit pipeline")
                try:
                    tts_plugin = _initialize_tts_plugin(
                        tts_provider=tts_provider,
                        voice_settings=voice_settings,
                        api_keys=api_keys,
                    )
                except Exception as tts_err:
                    logger.warning(
                        "⚠️ Text-mode TTS setup failed (%s); proceeding without TTS (will force LLM fallback)",
                        tts_err,
                    )
                    tts_plugin = None
            
            # Remove multilingual turn detector to avoid heavy HF model load/crash
            turn_detect = None
            
            # Initialize context manager if we have Supabase credentials
            context_manager = None
            client_id = metadata.get("client_id")  # Define at outer scope for use throughout
            start_context_manager = time.perf_counter()
            try:
                # Check if we can connect to client's Supabase
                logger.info("🔍 Checking for Supabase credentials in metadata...")
                client_supabase_url = metadata.get("supabase_url")
                # Get service role key (required - no fallback)
                client_supabase_key = metadata.get("supabase_service_key") or metadata.get("supabase_service_role_key")
                
                # If no service key, try to load from environment
                if not client_supabase_key:
                    if client_id:
                        env_key = f"CLIENT_{client_id.replace('-', '_').upper()}_SUPABASE_SERVICE_KEY"
                        client_supabase_key = os.getenv(env_key)
                        if client_supabase_key:
                            logger.info(f"📌 Using service key from environment: {env_key}")
                
                # NO FALLBACK POLICY: Fail fast if no service role key
                if not client_supabase_key:
                    logger.error("❌ No service role key found for client Supabase - cannot proceed")
                    raise ValueError("Client Supabase service role key is required - no fallback to anon key allowed")
                
                logger.info(f"📌 Supabase URL found: {bool(client_supabase_url)}")
                logger.info(f"📌 Supabase key found: {bool(client_supabase_key)}")
                
                if client_supabase_url and client_supabase_key:
                    from supabase import create_client
                    try:
                        client_supabase = create_client(client_supabase_url, client_supabase_key)
                        logger.info("✅ Client Supabase connection created")
                    except Exception as e:
                        logger.error(f"Failed to create Supabase client: {e}")
                        client_supabase = None
                    
                    # Create context manager
                    context_manager = AgentContextManager(
                        supabase_client=client_supabase,
                        agent_config=metadata,
                        user_id=metadata.get("user_id", "unknown"),
                        client_id=metadata.get("client_id", "unknown"),
                        api_keys=api_keys
                    )
                    logger.info("✅ Context manager initialized successfully")
                else:
                    logger.warning("No client Supabase credentials found - context features disabled")
            except Exception as e:
                logger.error(f"Failed to initialize context manager: {e}")
                logger.error(f"Context initialization error details: {type(e).__name__}: {str(e)}")
                context_manager = None
                # Continue without context - don't fail the entire agent
            perf_summary['context_manager_init'] = time.perf_counter() - start_context_manager

            # Connect to the room first
            start_connect = time.perf_counter()
            logger.info("Connecting to room with close_on_disconnect=False for stability...")
            # Connect; per SDK version, input options are applied on session.start below
            await ctx.connect(auto_subscribe="audio_only")  # Only subscribe to audio tracks for voice agents
            logger.info("✅ Connected to room successfully")
            perf_summary['room_connection'] = time.perf_counter() - start_connect
            
            # First, try to build initial context if context manager is available
            enhanced_prompt = system_prompt
            
            if context_manager:
                try:
                    # Build initial context (only user profile, no RAG searches)
                    logger.info("Building initial context for agent...")
                    initial_context = await context_manager.build_initial_context(
                        user_id=ctx.user_id  # Pass user_id for initial context
                    )
                    
                    logger.info("✅ Initial context built successfully")
                    
                    enhanced_prompt = initial_context["enhanced_system_prompt"]
                    
                    # Log context metadata for debugging
                    logger.info(f"Initial context metadata: {initial_context['context_metadata']}")
                    
                    # If development mode, log the full enhanced prompt
                    if os.getenv("DEVELOPMENT_MODE", "false").lower() == "true":
                        logger.info(f"Enhanced System Prompt:\n{enhanced_prompt}")
                except Exception as e:
                    logger.error(f"Failed to build initial context: {e}")
                    logger.error(f"Context error type: {type(e).__name__}")
                    logger.error(f"Context error details: {str(e)}")
                    
                    # Initial context errors are not critical - we can continue with basic prompt
                    logger.warning("Initial context enhancement failed, continuing with basic prompt")
            
            # Keep the original enhanced prompt without hardcoded greeting
            # The proactive greeting will be handled via generate_reply() after session.start()

            # Add voice-specific instructions for formatting and behavior
            # This ensures the LLM outputs well-formatted responses and doesn't comment on message format
            voice_instruction = (
                "\n\n## Response Guidelines\n\n"
                "**Voice Conversation:** This is a voice conversation. The user is speaking, not typing. "
                "Never comment on how the user is 'typing' or the 'format' of their messages. "
                "If a previous response was interrupted, continue naturally without mentioning it.\n\n"
                "**Formatting:** Structure your responses for readability:\n"
                "- Use **bold** for key terms, names, and important concepts\n"
                "- Use headers (##) for major topics when giving longer explanations\n"
                "- Use numbered lists (1. 2. 3.) for steps or sequences\n"
                "- Use bullet points (-) for listing items or features\n"
                "- Add clear paragraph breaks between distinct ideas\n"
                "- Keep sentences concise and easy to follow when spoken aloud"
            )
            enhanced_prompt = enhanced_prompt + voice_instruction
            greeting_enhanced_prompt = enhanced_prompt
            
            # Extract the user profile from initial context if available
            user_profile = None
            if 'initial_context' in locals() and initial_context:
                # Log what's in initial_context for debugging
                logger.info(f"📊 DIAGNOSTIC: initial_context keys: {list(initial_context.keys()) if isinstance(initial_context, dict) else 'not a dict'}")
                if "raw_context_data" in initial_context:
                    logger.info(f"📊 DIAGNOSTIC: raw_context_data keys: {list(initial_context['raw_context_data'].keys())}")
                    user_profile = initial_context.get("raw_context_data", {}).get("user_profile")
                    logger.info(f"📊 DIAGNOSTIC: Extracted user_profile: {user_profile}")
            
            # Extract names for later use in proactive greeting (prefer profile)
            user_name = (
                metadata.get("user_name")
                or metadata.get("display_name")
                or (user_profile.get("full_name") if isinstance(user_profile, dict) and user_profile.get("full_name") else None)
                or (user_profile.get("name") if isinstance(user_profile, dict) and user_profile.get("name") else None)
                or "there"
            )
            agent_name = metadata.get("name", metadata.get("agent_name", "your AI assistant"))
            
            logger.info(f"📢 Agent will greet user '{user_name}' as '{agent_name}' after session starts")
            
            # Use base LLM plugin directly (RAG/context injection handled in SidekickAgent hooks)
            logger.info("✅ Using base LLM plugin directly (no wrapper)")

            # Preflight: verify LLM invocation works (base and wrapper) before starting session
            async def _llm_preflight():
                try:
                    # Base LLM
                    base_msgs = [
                        llm.ChatMessage(role="system", content=[{"type": "text", "text": "You are a concise assistant."}]),
                        llm.ChatMessage(role="user", content=[{"type": "text", "text": "Reply with the single word: pong"}])
                    ]
                    logger.info("🔬 LLM preflight (base): starting")
                    base_stream = llm_plugin.chat(base_msgs)
                    first = None
                    async for chunk in base_stream:
                        first = chunk
                        break
                    if first is None:
                        raise RuntimeError("No tokens from base LLM preflight")
                    logger.info("✅ LLM preflight (base) produced tokens")

                    # No wrapper preflight (wrapper removed)
                except Exception as e:
                    logger.critical(f"🚨 LLM preflight failed: {type(e).__name__}: {e}")
                    raise

            # Optionally run LLM preflight if explicitly enabled via env
            if os.getenv("ENABLE_LLM_PREFLIGHT", "false").lower() == "true":
                # Run but do NOT abort session if it fails; log and continue
                try:
                    await asyncio.wait_for(_llm_preflight(), timeout=6.0)
                except Exception as e:
                    logger.warning(f"LLM preflight failed (non-blocking): {type(e).__name__}: {e}")
            else:
                logger.info("Skipping LLM preflight (ENABLE_LLM_PREFLIGHT=false)")
            
            # Create and configure the voice agent session with instructions
            # In LiveKit SDK v1.0+, AgentSession IS the agent - no separate Agent object is required
            logger.info("Creating voice agent session with system prompt...")
            # Create SidekickAgent directly - eliminate nested AgentSession + voice.Agent architecture
            # This fixes duplicate event handling by using single layer architecture
            
            # Get agent_id and config from metadata (prefer UUID)
            agent_id = metadata.get("agent_id") or metadata.get("agent_slug") or "default"
            agent_slug = metadata.get("agent_slug") or metadata.get("agent_id") or "default"
            show_citations = metadata.get("show_citations", True)
            dataset_ids = metadata.get("dataset_ids", [])
            
            agent = SidekickAgent(
                instructions=enhanced_prompt,
                stt=stt_plugin,
                llm=llm_plugin,
                tts=tts_plugin,
                vad=vad,
                context_manager=context_manager,
                user_id=ctx.user_id,
                client_id=client_id,
                agent_config={
                    'id': agent_id,
                    'agent_slug': agent_slug,
                    'show_citations': show_citations,
                    'dataset_ids': dataset_ids,
                    'rag_results_limit': metadata.get("rag_results_limit"),
                    'rerank': metadata.get("rerank"),
                    'api_keys': metadata.get("api_keys"),
                },
            )
            logger.info("✅ Voice agent created with single-layer architecture")
            if is_text_mode and text_response_collector:
                agent.attach_text_response_collector(text_response_collector)
                logger.info("📝 Text-only mode collector attached to agent")
            
            # Store references for event handlers in agent
            agent._room = ctx.room  # Store room reference for agent use
            # Enforce conversation_id from metadata (no-fallback policy)
            conv_id = metadata.get("conversation_id")
            if not conv_id:
                logger.critical("❌ Missing conversation_id in metadata - cannot proceed (no-fallback policy)")
                raise ConfigurationError("conversation_id is required in room/job metadata")
            agent._conversation_id = conv_id
            logger.info(f"📌 Using conversation_id: {agent._conversation_id}")
            client_conv_id = metadata.get("client_conversation_id") or conv_id
            agent._client_conversation_id = client_conv_id
            # Pass the Supabase client that was created earlier
            agent._supabase_client = client_supabase if 'client_supabase' in locals() else None
            # Ensure transcript storage uses UUID when available
            agent._agent_id = metadata.get("agent_id") or metadata.get("agent_slug")
            agent._user_id = metadata.get("user_id") or ctx.user_id

            base_tool_context: Dict[str, Any] = {
                "conversation_id": agent._conversation_id,
                "client_conversation_id": metadata.get("client_conversation_id") or agent._conversation_id,
                "user_id": agent._user_id,
                "agent_id": metadata.get("agent_id"),  # UUID for update_user_overview tool
                "agent_slug": metadata.get("agent_slug") or metadata.get("agent_id"),
                "client_id": metadata.get("client_id") or client_id,
                "session_id": metadata.get("session_id")
                or metadata.get("voice_session_id")
                or metadata.get("room_session_id"),
            }
            if is_text_mode:
                user_msg = (metadata.get("user_message") or "").strip()
                if user_msg:
                    base_tool_context["latest_user_text"] = user_msg
                    try:
                        agent.latest_user_text = user_msg
                        agent._current_turn_text = user_msg
                    except Exception:
                        pass

            registry: Optional[ToolRegistry] = None
            tracked_tool_slugs: List[str] = []
            tool_results_buffer: List[Dict[str, Any]] = []
            try:
                agent._tool_results_buffer = tool_results_buffer  # type: ignore[attr-defined]
            except Exception:
                pass

            def _tool_result_callback(entry: Dict[str, Any]) -> None:
                if not isinstance(entry, dict):
                    return
                tool_results_buffer.append(entry)
                try:
                    agent._latest_tool_results = list(tool_results_buffer)
                except Exception:
                    pass

            def push_runtime_context(updates: Dict[str, Any]) -> None:
                if not registry or not tracked_tool_slugs or not isinstance(updates, dict):
                    return
                sanitized = {k: v for k, v in updates.items() if v is not None}
                if not sanitized:
                    return
                for slug in tracked_tool_slugs:
                    registry.update_runtime_context(slug, sanitized)
                try:
                    logger.info(
                        "🧰 Runtime context updated for %s with keys=%s",
                        tracked_tool_slugs,
                        list(sanitized.keys()),
                    )
                except Exception:
                    pass
            
            # Log what's available for transcript storage
            logger.info(f"📝 Transcript storage setup:")
            logger.info(f"   - Has Supabase: {agent._supabase_client is not None}")
            logger.info(f"   - Conversation ID: {agent._conversation_id}")
            logger.info(f"   - Agent ID: {agent._agent_id}")
            logger.info(f"   - User ID: {agent._user_id}")
            
            logger.info(f"📊 DIAGNOSTIC: Agent type: {type(agent)}")
            logger.info(f"📊 DIAGNOSTIC: Agent inherits from: {type(agent).__bases__}")
            
            # Set up transcript storage with room reference
            agent.setup_transcript_storage(ctx.room)
            
            # Register tools (Abilities) if provided in metadata
            # Always include built-in tools like update_user_overview
            try:
                tool_defs = list(metadata.get("tools") or [])

                # Add built-in user_overview tool for all agents
                user_overview_tool_def = {
                    "id": "builtin_update_user_overview",
                    "slug": "update_user_overview",
                    "type": "user_overview",
                    "description": "Update persistent notes about this user (shared across all sidekicks)."
                }
                tool_defs.append(user_overview_tool_def)

                if tool_defs:
                    logger.info(f"🧰 Preparing to register tools: count={len(tool_defs)} (including built-in)")
                    try:
                        slugs = [t.get("slug") or t.get("name") or t.get("id") for t in tool_defs]
                        logger.info(f"🧰 Tool defs slugs: {slugs}")
                    except Exception:
                        pass
                    primary_supabase_client = client_supabase if 'client_supabase' in locals() else None
                    registry = ToolRegistry(
                        tools_config=metadata.get("tools_config") or {},
                        api_keys=metadata.get("api_keys") or {},
                        primary_supabase_client=primary_supabase_client,
                        platform_supabase_client=PLATFORM_SUPABASE,
                        tool_result_callback=_tool_result_callback if is_text_mode else None,
                    )
                    tools = registry.build(tool_defs)
                    if tools:
                        tracked_tool_slugs = []
                        for tool_def in tool_defs:
                            tool_type = tool_def.get("type")
                            # Track runtime context for tools that need user/client context
                            if tool_type not in {"n8n", "asana", "user_overview", "content_catalyst", "documentsense"}:
                                continue
                            slug_candidate = (
                                tool_def.get("slug")
                                or tool_def.get("name")
                                or tool_def.get("id")
                            )
                            if slug_candidate:
                                tracked_tool_slugs.append(slug_candidate)
                        if tracked_tool_slugs:
                            logger.info(
                                "🧰 Tracking runtime context for tools: %s",
                                tracked_tool_slugs,
                            )
                            push_runtime_context(base_tool_context)
                        combined_tools = list(agent.tools) + list(tools)
                        update_fn = getattr(agent, "update_tools", None)
                        if callable(update_fn):
                            result = update_fn(combined_tools)
                            if inspect.isawaitable(result):
                                await result
                            logger.info(
                                "🧰 Registered %s tools via agent.update_tools",
                                len(tools),
                            )
                        else:
                            fallback = list(getattr(agent, "_injected_tools", []) or [])
                            fallback.extend(tools)
                            setattr(agent, "_injected_tools", fallback)
                            logger.info(
                                "🧰 Agent update_tools missing; stored %s tools on agent fallback",
                                len(tools)
                            )
                    else:
                        logger.warning("🧰 No tools were built from provided definitions")
            except Exception as e:
                logger.warning(f"Tool registration failed: {type(e).__name__}: {e}")
            
            # ========================================================================
            # CREATE AGENT SESSION **AFTER** TOOLS ARE REGISTERED
            # This ensures the LLM has function definitions available for calling
            # ========================================================================
            logger.info("Creating AgentSession with plugins (after tool registration)...")
            # Use LiveKit's ML-based turn detection model instead of STT-based turn detection
            # This provides better handling of multi-part requests and natural pauses
            # The model understands conversational context to reduce fragmentation
            session = voice.AgentSession(
                vad=vad,
                stt=stt_plugin,
                llm=llm_plugin,
                tts=tts_plugin,
                turn_detection=EnglishModel(),           # ML-based turn detection
                # TTS-aligned transcriptions for better frontend synchronization
                use_tts_aligned_transcript=True,         # Enable word-level transcription timing (Cartesia/ElevenLabs)
                # Endpointing parameters for turn detection model
                min_endpointing_delay=2.0,               # INCREASED: Allow 2s pause before considering turn complete
                max_endpointing_delay=10.0,              # INCREASED: Allow longer thoughtful pauses
                # Interruption settings that prevent scheduler from getting stuck
                allow_interruptions=True,
                min_interruption_duration=0.5,           # Increased to avoid accidental interruptions
                min_interruption_words=0,                # Duration-based, not word-based
                resume_false_interruption=False,         # CRITICAL: Never try to resume - treat all interruptions as final
                false_interruption_timeout=10.0,         # Very high timeout - essentially disable false interruption detection
                discard_audio_if_uninterruptible=True   # Always discard audio on interruption
            )
            logger.info("✅ AgentSession created with %s tools available to LLM", len(agent.tools))
            # Preserve the TTS plugin reference for text-mode diagnostics
            try:
                session._text_tts_plugin = tts_plugin
            except Exception:
                pass
            
            # Log and capture STT transcripts; commit turn on finals
            commit_delay = float(os.getenv("VOICE_TURN_COMMIT_DELAY", "1.4"))
            commit_timeout = float(os.getenv("VOICE_TRANSCRIPT_TIMEOUT", "0.8"))

            if not hasattr(session, "_pending_commit_task"):
                session._pending_commit_task = None
            if not hasattr(session, "_current_turn_text"):
                session._current_turn_text = ""
            if not hasattr(agent, "_current_turn_text"):
                agent._current_turn_text = ""
            if not hasattr(session, "_last_committed_text"):
                session._last_committed_text = ""
            if not hasattr(agent, "_last_committed_text"):
                agent._last_committed_text = ""

            def _schedule_turn_commit():
                pending = getattr(session, "_pending_commit_task", None)
                if pending and not pending.done():
                    pending.cancel()

                async def _delayed_commit():
                    try:
                        await asyncio.sleep(commit_delay)
                        buffered = getattr(session, "_current_turn_text", "").strip()
                        if not buffered:
                            logger.debug("Delayed commit skipped (buffer cleared before execution)")
                            return
                        session.latest_user_text = buffered
                        agent.latest_user_text = buffered
                        session._last_committed_text = buffered
                        agent._last_committed_text = buffered
                        push_runtime_context({"latest_user_text": buffered})
                        session.commit_user_turn(transcript_timeout=commit_timeout)
                        session._current_turn_text = ""
                        agent._current_turn_text = ""
                    except asyncio.CancelledError:
                        logger.debug("Delayed commit cancelled before execution")
                    except Exception as ce:
                        logger.warning(f"commit_user_turn failed: {type(ce).__name__}: {ce}")
                    finally:
                        session._pending_commit_task = None

                session._pending_commit_task = asyncio.create_task(_delayed_commit())

            def _commit_user_transcript_text(user_text: str) -> None:
                if not hasattr(agent, "store_transcript"):
                    return
                try:
                    logger.info(
                        "📝 Scheduling user transcript commit (turn_id=%s, len=%s)",
                        getattr(agent, "_current_turn_id", None),
                        len(user_text),
                    )
                except Exception:
                    pass
                if _should_skip_user_commit(agent, user_text):
                    logger.info(
                        "📝 Duplicate user transcript suppressed for active turn (turn_id=%s)",
                        getattr(agent, "_current_turn_id", None) or "pending",
                    )
                    return
                try:
                    agent._last_user_commit = user_text  # type: ignore[attr-defined]
                    agent._pending_user_commit = True  # type: ignore[attr-defined]
                except Exception:
                    pass
                normalized_user_text = _normalize_transcript_text(user_text)
                turn_snapshot = getattr(agent, "_current_turn_id", None)

                async def _store_user_transcript():
                    try:
                        await agent.store_transcript("user", user_text)
                        try:
                            agent._last_user_commit_turn = turn_snapshot or getattr(agent, "_current_turn_id", None)  # type: ignore[attr-defined]
                            agent._last_user_commit_normalized = normalized_user_text  # type: ignore[attr-defined]
                        except Exception:
                            pass
                    finally:
                        try:
                            agent._pending_user_commit = False  # type: ignore[attr-defined]
                        except Exception:
                            pass

                asyncio.create_task(_store_user_transcript())

            def _merge_transcript_text(existing: str, incoming: str) -> str:
                """
                Combine partial ASR chunks into a single utterance while avoiding obvious duplication.

                The Deepgram stream we receive sometimes emits disjoint final chunks (e.g., "Hey" then
                "is coming"), so we append when the new chunk does not already appear in the aggregate.
                If the recognizer sends the entire sentence, we replace the aggregate to keep spacing right.
                """

                if not incoming:
                    return existing

                incoming = incoming.strip()
                if not incoming:
                    return existing

                if not existing:
                    return incoming

                # If the incoming chunk already contains the existing text, prefer the richer version
                if incoming.startswith(existing) or existing in incoming:
                    return incoming

                # If the existing text already includes this chunk (or a trimmed variant), keep existing
                if incoming in existing:
                    return existing

                # Otherwise append with a space separator
                return f"{existing.rstrip()} {incoming}".strip()


            def _strip_assistant_echo(txt_raw: str, txt_norm: str, recent_greet: str, last_assistant: str):
                """
                Remove assistant/greeting phrases that leaked into the mic.
                Returns (clean_raw, clean_norm). If everything is stripped, returns ("", "").
                """
                clean_raw = txt_raw or ""
                clean_norm = txt_norm or ""

                echo_candidates = []
                if recent_greet:
                    echo_candidates.append(recent_greet)
                if last_assistant:
                    echo_candidates.append(last_assistant)
                echo_candidates.append("how can i help you")  # common greeting phrase

                for phrase in echo_candidates:
                    if not phrase:
                        continue
                    p_norm = _normalize_for_compare(phrase)
                    if not p_norm:
                        continue
                    if p_norm in clean_norm:
                        clean_norm = clean_norm.replace(p_norm, "").strip()
                        try:
                            import re
                            clean_raw = re.sub(re.escape(phrase), "", clean_raw, flags=re.IGNORECASE).strip()
                        except Exception:
                            pass

                return clean_raw, clean_norm

            @session.on("user_input_transcribed")
            def on_user_input_transcribed(ev):
                try:
                    txt = getattr(ev, 'transcript', '') or ''
                    is_final = bool(getattr(ev, 'is_final', False))
                    logger.info(f"📝 STT transcript (raw): '{txt[:200]}' final={is_final}")

                    # Drop/strip transcripts that include the agent's recent greeting/response (echo)
                    txt_norm = _normalize_for_compare(txt)
                    recent_greet = getattr(session, "_recent_greeting_norm", "")
                    last_assistant = _normalize_for_compare(getattr(agent, "_last_assistant_commit", ""))
                    if txt_norm:
                        stripped_raw, stripped_norm = _strip_assistant_echo(txt, txt_norm, recent_greet, last_assistant)
                        if stripped_norm != txt_norm:
                            logger.info("🔇 Stripped assistant echo from transcript (remaining='%s')", stripped_raw[:120])
                        txt, txt_norm = stripped_raw, stripped_norm
                    if not txt_norm:
                        logger.info("🚫 Dropping transcript that matches recent assistant speech (echo suppression)")
                        return

                    if txt:
                        prev_turn_text = getattr(session, "_current_turn_text", "")
                        if not prev_turn_text:
                            try:
                                session._user_transcript_committed = False
                                session._user_transcript_committed_text = ""
                                session._user_transcript_committed_turn = None
                            except Exception:
                                pass
                        merged = _merge_transcript_text(
                            prev_turn_text,
                            txt,
                        )
                        session._current_turn_text = merged
                        agent._current_turn_text = merged
                        session.latest_user_text = merged
                        agent.latest_user_text = merged
                        if is_final:
                            # Only interrupt if this is NOT a duplicate transcript for the same turn
                            should_skip_duplicate = _should_skip_user_commit(agent, merged)
                            if not should_skip_duplicate:
                                try:
                                    current_speech = getattr(session, 'current_speech', None)
                                    logger.info("🔊 Attempting interrupt: current_speech=%s, merged_text=%s",
                                               current_speech is not None, merged[:50] if merged else "")

                                    # WORKAROUND: The SDK's current_speech may be None even when audio is playing
                                    # This happens because _current_speech is cleared after _wait_for_generation()
                                    # but audio playout continues. We need to aggressively clear all audio buffers.
                                    audio_buffer_cleared = False
                                    try:
                                        if session.output and session.output.audio:
                                            # Traverse the entire audio output chain and clear all buffers
                                            audio_output = session.output.audio
                                            chain_depth = 0
                                            while audio_output and chain_depth < 10:
                                                chain_depth += 1
                                                output_type = type(audio_output).__name__

                                                # Clear buffer if available
                                                if hasattr(audio_output, 'clear_buffer'):
                                                    audio_output.clear_buffer()
                                                    logger.info(f"🔇 Cleared buffer on {output_type}")
                                                    audio_buffer_cleared = True

                                                # Also clear the underlying rtc.AudioSource queue if accessible
                                                if hasattr(audio_output, '_audio_source'):
                                                    src = audio_output._audio_source
                                                    if hasattr(src, 'clear_queue'):
                                                        src.clear_queue()
                                                        logger.info(f"🔇 Cleared rtc.AudioSource queue on {output_type}")

                                                # Also clear any internal buffers
                                                if hasattr(audio_output, '_audio_buf'):
                                                    buf = audio_output._audio_buf
                                                    # Drain the channel
                                                    try:
                                                        while True:
                                                            buf.recv_nowait()
                                                    except Exception:
                                                        pass
                                                    logger.info(f"🔇 Drained _audio_buf on {output_type}")

                                                # Move to next in chain
                                                audio_output = getattr(audio_output, '_next_in_chain', None)
                                    except Exception as audio_clear_err:
                                        logger.warning("⚠️ Audio buffer clear error: %s", audio_clear_err)

                                    # WORKAROUND #2: Directly interrupt the speech handle if we stored one
                                    # The SDK's _current_speech may be None, but we track our own handle
                                    try:
                                        active_handle = getattr(session, '_active_speech_handle', None)
                                        if active_handle and not active_handle.done() and not active_handle.interrupted:
                                            logger.info("🔇 Directly interrupting stored speech handle")
                                            active_handle.interrupt(force=True)
                                            # Clear our reference
                                            session._active_speech_handle = None
                                    except Exception as handle_err:
                                        logger.debug("Could not interrupt stored handle: %s", handle_err)

                                    fut = session.interrupt(force=True)
                                    if fut:
                                        if asyncio.iscoroutine(fut) or isinstance(fut, asyncio.Future):
                                            async def _await_interrupt(task):
                                                try:
                                                    await task
                                                    logger.info("⛔ Assistant speech interrupted due to user transcript final chunk")
                                                except Exception as interrupt_err:
                                                    logger.warning("⚠️ Interrupt future raised %s: %s", type(interrupt_err).__name__, interrupt_err)

                                            asyncio.create_task(_await_interrupt(fut))
                                        else:
                                            logger.info("⛔ Assistant speech interrupted (sync) due to user transcript final chunk")
                                    elif audio_buffer_cleared:
                                        logger.info("⛔ Audio interrupted via buffer clear (no active speech handle)")
                                    else:
                                        logger.debug("🔊 Interrupt returned None (no speech to interrupt)")
                                except Exception as interrupt_exc:
                                    logger.warning("⚠️ Interrupt call failed: %s: %s", type(interrupt_exc).__name__, interrupt_exc)
                            else:
                                logger.info("⏭️  Skipping interruption for duplicate final transcript (turn_id=%s)", getattr(agent, "_current_turn_id", None))
                            # Final chunk marks end of this user utterance; clear buffer immediately
                            session._current_turn_text = ""
                            agent._current_turn_text = ""
                        if is_final:
                            push_runtime_context({"latest_user_text": merged})
                            try:
                                session._user_transcript_committed = True
                                session._user_transcript_committed_text = _normalize_transcript_text(merged)
                                session._user_transcript_committed_turn = getattr(agent, "_current_turn_id", None)
                            except Exception:
                                pass
                            _schedule_turn_commit()
                            _commit_user_transcript_text(merged)
                except Exception as e:
                    logger.error(f"user_input_transcribed handler failed: {e}")

            # Minimal capture of latest user text for RAG, without watchdog or transcript writes
            @session.on("user_speech_committed")
            def on_user_speech(msg: llm.ChatMessage):
                try:
                    user_text = None
                    if hasattr(msg, 'content'):
                        if isinstance(msg.content, str):
                            user_text = msg.content
                        elif isinstance(msg.content, list):
                            for part in msg.content:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    user_text = part.get("text")
                                    break
                    if user_text:
                        normalized_user_text = _normalize_transcript_text(user_text)
                        if normalized_user_text:
                            already_committed = getattr(session, "_user_transcript_committed", False)
                            committed_text = getattr(session, "_user_transcript_committed_text", "")
                            if already_committed and committed_text == normalized_user_text:
                                logger.debug("📝 Skipping duplicate fallback transcript from user_speech_committed")
                                return
                        session.latest_user_text = user_text
                        agent.latest_user_text = user_text
                        logger.info(f"💬 Captured user speech: {user_text[:100]}...")
                        push_runtime_context({"latest_user_text": user_text})
                        if normalized_user_text:
                            try:
                                session._user_transcript_committed = True
                                session._user_transcript_committed_text = normalized_user_text
                                session._user_transcript_committed_turn = getattr(agent, "_current_turn_id", None)
                            except Exception:
                                pass
                        _commit_user_transcript_text(user_text)
                except Exception as e:
                    logger.error(f"Failed to capture user speech: {e}")

            # Deterministic finalize: commit assistant transcript on agent_speech_committed
            # NOTE: For voice mode, transcription_node handles all assistant transcript storage
            # This handler is now a NO-OP for assistant transcripts to prevent duplicates
            @session.on("agent_speech_committed")
            def on_agent_speech(msg: llm.ChatMessage):
                try:
                    agent_text = None
                    if hasattr(msg, 'content'):
                        if isinstance(msg.content, str):
                            agent_text = msg.content
                        elif isinstance(msg.content, list):
                            for part in msg.content:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    agent_text = part.get("text")
                                    break
                    if not agent_text:
                        return

                    # ALWAYS skip assistant transcript storage here - transcription_node handles it
                    # The transcription_node provides better streaming UX and is the authoritative source
                    logger.debug(f"📝 agent_speech_committed received ({len(agent_text)} chars) - skipping storage (transcription_node handles it)")

                    # Just track for deduplication in case store_transcript is called elsewhere
                    try:
                        agent._last_assistant_commit = agent_text
                    except Exception:
                        pass

                    # Reset user transcript state for next turn
                    try:
                        session._user_transcript_committed = False
                        session._user_transcript_committed_text = ""
                        session._user_transcript_committed_turn = None
                    except Exception:
                        pass
                except Exception as e:
                    logger.error(f"Failed in agent_speech_committed handler: {e}")

            # Store session reference on agent for access in on_user_turn_completed
            agent._agent_session = session
            
            # Start the session with the agent and room
            logger.info("Starting AgentSession with agent and room...")
            # Import room_io for input options
            from livekit.agents.voice import room_io
            
            # Configure input options to prevent early disconnect
            input_options = room_io.RoomInputOptions(
                close_on_disconnect=False  # Keep agent running even if user disconnects briefly
            )

            output_options = room_io.RoomOutputOptions(
                audio_enabled=True,
                transcription_enabled=True,
                audio_track_name="agent_audio",
            )

            if not is_text_mode:
                logger.info("Priming RoomIO audio output before starting AgentSession...")
                try:
                    session_room_io = room_io.RoomIO(
                        agent_session=session,
                        room=ctx.room,
                        input_options=input_options,
                        output_options=output_options,
                    )
                    await session_room_io.start()
                    logger.info(
                        "✅ RoomIO primed | audio_attached=%s transcription_attached=%s",
                        bool(session.output.audio),
                        bool(session.output.transcription),
                    )
                except Exception as room_io_err:
                    logger.error(f"❌ Failed to initialize RoomIO before session.start: {room_io_err}")
                    raise
            else:
                logger.info("📝 Text-only mode: skipping RoomIO audio priming")
        except Exception as agent_err:
            logger.error("❌ Agent session initialization failed: %s", agent_err, exc_info=True)
            raise
    except Exception as job_err:
        logger.error("❌ Failed to initialize job context or agent runtime: %s", job_err, exc_info=True)
        raise
