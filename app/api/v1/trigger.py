"""
Agent trigger endpoint for WordPress plugin integration
"""
from typing import Optional, Dict, Any, List, Tuple, Callable, Awaitable, AsyncIterator
import copy
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from enum import Enum
import logging
import asyncio
import json
import uuid
import time
from datetime import datetime
import traceback  # For detailed errors
import re

from app.services.agent_service_supabase import AgentService
from app.services.client_service_supabase import ClientService
from app.core.dependencies import get_client_service, get_agent_service
from app.integrations.livekit_client import LiveKitManager
from app.agent_modules.transcript_store import store_turn
from livekit import api
import os
from supabase import create_client, Client as SupabaseClient
from app.config import settings
# Tools service for abilities
from app.services.tools_service_supabase import ToolsService
from app.services.document_processor import document_processor
from app.utils.tool_prompts import apply_tool_prompt_instructions
from app.services.usage_tracking import usage_tracking_service, QuotaType
from livekit.agents.llm.tool_context import ToolContext
from app.agent_modules.tool_registry import ToolRegistry
# Redis dedupe removed

DATASET_ID_FALLBACKS: Dict[str, List[int]] = {
    "clarence-coherence": [561, 562, 563, 564, 565, 566, 567, 568, 569, 570, 571],
    "able": list(range(7, 36)),
}


def _normalize_conversation_id_value(raw_value: str) -> Tuple[str, Optional[str]]:
    """
    Ensure conversation_id is a UUID string.
    Returns (normalized_uuid, original_value_if_changed).
    """
    if not raw_value:
        new_id = str(uuid.uuid4())
        return new_id, None
    try:
        normalized = str(uuid.UUID(str(raw_value)))
        return normalized, None
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, str(raw_value))), str(raw_value)


async def _resolve_agent_dataset_ids(client_id: str, agent) -> List[Any]:
    """Fetch document IDs assigned to an agent, falling back to known defaults when necessary."""
    dataset_ids: List[Any] = []

    try:
        supabase = await document_processor._get_client_supabase(client_id)
        if not supabase:
            logger.warning(
                "No Supabase client available when resolving dataset IDs for agent %s (client %s)",
                getattr(agent, "slug", agent),
                client_id,
            )
        else:
            response = (
                supabase
                .table('agent_documents')
                .select('document_id')
                .eq('agent_id', agent.id)
                .eq('enabled', True)
                .order('document_id')
                .execute()
            )
            records = getattr(response, "data", None) or []
            seen: set[str] = set()
            normalized: List[Any] = []
            for row in records:
                doc_id = row.get("document_id")
                if doc_id is None:
                    continue
                key = str(doc_id)
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(doc_id)
            dataset_ids = normalized
            if dataset_ids:
                logger.info(
                    "📚 Resolved %s dataset IDs for agent %s (client %s)",
                    len(dataset_ids),
                    getattr(agent, "slug", agent),
                    client_id,
                )
    except Exception as exc:
        logger.warning(
            "Failed to resolve dataset IDs for agent %s (client %s): %s",
            getattr(agent, "slug", agent),
            client_id,
            exc,
        )

    if dataset_ids:
        return dataset_ids

    fallback = DATASET_ID_FALLBACKS.get(getattr(agent, "slug", None))
    if fallback:
        logger.info(
            "Using fallback dataset IDs (%s items) for agent %s",
            len(fallback),
            getattr(agent, "slug", agent),
        )
        return fallback

    return []

# --- Helpers ---
def _normalize_ws_url(url: Optional[str]) -> Optional[str]:
    """Ensure browser receives proper ws/wss scheme for LiveKit server URLs."""
    if not url or not isinstance(url, str):
        return url
    if url.startswith("https://"):
        return url.replace("https://", "wss://", 1)
    if url.startswith("http://"):
        return url.replace("http://", "ws://", 1)
    return url


def _extract_agent_tools_config(agent: Any) -> Dict[str, Any]:
    """Normalize the agent.tools_config field into a dict."""
    tools_config: Dict[str, Any] = {}
    if getattr(agent, "tools_config", None):
        raw_cfg = agent.tools_config
        if isinstance(raw_cfg, str):
            try:
                tools_config = json.loads(raw_cfg)
            except json.JSONDecodeError:
                logger.warning("Failed to parse agent.tools_config string; defaulting to empty dict")
                tools_config = {}
        elif isinstance(raw_cfg, dict):
            tools_config = raw_cfg
    return tools_config


def _voice_settings_dict(agent: Any) -> Dict[str, Any]:
    voice_settings = getattr(agent, "voice_settings", None)
    if not voice_settings:
        return {}
    if isinstance(voice_settings, dict):
        return voice_settings
    if hasattr(voice_settings, "model_dump"):
        try:
            return voice_settings.model_dump()
        except Exception:
            return {}
    return {}


def _apply_cartesia_emotion_prompt(prompt: str, agent: Any) -> str:
    """
    Append Cartesia Sonic-3 emotion instructions when enabled so the LLM knows
    how to wrap responses with appropriate SSML tags.
    """
    voice_settings = _voice_settings_dict(agent)
    if not voice_settings:
        return prompt

    provider = voice_settings.get("tts_provider") or voice_settings.get("provider")
    model = voice_settings.get("model") or voice_settings.get("tts_model")
    emotions_enabled = voice_settings.get("cartesia_emotions_enabled") or voice_settings.get("provider_config", {}).get("cartesia_emotions_enabled")

    if provider != "cartesia" or model != "sonic-3" or not emotions_enabled:
        return prompt

    style = voice_settings.get("cartesia_emotion_style") or "neutral"
    intensity = voice_settings.get("cartesia_emotion_intensity") or 3
    volume = voice_settings.get("cartesia_emotion_volume") or "medium"
    speed = voice_settings.get("cartesia_emotion_speed") or "medium"

    instructions = (
        "\n\nCartesia Sonic-3 emotion controls are enabled for this sidekick. When it improves clarity "
        "or empathy, you may wrap short segments of your response in Cartesia's SSML <emotion> tags. "
        "Use tags such as "
        f"<emotion style=\"{style}\" intensity=\"{intensity}\" volume=\"{volume}\" speed=\"{speed}\">Your text</emotion>. "
        "Only apply tags to the specific words or sentences that should carry the emotion, and choose emotion styles "
        "that match the user's tone. Reference: https://docs.cartesia.ai/build-with-cartesia/sonic-3/volume-speed-emotion"
    )

    return prompt + instructions


def _extract_delta_from_chunk(chunk: Any) -> Optional[str]:
    """Best-effort extraction of text delta from various provider chunk shapes."""
    delta = None
    try:
        if hasattr(chunk, "choices") and chunk.choices:
            choice = chunk.choices[0]
            part = getattr(choice, "delta", None) or getattr(choice, "message", None)
            if part and hasattr(part, "content") and part.content:
                delta = part.content
        if not delta and hasattr(chunk, "content") and chunk.content:
            delta = chunk.content
        if not delta and hasattr(chunk, "text") and getattr(chunk, "text"):
            delta = getattr(chunk, "text")
        if not delta and isinstance(chunk, str):
            delta = chunk if chunk.strip() else None
    except Exception:
        delta = None

    if delta:
        return str(delta)

    # Regex fallback: try to extract content='...' fragments from stringified chunk
    try:
        s = str(chunk)
        matches = re.findall(r"content='([^']*)'", s) or re.findall(r'content="([^"]*)"', s)
        if matches:
            return "".join(matches)
    except Exception:
        return None

    return None


async def _iter_llm_deltas(stream: Any) -> AsyncIterator[str]:
    """Yield text deltas from an async LLM stream."""
    async for chunk in stream:
        delta = _extract_delta_from_chunk(chunk)
        if not delta:
            continue
        yield delta


def _extract_api_keys(client: Any) -> Dict[str, Any]:
    """Collect API keys from client settings with graceful fallbacks."""
    api_keys: Dict[str, Any] = {}
    settings_obj = getattr(client, "settings", None)
    if settings_obj and getattr(settings_obj, "api_keys", None):
        api_keys = {
            "openai_api_key": settings_obj.api_keys.openai_api_key,
            "groq_api_key": settings_obj.api_keys.groq_api_key,
            "cerebras_api_key": getattr(settings_obj.api_keys, "cerebras_api_key", None),
            "deepgram_api_key": settings_obj.api_keys.deepgram_api_key,
            "elevenlabs_api_key": settings_obj.api_keys.elevenlabs_api_key,
            "cartesia_api_key": settings_obj.api_keys.cartesia_api_key,
            "anthropic_api_key": getattr(settings_obj.api_keys, "anthropic_api_key", None),
            "novita_api_key": settings_obj.api_keys.novita_api_key,
            "cohere_api_key": settings_obj.api_keys.cohere_api_key,
            "siliconflow_api_key": settings_obj.api_keys.siliconflow_api_key,
            "jina_api_key": settings_obj.api_keys.jina_api_key,
            "perplexity_api_key": getattr(settings_obj.api_keys, "perplexity_api_key", None),
        }
    else:
        api_keys = {}

    # Fallback to legacy locations on the client object itself
    if getattr(client, "perplexity_api_key", None):
        api_keys.setdefault("perplexity_api_key", client.perplexity_api_key)

    return api_keys


async def _get_agent_tools(
    tools_service: Optional[ToolsService],
    client_id: str,
    agent_id: str,
) -> List[Dict[str, Any]]:
    """Fetch and normalize assigned tools for a given agent."""
    if not tools_service:
        return []

    try:
        assigned_tools = await tools_service.list_agent_tools(client_id, agent_id)
    except Exception as exc:
        logger.error(f"Unable to fetch tools for agent {agent_id}: {exc}")
        return []

    tools_payload: List[Dict[str, Any]] = []
    for tool in assigned_tools:
        tool_dict = tool.dict()
        for ts_field in ("created_at", "updated_at"):
            if ts_field in tool_dict and hasattr(tool_dict[ts_field], "isoformat"):
                tool_dict[ts_field] = tool_dict[ts_field].isoformat()
        tools_payload.append(tool_dict)
    return tools_payload


