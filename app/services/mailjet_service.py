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


    # ============================================================
    # User-Facing Transactional Emails
    # ============================================================

    async def send_order_confirmation_email(
        self,
        to_email: str,
        to_name: str,
        order_data: Dict[str, Any],
        verification_url: str,
    ) -> bool:
        """
        Send order confirmation with verification link to customer.

        Args:
            to_email: Customer email address
            to_name: Customer name
            order_data: Dict with order_number, tier_name, price, etc.
            verification_url: Full URL for email verification
        """
        if not self._client or not self._sender_email:
            logger.warning("Mailjet not configured; skipping order confirmation email to %s", to_email)
            return False

        order_number = order_data.get("order_number", "N/A")
        tier_name = order_data.get("tier_name", "Unknown")
        price = order_data.get("price", 0)

        subject = f"Welcome to {settings.platform_name} - Activate Your Account"

        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;font-family:Inter,Helvetica,Arial,sans-serif;background:#f4f4f5;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;margin:0 auto;">
        <!-- Header -->
        <tr>
            <td style="background:linear-gradient(135deg,#01a4a6 0%,#018789 100%);padding:32px 24px;text-align:center;">
                <h1 style="margin:0;color:#ffffff;font-size:28px;font-weight:700;">Welcome to {escape(settings.platform_name)}!</h1>
            </td>
        </tr>

        <!-- Body -->
        <tr>
            <td style="background:#ffffff;padding:32px 24px;">
                <p style="margin:0 0 16px;color:#374151;font-size:16px;line-height:1.6;">
                    Hi {escape(to_name)},
                </p>

                <p style="margin:0 0 24px;color:#374151;font-size:16px;line-height:1.6;">
                    Thank you for your order! Your AI sidekick is being prepared. Here are your order details:
                </p>

                <!-- Order Box -->
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;margin:0 0 24px;">
                    <tr>
                        <td style="padding:16px;">
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td style="padding:8px 0;color:#6b7280;font-size:14px;">Order Number</td>
                                    <td style="padding:8px 0;color:#111827;font-size:14px;text-align:right;font-weight:600;">{escape(order_number)}</td>
                                </tr>
                                <tr>
                                    <td style="padding:8px 0;color:#6b7280;font-size:14px;">Plan</td>
                                    <td style="padding:8px 0;color:#111827;font-size:14px;text-align:right;font-weight:600;">{escape(tier_name)}</td>
                                </tr>
                                <tr>
                                    <td style="padding:8px 0;color:#6b7280;font-size:14px;">Price</td>
                                    <td style="padding:8px 0;color:#111827;font-size:14px;text-align:right;font-weight:600;">${price}/month</td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>

                <!-- CTA Section -->
                <div style="background:#f0fdfa;border:1px solid #99f6e4;border-radius:8px;padding:24px;text-align:center;margin:0 0 24px;">
                    <h2 style="margin:0 0 12px;color:#0d9488;font-size:20px;font-weight:600;">Activate Your Account</h2>
                    <p style="margin:0 0 20px;color:#374151;font-size:14px;">
                        Please verify your email to access your dashboard:
                    </p>
                    <a href="{escape(verification_url)}"
                       style="display:inline-block;background:#01a4a6;color:#ffffff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:600;font-size:16px;">
                        Verify Email &amp; Activate
                    </a>
                </div>

                <p style="margin:0 0 8px;color:#6b7280;font-size:13px;">
                    This verification link expires in 24 hours.
                </p>
                <p style="margin:0 0 24px;color:#6b7280;font-size:13px;">
                    If you didn't create this account, you can safely ignore this email.
                </p>

                <!-- Next Steps -->
                <h3 style="margin:0 0 12px;color:#111827;font-size:16px;font-weight:600;">What happens next?</h3>
                <ol style="margin:0 0 24px;padding-left:20px;color:#374151;font-size:14px;line-height:1.8;">
                    <li>Click the button above to verify your email</li>
                    <li>Your sidekick infrastructure is being set up (takes a few minutes)</li>
                    <li>Once ready, log in to your dashboard to create your first AI sidekick</li>
                </ol>
            </td>
        </tr>

        <!-- Footer -->
        <tr>
            <td style="background:#1f2937;padding:24px;text-align:center;">
                <p style="margin:0 0 8px;color:#9ca3af;font-size:14px;">
                    {escape(settings.platform_name)} - Your AI Sidekick for the Hero's Journey
                </p>
                <p style="margin:0;color:#6b7280;font-size:12px;">
                    Questions? Reply to this email or contact team@sidekickforge.com
                </p>
            </td>
        </tr>
    </table>
