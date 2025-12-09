"""Telegram webhook integration for Sidekick Forge channels."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.trigger import (
    TriggerAgentRequest,
    TriggerMode,
    handle_text_trigger_via_livekit,
)
from app.config import settings
from app.core.dependencies import get_agent_service
from app.integrations.telegram_client import TelegramClient
from app.services.tools_service_supabase import ToolsService
from app.services.voice_adapter import synthesize_voice, transcribe_audio
from app.services.agent_service_supabase import AgentService
from app.integrations.supabase_client import supabase_manager
from app.admin.routes import _pending_telegram_codes

router = APIRouter()
logger = logging.getLogger(__name__)

_telegram_clients: Dict[str, TelegramClient] = {}


def _get_telegram_client(bot_token: Optional[str] = None) -> TelegramClient:
    """Get or initialize a Telegram client for the given token."""
    token = bot_token or settings.telegram_bot_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram bot not configured",
        )
    if token not in _telegram_clients:
        _telegram_clients[token] = TelegramClient(token)
    return _telegram_clients[token]


async def _resolve_agent_and_client(
    agent_service: AgentService,
    agent_slug: str,
    client_override: Optional[str] = None,
) -> Tuple[Optional[Any], Optional[Any], Optional[str]]:
    """Resolve agent/client for routing."""
    client_id = client_override
    if not client_id:
        try:
            all_agents = await agent_service.get_all_agents_with_clients()
            for agent_row in all_agents:
                if agent_row.get("slug") == agent_slug:
                    client_id = agent_row.get("client_id")
                    break
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to auto-detect agent %s: %s", agent_slug, exc, exc_info=True)
            client_id = None

    if not client_id:
        return None, None, None

    agent = await agent_service.get_agent(client_id, agent_slug)
    client = await agent_service.client_service.get_client(client_id)
    return agent, client, client_id


def _extract_message(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize Telegram update to a message payload."""
    if not update:
        return None
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        if update.get(key):
            return update[key]
    return None