def _apply_tool_prompt_sections(agent_context: Dict[str, Any], tools_payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Append hidden tool instructions to the system prompt."""
    if not tools_payload:
        return []
    try:
        updated_prompt, appended_sections = apply_tool_prompt_instructions(
            agent_context.get("system_prompt"), tools_payload
        )
        agent_context["system_prompt"] = updated_prompt
        if appended_sections:
            agent_context["tool_prompt_sections"] = appended_sections
            try:
                slugs = [section.get("slug") or section.get("name") for section in appended_sections]
                logger.info(
                    "🧠 Appended hidden tool instructions",
                    extra={"count": len(appended_sections), "abilities": slugs},
                )
            except Exception:
                pass
        return appended_sections or []
    except Exception:
        logger.warning("Failed to apply hidden tool instructions to system prompt", exc_info=True)
        return []


async def _build_agent_context_for_dispatch(
    *,
    agent: Any,
    client: Any,
    conversation_id: str,
    user_id: str,
    session_id: Optional[str],
    mode: str,
    request_context: Optional[Dict[str, Any]] = None,
    client_conversation_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], str, str, str]:
    """Construct metadata payload shared with the worker for voice/text modes."""
    if not agent.voice_settings or not agent.voice_settings.llm_provider:
        raise HTTPException(status_code=400, detail="Agent voice settings with LLM provider are required")

    voice_settings = agent.voice_settings.dict()
    normalized_llm = voice_settings.get("llm_provider")
    normalized_stt = voice_settings.get("stt_provider")
    normalized_tts = voice_settings.get("tts_provider") or voice_settings.get("provider")

    missing = []
    if not normalized_llm:
        missing.append("llm_provider")
    if not normalized_stt:
        missing.append("stt_provider")
    if not normalized_tts:
        missing.append("tts_provider")
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing voice settings: {', '.join(missing)}")

    if normalized_tts == "cartesia":
        tts_model_provided = voice_settings.get("model") or voice_settings.get("tts_model")
        if not tts_model_provided:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cartesia TTS requires a model. Set voice_settings.model "
                    "(e.g., 'sonic-english' or 'sonic-2')."
                ),
            )

    embedding_cfg: Dict[str, Any] = {}
    # Check client.settings.additional_settings first (PlatformClient model structure)
    if client.settings and getattr(client.settings, "additional_settings", None):
        settings_additional = client.settings.additional_settings
        if isinstance(settings_additional, dict) and settings_additional.get("embedding"):
            embedding_cfg = settings_additional.get("embedding", {})
            logger.info(f"📦 Embedding config from client.settings.additional_settings: {embedding_cfg}")
    # Fallback: check client.additional_settings directly (for backward compatibility)
    if not embedding_cfg and getattr(client, "additional_settings", None) and client.additional_settings.get("embedding"):
        embedding_cfg = client.additional_settings.get("embedding", {})
        logger.info(f"📦 Embedding config from client.additional_settings (direct): {embedding_cfg}")
    # Legacy path: check client.settings.embedding
    if not embedding_cfg and client.settings and getattr(client.settings, "embedding", None):
        try:
            embedding_cfg = client.settings.embedding.dict()
        except Exception:
            embedding_cfg = dict(client.settings.embedding) if isinstance(client.settings.embedding, dict) else {}
        logger.info(f"📦 Embedding config from client.settings.embedding: {embedding_cfg}")
    # Fallback: if embedding config is still empty, derive a safe default so the context manager can initialize
    if not embedding_cfg:
        embedding_cfg = {
            "provider": "siliconflow",
            "document_model": "Qwen/Qwen3-Embedding-4B",
            "conversation_model": "Qwen/Qwen3-Embedding-4B",
            "dimension": 1024,
        }
        logger.info(f"📦 Using FALLBACK embedding config: {embedding_cfg}")

    tools_config = _extract_agent_tools_config(agent)
    api_keys_map = _extract_api_keys(client)

    # Rerank settings for downstream agent/worker
    rerank_cfg: Dict[str, Any] = {}
    additional_settings = getattr(client, "additional_settings", None) or getattr(getattr(client, "settings", None), "additional_settings", None)
    if isinstance(additional_settings, str):
        try:
            additional_settings = json.loads(additional_settings)
        except Exception:
            additional_settings = {}

    # Prefer explicit additional_settings override (platform UI stores JSONB here)
    if isinstance(additional_settings, dict) and additional_settings.get("rerank"):
        rerank_cfg = additional_settings.get("rerank", {}) or {}
    else:
        try:
            if client.settings and getattr(client.settings, "rerank", None):
                rerank_cfg = client.settings.rerank.dict()
        except Exception:
            try:
                rerank_cfg = dict(client.settings.rerank) if getattr(client.settings, "rerank", None) else {}
            except Exception:
                rerank_cfg = {}

    agent_context: Dict[str, Any] = {
        "client_id": client.id,
        "agent_slug": agent.slug,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "system_prompt": agent.system_prompt,
        "voice_settings": voice_settings,
        "webhooks": agent.webhooks.dict() if agent.webhooks else {},
        "user_id": user_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "context": request_context or {},
        "tools_config": tools_config,
        "embedding": embedding_cfg,
        "rerank": rerank_cfg,
        "mode": mode,
        "api_keys": api_keys_map,
    }
    if client_conversation_id:
        agent_context["client_conversation_id"] = client_conversation_id

    # Include Supabase credentials when available for worker-side context
    if client.settings and getattr(client.settings, "supabase", None):
        agent_context["supabase_url"] = client.settings.supabase.url
        agent_context["supabase_anon_key"] = client.settings.supabase.anon_key
        agent_context["supabase_service_role_key"] = client.settings.supabase.service_role_key
    else:
        agent_context["supabase_url"] = getattr(client, "supabase_url", None)
        agent_context["supabase_anon_key"] = getattr(client, "supabase_anon_key", None)
        agent_context["supabase_service_role_key"] = getattr(client, "supabase_service_role_key", None)

    agent_dataset_ids = await _resolve_agent_dataset_ids(client.id, agent)
    if agent_dataset_ids:
        agent_context["dataset_ids"] = agent_dataset_ids

    return agent_context, normalized_llm, normalized_stt, normalized_tts


async def _gather_text_tool_outputs(
    *,
    agent: Any,
    client: Any,
    api_keys: Dict[str, Any],
    tools_service: Optional[ToolsService],
    conversation_id: str,
    user_id: str,
    user_message: str,
    session_id: Optional[str],
    recent_history: Optional[List[Dict[str, Any]]] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fetch assigned tools (Abilities), execute them, and return summaries for text chat."""
    result: Dict[str, Any] = {
        "tools_payload": [],
        "tool_results": [],
        "tool_instructions": [],
        "tool_context_summary": None,
    }

    if not tools_service or not getattr(agent, "id", None) or not getattr(client, "id", None):
        return result

    try:
        assigned_tools = await tools_service.list_agent_tools(client.id, agent.id)
    except Exception as exc:
        logger.error(f"Unable to fetch tools for agent {agent.slug}: {exc}")
        return result

    if not assigned_tools:
        return result

    tools_payload = []
    for tool in assigned_tools:
        tool_dict = tool.dict()
        for ts_field in ("created_at", "updated_at"):
            value = tool_dict.get(ts_field)
            if hasattr(value, "isoformat"):
                tool_dict[ts_field] = value.isoformat()
        tools_payload.append(tool_dict)
    result["tools_payload"] = tools_payload

    # Collect any system prompt instructions from tool config so the LLM knows when to use them.
    instructions: List[str] = []
    for tool in tools_payload:
        cfg = tool.get("config") or {}
        instruction = cfg.get("system_prompt_instructions")
        if instruction:
            slug = tool.get("slug") or tool.get("name") or "ability"
            instructions.append(f"Ability '{slug}': {instruction}")
    result["tool_instructions"] = instructions

    try:
        tools_config = _extract_agent_tools_config(agent)
        
        # Get Supabase clients for abilities (e.g., Asana OAuth)
        primary_supabase_client = None
        platform_supabase_client = None
        try:
            # Get client's Supabase for OAuth connections
            if hasattr(client, 'settings') and hasattr(client.settings, 'supabase'):
                supabase_config = client.settings.supabase
                if supabase_config and supabase_config.url and supabase_config.service_role_key:
                    primary_supabase_client = create_client(
                        str(supabase_config.url),
                        str(supabase_config.service_role_key)
                    )
            
            # Get platform Supabase client
            platform_supabase_client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        except Exception as exc:
            logger.warning(f"Could not initialize Supabase clients for abilities: {exc}")
        
        registry = ToolRegistry(
            tools_config=tools_config,
            api_keys=api_keys or {},
            primary_supabase_client=primary_supabase_client,
            platform_supabase_client=platform_supabase_client
        )
        built_tools = registry.build(tools_payload)
    except Exception as exc:
        logger.error(f"Failed to initialize tool registry for agent {agent.slug}: {exc}")
        return result

    if not built_tools:
        return result

    tool_context = ToolContext(built_tools.copy())
    base_runtime_context = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "agent_slug": getattr(agent, "slug", None),
        "client_id": getattr(client, "id", None),
        "session_id": session_id,
    }
    if extra_metadata:
        for key, value in extra_metadata.items():
            if value is not None:
                base_runtime_context[key] = value

    # Prime registry runtime context for n8n tools so webhook payloads include identifiers.
    for tool_def in tools_payload:
        slug = tool_def.get("slug") or tool_def.get("name") or tool_def.get("id")
        if slug:
            registry.update_runtime_context(slug, base_runtime_context)

    tool_results: List[Dict[str, Any]] = []

    # Prepare conversation history for abilities that expect message arrays (Perplexity, etc.)
    history_messages: List[Dict[str, str]] = []
    if recent_history:
        for row in recent_history:
            role = row.get("role")
            content = row.get("content")
            if role in ("user", "assistant") and content:
                history_messages.append({"role": role, "content": content})
    history_messages.append({"role": "user", "content": user_message})

    for tool_def in tools_payload:
        slug = tool_def.get("slug") or tool_def.get("name") or tool_def.get("id") or "ability"
        tool_type = tool_def.get("type")
        fn = tool_context.function_tools.get(slug)
        if not fn:
            logger.warning(f"Tool function not found for slug={slug}; skipping")
            continue

        args: Dict[str, Any] = {}
        if tool_type == "mcp":  # Perplexity remote/MCP
            args["messages"] = history_messages
        else:
            args["user_inquiry"] = user_message
            args["metadata"] = {**base_runtime_context, **(extra_metadata or {})}

        try:
            output = fn(**args)
            if asyncio.iscoroutine(output):
                output = await output
            if output is None:
                output = ""
            output_text = output if isinstance(output, str) else json.dumps(output)
            tool_results.append({
                "slug": slug,
                "type": tool_type,
                "success": True,
                "output": output_text,
            })
        except Exception as exc:
            logger.error(f"Ability '{slug}' execution failed: {exc}")
            tool_results.append({
                "slug": slug,
                "type": tool_type,
                "success": False,
                "error": str(exc),
            })

    result["tool_results"] = tool_results

    if tool_results:
        summary_lines = []
        for entry in tool_results:
            if entry.get("success"):
                summary_lines.append(
                    f"Ability '{entry['slug']}' responded with:\n{entry['output']}"
                )
            else:
                summary_lines.append(
                    f"Ability '{entry['slug']}' failed: {entry.get('error')}"
                )
        result["tool_context_summary"] = "\n\n".join(summary_lines)

    return result

# --- Performance Logging Helper ---
def log_perf(event: str, room_name: str, details: Dict[str, Any]):
    log_entry = {
        "event": event,
        "room_name": room_name,
        "details": details
    }
    logger.info(f"PERF: {json.dumps(log_entry)}")

logger = logging.getLogger(__name__)

