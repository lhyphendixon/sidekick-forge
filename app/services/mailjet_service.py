from __future__ import annotations

import asyncio
import logging
from email.utils import parseaddr
from html import escape
from typing import Any, Dict, List, Optional, Tuple

try:
    from mailjet_rest import Client  # type: ignore
except ModuleNotFoundError:
    Client = None  # type: ignore

from app.config import settings

logger = logging.getLogger(__name__)


def _parse_recipient(entry: str, fallback_name: str) -> Optional[Dict[str, str]]:
    if not entry:
        return None
    name, email = parseaddr(entry)
    email = (email or entry).strip()
    if not email:
        return None
    return {
        "Email": email,
        "Name": name.strip() or fallback_name,
    }


class MailjetService:
    """Thin wrapper around Mailjet's transactional email API."""

    def __init__(self) -> None:
        self._sender_email = settings.mailjet_sender_email
        self._sender_name = settings.mailjet_sender_name or settings.platform_name
        self._recipients: List[Dict[str, str]] = []

        if settings.mailjet_notification_recipients:
            for raw in settings.mailjet_notification_recipients:
                parsed = _parse_recipient(raw, self._sender_name)
                if parsed:
                    self._recipients.append(parsed)

        if Client and settings.mailjet_api_key and settings.mailjet_api_secret:
            self._client: Optional[Client] = Client(
                auth=(settings.mailjet_api_key, settings.mailjet_api_secret),
                version="v3.1",
            )
        else:
            self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(self._client and self._sender_email and self._recipients)

    async def send_submission_notification(self, submission_type: str, submission: Dict[str, Any]) -> bool:
        """Send a marketing submission notification email."""
        if not self.is_configured:
            logger.debug("Mailjet service not configured; skipping notification for %s", submission_type)
            return False

        payload = self._build_message(submission_type, submission)
        try:
            await asyncio.to_thread(self._send, payload)
            logger.info(
                "Mailjet notification sent for %s submission %s",
                submission_type,
                submission.get("id"),
            )
            return True
        except Exception:
            logger.exception("Failed to dispatch Mailjet notification")
            return False

    def _send(self, payload: Dict[str, Any]) -> None:
        if not self._client:
            raise RuntimeError("Mailjet client is not initialized")
        response = self._client.send.create(data=payload)
        if response.status_code >= 400:
            # Mailjet returns JSON body with ErrorIdentifier / ErrorMessage
            raise RuntimeError(f"Mailjet send failed ({response.status_code}): {response.json()}")

    def _build_message(self, submission_type: str, submission: Dict[str, Any]) -> Dict[str, Any]:
        friendly_type = self._friendly_type(submission_type)
        subject = f"[{settings.platform_name}] New {friendly_type} submission"
        lines = self._collect_lines(submission_type, submission)
        text_body = "\n".join(f"{label}: {value}" for label, value in lines if value)
        html_rows = "".join(
            f"<tr><th align='left' style='padding:4px 8px 4px 0;color:#111;'>{escape(label)}</th>"
            f"<td style='padding:4px 0 4px 8px;color:#333;'>{escape(value).replace(chr(10), '<br/>')}</td></tr>"
            for label, value in lines
            if value
        )
        html_body = (
            f"<h2 style='font-family:Inter,Helvetica,sans-serif;color:#111;'>New {escape(friendly_type)} submission</h2>"
            f"<table style='border-collapse:collapse;font-family:Inter,Helvetica,sans-serif;font-size:14px;'>"
            f"{html_rows}"
            "</table>"
        )

        message: Dict[str, Any] = {
            "From": {"Email": self._sender_email, "Name": self._sender_name},
            "To": self._recipients,
            "Subject": subject,
            "TextPart": text_body or subject,
            "HTMLPart": html_body,
            "CustomID": f"marketing_{submission_type}_{submission.get('id', 'unknown')}",
        }

        reply_email = submission.get("email")
        reply_name = submission.get("full_name") or submission.get("first_name")
        if reply_email:
            message["ReplyTo"] = {"Email": reply_email, "Name": reply_name or reply_email}

        return {"Messages": [message]}

    @staticmethod
    def _friendly_type(submission_type: str) -> str:
        mapping = {
            "contact": "contact form",
            "demo": "demo request",
            "early_access": "early access request",
        }
        return mapping.get(submission_type, submission_type or "marketing")

    def _collect_lines(self, submission_type: str, submission: Dict[str, Any]) -> List[Tuple[str, str]]:
        lines: List[Tuple[str, str]] = []

        def add(label: str, value: Optional[Any]) -> None:
            if value is None:
                return
            text = str(value).strip()
            if text:
                lines.append((label, text))

        full_name = submission.get("full_name")
        if not full_name:
            first = submission.get("first_name") or ""
            last = submission.get("last_name") or ""
            combined = " ".join(part for part in (first, last) if part)
            full_name = combined or None
        add("Name", full_name)
        add("Email", submission.get("email"))
        add("Subject", submission.get("subject") or submission.get("notes"))
        add("Company", submission.get("company") or submission.get("business_name"))
        add("Phone", submission.get("phone_number"))

        if submission_type == "early_access":
            add("Stage", submission.get("stage"))
            add("Primary use case", submission.get("use_case"))

        if submission_type == "demo":
            add("Priority", submission.get("priority"))

        status = submission.get("status")
        if status:
            add("Status", status)
        priority = submission.get("priority")
        if priority and submission_type != "demo":
            add("Priority", priority)

        message = submission.get("message")
        if message:
            add("Message", message)

        add("Submission type", submission_type)
        add("Submission ID", submission.get("id"))
        add("Created at", submission.get("created_at"))
        add("IP address", submission.get("ip_address"))
        add("Referrer", submission.get("referrer"))
        add("User agent", submission.get("user_agent"))

        for key in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"):
            add(key.replace("_", " ").title(), submission.get(key))

        return lines


mailjet_service = MailjetService()