</body>
</html>
"""

        text_body = f"""Welcome to {settings.platform_name}!

Hi {to_name},

Thank you for your order! Here are your details:

Order Number: {order_number}
Plan: {tier_name}
Price: ${price}/month

ACTIVATE YOUR ACCOUNT
Please verify your email to access your dashboard:
{verification_url}

This link expires in 24 hours.

What happens next?
1. Click the link above to verify your email
2. Your sidekick infrastructure is being set up
3. Once ready, log in to create your first AI sidekick

Questions? Reply to this email or contact team@sidekickforge.com

{settings.platform_name} - Your AI Sidekick for the Hero's Journey
"""

        payload = {
            "Messages": [{
                "From": {"Email": self._sender_email, "Name": self._sender_name},
                "To": [{"Email": to_email, "Name": to_name}],
                "Subject": subject,
                "TextPart": text_body,
                "HTMLPart": html_body,
                "CustomID": f"order_confirmation_{order_data.get('order_id', 'unknown')}",
                "TrackClicks": "disabled",  # Disable link tracking to avoid redirect through old domain
            }]
        }

        try:
            await asyncio.to_thread(self._send, payload)
            logger.info("Order confirmation email sent to %s for order %s", to_email, order_number)
            return True
        except Exception:
            logger.exception("Failed to send order confirmation email to %s", to_email)
            return False

    async def send_verification_email(
        self,
        to_email: str,
        to_name: str,
        verification_url: str,
    ) -> bool:
        """
        Send a standalone verification/resend email.

        Args:
            to_email: Customer email address
            to_name: Customer name
            verification_url: Full URL for email verification
        """
        if not self._client or not self._sender_email:
            logger.warning("Mailjet not configured; skipping verification email to %s", to_email)
            return False

        subject = f"Verify your {settings.platform_name} account"

        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;font-family:Inter,Helvetica,Arial,sans-serif;background:#f4f4f5;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;margin:0 auto;">
        <!-- Header -->
        <tr>
            <td style="background:linear-gradient(135deg,#01a4a6 0%,#018789 100%);padding:32px 24px;text-align:center;">
                <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;">Verify Your Email</h1>
            </td>
        </tr>

        <!-- Body -->
        <tr>
            <td style="background:#ffffff;padding:32px 24px;">
                <p style="margin:0 0 16px;color:#374151;font-size:16px;line-height:1.6;">
                    Hi {escape(to_name)},
                </p>

                <p style="margin:0 0 24px;color:#374151;font-size:16px;line-height:1.6;">
                    Click the button below to verify your email and activate your account:
                </p>

                <div style="text-align:center;margin:0 0 24px;">
                    <a href="{escape(verification_url)}"
                       style="display:inline-block;background:#01a4a6;color:#ffffff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:600;font-size:16px;">
                        Verify Email
                    </a>
                </div>

                <p style="margin:0;color:#6b7280;font-size:13px;">
                    This link expires in 24 hours. If you didn't request this, you can safely ignore this email.
                </p>
            </td>
        </tr>

        <!-- Footer -->
        <tr>
            <td style="background:#1f2937;padding:24px;text-align:center;">
                <p style="margin:0;color:#6b7280;font-size:12px;">
                    {escape(settings.platform_name)}
                </p>
            </td>
        </tr>
    </table>
</body>
</html>
"""

        text_body = f"""Verify Your Email

Hi {to_name},

Click the link below to verify your email and activate your account:
{verification_url}

This link expires in 24 hours.

{settings.platform_name}
"""

        payload = {
            "Messages": [{
                "From": {"Email": self._sender_email, "Name": self._sender_name},
                "To": [{"Email": to_email, "Name": to_name}],
                "Subject": subject,
                "TextPart": text_body,
                "HTMLPart": html_body,
                "CustomID": f"verification_resend_{to_email}",
                "TrackClicks": "disabled",  # Disable link tracking to avoid redirect through old domain
            }]
        }

        try:
            await asyncio.to_thread(self._send, payload)
            logger.info("Verification email sent to %s", to_email)
            return True
        except Exception:
            logger.exception("Failed to send verification email to %s", to_email)
            return False


mailjet_service = MailjetService()