router = APIRouter(tags=["trigger"])

# Simple in-process dedupe for dispatches to avoid dual agent sessions per room
RECENT_DISPATCH_TTL_SEC = 10.0
_recent_dispatches: Dict[str, float] = {}

def _should_dispatch(room_name: str) -> bool:
    """Return True if we should dispatch for this room now; False if a recent dispatch exists."""
    try:
        now = time.time()
        # Drop expired entries
        expired = [r for r, ts in _recent_dispatches.items() if now - ts > RECENT_DISPATCH_TTL_SEC]
        for r in expired:
            _recent_dispatches.pop(r, None)
        ts = _recent_dispatches.get(room_name)
        if ts and (now - ts) < RECENT_DISPATCH_TTL_SEC:
            logger.info(f"🛑 Duplicate dispatch suppressed for room {room_name} (within {RECENT_DISPATCH_TTL_SEC}s)")
            return False
        _recent_dispatches[room_name] = now
        return True
    except Exception:
        # Fail-open if dedupe cache access has an error
        return True

# Cross-process dedupe removed; relying on in-process dedupe only

class CreateRoomRequest(BaseModel):
    """Request model for creating a LiveKit room"""
    room_name: str = Field(..., description="Name of the room to create")
    client_id: str = Field(..., description="Client ID for LiveKit credentials")
    max_participants: int = Field(10, description="Maximum participants allowed")
    empty_timeout: int = Field(600, description="Timeout in seconds for empty rooms")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Room metadata")


class CreateRoomResponse(BaseModel):
    """Response model for room creation"""
    success: bool
    room_name: str
    room_info: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class TriggerMode(str, Enum):
    """Agent trigger modes"""
    VOICE = "voice"
    TEXT = "text"
    VIDEO = "video"


class TriggerAgentRequest(BaseModel):
    """Request model for triggering an agent"""
    # Agent identification
    agent_slug: str = Field(..., description="Slug of the agent to trigger")
    client_id: Optional[str] = Field(None, description="Client ID (auto-detected if not provided)")
    
    # Mode and content
    mode: TriggerMode = Field(..., description="Trigger mode: voice or text")
    message: Optional[str] = Field(None, description="Text message (required for text mode)")
    
    # Voice mode parameters
    room_name: Optional[str] = Field(None, description="LiveKit room name (required for voice mode)")
    platform: Optional[str] = Field("livekit", description="Voice platform (default: livekit)")
    
    # Session and user info
    user_id: str = Field(..., description="User identifier")
    session_id: Optional[str] = Field(None, description="Session identifier")
    conversation_id: Optional[str] = Field(None, description="Conversation identifier")
    
    # Optional context
    context: Optional[Dict[str, Any]] = Field(None, description="Additional context data")


class TriggerAgentResponse(BaseModel):
    """Response model for agent trigger"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    agent_info: Optional[Dict[str, Any]] = None


# Using dependencies from core module - no need to redefine here


@router.post("/trigger-agent", response_model=TriggerAgentResponse)
async def trigger_agent(
    request: TriggerAgentRequest,
    agent_service: AgentService = Depends(get_agent_service)
) -> TriggerAgentResponse:
    """
    Trigger an AI agent for voice or text interaction
    
    This endpoint handles:
    - Voice mode: Triggers Python LiveKit agent to join a room
    - Text mode: Processes text messages through the agent
    """
    request_start = time.time()
    try:
        logger.debug(f"Received trigger request", extra={'agent_slug': request.agent_slug, 'mode': request.mode, 'user_id': request.user_id})
        logger.info(f"🚀 STARTING trigger-agent request: agent={request.agent_slug}, mode={request.mode}, user={request.user_id}")
        logger.info(f"Room name requested: {request.room_name}")
        
        # Validate mode-specific requirements
        if request.mode == TriggerMode.VOICE and not request.room_name:
            raise HTTPException(status_code=400, detail="room_name is required for voice mode")
        
        if request.mode == TriggerMode.TEXT and not request.message:
            raise HTTPException(status_code=400, detail="message is required for text mode")
        
        # Auto-detect client_id if not provided by finding agent across all clients
        client_id = request.client_id
        if not client_id:
            logger.info(f"Auto-detecting client for agent {request.agent_slug}")
            all_agents = await agent_service.get_all_agents_with_clients()
            for agent in all_agents:
                # agent is a dict from get_all_agents_with_clients
                if agent.get("slug") == request.agent_slug:
                    client_id = agent.get("client_id")
                    logger.info(f"Found agent {request.agent_slug} in client {client_id}")
                    break
            
            if not client_id:
                raise HTTPException(
                    status_code=404, 
                    detail=f"Agent '{request.agent_slug}' not found in any client"
                )
        
        # Get agent configuration
        agent = await agent_service.get_agent(client_id, request.agent_slug)
        if not agent:
            raise HTTPException(
                status_code=404, 
                detail=f"Agent '{request.agent_slug}' not found in client '{client_id}'"
            )
        
        if not agent.enabled:
            raise HTTPException(
                status_code=400,
                detail=f"Agent '{request.agent_slug}' is currently disabled"
            )

        # Validate chat mode is enabled for the requested mode
        voice_chat_enabled = getattr(agent, 'voice_chat_enabled', True)
        text_chat_enabled = getattr(agent, 'text_chat_enabled', True)
        video_chat_enabled = getattr(agent, 'video_chat_enabled', False)
        # Handle None values (default to True for backwards compatibility, except video which defaults False)
        if voice_chat_enabled is None:
            voice_chat_enabled = True
        if text_chat_enabled is None:
            text_chat_enabled = True
        if video_chat_enabled is None:
            video_chat_enabled = False

        if request.mode == TriggerMode.VOICE and not voice_chat_enabled:
            raise HTTPException(
                status_code=400,
                detail=f"Voice chat is disabled for agent '{request.agent_slug}'"
            )
        if request.mode == TriggerMode.TEXT and not text_chat_enabled:
            raise HTTPException(
                status_code=400,
                detail=f"Text chat is disabled for agent '{request.agent_slug}'"
            )
        if request.mode == TriggerMode.VIDEO and not video_chat_enabled:
            raise HTTPException(
                status_code=400,
                detail=f"Video chat is disabled for agent '{request.agent_slug}'"
            )

        # Get client configuration for LiveKit/API keys
        client = await agent_service.client_service.get_client(client_id)
        if not client:
            raise HTTPException(
                status_code=404, 
                detail=f"Client '{client_id}' not found"
            )
        
        tools_service = ToolsService(agent_service.client_service)

        # Process based on mode
        if request.mode == TriggerMode.VOICE:
            result = await handle_voice_trigger(request, agent, client, tools_service)
            # Enforce no-fallback: conversation_id must be present in result
            try:
                if not isinstance(result, dict) or not result.get("conversation_id"):
                    logger.error("❌ voice result missing conversation_id; refusing to return success")
                    raise HTTPException(status_code=500, detail="Missing conversation_id in voice trigger result")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Validation error for voice result: {e}")
                raise HTTPException(status_code=500, detail="Invalid voice trigger result")
        elif request.mode == TriggerMode.VIDEO:
            # Video mode uses same LiveKit infrastructure as voice, but with mode='video'
            # The agent worker will initialize the Bithuman avatar session
            result = await handle_voice_trigger(request, agent, client, tools_service, mode_override="video")
            try:
                if not isinstance(result, dict) or not result.get("conversation_id"):
                    logger.error("❌ video result missing conversation_id; refusing to return success")
                    raise HTTPException(status_code=500, detail="Missing conversation_id in video trigger result")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Validation error for video result: {e}")
                raise HTTPException(status_code=500, detail="Invalid video trigger result")
        else:  # TEXT mode
            if settings.enable_livekit_text_dispatch:
                logger.info("🔄 ENABLE_LIVEKIT_TEXT_DISPATCH=true -> routing via LiveKit worker")
                result = await handle_text_trigger_via_livekit(request, agent, client, tools_service)
            else:
                logger.warning(
                    "⚠️ Legacy text execution path in use (ENABLE_LIVEKIT_TEXT_DISPATCH=false). "
                    "This mode is deprecated and will be removed soon."
                )
                result = await handle_text_trigger(request, agent, client, tools_service)
        
        request_total = time.time() - request_start
        logger.info(f"✅ COMPLETED trigger-agent request in {request_total:.2f}s")
        
        resp = TriggerAgentResponse(
            success=True,
            message=f"Agent {request.agent_slug} triggered successfully in {request.mode} mode",
            data=result,
            agent_info={
                "slug": agent.slug,
                "name": agent.name,
                "client_id": client_id,
                "client_name": client.name,
                "voice_provider": agent.voice_settings.provider if agent.voice_settings else "livekit",
                "voice_id": agent.voice_settings.voice_id if agent.voice_settings else "alloy"
            }
        )
        try:
            cd = resp.data or {}
            logger.info(f"🔎 trigger-agent response data keys: {list(cd.keys())}")
            logger.info(f"🔎 trigger-agent response conversation_id: {cd.get('conversation_id')}")
        except Exception:
            pass
        return resp
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering agent {request.agent_slug}: {str(e)}", exc_info=True, extra={'traceback': traceback.format_exc()})
        raise HTTPException(
            status_code=500, 
            detail=f"Internal error triggering agent: {str(e)}"
        )


@router.post("/create-room", response_model=CreateRoomResponse)
async def create_livekit_room(
    request: CreateRoomRequest,
    agent_service: AgentService = Depends(get_agent_service)
) -> CreateRoomResponse:
    """
    Create a LiveKit room for voice interactions
    
    This endpoint allows frontends to pre-create rooms before triggering agents,
    ensuring rooms are ready and avoiding timing issues.
    """
    try:
        logger.info(f"Creating LiveKit room {request.room_name} for client {request.client_id}")
        
        # Get client configuration for LiveKit credentials
        client = await agent_service.client_service.get_client(request.client_id)
        if not client:
            raise HTTPException(
                status_code=404, 
                detail=f"Client '{request.client_id}' not found"
            )
        
        # Use backend LiveKit infrastructure (thin client - no client credentials needed)
        from app.integrations.livekit_client import livekit_manager
        backend_livekit = livekit_manager
        
        # Ensure LiveKit manager is initialized
        if not backend_livekit._initialized:
            await backend_livekit.initialize()
        
        logger.info(f"🏢 Creating room with backend LiveKit infrastructure")
        
        # Create the room
        room_metadata = {
            "client_id": request.client_id,
            "client_name": client.name,
            "created_by": "sidekick_backend_api",
            "created_at": datetime.now().isoformat(),
            **(request.metadata or {})
        }
        
        room_info = await backend_livekit.create_room(
            name=request.room_name,
            empty_timeout=request.empty_timeout,
            max_participants=request.max_participants,
            metadata=room_metadata
        )
        
        logger.info(f"✅ Successfully created room {request.room_name}")
        
        return CreateRoomResponse(
            success=True,
            room_name=request.room_name,
            room_info={
                "name": room_info["name"],
                "created_at": room_info["created_at"].isoformat(),
                "max_participants": room_info["max_participants"],
                "metadata": room_metadata,
                "server_url": _normalize_ws_url(backend_livekit.url)
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating room {request.room_name}: {str(e)}")
        return CreateRoomResponse(
            success=False,
            room_name=request.room_name,
            error=str(e)
        )


async def handle_voice_trigger(
    request: TriggerAgentRequest, 
    agent, 
    client,
    tools_service: Optional[ToolsService] = None,
    mode_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Handle voice mode agent triggering
    
    This creates a LiveKit room (if needed) and triggers a Python LiveKit agent to join it.
    For video mode, pass mode_override="video" to enable avatar rendering.
    """
    effective_mode = mode_override or "voice"
    logger.debug(f"Starting {effective_mode} trigger handling", extra={'agent_slug': agent.slug, 'room_name': request.room_name})
    logger.info(f"Handling {effective_mode} trigger for agent {agent.slug} in room {request.room_name}")
    
    voice_trigger_start = time.time()
    
    # Use backend's LiveKit credentials for ALL operations (true thin client)
    # Clients don't need LiveKit credentials - backend owns the infrastructure
    from app.integrations.livekit_client import livekit_manager
    backend_livekit = livekit_manager
    
    # Ensure LiveKit manager is initialized
    if not backend_livekit._initialized:
        await backend_livekit.initialize()
    
    logger.info(f"🏢 Using backend LiveKit infrastructure for thin client architecture")
    
    raw_conversation_id = request.conversation_id or str(uuid.uuid4())
    conversation_id, original_client_id = _normalize_conversation_id_value(raw_conversation_id)
    client_conversation_id = original_client_id or raw_conversation_id
    agent_context, normalized_llm, normalized_stt, normalized_tts = await _build_agent_context_for_dispatch(
        agent=agent,
        client=client,
        conversation_id=conversation_id,
        user_id=request.user_id,
        session_id=request.session_id,
        mode=effective_mode,
        request_context=request.context,
        client_conversation_id=client_conversation_id,
    )

    tools_payload = await _get_agent_tools(tools_service, client.id, agent.id)
    if tools_payload:
        agent_context["tools"] = tools_payload
    appended_sections = _apply_tool_prompt_sections(agent_context, tools_payload)
    
    # Ensure the room exists (create if it doesn't)
    room_start = time.time()
    room_info = await ensure_livekit_room_exists(
        backend_livekit,
        request.room_name,
        agent_name=settings.livekit_agent_name,
        agent_slug=agent.slug,
        user_id=request.user_id,
        agent_config=agent_context,
        enable_agent_dispatch=False
    )
    room_duration = time.time() - room_start
    logger.info(f"⏱️ Room ensure process took {room_duration:.2f}s")
    logger.info(f"Room ensured: {room_info['status']} for room {room_info.get('room_name', request.room_name)}")
    
    # Generate user token for frontend to join the room (thin client)
    token_start = time.time()
    user_token = backend_livekit.create_token(
        identity=f"user_{request.user_id}",
        room_name=request.room_name,
        metadata={"user_id": request.user_id, "client_id": client.id},
        dispatch_agent_name=None,  # Disable token dispatch
        dispatch_metadata=None
    )
    token_duration = time.time() - token_start
    logger.info(f"⏱️ User token generation took {token_duration:.2f}s")
    logger.debug(f"Generated user token", extra={'token_length': len(user_token)})
    
    # Validate API keys for selected providers before dispatch (fail fast)
    try:
        selected_llm = normalized_llm
        selected_stt = normalized_stt
        selected_tts = normalized_tts
        provider_to_key = {
            "openai": "openai_api_key",
            "groq": "groq_api_key",
            "cerebras": "cerebras_api_key",
            "deepgram": "deepgram_api_key",
            "elevenlabs": "elevenlabs_api_key",
            "cartesia": "cartesia_api_key",
        }
        api_keys_map = agent_context.get("api_keys", {})
        # Debug missing keys
        logger.info(f"API keys present for dispatch: {list(k for k,v in api_keys_map.items() if v) }")
        missing_keys = []
        for provider in (selected_llm, selected_stt, selected_tts):
            key_name = provider_to_key.get(provider)
            if key_name and not api_keys_map.get(key_name):
                missing_keys.append(f"{provider}:{key_name}")
        if missing_keys:
            raise HTTPException(status_code=400, detail=f"Missing API keys for providers: {', '.join(missing_keys)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Provider key validation failed: {e}")

    # EXPLICITLY DISPATCH THE AGENT via LiveKit API to guarantee job delivery
    # Skip explicit dispatch if we just dispatched for this room or auto-dispatch was used
    dispatch_info = {"status": "skipped", "reason": "recent or auto-dispatch"}
    if room_info and room_info.get("status") in ("existing", "created"):
        # Use only local dedupe
        local_ok = _should_dispatch(request.room_name)
        if local_ok:
            try:
                dispatch_info = await dispatch_agent_job(
                    livekit_manager=backend_livekit,
                    room_name=request.room_name,
                    agent=agent,
                    client=client,
                    user_id=request.user_id,
                    conversation_id=conversation_id,
                    session_id=request.session_id,
                    tools=agent_context.get("tools"),
                    tools_config=agent_context.get("tools_config"),
                    api_keys=agent_context.get("api_keys"),
                    agent_context=agent_context,
                )
            except Exception as e:
                logger.error(f"❌ Explicit dispatch failed: {e}")
                # Fail fast per no-fallback policy
                raise HTTPException(status_code=502, detail=f"Explicit dispatch failed: {e}")
    
    # Add a small delay to ensure room is fully ready
    if room_info["status"] == "created":
        logger.info(f"Waiting for room {request.room_name} to be fully ready...")
        await asyncio.sleep(0.5)  # Small delay for room initialization
        
        # Verify the room exists in LiveKit after creation
        verify_start = time.time()
        room_check = await backend_livekit.get_room(request.room_name)
        if not room_check:
            logger.error(f"❌ Room {request.room_name} not found immediately after creation!")
        else:
            logger.info(f"✅ Room {request.room_name} verified in LiveKit with agent dispatch enabled")
        verify_duration = time.time() - verify_start
        logger.info(f"⏱️ Post-creation verification took {verify_duration:.2f}s")
    
    # Room has been created and agent has been explicitly dispatched
    logger.info(f"🎯 Room {request.room_name} ready; agent dispatched explicitly via API")
    
    voice_trigger_total = time.time() - voice_trigger_start
    logger.info(f"⏱️ TOTAL voice trigger process took {voice_trigger_total:.2f}s")

    # Enforce conversation_id presence before returning (no fallback policy)
    try:
        if not conversation_id:
            conversation_id = str(uuid.uuid4())
            agent_context["conversation_id"] = conversation_id
        # Also ensure room_info metadata carries it
        try:
            meta = room_info.get("metadata") if isinstance(room_info, dict) else None
            if isinstance(meta, dict):
                meta["conversation_id"] = conversation_id
            elif isinstance(meta, str):
                import json as _json
                try:
                    m = _json.loads(meta)
                    if isinstance(m, dict):
                        m["conversation_id"] = conversation_id
                        room_info["metadata"] = m
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass
    # Log the conversation_id we are returning to the client (no PII)
    try:
        logger.info(f"📝 Returning voice trigger: room={request.room_name} conversation_id={conversation_id}")
    except Exception:
        pass

    try:
        logger.info(f"🧩 trigger-agent result keys: {list(result.keys())}")
    except Exception:
        pass
    return {
        "mode": "voice",
        "room_name": request.room_name,
        "platform": request.platform,
        "conversation_id": client_conversation_id,
        "agent_context": agent_context,
        "livekit_config": {
            "server_url": _normalize_ws_url(backend_livekit.url),
            "user_token": user_token,
            "configured": True
        },
        "room_info": room_info,
        "dispatch_info": dispatch_info,  # Use the actual dispatch_info from explicit dispatch
        "status": "voice_agent_triggered",
        "message": f"Room {request.room_name} ready with explicit agent dispatch to '{settings.livekit_agent_name}', user token provided.",
        "total_duration_ms": int(voice_trigger_total * 1000)
    }


