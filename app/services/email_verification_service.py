"""Email verification service for the sidekick email channel.

Handles the one-time verification flow that links a sender's personal email
to their Sidekick Forge user account. Verification is per-client — once
verified for one sidekick, the sender is verified for all sidekicks owned
by the same client.

Table: verified_email_links (platform Supabase)
"""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.integrations.supabase_client import supabase_manager

logger = logging.getLogger(__name__)

TABLE = "verified_email_links"
CODE_LENGTH = 6
CODE_EXPIRY_MINUTES = 10


def _generate_code() -> str:
    """Generate a 6-character alphanumeric verification code."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(CODE_LENGTH))


class EmailVerificationService:
    """Manages email-to-user verification for the sidekick email channel."""

    async def _get_client(self):
        if not getattr(supabase_manager, "_initialized", False):
            await supabase_manager.initialize()
        return supabase_manager.admin_client

    async def get_verified_link(
        self, email_address: str, client_id: str
    ) -> Optional[Dict[str, Any]]:
        """Look up a verified email link for a sender + client.

        Returns the link row if verified_at is set, None otherwise.
        """
        email_address = email_address.strip().lower()
        client = await self._get_client()
        try:
            result = (
                client.table(TABLE)
                .select("*")
                .eq("email_address", email_address)
                .eq("client_id", client_id)
                .not_.is_("verified_at", "null")
                .execute()
            )
            if result.data:
                return result.data[0]
        except Exception as exc:
            logger.error("Failed to look up verified email link: %s", exc)
        return None

    async def find_user_by_email(self, email_address: str) -> Optional[Dict[str, Any]]:
        """Find a registered Sidekick Forge user by email.

        Returns the auth user dict if found, None otherwise.
        """
        return await supabase_manager.find_auth_user_by_email(email_address)

    async def start_verification(
        self,
        email_address: str,
        client_id: str,
        user_id: str,
        agent_slug: str,
        message: str,
        subject: str,
        message_id: str,
    ) -> str:
        """Create a pending verification record and return the code.

        Stores the original message so it can be processed after verification.
        """
        email_address = email_address.strip().lower()
        code = _generate_code()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=CODE_EXPIRY_MINUTES)

        client = await self._get_client()
        try:
            client.table(TABLE).upsert(
                {
                    "email_address": email_address,
                    "client_id": client_id,
                    "user_id": user_id,
                    "verification_code": code,
                    "code_expires_at": expires_at.isoformat(),
                    "pending_message": message,
                    "pending_subject": subject,
                    "pending_message_id": message_id,
                    "pending_agent_slug": agent_slug,
                    "verified_at": None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="email_address,client_id",
            ).execute()
            logger.info(
                "Started email verification for %s (client %s)", email_address, client_id
            )
        except Exception as exc:
            logger.error("Failed to start email verification: %s", exc)
            raise

        return code

    async def verify_code(
        self, email_address: str, client_id: str, code: str
    ) -> Optional[Dict[str, Any]]:
        """Attempt to verify a code. Returns the pending message data on success, None on failure."""
        email_address = email_address.strip().lower()
        code = code.strip().upper()

        client = await self._get_client()
        try:
            result = (
                client.table(TABLE)
                .select("*")
                .eq("email_address", email_address)
                .eq("client_id", client_id)
                .eq("verification_code", code)
                .is_("verified_at", "null")
                .execute()
            )
            if not result.data:
                return None

            row = result.data[0]

            # Check expiry
            expires_at = row.get("code_expires_at")
            if expires_at:
                exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp:
                    logger.info("Verification code expired for %s", email_address)
                    return None

            # Mark as verified, clear code and pending data
            now = datetime.now(timezone.utc).isoformat()
            client.table(TABLE).update(
                {
                    "verified_at": now,
                    "verification_code": None,
                    "code_expires_at": None,
                    "pending_message": None,
                    "pending_subject": None,
                    "pending_message_id": None,
                    "pending_agent_slug": None,
                    "updated_at": now,
                }
            ).eq("email_address", email_address).eq("client_id", client_id).execute()

            logger.info("Email verified: %s for client %s", email_address, client_id)

            return {
                "user_id": row["user_id"],
                "pending_message": row.get("pending_message"),
                "pending_subject": row.get("pending_subject"),
                "pending_message_id": row.get("pending_message_id"),
                "pending_agent_slug": row.get("pending_agent_slug"),
            }

        except Exception as exc:
            logger.error("Failed to verify email code: %s", exc)
            return None

    async def get_pending_verification(
        self, email_address: str, client_id: str
    ) -> Optional[Dict[str, Any]]:
        """Check if there's a pending (unverified) verification for this sender + client."""
        email_address = email_address.strip().lower()
        client = await self._get_client()
        try:
            result = (
                client.table(TABLE)
                .select("*")
                .eq("email_address", email_address)
                .eq("client_id", client_id)
                .is_("verified_at", "null")
                .not_.is_("verification_code", "null")
                .execute()
            )
            if result.data:
                return result.data[0]
        except Exception as exc:
            logger.error("Failed to check pending verification: %s", exc)
        return None


email_verification_service = EmailVerificationService()
