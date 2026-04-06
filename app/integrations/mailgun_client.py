"""Async Mailgun client for sending emails and verifying inbound webhook signatures."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class MailgunClient:
    """Minimal async wrapper around the Mailgun API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        domain: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or settings.mailgun_api_key or ""
        self.domain = domain or settings.mailgun_domain or "sidekickforge.com"
        self.base_url = (base_url or settings.mailgun_base_url or "https://api.mailgun.net").rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            auth=("api", self.api_key),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.domain)

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            logger.debug("Failed to close Mailgun HTTP client", exc_info=True)

    async def send_email(
        self,
        from_addr: str,
        from_name: str,
        to: str,
        subject: str,
        text: str,
        html: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Send an email via Mailgun.

        Args:
            from_addr: Sender email address.
            from_name: Sender display name.
            to: Recipient email address.
            subject: Email subject.
            text: Plain-text body.
            html: Optional HTML body.
            headers: Optional extra headers (e.g. In-Reply-To, References).

        Returns:
            Mailgun response dict on success, None on failure.
        """
        url = f"{self.base_url}/v3/{self.domain}/messages"
        data: Dict[str, str] = {
            "from": f"{from_name} <{from_addr}>",
            "to": to,
            "subject": subject,
            "text": text,
        }
        if html:
            data["html"] = html

        # Mailgun supports custom headers via h: prefix
        if headers:
            for key, value in headers.items():
                data[f"h:{key}"] = value

        try:
            response = await self._client.post(url, data=data)
            if response.status_code >= 400:
                logger.error(
                    "Mailgun send failed: %s %s",
                    response.status_code,
                    response.text,
                )
                return None
            return response.json()
        except Exception:
            logger.exception("Mailgun send_email request failed")
            return None

    async def send_reply(
        self,
        agent_email: str,
        agent_name: str,
        to: str,
        subject: str,
        body_text: str,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Send a threaded reply from a sidekick.

        Automatically sets display name to '{agent_name} | Sidekick Forge'
        and threading headers for proper email client threading.
        """
        display_name = f"{agent_name} | Sidekick Forge"
        headers: Dict[str, str] = {}
        if in_reply_to:
            headers["In-Reply-To"] = in_reply_to
        if references:
            headers["References"] = references
        elif in_reply_to:
            # If no explicit References, use In-Reply-To as References
            headers["References"] = in_reply_to

        # Standard Re: threading for subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        return await self.send_email(
            from_addr=agent_email,
            from_name=display_name,
            to=to,
            subject=subject,
            text=body_text,
            headers=headers if headers else None,
        )

    @staticmethod
    def verify_webhook_signature(
        token: str,
        timestamp: str,
        signature: str,
        signing_key: Optional[str] = None,
    ) -> bool:
        """Verify a Mailgun inbound webhook signature.

        Mailgun signs webhooks with HMAC-SHA256:
            HMAC(signing_key, timestamp + token) == signature

        Also rejects timestamps older than 5 minutes to prevent replay.
        """
        key = signing_key or settings.mailgun_webhook_signing_key or ""
        if not key:
            logger.warning("No Mailgun webhook signing key configured; skipping verification")
            return True  # Fail open during development — tighten before production

        # Reject stale timestamps (> 5 min)
        try:
            ts = int(timestamp)
            if abs(time.time() - ts) > 300:
                logger.warning("Mailgun webhook timestamp too old: %s", timestamp)
                return False
        except (ValueError, TypeError):
            logger.warning("Invalid Mailgun webhook timestamp: %s", timestamp)
            return False

        expected = hmac.new(
            key.encode("utf-8"),
            f"{timestamp}{token}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)