async def _store_conversation_turn(
    supabase_client: SupabaseClient,
    user_id: str,
    agent_id: str,
    conversation_id: str,
    user_message: str,
    agent_response: str,
    session_id: Optional[str] = None,
    context_manager=None,  # Optional context manager for embeddings
    citations: Optional[List[Dict[str, Any]]] = None,  # Optional citations from RAG
    metadata: Optional[Dict[str, Any]] = None,  # Optional metadata
    client_id: Optional[str] = None  # Required for multi-tenant schemas with RLS
) -> None:
    """
    Store a conversation turn using the unified transcript store.
    """
    try:
        # Prepare turn data for the transcript store
        turn_data = {
            'conversation_id': conversation_id,
            'session_id': session_id,
            'agent_id': agent_id,
            'user_id': user_id,
            'client_id': client_id,  # Required for multi-tenant schemas with RLS
            'user_text': user_message,
            'assistant_text': agent_response,
            'citations': citations,
            'metadata': metadata or {},
            'embedder': context_manager.embedder if context_manager and hasattr(context_manager, 'embedder') else None
        }
        
        # Use the unified transcript store
        result = await store_turn(turn_data, supabase_client)
        
        if result['success']:
            logger.info(
                f"✅ Stored conversation turn | "
                f"turn_id={result['turn_id']} | "
                f"conversation_id={conversation_id} | "
                f"citations={len(citations) if citations else 0}"
            )
        else:
            logger.error(f"❌ Failed to store conversation turn: {result.get('error')}")
            
    except Exception as e:
        logger.error(f"❌ Failed to store conversation turn: {e}")
        logger.error(f"User message: {user_message[:100]}...")
        logger.error(f"Assistant response: {agent_response[:100]}...")


