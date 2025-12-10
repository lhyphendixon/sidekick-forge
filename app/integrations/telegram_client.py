"""Lightweight Telegram client for sending messages, voice notes, and downloading files."""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class TelegramClient:
    """Minimal async wrapper around Telegram Bot API."""

    def __init__(self, bot_token: str, timeout: float = 15.0) -> None:
        if not bot_token:
            raise ValueError("Telegram bot token is required")
        self.bot_token = bot_token
        self.api_base = f"https://api.telegram.org/bot{bot_token}"
        self.file_base = f"https://api.telegram.org/file/bot{bot_token}"
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        try:
            await self._client.aclose()
        except Exception:
            logger.debug("Failed to close Telegram HTTP client", exc_info=True)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
        parse_mode: Optional[str] = None,
    ) -> None:
        """Send a text message."""
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        if parse_mode:
            payload["parse_mode"] = parse_mode

        response = await self._client.post(f"{self.api_base}/sendMessage", data=payload)
        if response.status_code >= 400:
            logger.warning(
                "Telegram sendMessage failed: %s %s",
                response.status_code,
                response.text,
            )

    async def send_voice(
        self,
        chat_id: int,
        voice_bytes: bytes,
        reply_to_message_id: Optional[int] = None,
        caption: Optional[str] = None,
    ) -> None:
        """Send a voice note (expects OGG/Opus)."""
        data = {"chat_id": chat_id}
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        if caption:
            data["caption"] = caption

        files = {
            "voice": ("reply.ogg", voice_bytes, "audio/ogg"),
        }
        response = await self._client.post(
            f"{self.api_base}/sendVoice",
            data=data,
            files=files,
        )
        if response.status_code >= 400:
            logger.warning(
                "Telegram sendVoice failed: %s %s",
                response.status_code,
                response.text,
            )

    async def get_file_path(self, file_id: str) -> Optional[str]:
        """Resolve a Telegram file_id to a downloadable path."""
        try:
            response = await self._client.get(f"{self.api_base}/getFile", params={"file_id": file_id})
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result", {}) if isinstance(payload, dict) else {}
            return result.get("file_path")
        except Exception as exc:
            logger.warning("Failed to resolve Telegram file %s: %s", file_id, exc)
            return None

    async def download_file(self, file_id: str) -> Optional[bytes]:
        """Download a Telegram file by id."""
        file_path = await self.get_file_path(file_id)
        if not file_path:
            return None
        url = f"{self.file_base}/{file_path}"
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            return response.content
        except Exception as exc:
            logger.warning("Failed to download Telegram file %s (%s): %s", file_id, file_path, exc)
            return None