async def _resolve_agent_by_secret(
    secret: str,
    agent_service: AgentService,
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """Find agent/client by matching the Telegram webhook secret."""
    try:
        agents = await agent_service.get_all_agents_with_clients()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load agents for Telegram secret resolution: %s", exc, exc_info=True)
        return None, None, None

    for agent_row in agents:
        tools_cfg = agent_row.get("tools_config") or {}
        channels_cfg = tools_cfg.get("channels") or {}
        tg_cfg = channels_cfg.get("telegram") or {}
        if tg_cfg.get("webhook_secret") == secret:
            return agent_row.get("client_id"), agent_row.get("slug"), tg_cfg

    return None, None, None


@router.post("/telegram")
async def handle_telegram_webhook(
    request: Request,
    agent_service: AgentService = Depends(get_agent_service),
):
    """Webhook endpoint to handle Telegram updates (text + voice)."""
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    update = await request.json()
    message = _extract_message(update)
    if not message:
        return {"ok": True, "ignored": True}

    chat = message.get("chat", {}) or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    from_user = message.get("from", {}) or {}

    if not chat_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing chat_id")

    # Determine agent/client based on secret or defaults
    agent_slug = settings.telegram_default_agent_slug or "farah-qubit"
    client_override = settings.telegram_default_client_id
    default_reply_mode = "auto"
    transcribe_voice = True
    channel_cfg: Dict[str, Any] = {}
    bot_token_override: Optional[str] = None

    if header_secret:
        sec_client_id, sec_agent_slug, sec_cfg = await _resolve_agent_by_secret(header_secret, agent_service)
        if sec_client_id and sec_agent_slug:
            agent_slug = sec_agent_slug
            client_override = sec_client_id
            channel_cfg = {"telegram": sec_cfg}
            bot_token_override = sec_cfg.get("bot_token")
            default_reply_mode = sec_cfg.get("reply_mode", default_reply_mode)
            transcribe_voice = sec_cfg.get("transcribe_voice", transcribe_voice)
        else:
            # If platform secret exists and doesn't match, reject
            if settings.telegram_webhook_secret and header_secret != settings.telegram_webhook_secret:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Telegram secret")
    elif settings.telegram_webhook_secret:
        # No header secret when one is configured globally
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Telegram secret")

    # Resolve agent/client using (possibly overridden) values
    agent, client, client_id = await _resolve_agent_and_client(agent_service, agent_slug, client_override)

    # Apply client-level channel overrides if present and not already from secret
    if not channel_cfg and client and getattr(client, "additional_settings", None):
        try:
            channel_cfg = client.additional_settings.get("channels", {}) or {}
        except Exception:
            channel_cfg = {}
    tg_cfg = channel_cfg.get("telegram", {}) if isinstance(channel_cfg, dict) else {}
    if tg_cfg:
        default_reply_mode = tg_cfg.get("reply_mode", default_reply_mode)
        transcribe_voice = tg_cfg.get("transcribe_voice", transcribe_voice)
        if tg_cfg.get("bot_token"):
            bot_token_override = tg_cfg.get("bot_token")
        agent_override = tg_cfg.get("default_agent_slug")
        if agent_override and getattr(agent, "slug", None) != agent_override:
            agent, client, client_id = await _resolve_agent_and_client(agent_service, agent_override, client_override or client_id)
            agent_slug = agent_override

    bot = _get_telegram_client(bot_token_override)

    if not agent or not client:
        await bot.send_message(chat_id, "Sidekick is not available right now. Please try again later.")
        return {"ok": False, "error": "agent_not_found"}

    voice = message.get("voice")
    inbound_text: Optional[str] = None
    wants_voice_reply = False
    if voice and not transcribe_voice:
        await bot.send_message(chat_id, "Voice messages are disabled for this workspace.")
        return {"ok": False, "error": "voice_disabled"}

    if voice:
        file_id = voice.get("file_id")
        audio_bytes = await bot.download_file(file_id) if file_id else None
        if not audio_bytes:
            await bot.send_message(chat_id, "I couldn't access that voice note. Please try again.")
            return {"ok": False, "error": "voice_download_failed"}
        inbound_text = await transcribe_audio(audio_bytes, agent=agent, client=client)
        wants_voice_reply = True
    else:
        inbound_text = (message.get("text") or message.get("caption") or "").strip()

    # Verification flow: handle "/start CODE" or plain code to link user
    verification_code = None
    if inbound_text:
        lowered = inbound_text.lower().strip()
        if lowered.startswith("/start"):
            parts = inbound_text.split()
            verification_code = parts[1].strip().upper() if len(parts) > 1 else None
        elif len(inbound_text.strip()) == 6:
            verification_code = inbound_text.strip().upper()

    if verification_code:
        pending_match = None
        for k, v in list(_pending_telegram_codes.items()):
            if v.get("code") == verification_code:
                pending_match = _pending_telegram_codes.pop(k, None)
                break

        if pending_match:
            user_id = pending_match.get("user_id")
            email = pending_match.get("email")
            try:
                if not getattr(supabase_manager, "_initialized", False):
                    await supabase_manager.initialize()
                await supabase_manager.update_user_profile(
                    user_id,
                    {
                        "telegram_username": from_user.get("username"),
                        "telegram_user_id": str(from_user.get("id")),
                        "telegram_verified_at": datetime.utcnow().isoformat(),
                    },
                    email=email,
                )
                # persist binding in dedicated table
                await supabase_manager.upsert_telegram_link(
                    user_id or "",
                    from_user.get("username"),
                    str(from_user.get("id")),
                )
                await bot.send_message(chat_id, "Telegram verified! You're now linked.")
                return {"ok": True, "mode": "verify"}
            except Exception as e:
                logger.error(f"Failed to verify telegram for user {user_id}: {e}", exc_info=True)
                await bot.send_message(chat_id, "Sorry, we couldn't complete verification. Please try again.")
                return {"ok": False, "error": "verify_failed"}

    if not inbound_text:
        await bot.send_message(chat_id, "Please send a text message or a voice note for me to respond to.")
        return {"ok": False, "error": "empty_message"}

    tools_service = ToolsService(agent_service.client_service)
    trigger_request = TriggerAgentRequest(
        agent_slug=agent_slug,
        client_id=client_id,
        mode=TriggerMode.TEXT,
        message=inbound_text,
        user_id=str(from_user.get("id") or chat_id),
        session_id=f"telegram-{chat_id}",
        conversation_id=f"telegram-{chat_id}",
        context={
            "channel": "telegram",
            "chat_id": chat_id,
            "username": from_user.get("username"),
            "first_name": from_user.get("first_name"),
            "last_name": from_user.get("last_name"),
            "telegram_user_id": from_user.get("id"),
        },
    )

    try:
        result = await handle_text_trigger_via_livekit(
            trigger_request,
            agent,
            client,
            tools_service,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Telegram dispatch failed: %s", exc, exc_info=True)
        await bot.send_message(chat_id, "I ran into an issue handling that message. Please try again.")
        return {"ok": False, "error": "processing_failed"}

    response_text = (
        (result or {}).get("response")
        or (result or {}).get("agent_response")
        or (result or {}).get("ai_response")
    )

    if not response_text:
        await bot.send_message(chat_id, "I couldn't generate a response just now. Please try again.")
        return {"ok": False, "error": "empty_response"}

    reply_mode = default_reply_mode or "auto"
    should_send_voice = wants_voice_reply and reply_mode in {"auto", "voice_on_voice"}
    if reply_mode == "text":
        should_send_voice = False

    if should_send_voice:
        audio_bytes = await synthesize_voice(response_text, agent=agent, client=client)
        if audio_bytes:
            await bot.send_voice(chat_id, audio_bytes, reply_to_message_id=message_id)
            return {"ok": True, "mode": "voice"}

    await bot.send_message(chat_id, response_text, reply_to_message_id=message_id)
    return {"ok": True, "mode": "text"}
