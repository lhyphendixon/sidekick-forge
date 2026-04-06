"""Email address reservation and management for sidekick email channel.

Uses the platform Supabase `email_address_registry` table for globally unique
email address assignments. Deleted addresses are held for 48 hours before release.

Table schema (platform Supabase):
    CREATE TABLE email_address_registry (
        email_address TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        agent_slug TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'held'
        released_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now()
    );
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import settings
from app.integrations.supabase_client import supabase_manager

logger = logging.getLogger(__name__)

TABLE = "email_address_registry"
HOLD_HOURS = 48
EMAIL_DOMAIN = "mail.sidekickforge.com"
LOCAL_PART_RE = re.compile(r"^[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?$")


def _normalize(address: str) -> str:
    """Normalize email address to lowercase and ensure correct domain."""
    address = address.strip().lower()
    if "@" not in address:
        address = f"{address}@{EMAIL_DOMAIN}"
    return address


def _validate_local_part(local: str) -> bool:
    """Validate the local part of the email address."""
    if not local or len(local) < 2 or len(local) > 64:
        return False
    return bool(LOCAL_PART_RE.match(local))


class EmailAddressService:
    """Manages globally unique sidekick email addresses."""

    async def _get_platform_client(self):
        """Get the platform Supabase client."""
        if not getattr(supabase_manager, "_initialized", False):
            await supabase_manager.initialize()
        return supabase_manager.admin_client

    async def check_availability(self, address: str) -> bool:
        """Check if an email address is available for registration.

        Returns True if:
        - No registry entry exists, OR
        - The entry has status='held' and released_at + 48h has passed.
        """
        address = _normalize(address)
        local = address.split("@")[0]
        if not _validate_local_part(local):
            return False

        client = await self._get_platform_client()
        try:
            result = (
                client.table(TABLE)
                .select("*")
                .eq("email_address", address)
                .execute()
            )
        except Exception as exc:
            logger.error("Failed to check email availability: %s", exc)
            return False

        if not result.data:
            return True

        row = result.data[0]
        if row["status"] == "active":
            return False

        # Check if hold period has expired
        if row["status"] == "held" and row.get("released_at"):
            released_at = datetime.fromisoformat(
                row["released_at"].replace("Z", "+00:00")
            )
            if datetime.now(timezone.utc) >= released_at + timedelta(hours=HOLD_HOURS):
                return True

        return False

    async def reserve(
        self, address: str, client_id: str, agent_slug: str
    ) -> bool:
        """Reserve an email address for a sidekick.

        Returns True on success, False if the address is unavailable.
        """
        address = _normalize(address)
        local = address.split("@")[0]
        if not _validate_local_part(local):
            logger.warning("Invalid email local part: %s", local)
            return False

        if not await self.check_availability(address):
            logger.info("Email address %s is not available", address)
            return False

        client = await self._get_platform_client()
        try:
            # Upsert: overwrite held entries whose hold has expired
            client.table(TABLE).upsert(
                {
                    "email_address": address,
                    "client_id": client_id,
                    "agent_slug": agent_slug,
                    "status": "active",
                    "released_at": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="email_address",
            ).execute()
            logger.info("Reserved email address %s for %s/%s", address, client_id, agent_slug)
            return True
        except Exception as exc:
            logger.error("Failed to reserve email address %s: %s", address, exc)
            return False

    async def release(self, address: str) -> bool:
        """Release an email address with a 48-hour hold period."""
        address = _normalize(address)
        client = await self._get_platform_client()
        try:
            client.table(TABLE).update(
                {
                    "status": "held",
                    "released_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("email_address", address).execute()
            logger.info("Released email address %s (48h hold)", address)
            return True
        except Exception as exc:
            logger.error("Failed to release email address %s: %s", address, exc)
            return False

    async def lookup(self, address: str) -> Optional[dict]:
        """Look up which client/agent owns an email address.

        Returns dict with client_id and agent_slug, or None if not found/held.
        """
        address = _normalize(address)
        client = await self._get_platform_client()
        try:
            result = (
                client.table(TABLE)
                .select("client_id, agent_slug")
                .eq("email_address", address)
                .eq("status", "active")
                .execute()
            )
            if result.data:
                return result.data[0]
            return None
        except Exception as exc:
            logger.error("Failed to look up email address %s: %s", address, exc)
            return None

    async def suggest_address(self, agent_slug: str) -> str:
        """Suggest an available email address based on the agent slug."""
        base = f"{agent_slug}@{EMAIL_DOMAIN}"
        if await self.check_availability(base):
            return base
        # Try numbered suffixes
        for i in range(2, 100):
            candidate = f"{agent_slug}-{i}@{EMAIL_DOMAIN}"
            if await self.check_availability(candidate):
                return candidate
        return f"{agent_slug}-agent@{EMAIL_DOMAIN}"


email_address_service = EmailAddressService()
