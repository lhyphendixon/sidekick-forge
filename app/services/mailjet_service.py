"""Transactional email service for marketing notifications.

Migrated from Mailjet to Mailgun. The class name and public API are preserved
so that existing callers (marketing routes) continue to work unchanged.
"""
from __future__ import annotations

import logging
from email.utils import parseaddr
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.integrations.mailgun_client import MailgunClient

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
    """Transactional email service (now backed by Mailgun).

    Class name kept as MailjetService for backwards compatibility with
    existing imports in marketing routes.
    """

    def __init__(self) -> None:
        self._sender_email = (
            settings.mailgun_sender_email
            or settings.mailjet_sender_email
        )
        self._sender_name = (
            settings.mailgun_sender_name
            or settings.mailjet_sender_name
            or settings.platform_name
        )
        self._recipients: List[Dict[str, str]] = []

        recipients_list = settings.mailgun_notification_recipients
        for raw in recipients_list:
            parsed = _parse_recipient(raw, self._sender_name)
            if parsed:
                self._recipients.append(parsed)

        self._mailgun = MailgunClient()

    @property
    def is_configured(self) -> bool:
        return bool(self._mailgun.is_configured and self._sender_email and self._recipients)

    async def send_submission_notification(self, submission_type: str, submission: Dict[str, Any]) -> bool:
        """Send a marketing submission notification email."""
        if not self.is_configured:
            logger.debug("Email service not configured; skipping notification for %s", submission_type)
            return False

        subject, text_body, html_body = self._build_message(submission_type, submission)
        reply_email = submission.get("email")
        reply_name = submission.get("full_name") or submission.get("first_name")
        headers: Dict[str, str] = {}
        if reply_email:
            headers["Reply-To"] = f"{reply_name or reply_email} <{reply_email}>"

        success = True
        for recipient in self._recipients:
            result = await self._mailgun.send_email(
                from_addr=self._sender_email,
                from_name=self._sender_name,
                to=recipient["Email"],
                subject=subject,
                text=text_body,
                html=html_body,
                headers=headers if headers else None,
            )
            if not result:
                success = False

        if success:
            logger.info(
                "Notification sent for %s submission %s",
                submission_type,
                submission.get("id"),
            )
        return success

    async def send_order_confirmation_email(
        self,
        to_email: str,
        to_name: str,
        order_data: Dict[str, Any],
        verification_url: Optional[str] = None,
    ) -> bool:
        """Send an order confirmation email to a customer.

        If *verification_url* is provided the email will include a prominent
        call-to-action asking the recipient to verify their email address.
        """
        if not self.is_configured:
            logger.debug("Email service not configured; skipping order confirmation")
            return False

        platform = settings.platform_name
        tier_name = order_data.get("tier_name", "")
        order_number = order_data.get("order_number", "")
        price = order_data.get("price", "")

        subject = f"[{platform}] Order Confirmation — {order_number}"

        # --- plain text ---
        text_body = f"Thank you for your order, {to_name}!\n\n"
        text_body += f"Plan: {tier_name}\n"
        text_body += f"Order: {order_number}\n"
        if price:
            text_body += f"Price: ${price}/mo\n"
        else:
            text_body += "Price: Free\n"
        if verification_url:
            text_body += (
                "\nPlease verify your email address to activate your account:\n"
                f"{verification_url}\n"
                "\nThis link expires in 24 hours.\n"
            )

        # --- HTML ---
        price_display = f"${escape(str(price))}/mo" if price else "Free"
        verify_block = ""
        if verification_url:
            safe_url = escape(verification_url)
            verify_block = (
                "<tr><td colspan='2' style='padding:24px 0 0 0;'>"
                "<p style='margin:0 0 12px 0;font-size:15px;color:#333;'>Please verify your email to activate your account:</p>"
                f"<a href='{safe_url}' style='display:inline-block;background:#01a4a6;color:#fff;"
                "padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;font-size:15px;'>"
                "Verify Email Address</a>"
                "<p style='margin:12px 0 0 0;font-size:13px;color:#888;'>This link expires in 24 hours.</p>"
                "</td></tr>"
            )

        html_body = (
            f"<div style='font-family:Inter,Helvetica,sans-serif;max-width:560px;margin:0 auto;'>"
            f"<h2 style='color:#111;'>Thanks for your order, {escape(to_name)}!</h2>"
            "<table style='border-collapse:collapse;font-size:14px;width:100%;'>"
            f"<tr><th align='left' style='padding:4px 8px 4px 0;color:#111;'>Plan</th>"
            f"<td style='padding:4px 0 4px 8px;color:#333;'>{escape(tier_name)}</td></tr>"
            f"<tr><th align='left' style='padding:4px 8px 4px 0;color:#111;'>Order</th>"
            f"<td style='padding:4px 0 4px 8px;color:#333;'>{escape(order_number)}</td></tr>"
            f"<tr><th align='left' style='padding:4px 8px 4px 0;color:#111;'>Price</th>"
            f"<td style='padding:4px 0 4px 8px;color:#333;'>{price_display}</td></tr>"
            f"{verify_block}"
            "</table></div>"
        )

        result = await self._mailgun.send_email(
            from_addr=self._sender_email,
            from_name=self._sender_name,
            to=to_email,
            subject=subject,
            text=text_body,
            html=html_body,
        )
        return result is not None

    async def send_verification_email(
        self,
        to_email: str,
        to_name: Optional[str] = None,
        verification_url: Optional[str] = None,
        verification_code: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        """Send an email verification link (or code as fallback)."""
        if not self.is_configured:
            logger.debug("Email service not configured; skipping verification email")
            return False

        platform = settings.platform_name
        display_name = to_name or to_email

        if verification_url:
            subject = f"[{platform}] Verify Your Email Address"
            text_body = (
                f"Hi {display_name},\n\n"
                "Please verify your email address to activate your account:\n"
                f"{verification_url}\n\n"
                "This link expires in 24 hours.\n"
            )
            safe_url = escape(verification_url)
            html_body = (
                f"<div style='font-family:Inter,Helvetica,sans-serif;max-width:560px;margin:0 auto;'>"
                f"<h2 style='color:#111;'>Verify your email</h2>"
                f"<p style='color:#333;font-size:15px;'>Hi {escape(display_name)},</p>"
                "<p style='color:#333;font-size:15px;'>Click the button below to verify your email and activate your account:</p>"
                f"<a href='{safe_url}' style='display:inline-block;background:#01a4a6;color:#fff;"
                "padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;font-size:15px;margin:16px 0;'>"
                "Verify Email Address</a>"
                "<p style='font-size:13px;color:#888;'>This link expires in 24 hours.</p>"
                "</div>"
            )
        else:
            subject = f"[{platform}] Your Verification Code"
            text_body = f"Your verification code is: {verification_code}\n\nThis code expires in 10 minutes."
            html_body = None

        result = await self._mailgun.send_email(
            from_addr=self._sender_email,
            from_name=self._sender_name,
            to=to_email,
            subject=subject,
            text=text_body,
            html=html_body,
        )
        return result is not None

    def _build_message(
        self, submission_type: str, submission: Dict[str, Any]
    ) -> Tuple[str, str, str]:
        """Build subject, text body, and HTML body for a submission notification."""
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
        return subject, text_body or subject, html_body

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

        status_val = submission.get("status")
        if status_val:
            add("Status", status_val)
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
