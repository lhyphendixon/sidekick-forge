"""Inbound email webhook for Sidekick Forge email channel.

Mailgun posts inbound emails to this endpoint via a catch-all route on
sidekickforge.com.  The handler resolves the recipient to a sidekick,
verifies the sender's identity (one-time verification code), then
dispatches the message through the standard LiveKit text pipeline and
sends the agent's response as a threaded email reply.

Verification flow:
    1. Sender emails sidekick for the first time.
    2. System checks if sender's email belongs to a registered user.
       - If not registered: polite rejection.
    3. System sends a one-time verification code.
    4. Sender replies with the code.
    5. System verifies, links the email, and processes the held message.
    6. All subsequent emails from that sender skip verification.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.trigger import (
    TriggerAgentRequest,
    TriggerMode,
    handle_text_trigger_via_livekit,
)
from app.core.dependencies import get_agent_service
from app.integrations.mailgun_client import MailgunClient
from app.services.agent_service_supabase import AgentService
from app.services.campaign_scan_service import campaign_scan_service
from app.services.email_address_service import email_address_service
from app.services.email_verification_service import email_verification_service
from app.services.tools_service_supabase import ToolsService

router = APIRouter()
logger = logging.getLogger(__name__)

_mailgun_client: Optional[MailgunClient] = None


def _get_mailgun_client() -> MailgunClient:
    global _mailgun_client
    if _mailgun_client is None:
        _mailgun_client = MailgunClient()
    return _mailgun_client


def _extract_email_address(raw: str) -> str:
    """Extract bare email from 'Name <email>' or plain 'email' format."""
    raw = raw.strip()
    if "<" in raw and ">" in raw:
        return raw.split("<")[1].split(">")[0].strip().lower()
    return raw.lower()


def _extract_verification_code(text: str) -> Optional[str]:
    """Try to extract a 6-character verification code from the email body.

    Handles cases like:
        - Just the code: "A7X92K"
        - Code in a reply: "A7X92K\n\nOn Apr 2..."
        - Code mentioned inline: "here is my code: A7X92K"
    """
    if not text:
        return None
    # Look for a standalone 6-char alphanumeric sequence
    match = re.search(r"\b([A-Z0-9]{6})\b", text.strip().upper())
    return match.group(1) if match else None


async def _dispatch_to_agent(
    agent_slug: str,
    client_id: str,
    sender: str,
    subject: str,
    body: str,
    message_id: str,
    in_reply_to: Optional[str],
    user_id: str,
    spf_result: str,
    dkim_result: str,
    agent_service: AgentService,
    agent,
    client,
) -> Optional[str]:
    """Dispatch an email to the agent pipeline and return the response text."""
    message_for_agent = body
    if subject:
        message_for_agent = f"[Email Subject: {subject}]\n\n{body}"

    tools_service = ToolsService(agent_service.client_service)
    trigger_request = TriggerAgentRequest(
        agent_slug=agent_slug,
        client_id=client_id,
        mode=TriggerMode.TEXT,
        message=message_for_agent,
        user_id=user_id,
        session_id=f"email-{sender}-{agent_slug}",
        conversation_id=f"email-{sender}-{agent_slug}",
        context={
            "channel": "email",
            "sender_email": sender,
            "subject": subject,
            "message_id": message_id,
            "in_reply_to": in_reply_to,
            "spf_result": spf_result,
            "dkim_result": dkim_result,
        },
    )

    result = await handle_text_trigger_via_livekit(
        trigger_request, agent, client, tools_service
    )

    return (
        (result or {}).get("response")
        or (result or {}).get("agent_response")
        or (result or {}).get("ai_response")
    )


@router.post("/email/inbound")
async def handle_inbound_email(
    request: Request,
    agent_service: AgentService = Depends(get_agent_service),
):
    """Webhook endpoint for Mailgun inbound email routing."""
    form = await request.form()

    # --- Verify webhook signature ---
    token = form.get("token", "")
    timestamp = form.get("timestamp", "")
    signature = form.get("signature", "")

    if not MailgunClient.verify_webhook_signature(
        str(token), str(timestamp), str(signature)
    ):
        logger.warning("Invalid Mailgun webhook signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # --- Parse email fields ---
    sender = _extract_email_address(str(form.get("sender", "")))
    recipient = _extract_email_address(str(form.get("recipient", "")))
    subject = str(form.get("subject", "")).strip()
    body_plain = str(form.get("body-plain", "")).strip()
    message_id = str(form.get("Message-Id", "")).strip()
    in_reply_to = str(form.get("In-Reply-To", "")).strip() or None

    # SPF / DKIM verification results from Mailgun
    spf_result = str(form.get("X-Mailgun-Spf", "unknown")).strip()
    dkim_result = str(form.get("X-Mailgun-Dkim-Check-Result", "unknown")).strip()

    if not sender or not recipient:
        logger.warning("Inbound email missing sender or recipient")
        return {"ok": False, "error": "missing_sender_or_recipient"}

    if not body_plain:
        logger.info("Inbound email from %s has no plain-text body, skipping", sender)
        return {"ok": False, "error": "empty_body"}

    # --- Resolve sidekick by recipient email ---
    registry = await email_address_service.lookup(recipient)
    if not registry:
        logger.info("No sidekick registered for email address %s", recipient)
        return {"ok": False, "error": "unknown_recipient"}

    client_id = registry["client_id"]
    agent_slug = registry["agent_slug"]

    agent = await agent_service.get_agent(client_id, agent_slug)
    client = await agent_service.client_service.get_client(client_id)

    if not agent or not client:
        logger.warning(
            "Could not resolve agent %s/%s for email %s",
            client_id, agent_slug, recipient,
        )
        return {"ok": False, "error": "agent_not_found"}

    # Check if email channel is enabled
    email_enabled = False
    if agent.channels and hasattr(agent.channels, "email"):
        email_enabled = agent.channels.email.enabled
    if not email_enabled and client.settings and client.settings.channels:
        email_enabled = client.settings.channels.email.enabled

    if not email_enabled:
        logger.info("Email channel not enabled for agent %s", agent_slug)
        return {"ok": False, "error": "email_channel_disabled"}

    mailgun = _get_mailgun_client()

    # ================================================================
    # CAMPAIGN SCAN CHECK (before verification)
    # Forwarded emails may arrive from any sender, so check for a pending
    # campaign scan before requiring sender verification.
    # ================================================================
    pending_scan = await campaign_scan_service.get_pending_scan_by_agent(agent_slug)
    if pending_scan and pending_scan["status"] == "pending":
        body_html = str(form.get("body-html", "")).strip()
        email_images = []
        try:
            cid_map_raw = str(form.get("content-id-map", "{}"))
            import json as _json
            cid_map = _json.loads(cid_map_raw) if cid_map_raw else {}
            for cid, url in cid_map.items():
                if isinstance(url, str):
                    email_images.append(url)
                elif isinstance(url, dict):
                    email_images.append(url.get("url", ""))
        except Exception:
            pass

        sb = await campaign_scan_service._get_platform_client()
        sb.table("campaign_scans").update({
            "email_subject": subject,
            "email_body_plain": body_plain,
            "email_body_html": body_html,
            "email_images": _json.dumps(email_images) if email_images else "[]",
        }).eq("id", pending_scan["id"]).execute()

        import asyncio
        asyncio.create_task(
            campaign_scan_service.process_scan(pending_scan["id"], client_id)
        )

        logger.info(
            "Forwarded email routed to campaign scan %s for %s (pre-verification)",
            pending_scan["id"], agent_slug,
        )

        await mailgun.send_reply(
            agent_email=recipient,
            agent_name=agent.name,
            to=sender,
            subject=subject,
            body_text="Got it! I'm analyzing your email now. Check the Campaign Scan widget for results.",
            in_reply_to=message_id or None,
            references=message_id or None,
        )
        return {"ok": True, "mode": "campaign_scan", "scan_id": pending_scan["id"]}

    # ================================================================
    # VERIFICATION FLOW
    # ================================================================

    # Check if sender is already verified for this client
    verified_link = await email_verification_service.get_verified_link(sender, client_id)

    if verified_link:
        # --- VERIFIED SENDER ---
        user_id = verified_link["user_id"]
        logger.info("Verified sender %s (user %s) emailing %s", sender, user_id, agent_slug)

        # --- Check for active Campaign Scan widget waiting for an email ---
        pending_scan = await campaign_scan_service.get_pending_scan(agent_slug, user_id)
        if pending_scan and pending_scan["status"] == "pending":
            # Route this email to the campaign scan pipeline instead of the agent
            body_html = str(form.get("body-html", "")).strip()
            # Extract inline image URLs from Mailgun content-id-map if available
            email_images = []
            try:
                cid_map_raw = str(form.get("content-id-map", "{}"))
                import json as _json
                cid_map = _json.loads(cid_map_raw) if cid_map_raw else {}
                for cid, url in cid_map.items():
                    if isinstance(url, str):
                        email_images.append(url)
                    elif isinstance(url, dict):
                        email_images.append(url.get("url", ""))
            except Exception:
                pass

            # Update the pending scan with email content
            sb = await campaign_scan_service._get_platform_client()
            sb.table("campaign_scans").update({
                "email_subject": subject,
                "email_body_plain": body_plain,
                "email_body_html": body_html,
                "email_images": _json.dumps(email_images) if email_images else "[]",
            }).eq("id", pending_scan["id"]).execute()

            # Process the scan asynchronously
            import asyncio
            asyncio.create_task(
                campaign_scan_service.process_scan(pending_scan["id"], client_id)
            )

            logger.info(
                "Forwarded email routed to campaign scan %s for %s",
                pending_scan["id"], agent_slug,
            )

            # Send acknowledgement
            await mailgun.send_reply(
                agent_email=recipient,
                agent_name=agent.name,
                to=sender,
                subject=subject,
                body_text="Got it! I'm analyzing your email now. Check the Campaign Scan widget for results.",
                in_reply_to=message_id or None,
                references=message_id or None,
            )
            return {"ok": True, "mode": "campaign_scan", "scan_id": pending_scan["id"]}

        # --- Normal message dispatch ---
        try:
            response_text = await _dispatch_to_agent(
                agent_slug=agent_slug,
                client_id=client_id,
                sender=sender,
                subject=subject,
                body=body_plain,
                message_id=message_id,
                in_reply_to=in_reply_to,
                user_id=user_id,
                spf_result=spf_result,
                dkim_result=dkim_result,
                agent_service=agent_service,
                agent=agent,
                client=client,
            )
        except Exception as exc:
            logger.error("Email dispatch failed: %s", exc, exc_info=True)
            return {"ok": False, "error": "processing_failed"}

        if not response_text:
            return {"ok": False, "error": "empty_response"}

        await mailgun.send_reply(
            agent_email=recipient,
            agent_name=agent.name,
            to=sender,
            subject=subject,
            body_text=response_text,
            in_reply_to=message_id or None,
            references=message_id or None,
        )
        return {"ok": True, "mode": "email"}

    # --- Check if this is a verification code reply ---
    pending = await email_verification_service.get_pending_verification(sender, client_id)
    if pending:
        code = _extract_verification_code(body_plain)
        if code:
            verified_data = await email_verification_service.verify_code(
                sender, client_id, code
            )
            if verified_data:
                # Verification succeeded — process the held original message
                logger.info("Email verified for %s, processing held message", sender)

                held_agent_slug = verified_data.get("pending_agent_slug") or agent_slug
                held_agent = agent
                if held_agent_slug != agent_slug:
                    held_agent = await agent_service.get_agent(client_id, held_agent_slug) or agent

                try:
                    response_text = await _dispatch_to_agent(
                        agent_slug=held_agent_slug,
                        client_id=client_id,
                        sender=sender,
                        subject=verified_data.get("pending_subject") or subject,
                        body=verified_data.get("pending_message") or body_plain,
                        message_id=verified_data.get("pending_message_id") or message_id,
                        in_reply_to=None,
                        user_id=verified_data["user_id"],
                        spf_result=spf_result,
                        dkim_result=dkim_result,
                        agent_service=agent_service,
                        agent=held_agent,
                        client=client,
                    )
                except Exception as exc:
                    logger.error("Held message dispatch failed: %s", exc, exc_info=True)
                    return {"ok": False, "error": "processing_failed"}

                if response_text:
                    await mailgun.send_reply(
                        agent_email=recipient,
                        agent_name=held_agent.name,
                        to=sender,
                        subject=verified_data.get("pending_subject") or subject,
                        body_text=response_text,
                        in_reply_to=verified_data.get("pending_message_id") or None,
                        references=verified_data.get("pending_message_id") or None,
                    )

                return {"ok": True, "mode": "email", "verified": True}
            else:
                # Wrong or expired code
                await mailgun.send_reply(
                    agent_email=recipient,
                    agent_name=agent.name,
                    to=sender,
                    subject=subject,
                    body_text=(
                        "That verification code is incorrect or has expired.\n\n"
                        "Please check the code and try again, or resend your "
                        "original message to get a new code."
                    ),
                    in_reply_to=message_id or None,
                    references=message_id or None,
                )
                return {"ok": False, "error": "invalid_code"}

        # They replied but didn't include a code
        await mailgun.send_reply(
            agent_email=recipient,
            agent_name=agent.name,
            to=sender,
            subject=subject,
            body_text=(
                "I'm still waiting for your verification code.\n\n"
                "Please reply with the 6-character code from the previous email "
                "to verify your identity."
            ),
            in_reply_to=message_id or None,
            references=message_id or None,
        )
        return {"ok": False, "error": "awaiting_code"}

    # --- NEW SENDER: check if they're a registered user ---
    auth_user = await email_verification_service.find_user_by_email(sender)
    if not auth_user:
        logger.info("Unregistered sender %s emailed %s", sender, recipient)
        await mailgun.send_reply(
            agent_email=recipient,
            agent_name=agent.name,
            to=sender,
            subject=subject,
            body_text=(
                "Thanks for reaching out! Unfortunately, you need a Sidekick Forge "
                "account to email this sidekick.\n\n"
                "Please sign up at sidekickforge.com and try again."
            ),
            in_reply_to=message_id or None,
            references=message_id or None,
        )
        return {"ok": False, "error": "unregistered_user"}

    # --- Registered user, start verification ---
    user_id = auth_user.get("id")
    user_name = (
        (auth_user.get("user_metadata") or {}).get("full_name")
        or sender.split("@")[0]
    )

    code = await email_verification_service.start_verification(
        email_address=sender,
        client_id=client_id,
        user_id=user_id,
        agent_slug=agent_slug,
        message=body_plain,
        subject=subject,
        message_id=message_id,
    )

    await mailgun.send_reply(
        agent_email=recipient,
        agent_name=agent.name,
        to=sender,
        subject=subject,
        body_text=(
            f"Hi {user_name}! Before I can help you, I need to verify your email address.\n\n"
            f"Please reply to this email with the following verification code:\n\n"
            f"    {code}\n\n"
            f"This is a one-time step. Once verified, I'll process your message "
            f"right away and you won't need to verify again.\n\n"
            f"This code expires in 10 minutes."
        ),
        in_reply_to=message_id or None,
        references=message_id or None,
    )

    logger.info("Verification code sent to %s for client %s", sender, client_id)
    return {"ok": True, "mode": "verification_sent"}