async def handle_text_trigger_via_livekit(
    request: TriggerAgentRequest,
    agent,
    client,
    tools_service: Optional[ToolsService] = None,
) -> Dict[str, Any]:
    """Route text mode through LiveKit worker for unified tool execution."""
    logger.info(f"🛠️ Routing text request for {agent.slug} through LiveKit worker")

    # Pre-flight quota check - reject if already exceeded
    try:
        await usage_tracking_service.initialize()
        is_allowed, quota_status = await usage_tracking_service.check_agent_quota(
            client_id=str(client.id),
            agent_id=str(agent.id),
            quota_type=QuotaType.TEXT,
        )
        if not is_allowed:
            logger.warning(
                "Text quota already exceeded for agent %s (client %s): %d/%d messages",
                agent.slug, client.id, quota_status.used, quota_status.limit
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "quota_exceeded",
                    "message": f"Text message quota exceeded. Used {quota_status.used} of {quota_status.limit} messages this month.",
                    "quota": {
                        "used": quota_status.used,
                        "limit": quota_status.limit,
                        "percent_used": quota_status.percent_used,
                    }
                }
            )
    except HTTPException:
        raise
    except Exception as quota_check_err:
        logger.warning("Failed to check text quota (allowing request): %s", quota_check_err)

    from app.integrations.livekit_client import livekit_manager

    backend_livekit = livekit_manager
    if not backend_livekit._initialized:
        await backend_livekit.initialize()

    raw_conversation_id = request.conversation_id or str(uuid.uuid4())
    conversation_id, original_client_id = _normalize_conversation_id_value(raw_conversation_id)
    client_conversation_id = original_client_id or raw_conversation_id
    agent_context, normalized_llm, normalized_stt, normalized_tts = await _build_agent_context_for_dispatch(
        agent=agent,
        client=client,
        conversation_id=conversation_id,
        user_id=request.user_id,
        session_id=request.session_id,
        mode="text",
        request_context=request.context,
        client_conversation_id=client_conversation_id,
    )
    try:
        logger.info("🔍 rerank config for dispatch: %s", agent_context.get("rerank"))
    except Exception:
        pass

    tools_payload = await _get_agent_tools(tools_service, client.id, agent.id)
    if tools_payload:
        agent_context["tools"] = tools_payload
    appended_sections = _apply_tool_prompt_sections(agent_context, tools_payload)

    agent_context["user_message"] = request.message

    reused_room = False
    if request.conversation_id:
        text_room_name, room_info, reused_room = await _get_or_create_text_room(
            backend_livekit,
            conversation_id=client_conversation_id,
            agent_slug=agent.slug,
            user_id=request.user_id,
            agent_context=agent_context,
        )
    else:
        text_room_name = f"text-{client_conversation_id}-{uuid.uuid4().hex[:8]}"
        room_info = await ensure_livekit_room_exists(
            backend_livekit,
            text_room_name,
            agent_name=settings.livekit_agent_name,
            agent_slug=agent.slug,
            user_id=request.user_id,
            agent_config=agent_context,
            enable_agent_dispatch=True,
        )

    dispatch_info = await dispatch_agent_job(
        livekit_manager=backend_livekit,
        room_name=text_room_name,
        agent=agent,
        client=client,
        user_id=request.user_id,
        conversation_id=conversation_id,
        session_id=request.session_id,
        tools=agent_context.get("tools"),
        tools_config=agent_context.get("tools_config"),
        api_keys=agent_context.get("api_keys"),
        agent_context=agent_context,
    )

    response_text, citations, tool_results = await _poll_for_text_response(
        backend_livekit,
        text_room_name,
    )
    citations = citations or []
    tool_results = tool_results or []

    # Track text usage for quota metering (per-agent)
    quota_exceeded = False
    try:
        await usage_tracking_service.initialize()
        is_within_quota, quota_status = await usage_tracking_service.increment_agent_text_usage(
            client_id=str(client.id),
            agent_id=str(agent.id),
            count=1,  # Each text exchange counts as 1 message
        )
        if not is_within_quota:
            quota_exceeded = True
            logger.warning(
                "Text quota exceeded for agent %s (client %s): %d/%d messages",
                agent.slug, client.id, quota_status.used, quota_status.limit
            )
    except Exception as usage_err:
        logger.warning("Failed to track text usage: %s", usage_err)

    # TEMP: disable text room cleanup to allow metadata inspection
    cleanup_status = "skipped (cleanup disabled)"

    tools_response = {
        "assigned": tools_payload,
        "results": tool_results,
        "context_summary": None,
        "prompt_sections": appended_sections,
    }

    supabase_url = agent_context.get("supabase_url")
    supabase_key = agent_context.get("supabase_service_role_key") or agent_context.get("supabase_service_key")
    client_supabase = None
    context_manager = None
    if supabase_url and supabase_key:
        try:
            client_supabase = create_client(supabase_url, supabase_key)
            from app.agent_modules.context import AgentContextManager

            context_manager = AgentContextManager(
                supabase_client=client_supabase,
                agent_config=agent_context,
                user_id=request.user_id,
                client_id=client.id,
                api_keys=agent_context.get("api_keys", {}),
            )
        except Exception as exc:
            logger.warning(f"Failed to initialize Supabase context manager for text mode: {exc}")

    if response_text and client_supabase:
        try:
            turn_metadata: Dict[str, Any] = {
                "agent_slug": agent.slug,
                "tool_results": tool_results,
                "tool_prompt_sections": appended_sections,
            }
            client_conv_id = agent_context.get("client_conversation_id")
            if client_conv_id:
                turn_metadata["client_conversation_id"] = client_conv_id
            await _store_conversation_turn(
                supabase_client=client_supabase,
                user_id=request.user_id,
                agent_id=agent.id,
                conversation_id=conversation_id,
                user_message=request.message,
                agent_response=response_text,
                session_id=request.session_id,
                context_manager=context_manager,
                citations=citations,
                metadata=turn_metadata,
                client_id=str(client.id),  # Required for multi-tenant schemas with RLS
            )
        except Exception as store_err:
            logger.error(f"Failed to store unified text conversation turn: {store_err}")

    return {
        "mode": "text_via_livekit",
        "message_received": request.message,
        "user_id": request.user_id,
        "conversation_id": client_conversation_id,
        "response": response_text,
        "agent_response": response_text,
        "ai_response": response_text,
        "citations": citations,
        "tools": tools_response,
        "dispatch_info": dispatch_info,
        "room_info": {
            "name": text_room_name,
            "status": room_info.get("status") if room_info else None,
            "cleanup": cleanup_status,
        },
        "providers": {
            "llm": normalized_llm,
            "stt": normalized_stt,
            "tts": normalized_tts,
        },
    }


async def handle_text_trigger(
    request: TriggerAgentRequest,
    agent,
    client,
    tools_service: Optional[ToolsService] = None,
    on_token: Optional[Callable[[str], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    """
    Handle text mode agent triggering with full RAG support
    
    This processes text messages through the agent using the same
    context-aware system as voice conversations.
    """
    logger.info(f"🚀 Starting RAG-powered text trigger for agent {agent.slug}")
    logger.info(f"💬 User message: {request.message[:100]}...")
    logger.warning(
        "⚠️ handle_text_trigger() is deprecated. Enable ENABLE_LIVEKIT_TEXT_DISPATCH to use the unified worker path."
    )
    
    # Initialize variables
    response_text = None
    user_id = request.user_id or "anonymous"
    raw_conversation_id = request.conversation_id or f"text_{request.session_id or uuid.uuid4().hex}"
    conversation_id, original_client_id = _normalize_conversation_id_value(raw_conversation_id)
    client_conversation_id = original_client_id or raw_conversation_id
    
    # Get LLM provider and model from agent voice settings - NO DEFAULTS
    if not agent.voice_settings or not agent.voice_settings.llm_provider:
        raise ValueError("Agent does not have voice settings configured with an LLM provider")
    
    llm_provider = agent.voice_settings.llm_provider
    llm_model = agent.voice_settings.llm_model
    
    # Log the agent's voice settings for debugging
    logger.info(f"Agent voice_settings: {agent.voice_settings}")
    logger.info(f"Using LLM provider: {llm_provider}, model: {llm_model}")
    
    # Prepare metadata for context
    # Check for embedding config in both locations (additional_settings first, then settings)
    embedding_cfg = {}
    additional_settings = getattr(client, "additional_settings", None)
    if not additional_settings and getattr(client, "settings", None):
        additional_settings = getattr(client.settings, "additional_settings", None)

    if isinstance(additional_settings, dict) and additional_settings.get("embedding"):
        embedding_cfg = additional_settings.get("embedding", {})
    elif client.settings and hasattr(client.settings, 'embedding') and client.settings.embedding:
        embedding_cfg = client.settings.embedding.dict()
    
    # Rerank settings for voice/worker metadata
    rerank_cfg = {}
    if isinstance(additional_settings, dict) and additional_settings.get("rerank"):
        rerank_cfg = additional_settings.get("rerank", {}) or {}
    else:
        try:
            if client.settings and hasattr(client.settings, 'rerank') and client.settings.rerank:
                rerank_cfg = client.settings.rerank.dict()
        except Exception:
            try:
                rerank_cfg = dict(client.settings.rerank) if client.settings and getattr(client.settings, "rerank", None) else {}
            except Exception:
                rerank_cfg = {}

    metadata = {
        "agent_slug": agent.slug,
        "agent_name": agent.name,
        "agent_id": agent.id,
        "system_prompt": agent.system_prompt,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "client_conversation_id": client_conversation_id,
        "client_id": client.id,
        "voice_settings": agent.voice_settings.dict() if agent.voice_settings else {},
        "embedding": embedding_cfg,
        "rerank": rerank_cfg,
        "rag_results_limit": getattr(agent, "rag_results_limit", None),
        "show_citations": getattr(agent, "show_citations", True),
    }
    
    agent_dataset_ids: List[Any] = await _resolve_agent_dataset_ids(client.id, agent)
    if agent_dataset_ids:
        metadata["dataset_ids"] = agent_dataset_ids
        logger.info(f"📚 Added dataset_ids for {agent.slug}: {len(agent_dataset_ids)} documents")
    
    # Get API keys from client configuration
    api_keys = {}
    if client.settings and client.settings.api_keys:
        api_keys = {
            "openai_api_key": client.settings.api_keys.openai_api_key,
            "groq_api_key": client.settings.api_keys.groq_api_key,
            "cerebras_api_key": getattr(client.settings.api_keys, 'cerebras_api_key', None),
            "deepgram_api_key": client.settings.api_keys.deepgram_api_key,
            "elevenlabs_api_key": client.settings.api_keys.elevenlabs_api_key,
            "cartesia_api_key": client.settings.api_keys.cartesia_api_key,
            "anthropic_api_key": getattr(client.settings.api_keys, 'anthropic_api_key', None),
            "novita_api_key": client.settings.api_keys.novita_api_key,
            "cohere_api_key": client.settings.api_keys.cohere_api_key,
            "siliconflow_api_key": client.settings.api_keys.siliconflow_api_key,
            "jina_api_key": client.settings.api_keys.jina_api_key,
        }
    metadata["api_keys"] = api_keys

    recent_rows: List[Dict[str, Any]] = []

    try:
        # Initialize context manager if we have Supabase credentials
        context_manager = None
        client_supabase = None
        
        # Check for Supabase credentials in both old and new locations
        supabase_url = None
        supabase_key = None
        
        # First check direct fields (new structure)
        if hasattr(client, 'supabase_url') and client.supabase_url:
            supabase_url = client.supabase_url
            supabase_key = client.supabase_service_role_key
        elif hasattr(client, 'supabase_project_url') and client.supabase_project_url:
            supabase_url = client.supabase_project_url
            supabase_key = getattr(client, 'supabase_service_role_key', None) or getattr(client, 'supabase_anon_key', None)
        # Then check settings.supabase (old structure)
        elif client.settings and hasattr(client.settings, 'supabase'):
            supabase_url = client.settings.supabase.url
            supabase_key = client.settings.supabase.service_role_key or client.settings.supabase.anon_key
        
        if supabase_url and supabase_key:
            logger.info("🔍 Initializing context manager for RAG...")
            from supabase import create_client
            
            # Create Supabase client for the client's database
            # Use service_role_key for server-side operations to bypass RLS
            client_supabase = create_client(supabase_url, supabase_key)
            
            # Import the context manager from agent_modules
            from app.agent_modules.context import AgentContextManager
            from app.agent_modules.llm_wrapper import ContextAwareLLM
            
            # Create context manager
            context_manager = AgentContextManager(
                supabase_client=client_supabase,
                agent_config=metadata,
                user_id=user_id,
                client_id=client.id,
                api_keys=api_keys
            )
            logger.info("✅ Context manager initialized")

        if client_supabase:
            try:
                recent_q = (
                    client_supabase
                    .table("conversation_transcripts")
                    .select("role,content")
                    .eq("conversation_id", conversation_id)
                    .order("created_at", desc=True)
                    .limit(20)
                    .execute()
                )
                recent_rows = list(reversed(recent_q.data or []))
                if recent_rows:
                    logger.info(f"📚 Loaded {len(recent_rows)} recent messages for buffer memory")
            except Exception as e:
                logger.warning(f"Couldn't load recent turns for buffer memory: {e}")

        # Configure LLM based on provider (shared factory)
        from app.shared.llm_factory import get_llm
        from livekit.agents import llm as lk_llm

        llm_plugin = get_llm(llm_provider, llm_model, api_keys)
        logger.info(f"✅ Initialized {llm_provider} LLM with model: {llm_model}")

        if not llm_plugin:
            raise ValueError(f"No valid API key for {llm_provider}")

        tools_payload: List[Dict[str, Any]] = []
        tool_results: List[Dict[str, Any]] = []
        tool_context_summary: Optional[str] = None
        tool_prompt_sections: List[Dict[str, Any]] = []
        tool_instructions: List[str] = []

        tool_execution = await _gather_text_tool_outputs(
            agent=agent,
            client=client,
            api_keys=api_keys or {},
            tools_service=tools_service,
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=request.message,
            session_id=request.session_id,
            recent_history=recent_rows,
            extra_metadata={
                "conversation_id": conversation_id,
                "rag_enabled": bool(context_manager),
            },
        )
        if tool_execution:
            tools_payload = tool_execution.get("tools_payload") or []
            tool_results = tool_execution.get("tool_results") or []
            tool_context_summary = tool_execution.get("tool_context_summary")
            tool_prompt_sections = []
            tool_instructions = tool_execution.get("tool_instructions") or []
            if tool_results:
                try:
                    slugs = [entry.get("slug") for entry in tool_results]
                    logger.info("🧰 Executed abilities for text chat", extra={"count": len(tool_results), "abilities": slugs})
                except Exception:
                    logger.info(f"🧰 Executed {len(tool_results)} abilities for text chat")

        enhanced_prompt = agent.system_prompt

        if context_manager:
            logger.info("🧠 Wrapping LLM with RAG context...")
            _contextual_llm = ContextAwareLLM(
                base_llm=llm_plugin,
                context_manager=context_manager,
                user_id=user_id
            )
            try:
                logger.info("🔍 Building complete context with RAG...")
                context_result = await context_manager.build_complete_context(
                    user_message=request.message,
                    user_id=user_id
                )
                enhanced_prompt = context_result.get("enhanced_system_prompt", agent.system_prompt)
                logger.info("✅ RAG context built successfully")
            except Exception as rag_error:
                logger.warning(f"⚠️ RAG context building failed (continuing without RAG): {rag_error}")
                enhanced_prompt = agent.system_prompt
        else:
            logger.warning("⚠️ No context manager available, using basic LLM")

        if tools_payload:
            try:
                enhanced_prompt, tool_prompt_sections = apply_tool_prompt_instructions(
                    enhanced_prompt,
                    tools_payload,
                )
                if tool_prompt_sections:
                    try:
                        abilities = [section.get("slug") or section.get("name") for section in tool_prompt_sections]
                        logger.info("🧠 Appended hidden tool instructions", extra={"count": len(tool_prompt_sections), "abilities": abilities})
                    except Exception:
                        logger.info(f"🧠 Appended {len(tool_prompt_sections)} hidden tool instruction sections")
            except Exception as tool_prompt_error:
                logger.warning(f"Failed to apply hidden tool instructions to system prompt: {tool_prompt_error}")

        enhanced_prompt = _apply_cartesia_emotion_prompt(enhanced_prompt, agent)

        ctx = lk_llm.ChatContext()
        ctx.add_message(role="system", content=enhanced_prompt)

        if tool_context_summary:
            ctx.add_message(
                role="system",
                content=f"Ability execution results shared with the assistant:\n{tool_context_summary}",
            )

        for row in recent_rows:
            role = row["role"] if row["role"] in ("user", "assistant") else "user"
            text = row["content"] or ""
            ctx.add_message(role=role, content=text)

        ctx.add_message(role="user", content=request.message)

        logger.info("🤖 Generating response with text abilities applied...")
        stream = llm_plugin.chat(chat_ctx=ctx)

        response_text = ""
        async for delta in _iter_llm_deltas(stream):
            response_text += delta
            if on_token:
                try:
                    await on_token(delta)
                except Exception as callback_error:
                    logger.warning("Streaming callback failed: %s", callback_error)

        if response_text:
            logger.info(f"✅ Generated response: {response_text[:100]}...")

        # Try to retrieve citations if RAG was used
        citations = None
        if context_manager and hasattr(context_manager, '_last_rag_results'):
            try:
                # Check if context manager has stored citations from the last RAG query
                last_results = getattr(context_manager, '_last_rag_results', None)
                if last_results and hasattr(last_results, 'citations'):
                    citations = [
                        {
                            "doc_id": getattr(c, 'doc_id', None),
                            "dataset_id": getattr(c, 'dataset_id', None),
                            "title": getattr(c, 'title', 'Unknown'),
                            "source_url": getattr(c, 'source_url', None),
                            "chunk_text": getattr(c, 'chunk_text', ''),
                            "score": getattr(c, 'score', 0.0)
                        }
                        for c in last_results.citations
                    ]
                    logger.info(f"📚 Retrieved {len(citations)} citations from context manager")
            except Exception as e:
                logger.warning(f"Failed to extract citations from context manager: {e}")
        
        # Normalize citations to a list for transcript storage helpers
        if citations is None:
            citations = []

        # Store conversation turn if we have a response and Supabase client
        if response_text and client_supabase:
            try:
                turn_metadata: Dict[str, Any] = {'agent_slug': agent.slug}
                if client_conversation_id:
                    turn_metadata['client_conversation_id'] = client_conversation_id
                if tool_results:
                    turn_metadata['tool_results'] = tool_results
                if tool_context_summary:
                    turn_metadata['tool_context_summary'] = tool_context_summary
                if tool_prompt_sections:
                    turn_metadata['tool_prompt_sections'] = tool_prompt_sections
                if tool_instructions:
                    turn_metadata['tool_instructions'] = tool_instructions

                await _store_conversation_turn(
                    supabase_client=client_supabase,
                    user_id=user_id,
                    agent_id=agent.id,
                    conversation_id=conversation_id,
                    user_message=request.message,
                    agent_response=response_text,
                    session_id=request.session_id,
                    context_manager=context_manager,  # Pass context manager for embeddings
                    citations=citations,  # Include citations if available
                    metadata=turn_metadata,  # Include agent metadata plus tool execution context
                    client_id=str(client.id),  # Required for multi-tenant schemas with RLS
                )
                logger.info(f"✅ Text conversation turn stored for conversation_id={conversation_id} with {len(citations) if citations else 0} citations")
            except Exception as e:
                logger.error(f"❌ Failed to store text conversation turn: {e}")
                # Continue - storage failure shouldn't break the response
                
    except Exception as e:
        logger.error(f"❌ Error in RAG text processing: {e}", exc_info=True)
        response_text = f"I apologize, but I encountered an error processing your message. Please try again."
    
    # Prepare response
    tools_response = {
        "assigned": tools_payload,
        "results": tool_results,
        "context_summary": tool_context_summary,
        "prompt_sections": tool_prompt_sections,
        "instructions": tool_instructions,
    }

    return {
        "mode": "text",
        "message_received": request.message,
        "user_id": user_id,
        "conversation_id": client_conversation_id,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "rag_enabled": bool(context_manager),
        "status": "text_message_processed",
        "response": response_text or f"I'm sorry, I couldn't process your message. Please ensure the {llm_provider} API key is configured.",
        "agent_response": response_text,
        "ai_response": response_text,
        "citations": citations,
        "tools": tools_response,
    }


async def dispatch_agent_job(
    livekit_manager: LiveKitManager,
    room_name: str,
    agent,
    client,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tools_config: Optional[Dict[str, Any]] = None,
    api_keys: Optional[Dict[str, Any]] = None,
    agent_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Explicit dispatch mode - Directly dispatch agent to room with full configuration.
    
    This ensures the agent receives all necessary configuration and API keys
    through job metadata, following LiveKit's recommended pattern.
    """
    logger.info(f"🚀 Starting agent dispatch: agent={agent.slug}, room={room_name}, client={client.id}")
    dispatch_start = time.time()
    
    try:
        # Prepare full agent configuration for job metadata
        # Build voice settings with proper defaults
        voice_settings = agent.voice_settings.dict() if agent.voice_settings else {}
        
        # Providers are validated in handle_voice_trigger; do not apply defaults here (no-fallback policy)
            
        # Debug logging to understand client structure
        logger.info(f"Client settings available: {bool(client.settings)}")
        if client.settings:
            logger.info(f"Client settings has supabase: {bool(client.settings.supabase)}")
            if client.settings.supabase:
                logger.info(f"Supabase URL: {client.settings.supabase.url[:50]}..." if client.settings.supabase.url else "No URL")
                logger.info(f"Supabase anon_key exists: {bool(client.settings.supabase.anon_key)}")
        
        api_keys_map: Dict[str, Any]
        if api_keys is not None:
            api_keys_map = dict(api_keys)
            if api_keys_map.get("perplexity_api_key") is None and getattr(client, "perplexity_api_key", None):
                api_keys_map["perplexity_api_key"] = getattr(client, "perplexity_api_key")
        elif client.settings and client.settings.api_keys:
            api_keys_map = {
                # LLM Providers
                "openai_api_key": client.settings.api_keys.openai_api_key,
                "groq_api_key": client.settings.api_keys.groq_api_key,
                "cerebras_api_key": getattr(client.settings.api_keys, 'cerebras_api_key', None),
                "deepinfra_api_key": client.settings.api_keys.deepinfra_api_key,
                "replicate_api_key": client.settings.api_keys.replicate_api_key,
                # Voice/Speech Providers
                "deepgram_api_key": client.settings.api_keys.deepgram_api_key,
                "elevenlabs_api_key": client.settings.api_keys.elevenlabs_api_key,
                "cartesia_api_key": client.settings.api_keys.cartesia_api_key,
                "speechify_api_key": client.settings.api_keys.speechify_api_key,
                # Embedding/Reranking Providers
                "novita_api_key": client.settings.api_keys.novita_api_key,
                "cohere_api_key": client.settings.api_keys.cohere_api_key,
                "siliconflow_api_key": client.settings.api_keys.siliconflow_api_key,
                "jina_api_key": client.settings.api_keys.jina_api_key,
                # Additional providers
                "anthropic_api_key": getattr(client.settings.api_keys, 'anthropic_api_key', None),
                "perplexity_api_key": (
                    client.settings.api_keys.perplexity_api_key
                    if getattr(client.settings.api_keys, 'perplexity_api_key', None)
                    else getattr(client, 'perplexity_api_key', None)
                ),
            }
        else:
            api_keys_map = {}
            if getattr(client, 'perplexity_api_key', None):
                api_keys_map["perplexity_api_key"] = getattr(client, 'perplexity_api_key')

        context_snapshot: Dict[str, Any] = agent_context or {}

        runtime_tools_config: Dict[str, Any] = {}
        if isinstance(tools_config, dict):
            runtime_tools_config = copy.deepcopy(tools_config)

        if tools:
            for tool_def in tools:
                try:
                    slug = tool_def.get("slug") or tool_def.get("name") or tool_def.get("id")
                except Exception:
                    slug = None
                if not slug:
                    continue

                existing_entry = runtime_tools_config.get(slug)
                if not isinstance(existing_entry, dict):
                    existing_entry = {}

                if tool_def.get("type") == "n8n":
                    context_defaults = {
                        "user_id": user_id,
                        "conversation_id": conversation_id,
                        "session_id": session_id or conversation_id,
                        "agent_slug": getattr(agent, "slug", None),
                        "client_id": getattr(client, "id", None),
                    }
                    runtime_context = {k: v for k, v in context_defaults.items() if v is not None}
                    user_defined_context = (
                        existing_entry.get("context")
                        if isinstance(existing_entry.get("context"), dict)
                        else {}
                    )
                    runtime_context.update(user_defined_context)
                    existing_entry["context"] = runtime_context
                    if "include_context" not in existing_entry:
                        existing_entry["include_context"] = True
                    if "strip_nulls" not in existing_entry:
                        existing_entry["strip_nulls"] = True

                runtime_tools_config[slug] = existing_entry

        context_dataset_ids: List[Any] = context_snapshot.get("dataset_ids") or []
        if not context_dataset_ids:
            fallback_ids = DATASET_ID_FALLBACKS.get(agent.slug)
            if fallback_ids:
                logger.info(
                    "Using fallback dataset IDs for dispatch metadata (agent=%s, count=%s)",
                    agent.slug,
                    len(fallback_ids),
                )
                context_dataset_ids = fallback_ids

        job_metadata = {
            "client_id": client.id,
            "agent_slug": agent.slug,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "system_prompt": context_snapshot.get("system_prompt"),
            "voice_settings": voice_settings,
            "webhooks": agent.webhooks.dict() if agent.webhooks else {},
            # Ensure worker stores transcripts under the same conversation
            "conversation_id": conversation_id,
            # Include client's Supabase credentials for context system
            "supabase_url": client.settings.supabase.url if client.settings and client.settings.supabase else None,
            "supabase_anon_key": client.settings.supabase.anon_key if client.settings and client.settings.supabase else None,
            "supabase_service_role_key": client.settings.supabase.service_role_key if client.settings and client.settings.supabase else None,
            # Include user_id if provided
            "user_id": user_id,
            # Include embedding configuration from client's additional_settings
            "embedding": context_snapshot.get("embedding")
            or (client.additional_settings.get("embedding", {}) if client.additional_settings else {}),
            # Include dataset_ids for RAG context (document IDs for this agent)
            "dataset_ids": context_dataset_ids,
            "api_keys": api_keys_map,
            # Carry interaction mode so the worker can skip STT/TTS for text sessions
            "mode": (
                context_snapshot.get("mode")
                or ("text" if context_snapshot.get("user_message") else "voice")
            ),
            "rerank": context_snapshot.get("rerank"),
            "user_message": context_snapshot.get("user_message"),
        }

        tools_payload = tools if tools is not None else context_snapshot.get("tools") or []
        tools_count = len(tools_payload)
        if tools_count:
            job_metadata["tools"] = tools_payload
        if runtime_tools_config:
            job_metadata["tools_config"] = runtime_tools_config
        if context_snapshot.get("tool_prompt_sections"):
            job_metadata["tool_prompt_sections"] = context_snapshot["tool_prompt_sections"]

        try:
            tools_keys = [t.get("slug") or t.get("name") for t in tools_payload] if tools_payload else []
            logger.info(
                f"🧰 Dispatch metadata summary: tools_count={tools_count}, tools_keys={tools_keys}, has_tools_config={bool(tools_config)}"
            )
        except Exception:
            pass
        
        # Create LiveKit API client for explicit dispatch
        api_start = time.time()
        livekit_api = api.LiveKitAPI(
            url=livekit_manager.url,
            api_key=livekit_manager.api_key,
            api_secret=livekit_manager.api_secret
        )
        api_duration = time.time() - api_start
        logger.info(f"⏱️ LiveKit API client creation took {api_duration:.2f}s")
        
        preferred_worker = await livekit_manager.get_warm_worker()
        if preferred_worker:
            job_metadata["preferred_worker_id"] = preferred_worker
            logger.info(f"♻️ Requesting warm worker {preferred_worker} for dispatch")

        # LiveKit has a 64KB metadata limit - check and truncate if needed
        LIVEKIT_METADATA_LIMIT = 60000  # Leave some headroom below 65536
        metadata_json = json.dumps(job_metadata)
        metadata_size = len(metadata_json)

        if metadata_size > LIVEKIT_METADATA_LIMIT:
            logger.warning(f"⚠️ Dispatch metadata ({metadata_size} bytes) exceeds limit ({LIVEKIT_METADATA_LIMIT}). Truncating system_prompt...")
            # Calculate how much we need to trim
            excess = metadata_size - LIVEKIT_METADATA_LIMIT
            system_prompt = job_metadata.get("system_prompt", "")
            if system_prompt and len(system_prompt) > excess + 1000:
                # Truncate system_prompt, keeping the beginning (persona/instructions)
                # and marking truncation
                truncated_prompt = system_prompt[:len(system_prompt) - excess - 100] + "\n\n[... context truncated due to size limits ...]"
                job_metadata["system_prompt"] = truncated_prompt
                metadata_json = json.dumps(job_metadata)
                logger.info(f"   - Truncated system_prompt from {len(system_prompt)} to {len(truncated_prompt)} chars")
                logger.info(f"   - New metadata size: {len(metadata_json)} bytes")

        logger.info(f"📤 Sending dispatch request:")
        logger.info(f"   - Room: {room_name}")
        logger.info(f"   - Agent name: {settings.livekit_agent_name}")
        logger.info(f"   - Metadata fields: {len(job_metadata)}")
        logger.info(f"   - Metadata size: {len(metadata_json)} bytes")

        # Update dispatch request with potentially truncated metadata
        dispatch_request = api.CreateAgentDispatchRequest(
            room=room_name,
            metadata=metadata_json,
            agent_name=settings.livekit_agent_name
        )

        # Dispatch ONCE - do NOT retry, as each create_dispatch creates a NEW job that results in duplicate processing
        # The agent will pick up the dispatch when it's available - no need to create multiple dispatches
        dispatch_api_start = time.time()
        dispatch_response = await livekit_api.agent_dispatch.create_dispatch(dispatch_request)
        dispatch_api_duration = time.time() - dispatch_api_start

        # Extract dispatch_id
        dispatch_id = None
        if hasattr(dispatch_response, 'dispatch_id'):
            dispatch_id = dispatch_response.dispatch_id
        elif hasattr(dispatch_response, 'agent_dispatch_id'):
            dispatch_id = dispatch_response.agent_dispatch_id
        elif hasattr(dispatch_response, 'id'):
            dispatch_id = dispatch_response.id

        worker_id = getattr(dispatch_response, "worker_id", None)

        logger.info(f"⏱️ Dispatch API call took {dispatch_api_duration:.2f}s")
        if worker_id:
            logger.info(f"✅ Agent dispatched with dispatch_id: {dispatch_id}, worker_id={worker_id}")
            await livekit_manager.return_worker_to_pool(worker_id)
        else:
            # worker_id=None is normal - the dispatch is queued and the agent will pick it up
            logger.info(f"✅ Agent dispatched (queued) with dispatch_id: {dispatch_id}, worker_id=None (agent will pick up when available)")
        
        dispatch_total_duration = time.time() - dispatch_start
        logger.info(f"⏱️ Total dispatch process took {dispatch_total_duration:.2f}s")
        
        return {
            "status": "dispatched",
            "dispatch_id": dispatch_id,
            "message": "Agent job dispatched to worker pool.",
            "mode": "explicit_dispatch",
            "agent": agent.slug,
            "metadata_size": len(json.dumps(job_metadata)),
            "duration_ms": int(dispatch_total_duration * 1000)
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to dispatch agent: {str(e)}", exc_info=True)
        logger.error(f"   - Error type: {type(e).__name__}")
        logger.error(f"   - Room: {room_name}")
        logger.error(f"   - Agent: {agent.slug}")
        # No fallback allowed: fail fast with clear error
        raise HTTPException(status_code=502, detail=f"Explicit dispatch failed: {type(e).__name__}: {e}")


async def _poll_for_text_response(
    livekit_manager: LiveKitManager,
    room_name: str,
    *,
    timeout: float = 90.0,  # Increased from 30s to 90s to allow for LLM streaming
    poll_interval: float = 0.3,  # Slightly slower polling to reduce overhead
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Poll LiveKit room metadata until the text worker returns a final response."""
    start_time = time.time()

    while time.time() - start_time < timeout:
        room_info = await livekit_manager.get_room(room_name)
        if not room_info:
            raise HTTPException(status_code=500, detail="Text mode room disappeared during processing")

        metadata_raw = room_info.get("metadata")
        if metadata_raw:
            try:
                metadata = (
                    json.loads(metadata_raw)
                    if isinstance(metadata_raw, str)
                    else dict(metadata_raw)
                )
            except Exception:
                logger.warning("Text response metadata is malformed; retrying")
                metadata = {}
        else:
            metadata = {}

        streaming_flag = metadata.get("streaming")
        text_response = metadata.get("text_response")

        # Only return when streaming is complete (or flag absent)
        if text_response and streaming_flag is not True:
            citations = metadata.get("citations") or []
            tool_results = metadata.get("tool_results") or []
            return text_response, citations, tool_results

        await asyncio.sleep(poll_interval)

    raise HTTPException(status_code=504, detail=f"Text response timeout after {timeout}s")


async def poll_for_text_response_streaming(
    livekit_manager: LiveKitManager,
    room_name: str,
    *,
    timeout: float = 90.0,
    poll_interval: float = 0.15,  # Faster polling for streaming
) -> AsyncIterator[Dict[str, Any]]:
    """
    Poll LiveKit room metadata and yield streaming updates.
    
    Yields dicts with:
      - {"delta": "..."} for partial text updates
      - {"done": True, "full_text": "...", "citations": [...], "tool_results": [...]} when complete
      - {"error": "..."} on error
    """
    start_time = time.time()
    last_partial_len = 0
    
    while time.time() - start_time < timeout:
        try:
            room_info = await livekit_manager.get_room(room_name)
        except Exception as e:
            logger.warning(f"Error fetching room info: {e}")
            await asyncio.sleep(poll_interval)
            continue
            
        if not room_info:
            yield {"error": "Room disappeared during processing"}
            return

        metadata_raw = room_info.get("metadata")
        if not metadata_raw:
            await asyncio.sleep(poll_interval)
            continue
            
        try:
            metadata = (
                json.loads(metadata_raw)
                if isinstance(metadata_raw, str)
                else dict(metadata_raw)
            )
        except Exception:
            await asyncio.sleep(poll_interval)
            continue

        # Check for partial streaming updates
        partial_text = metadata.get("text_response_partial", "")
        if partial_text and len(partial_text) > last_partial_len:
            # Yield the new delta
            delta = partial_text[last_partial_len:]
            last_partial_len = len(partial_text)
            yield {"delta": delta}

        # Check if streaming is complete
        streaming_flag = metadata.get("streaming")
        text_response = metadata.get("text_response")

        if text_response and streaming_flag is not True:
            citations = metadata.get("citations") or []
            tool_results = metadata.get("tool_results") or []
            widget = metadata.get("widget")  # Widget trigger from agent

            # Debug logging to diagnose widget timing issues
            logger.info(f"[poll-stream] Returning final response. widget={widget is not None}, streaming={streaming_flag}, text_len={len(text_response) if text_response else 0}")
            if not widget:
                # Log metadata keys to help debug missing widget
                logger.warning(f"[poll-stream] Widget NOT present in metadata. Keys: {list(metadata.keys())}")

            result = {
                "done": True,
                "full_text": text_response,
                "citations": citations,
                "tool_results": tool_results,
            }
            if widget:
                result["widget"] = widget
            yield result
            return

        await asyncio.sleep(poll_interval)

    yield {"error": f"Text response timeout after {timeout}s"}


async def _get_or_create_text_room(
    livekit_manager: LiveKitManager,
    *,
    conversation_id: str,
    agent_slug: str,
    user_id: str,
    agent_context: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], bool]:
    """
    Get or create a persistent text room for a conversation.

    This enables room reuse across multiple turns in the same conversation,
    reducing overhead and improving performance for multi-turn text chats.

    Returns:
        Tuple of (room_name, room_info, was_reused)
    """
    room_name = f"text-conv-{conversation_id}"

    # Check if an existing room already tracks this conversation
    existing_room = await livekit_manager.get_room(room_name)
    if existing_room:
        logger.info(f"♻️ Reusing existing text room for conversation {conversation_id}")

        merged_metadata: Dict[str, Any] = {}
        existing_metadata_raw = existing_room.get("metadata")
        if existing_metadata_raw:
            try:
                merged_metadata = (
                    json.loads(existing_metadata_raw)
                    if isinstance(existing_metadata_raw, str)
                    else dict(existing_metadata_raw)
                )
            except Exception:
                logger.warning("Failed to parse existing room metadata; using empty dict")
                merged_metadata = {}

        merged_metadata.update(agent_context or {})
        try:
            await livekit_manager.update_room_metadata(room_name, merged_metadata)
        except Exception as e:
            logger.warning(f"Failed to update room metadata for reuse: {e}")

        room_info = {
            "name": room_name,
            "status": "existing",
            "metadata": merged_metadata,
        }
        return room_name, room_info, True

    # Otherwise create a new persistent text room
    logger.info(f"🆕 Creating new text room for conversation {conversation_id}")
    room_info = await ensure_livekit_room_exists(
        livekit_manager,
        room_name,
        agent_name=settings.livekit_agent_name,
        agent_slug=agent_slug,
        user_id=user_id,
        agent_config=agent_context,
        enable_agent_dispatch=False,
        empty_timeout=3600,  # 1 hour for multi-turn conversations
    )
    return room_name, room_info, False


async def ensure_livekit_room_exists(
    livekit_manager: LiveKitManager,
    room_name: str,
    agent_name: str = None,
    agent_slug: str = None,
    user_id: str = None,
    agent_config: Dict[str, Any] = None,
    enable_agent_dispatch: bool = False,
    empty_timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Ensure a LiveKit room exists, creating it if necessary
    
    This function handles the room lifecycle to prevent timing issues:
    1. Check if room already exists
    2. Create room with appropriate settings if it doesn't exist
    3. Return room information for the frontend
    """
    import time
    start_time = time.time()
    
    logger.debug(f"Checking if room exists", extra={'room_name': room_name})
    try:
        # First, check if the room already exists
        check_start = time.time()
        existing_room = await livekit_manager.get_room(room_name)
        check_duration = time.time() - check_start
        logger.info(f"⏱️ Room existence check took {check_duration:.2f}s")
        
        if existing_room:
            logger.info(f"✅ Room {room_name} already exists with {existing_room['num_participants']} participants")

            merged_metadata: Dict[str, Any] = {}
            existing_metadata_raw = existing_room.get("metadata")
            if existing_metadata_raw:
                try:
                    merged_metadata = (
                        json.loads(existing_metadata_raw)
                        if isinstance(existing_metadata_raw, str)
                        else dict(existing_metadata_raw)
                    )
                except Exception:
                    logger.warning("Failed to parse existing room metadata; starting fresh", exc_info=True)
                    merged_metadata = {}

            if agent_config:
                merged_metadata.update(agent_config)

            merged_metadata.update(
                {
                    "agent_name": agent_name or merged_metadata.get("agent_name"),
                    "agent_slug": agent_slug or merged_metadata.get("agent_slug"),
                    "user_id": user_id or merged_metadata.get("user_id"),
                    "created_by": merged_metadata.get("created_by", "sidekick_backend"),
                }
            )

            def _json_safe(value: Any) -> Any:
                if isinstance(value, datetime):
                    return value.isoformat()
                if isinstance(value, list):
                    return [_json_safe(v) for v in value]
                if isinstance(value, dict):
                    return {k: _json_safe(v) for k, v in value.items()}
                return value

            safe_metadata = _json_safe(merged_metadata)

            try:
                updated = await livekit_manager.update_room_metadata(room_name, safe_metadata)
                if updated:
                    logger.info(
                        "🛠️ Updated existing room metadata with latest agent context",
                        extra={
                            "has_tools": bool(safe_metadata.get("tools")),
                            "has_tools_config": bool(safe_metadata.get("tools_config")),
                            "has_api_keys": bool(safe_metadata.get("api_keys")),
                        },
                    )
                    existing_room["metadata"] = safe_metadata
                else:
                    logger.warning("Failed to push updated metadata to LiveKit room")
            except Exception:
                logger.warning("Error while updating room metadata", exc_info=True)

            total_duration = time.time() - start_time
            return {
                "room_name": room_name,
                "status": "existing",
                "participants": existing_room['num_participants'],
                "created_at": existing_room.get('creation_time'),
                "metadata": safe_metadata,
                "message": f"Room {room_name} already exists and is ready",
                "duration_ms": int(total_duration * 1000)
            }
        
        # Room doesn't exist, create it
        logger.info(f"🏗️ Creating new LiveKit room: {room_name}")
        
        # Start with the full agent configuration as the base for the metadata
        room_metadata = agent_config if agent_config is not None else {}
        
        # Add or overwrite general room information
        room_metadata.update({
            "agent_name": agent_name,
            "agent_slug": agent_slug,
            "user_id": user_id,
            "created_by": "sidekick_backend",
            "created_at": datetime.now().isoformat()
        })
        
        # Convert metadata to JSON string (LiveKit expects JSON string)
        import json
        metadata_json = json.dumps(room_metadata)

        # LiveKit has a 64KB metadata limit - truncate room metadata if needed
        LIVEKIT_METADATA_LIMIT = 60000  # Leave headroom below 65536
        metadata_size = len(metadata_json)
        if metadata_size > LIVEKIT_METADATA_LIMIT:
            logger.warning(f"⚠️ Room metadata ({metadata_size} bytes) exceeds limit. Truncating system_prompt...")
            system_prompt = room_metadata.get("system_prompt", "")
            excess = metadata_size - LIVEKIT_METADATA_LIMIT
            if system_prompt and len(system_prompt) > excess + 1000:
                truncated_prompt = system_prompt[:len(system_prompt) - excess - 100] + "\n\n[... context truncated due to size limits ...]"
                room_metadata["system_prompt"] = truncated_prompt
                metadata_json = json.dumps(room_metadata)
                logger.info(f"   - Truncated room system_prompt from {len(system_prompt)} to {len(truncated_prompt)} chars")
                logger.info(f"   - New room metadata size: {len(metadata_json)} bytes")

        create_start = time.time()
        room_info = await livekit_manager.create_room(
            name=room_name,
            empty_timeout=empty_timeout if empty_timeout is not None else 1800,
            max_participants=10,
            metadata=metadata_json,
            enable_agent_dispatch=enable_agent_dispatch,
            agent_name=agent_name if enable_agent_dispatch else None,
        )
        create_duration = time.time() - create_start
        logger.info(f"⏱️ Room creation took {create_duration:.2f}s")
        
        logger.info(f"✅ Created room {room_name} successfully")
        
        # Quick wait to ensure room is fully created
        wait_start = time.time()
        await asyncio.sleep(0.2)  # Reduced from 1s to 0.2s
        wait_duration = time.time() - wait_start
        logger.info(f"⏱️ Room ready wait took {wait_duration:.2f}s")
        
        # No placeholder token required; room lifetime is managed by empty_timeout
        
        # Verify room was created
        verify_start = time.time()
        verification = await livekit_manager.get_room(room_name)
        verify_duration = time.time() - verify_start
        logger.info(f"⏱️ Room verification took {verify_duration:.2f}s")
        
        if not verification:
            raise Exception(f"Room {room_name} was created but cannot be verified")
        
        logger.debug(f"Room verified", extra={'status': 'success'})
        return {
            "room_name": room_name,
            "status": "created",
            "participants": 0,
            "created_at": room_info["created_at"].isoformat(),
            "max_participants": room_info["max_participants"],
            "metadata": room_metadata,
            "message": f"Room {room_name} created successfully and ready for participants"
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to ensure room {room_name} exists: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create/verify LiveKit room: {str(e)}"
        )
