"""
Usage Tracking Service for Tier-Based Quotas

Tracks voice minutes, text messages, and embeddings for clients/agents.
Supports both client-level and per-agent (sidekick-specific) tracking.
Provides quota checking and enforcement.

IMPORTANT: Quotas are set at the CLIENT level, not per-agent. A client with
multiple sidekicks shares their quota pool across all agents. Usage is tracked
per-agent for visibility, but quota enforcement aggregates across all agents.
"""

import logging
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, date
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class QuotaType(Enum):
    VOICE = "voice"
    TEXT = "text"
    EMBEDDING = "embedding"


@dataclass
class QuotaStatus:
    used: int
    limit: int
    remaining: int
    percent_used: float
    is_exceeded: bool
    is_warning: bool  # True if > 80% used

    @property
    def display_used(self) -> str:
        """Human-readable used amount"""
        return str(self.used)

    @property
    def display_limit(self) -> str:
        """Human-readable limit"""
        return "Unlimited" if self.limit == 0 else str(self.limit)


@dataclass
class VoiceQuotaStatus(QuotaStatus):
    """Voice quota with time formatting"""

    @property
    def minutes_used(self) -> float:
        return round(self.used / 60, 1)

    @property
    def minutes_limit(self) -> float:
        return round(self.limit / 60, 1) if self.limit > 0 else 0

    @property
    def minutes_remaining(self) -> float:
        return round(self.remaining / 60, 1)

    @property
    def display_used(self) -> str:
        return f"{self.minutes_used} min"

    @property
    def display_limit(self) -> str:
        return "Unlimited" if self.limit == 0 else f"{self.minutes_limit} min"


@dataclass
class AgentUsageRecord:
    """Per-agent usage record with all quota types"""
    agent_id: str
    agent_name: str = ""
    agent_slug: str = ""
    voice: Optional[VoiceQuotaStatus] = None
    text: Optional[QuotaStatus] = None
    embedding: Optional[QuotaStatus] = None


@dataclass
class ClientAggregatedUsage:
    """Aggregated usage across all agents for a client (for quota enforcement)"""
    client_id: str
    period_start: date
    agent_count: int
    voice: VoiceQuotaStatus
    text: QuotaStatus
    embedding: QuotaStatus


class UsageTrackingService:
    """Service for tracking and enforcing usage quotas"""

    # Default quotas for Adventurer tier
    DEFAULT_VOICE_SECONDS = 6000  # 100 minutes
    DEFAULT_TEXT_MESSAGES = 1000
    DEFAULT_EMBEDDING_CHUNKS = 10000

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._initialized = False

    async def initialize(self, supabase_client=None):
        """Initialize with Supabase client"""
        if supabase_client:
            self.supabase = supabase_client
        if not self.supabase:
            from app.integrations.supabase_client import supabase_manager
            await supabase_manager.initialize()
            self.supabase = supabase_manager.admin_client
        self._initialized = True

    def _ensure_initialized(self):
        if not self._initialized or not self.supabase:
            raise RuntimeError("UsageTrackingService not initialized. Call initialize() first.")

    def _get_period_start(self) -> date:
        """Get the first day of the current billing period (month)"""
        today = date.today()
        return date(today.year, today.month, 1)

    async def get_or_create_usage_record(self, client_id: str) -> Dict[str, Any]:
        """Get or create the current period's usage record for a client"""
        self._ensure_initialized()
        period_start = self._get_period_start()

        # Try to get existing record
        result = self.supabase.table("client_usage").select("*").eq(
            "client_id", client_id
        ).eq("period_start", period_start.isoformat()).execute()

        if result.data:
            return result.data[0]

        # Get client's tier for appropriate limits
        client_result = self.supabase.table("clients").select("tier").eq("id", client_id).single().execute()
        tier = client_result.data.get("tier", "adventurer") if client_result.data else "adventurer"

        # Get tier quotas
        quota_result = self.supabase.table("tier_quotas").select("*").eq("tier", tier).single().execute()
        quotas = quota_result.data if quota_result.data else {}

        # Create new record
        new_record = {
            "client_id": client_id,
            "period_start": period_start.isoformat(),
            "voice_seconds_used": 0,
            "voice_seconds_limit": quotas.get("voice_seconds_per_month", self.DEFAULT_VOICE_SECONDS),
            "text_messages_used": 0,
            "text_messages_limit": quotas.get("text_messages_per_month", self.DEFAULT_TEXT_MESSAGES),
            "embedding_chunks_used": 0,
            "embedding_chunks_limit": quotas.get("embedding_chunks_per_month", self.DEFAULT_EMBEDDING_CHUNKS),
        }

        insert_result = self.supabase.table("client_usage").insert(new_record).execute()
        return insert_result.data[0] if insert_result.data else new_record

    async def get_quota_status(self, client_id: str, quota_type: QuotaType) -> QuotaStatus:
        """Get the current quota status for a client"""
        record = await self.get_or_create_usage_record(client_id)

        if quota_type == QuotaType.VOICE:
            used = record.get("voice_seconds_used", 0)
            limit = record.get("voice_seconds_limit", self.DEFAULT_VOICE_SECONDS)
            remaining = max(0, limit - used) if limit > 0 else float('inf')
            percent = (used / limit * 100) if limit > 0 else 0

            return VoiceQuotaStatus(
                used=used,
                limit=limit,
                remaining=remaining if limit > 0 else 0,
                percent_used=round(percent, 1),
                is_exceeded=limit > 0 and used >= limit,
                is_warning=limit > 0 and percent >= 80
            )

        elif quota_type == QuotaType.TEXT:
            used = record.get("text_messages_used", 0)
            limit = record.get("text_messages_limit", self.DEFAULT_TEXT_MESSAGES)
            remaining = max(0, limit - used) if limit > 0 else float('inf')
            percent = (used / limit * 100) if limit > 0 else 0

            return QuotaStatus(
                used=used,
                limit=limit,
                remaining=remaining if limit > 0 else 0,
                percent_used=round(percent, 1),
                is_exceeded=limit > 0 and used >= limit,
                is_warning=limit > 0 and percent >= 80
            )

        elif quota_type == QuotaType.EMBEDDING:
            used = record.get("embedding_chunks_used", 0)
            limit = record.get("embedding_chunks_limit", self.DEFAULT_EMBEDDING_CHUNKS)
            remaining = max(0, limit - used) if limit > 0 else float('inf')
            percent = (used / limit * 100) if limit > 0 else 0

            return QuotaStatus(
                used=used,
                limit=limit,
                remaining=remaining if limit > 0 else 0,
                percent_used=round(percent, 1),
                is_exceeded=limit > 0 and used >= limit,
                is_warning=limit > 0 and percent >= 80
            )

    async def get_all_quotas(self, client_id: str) -> Dict[str, QuotaStatus]:
        """Get all quota statuses for a client"""
        return {
            "voice": await self.get_quota_status(client_id, QuotaType.VOICE),
            "text": await self.get_quota_status(client_id, QuotaType.TEXT),
            "embedding": await self.get_quota_status(client_id, QuotaType.EMBEDDING),
        }

    async def check_quota(self, client_id: str, quota_type: QuotaType) -> Tuple[bool, QuotaStatus]:
        """Check if a client is within quota. Returns (is_allowed, status)"""
        status = await self.get_quota_status(client_id, quota_type)
        return (not status.is_exceeded, status)

    async def get_client_aggregated_usage(self, client_id: str) -> ClientAggregatedUsage:
        """
        Get aggregated usage across ALL agents for a client.

        This is the primary method for quota enforcement - quotas are set at the
        client level, so we must aggregate usage from all sidekicks to check limits.
        """
        self._ensure_initialized()
        period_start = self._get_period_start()

        # Use the database function for efficient aggregation
        try:
            result = self.supabase.rpc(
                'get_client_aggregated_usage',
                {'p_client_id': client_id, 'p_period_start': period_start.isoformat()}
            ).execute()

            if result.data and len(result.data) > 0:
                data = result.data[0]

                voice_used = data.get('total_voice_seconds', 0) or 0
                voice_limit = data.get('voice_limit', self.DEFAULT_VOICE_SECONDS) or self.DEFAULT_VOICE_SECONDS
                voice_remaining = max(0, voice_limit - voice_used) if voice_limit > 0 else 0
                voice_percent = data.get('voice_percent_used', 0) or 0

                text_used = data.get('total_text_messages', 0) or 0
                text_limit = data.get('text_limit', self.DEFAULT_TEXT_MESSAGES) or self.DEFAULT_TEXT_MESSAGES
                text_remaining = max(0, text_limit - text_used) if text_limit > 0 else 0
                text_percent = data.get('text_percent_used', 0) or 0

                embed_used = data.get('total_embedding_chunks', 0) or 0
                embed_limit = data.get('embedding_limit', self.DEFAULT_EMBEDDING_CHUNKS) or self.DEFAULT_EMBEDDING_CHUNKS
                embed_remaining = max(0, embed_limit - embed_used) if embed_limit > 0 else 0
                embed_percent = data.get('embedding_percent_used', 0) or 0

                return ClientAggregatedUsage(
                    client_id=client_id,
                    period_start=period_start,
                    agent_count=data.get('agent_count', 0) or 0,
                    voice=VoiceQuotaStatus(
                        used=voice_used,
                        limit=voice_limit,
                        remaining=voice_remaining,
                        percent_used=float(voice_percent),
                        is_exceeded=voice_limit > 0 and voice_used >= voice_limit,
                        is_warning=voice_limit > 0 and voice_percent >= 80,
                    ),
                    text=QuotaStatus(
                        used=text_used,
                        limit=text_limit,
                        remaining=text_remaining,
                        percent_used=float(text_percent),
                        is_exceeded=text_limit > 0 and text_used >= text_limit,
                        is_warning=text_limit > 0 and text_percent >= 80,
                    ),
                    embedding=QuotaStatus(
                        used=embed_used,
                        limit=embed_limit,
                        remaining=embed_remaining,
                        percent_used=float(embed_percent),
                        is_exceeded=embed_limit > 0 and embed_used >= embed_limit,
                        is_warning=embed_limit > 0 and embed_percent >= 80,
                    ),
                )
        except Exception as e:
            logger.warning("Failed to get aggregated usage via RPC, falling back to manual aggregation: %s", e)

        # Fallback: manual aggregation from agent_usage table
        return await self._get_client_aggregated_usage_fallback(client_id, period_start)

    async def _get_client_aggregated_usage_fallback(
        self, client_id: str, period_start: date
    ) -> ClientAggregatedUsage:
        """Fallback aggregation if RPC is not available"""
        # Get all agent usage for this client/period
        result = self.supabase.table("agent_usage").select("*").eq(
            "client_id", client_id
        ).eq("period_start", period_start.isoformat()).execute()

        # Get tier quotas
        client_result = self.supabase.table("clients").select("tier").eq("id", client_id).single().execute()
        tier = client_result.data.get("tier", "adventurer") if client_result.data else "adventurer"

        quota_result = self.supabase.table("tier_quotas").select("*").eq("tier", tier).single().execute()
        quotas = quota_result.data if quota_result.data else {}

        voice_limit = quotas.get("voice_seconds_per_month", self.DEFAULT_VOICE_SECONDS)
        text_limit = quotas.get("text_messages_per_month", self.DEFAULT_TEXT_MESSAGES)
        embed_limit = quotas.get("embedding_chunks_per_month", self.DEFAULT_EMBEDDING_CHUNKS)

        # Aggregate usage
        voice_used = sum(r.get("voice_seconds_used", 0) or 0 for r in (result.data or []))
        text_used = sum(r.get("text_messages_used", 0) or 0 for r in (result.data or []))
        embed_used = sum(r.get("embedding_chunks_used", 0) or 0 for r in (result.data or []))
        agent_count = len(set(r.get("agent_id") for r in (result.data or [])))

        voice_percent = (voice_used / voice_limit * 100) if voice_limit > 0 else 0
        text_percent = (text_used / text_limit * 100) if text_limit > 0 else 0
        embed_percent = (embed_used / embed_limit * 100) if embed_limit > 0 else 0

        return ClientAggregatedUsage(
            client_id=client_id,
            period_start=period_start,
            agent_count=agent_count,
            voice=VoiceQuotaStatus(
                used=voice_used,
                limit=voice_limit,
                remaining=max(0, voice_limit - voice_used) if voice_limit > 0 else 0,
                percent_used=round(voice_percent, 1),
                is_exceeded=voice_limit > 0 and voice_used >= voice_limit,
                is_warning=voice_limit > 0 and voice_percent >= 80,
            ),
            text=QuotaStatus(
                used=text_used,
                limit=text_limit,
                remaining=max(0, text_limit - text_used) if text_limit > 0 else 0,
                percent_used=round(text_percent, 1),
                is_exceeded=text_limit > 0 and text_used >= text_limit,
                is_warning=text_limit > 0 and text_percent >= 80,
            ),
            embedding=QuotaStatus(
                used=embed_used,
                limit=embed_limit,
                remaining=max(0, embed_limit - embed_used) if embed_limit > 0 else 0,
                percent_used=round(embed_percent, 1),
                is_exceeded=embed_limit > 0 and embed_used >= embed_limit,
                is_warning=embed_limit > 0 and embed_percent >= 80,
            ),
        )

    async def check_client_quota(self, client_id: str, quota_type: QuotaType) -> Tuple[bool, QuotaStatus]:
        """
        Check if a client is within their aggregated quota.

        This checks against the CLIENT's total usage across all sidekicks,
        not just a single agent's usage.
        """
        aggregated = await self.get_client_aggregated_usage(client_id)

        if quota_type == QuotaType.VOICE:
            return (not aggregated.voice.is_exceeded, aggregated.voice)
        elif quota_type == QuotaType.TEXT:
            return (not aggregated.text.is_exceeded, aggregated.text)
        elif quota_type == QuotaType.EMBEDDING:
            return (not aggregated.embedding.is_exceeded, aggregated.embedding)

        raise ValueError(f"Unknown quota type: {quota_type}")

    async def increment_voice_usage(self, client_id: str, seconds: int) -> Tuple[bool, VoiceQuotaStatus]:
        """
        Increment voice usage by seconds.
        Returns (is_within_quota, updated_status)
        """
        self._ensure_initialized()
        record = await self.get_or_create_usage_record(client_id)

        new_used = record.get("voice_seconds_used", 0) + seconds
        limit = record.get("voice_seconds_limit", self.DEFAULT_VOICE_SECONDS)

        # Update the record
        self.supabase.table("client_usage").update({
            "voice_seconds_used": new_used,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", record["id"]).execute()

        remaining = max(0, limit - new_used) if limit > 0 else 0
        percent = (new_used / limit * 100) if limit > 0 else 0

        status = VoiceQuotaStatus(
            used=new_used,
            limit=limit,
            remaining=remaining,
            percent_used=round(percent, 1),
            is_exceeded=limit > 0 and new_used >= limit,
            is_warning=limit > 0 and percent >= 80
        )

        is_within = limit == 0 or new_used <= limit
        return (is_within, status)

    async def increment_text_usage(self, client_id: str, count: int = 1) -> Tuple[bool, QuotaStatus]:
        """
        Increment text message usage.
        Returns (is_within_quota, updated_status)
        """
        self._ensure_initialized()
        record = await self.get_or_create_usage_record(client_id)

        new_used = record.get("text_messages_used", 0) + count
        limit = record.get("text_messages_limit", self.DEFAULT_TEXT_MESSAGES)

        # Update the record
        self.supabase.table("client_usage").update({
            "text_messages_used": new_used,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", record["id"]).execute()

        remaining = max(0, limit - new_used) if limit > 0 else 0
        percent = (new_used / limit * 100) if limit > 0 else 0

        status = QuotaStatus(
            used=new_used,
            limit=limit,
            remaining=remaining,
            percent_used=round(percent, 1),
            is_exceeded=limit > 0 and new_used >= limit,
            is_warning=limit > 0 and percent >= 80
        )

        is_within = limit == 0 or new_used <= limit
        return (is_within, status)

    async def increment_embedding_usage(self, client_id: str, chunks: int) -> Tuple[bool, QuotaStatus]:
        """
        Increment embedding chunk usage.
        Returns (is_within_quota, updated_status)
        """
        self._ensure_initialized()
        record = await self.get_or_create_usage_record(client_id)

        new_used = record.get("embedding_chunks_used", 0) + chunks
        limit = record.get("embedding_chunks_limit", self.DEFAULT_EMBEDDING_CHUNKS)

        # Update the record
        self.supabase.table("client_usage").update({
            "embedding_chunks_used": new_used,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", record["id"]).execute()

        remaining = max(0, limit - new_used) if limit > 0 else 0
        percent = (new_used / limit * 100) if limit > 0 else 0

        status = QuotaStatus(
            used=new_used,
            limit=limit,
            remaining=remaining,
            percent_used=round(percent, 1),
            is_exceeded=limit > 0 and new_used >= limit,
            is_warning=limit > 0 and percent >= 80
        )

        is_within = limit == 0 or new_used <= limit
        return (is_within, status)

    # =========================================================================
    # Per-Agent (Sidekick-Specific) Usage Tracking
    # =========================================================================

    async def get_or_create_agent_usage_record(
        self,
        client_id: str,
        agent_id: str,
    ) -> Dict[str, Any]:
        """Get or create the current period's usage record for a specific agent"""
        self._ensure_initialized()
        period_start = self._get_period_start()

        # Try to get existing record
        result = self.supabase.table("agent_usage").select("*").eq(
            "client_id", client_id
        ).eq("agent_id", agent_id).eq(
            "period_start", period_start.isoformat()
        ).execute()

        if result.data:
            return result.data[0]

        # Get client's tier for appropriate limits
        client_result = self.supabase.table("clients").select("tier").eq("id", client_id).single().execute()
        tier = client_result.data.get("tier", "adventurer") if client_result.data else "adventurer"

        # Get tier quotas
        quota_result = self.supabase.table("tier_quotas").select("*").eq("tier", tier).single().execute()
        quotas = quota_result.data if quota_result.data else {}

        # Create new record
        new_record = {
            "client_id": client_id,
            "agent_id": agent_id,
            "period_start": period_start.isoformat(),
            "voice_seconds_used": 0,
            "voice_seconds_limit": quotas.get("voice_seconds_per_month", self.DEFAULT_VOICE_SECONDS),
            "text_messages_used": 0,
            "text_messages_limit": quotas.get("text_messages_per_month", self.DEFAULT_TEXT_MESSAGES),
            "embedding_chunks_used": 0,
            "embedding_chunks_limit": quotas.get("embedding_chunks_per_month", self.DEFAULT_EMBEDDING_CHUNKS),
        }

        insert_result = self.supabase.table("agent_usage").insert(new_record).execute()
        return insert_result.data[0] if insert_result.data else new_record

    async def get_agent_quota_status(
        self,
        client_id: str,
        agent_id: str,
        quota_type: QuotaType,
    ) -> QuotaStatus:
        """Get the current quota status for a specific agent"""
        record = await self.get_or_create_agent_usage_record(client_id, agent_id)

        if quota_type == QuotaType.VOICE:
            used = record.get("voice_seconds_used", 0)
            limit = record.get("voice_seconds_limit", self.DEFAULT_VOICE_SECONDS)
            remaining = max(0, limit - used) if limit > 0 else float('inf')
            percent = (used / limit * 100) if limit > 0 else 0

            return VoiceQuotaStatus(
                used=used,
                limit=limit,
                remaining=remaining if limit > 0 else 0,
                percent_used=round(percent, 1),
                is_exceeded=limit > 0 and used >= limit,
                is_warning=limit > 0 and percent >= 80
            )

        elif quota_type == QuotaType.TEXT:
            used = record.get("text_messages_used", 0)
            limit = record.get("text_messages_limit", self.DEFAULT_TEXT_MESSAGES)
            remaining = max(0, limit - used) if limit > 0 else float('inf')
            percent = (used / limit * 100) if limit > 0 else 0

            return QuotaStatus(
                used=used,
                limit=limit,
                remaining=remaining if limit > 0 else 0,
                percent_used=round(percent, 1),
                is_exceeded=limit > 0 and used >= limit,
                is_warning=limit > 0 and percent >= 80
            )

        elif quota_type == QuotaType.EMBEDDING:
            used = record.get("embedding_chunks_used", 0)
            limit = record.get("embedding_chunks_limit", self.DEFAULT_EMBEDDING_CHUNKS)
            remaining = max(0, limit - used) if limit > 0 else float('inf')
            percent = (used / limit * 100) if limit > 0 else 0

            return QuotaStatus(
                used=used,
                limit=limit,
                remaining=remaining if limit > 0 else 0,
                percent_used=round(percent, 1),
                is_exceeded=limit > 0 and used >= limit,
                is_warning=limit > 0 and percent >= 80
            )

    async def get_all_agent_quotas(
        self,
        client_id: str,
        agent_id: str,
    ) -> Dict[str, QuotaStatus]:
        """Get all quota statuses for a specific agent"""
        return {
            "voice": await self.get_agent_quota_status(client_id, agent_id, QuotaType.VOICE),
            "text": await self.get_agent_quota_status(client_id, agent_id, QuotaType.TEXT),
            "embedding": await self.get_agent_quota_status(client_id, agent_id, QuotaType.EMBEDDING),
        }

    async def check_agent_quota(
        self,
        client_id: str,
        agent_id: str,
        quota_type: QuotaType,
    ) -> Tuple[bool, QuotaStatus]:
        """Check if an agent is within quota. Returns (is_allowed, status)"""
        status = await self.get_agent_quota_status(client_id, agent_id, quota_type)
        return (not status.is_exceeded, status)

    async def increment_agent_voice_usage(
        self,
        client_id: str,
        agent_id: str,
        seconds: int,
    ) -> Tuple[bool, VoiceQuotaStatus]:
        """
        Increment voice usage for a specific agent using ATOMIC database operation.
        Returns (is_within_quota, updated_status) where status is the CLIENT-LEVEL
        aggregated quota status (not per-agent).

        Usage is tracked per-agent, but quota limits are enforced at the client level.
        """
        self._ensure_initialized()

        # Use atomic RPC function to prevent race conditions
        try:
            result = self.supabase.rpc(
                'increment_agent_voice_seconds',
                {
                    'p_client_id': client_id,
                    'p_agent_id': agent_id,
                    'p_seconds': seconds
                }
            ).execute()

            if result.data:
                logger.debug(
                    "Atomic voice increment: agent=%s, added=%ds, result=%s",
                    agent_id, seconds, result.data
                )
        except Exception as e:
            # Fallback to non-atomic if RPC doesn't exist yet
            logger.warning("Atomic increment RPC failed, using fallback: %s", e)
            record = await self.get_or_create_agent_usage_record(client_id, agent_id)
            new_agent_used = record.get("voice_seconds_used", 0) + seconds
            self.supabase.table("agent_usage").update({
                "voice_seconds_used": new_agent_used,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", record["id"]).execute()

        # Get CLIENT-LEVEL aggregated usage for quota enforcement
        aggregated = await self.get_client_aggregated_usage(client_id)
        client_status = aggregated.voice

        # Log quota warnings using client-level aggregation
        if client_status.is_exceeded:
            logger.warning(
                "Client %s has EXCEEDED voice quota (via agent %s): %d/%d seconds (%.1f%%)",
                client_id, agent_id, client_status.used, client_status.limit, client_status.percent_used
            )
        elif client_status.is_warning:
            logger.info(
                "Client %s voice quota warning (via agent %s): %.1f%% used (%d/%d seconds)",
                client_id, agent_id, client_status.percent_used, client_status.used, client_status.limit
            )
        else:
            logger.info(
                "Tracked voice usage: agent=%s, client=%s, duration=%ds, client_total=%d/%d seconds (%.1f%%)",
                agent_id, client_id, seconds,
                client_status.used, client_status.limit, client_status.percent_used
            )

        is_within = not client_status.is_exceeded
        return (is_within, client_status)

    async def increment_agent_text_usage(
        self,
        client_id: str,
        agent_id: str,
        count: int = 1,
    ) -> Tuple[bool, QuotaStatus]:
        """
        Increment text message usage for a specific agent using ATOMIC database operation.
        Returns (is_within_quota, updated_status) where status is the CLIENT-LEVEL
        aggregated quota status (not per-agent).

        Usage is tracked per-agent, but quota limits are enforced at the client level.
        """
        self._ensure_initialized()

        # Use atomic RPC function to prevent race conditions
        try:
            result = self.supabase.rpc(
                'increment_agent_text_messages',
                {
                    'p_client_id': client_id,
                    'p_agent_id': agent_id,
                    'p_count': count
                }
            ).execute()

            if result.data:
                logger.debug(
                    "Atomic text increment: agent=%s, added=%d, result=%s",
                    agent_id, count, result.data
                )
        except Exception as e:
            # Fallback to non-atomic if RPC doesn't exist yet
            logger.warning("Atomic text increment RPC failed, using fallback: %s", e)
            record = await self.get_or_create_agent_usage_record(client_id, agent_id)
            new_agent_used = record.get("text_messages_used", 0) + count
            self.supabase.table("agent_usage").update({
                "text_messages_used": new_agent_used,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", record["id"]).execute()

        # Get CLIENT-LEVEL aggregated usage for quota enforcement
        aggregated = await self.get_client_aggregated_usage(client_id)
        client_status = aggregated.text

        # Log quota warnings using client-level aggregation
        if client_status.is_exceeded:
            logger.warning(
                "Client %s has EXCEEDED text quota (via agent %s): %d/%d messages (%.1f%%)",
                client_id, agent_id, client_status.used, client_status.limit, client_status.percent_used
            )
        elif client_status.is_warning:
            logger.info(
                "Client %s text quota warning (via agent %s): %.1f%% used (%d/%d messages)",
                client_id, agent_id, client_status.percent_used, client_status.used, client_status.limit
            )
        else:
            logger.info(
                "Tracked text usage: agent=%s, client=%s, count=%d, client_total=%d/%d messages (%.1f%%)",
                agent_id, client_id, count,
                client_status.used, client_status.limit, client_status.percent_used
            )

        is_within = not client_status.is_exceeded
        return (is_within, client_status)

    async def increment_agent_embedding_usage(
        self,
        client_id: str,
        agent_id: str,
        chunks: int,
    ) -> Tuple[bool, QuotaStatus]:
        """
        Increment embedding chunk usage for a specific agent using ATOMIC database operation.
        Returns (is_within_quota, updated_status) where status is the CLIENT-LEVEL
        aggregated quota status (not per-agent).

        Usage is tracked per-agent, but quota limits are enforced at the client level.
        """
        self._ensure_initialized()

        # Use atomic RPC function to prevent race conditions
        try:
            result = self.supabase.rpc(
                'increment_agent_embedding_chunks',
                {
                    'p_client_id': client_id,
                    'p_agent_id': agent_id,
                    'p_chunks': chunks
                }
            ).execute()

            if result.data:
                logger.debug(
                    "Atomic embedding increment: agent=%s, added=%d, result=%s",
                    agent_id, chunks, result.data
                )
        except Exception as e:
            # Fallback to non-atomic if RPC doesn't exist yet
            logger.warning("Atomic embedding increment RPC failed, using fallback: %s", e)
            record = await self.get_or_create_agent_usage_record(client_id, agent_id)
            new_agent_used = record.get("embedding_chunks_used", 0) + chunks
            self.supabase.table("agent_usage").update({
                "embedding_chunks_used": new_agent_used,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", record["id"]).execute()

        # Get CLIENT-LEVEL aggregated usage for quota enforcement
        aggregated = await self.get_client_aggregated_usage(client_id)
        client_status = aggregated.embedding

        # Log quota warnings using client-level aggregation
        if client_status.is_exceeded:
            logger.warning(
                "Client %s has EXCEEDED embedding quota (via agent %s): %d/%d chunks (%.1f%%)",
                client_id, agent_id, client_status.used, client_status.limit, client_status.percent_used
            )
        elif client_status.is_warning:
            logger.info(
                "Client %s embedding quota warning (via agent %s): %.1f%% used (%d/%d chunks)",
                client_id, agent_id, client_status.percent_used, client_status.used, client_status.limit
            )
        else:
            logger.info(
                "Tracked embedding usage: agent=%s, client=%s, chunks=%d, client_total=%d/%d chunks (%.1f%%)",
                agent_id, client_id, chunks,
                client_status.used, client_status.limit, client_status.percent_used
            )

        is_within = not client_status.is_exceeded
        return (is_within, client_status)

    async def get_all_agents_usage(
        self,
        client_id: str,
        client_supabase=None,
    ) -> List[AgentUsageRecord]:
        """
        Get usage for all agents belonging to a client.
        Returns a list of AgentUsageRecord objects with usage stats per agent.
        """
        self._ensure_initialized()
        period_start = self._get_period_start()

        # Get all usage records for this client/period
        usage_result = self.supabase.table("agent_usage").select("*").eq(
            "client_id", client_id
        ).eq("period_start", period_start.isoformat()).execute()

        usage_by_agent = {r["agent_id"]: r for r in (usage_result.data or [])}

        # Try to get agent names from client's Supabase
        agents_info = {}
        if client_supabase:
            try:
                agents_result = client_supabase.table("agents").select("id, name, slug").execute()
                if agents_result.data:
                    for a in agents_result.data:
                        agents_info[str(a["id"])] = {"name": a.get("name", ""), "slug": a.get("slug", "")}
            except Exception as e:
                logger.warning("Failed to fetch agent info from client Supabase: %s", e)

        records = []
        for agent_id, usage in usage_by_agent.items():
            agent_info = agents_info.get(agent_id, {})

            voice_used = usage.get("voice_seconds_used", 0)
            voice_limit = usage.get("voice_seconds_limit", self.DEFAULT_VOICE_SECONDS)
            voice_remaining = max(0, voice_limit - voice_used) if voice_limit > 0 else 0
            voice_percent = (voice_used / voice_limit * 100) if voice_limit > 0 else 0

            text_used = usage.get("text_messages_used", 0)
            text_limit = usage.get("text_messages_limit", self.DEFAULT_TEXT_MESSAGES)
            text_remaining = max(0, text_limit - text_used) if text_limit > 0 else 0
            text_percent = (text_used / text_limit * 100) if text_limit > 0 else 0

            embed_used = usage.get("embedding_chunks_used", 0)
            embed_limit = usage.get("embedding_chunks_limit", self.DEFAULT_EMBEDDING_CHUNKS)
            embed_remaining = max(0, embed_limit - embed_used) if embed_limit > 0 else 0
            embed_percent = (embed_used / embed_limit * 100) if embed_limit > 0 else 0

            records.append(AgentUsageRecord(
                agent_id=agent_id,
                agent_name=agent_info.get("name", ""),
                agent_slug=agent_info.get("slug", ""),
                voice=VoiceQuotaStatus(
                    used=voice_used,
                    limit=voice_limit,
                    remaining=voice_remaining,
                    percent_used=round(voice_percent, 1),
                    is_exceeded=voice_limit > 0 and voice_used >= voice_limit,
                    is_warning=voice_limit > 0 and voice_percent >= 80,
                ),
                text=QuotaStatus(
                    used=text_used,
                    limit=text_limit,
                    remaining=text_remaining,
                    percent_used=round(text_percent, 1),
                    is_exceeded=text_limit > 0 and text_used >= text_limit,
                    is_warning=text_limit > 0 and text_percent >= 80,
                ),
                embedding=QuotaStatus(
                    used=embed_used,
                    limit=embed_limit,
                    remaining=embed_remaining,
                    percent_used=round(embed_percent, 1),
                    is_exceeded=embed_limit > 0 and embed_used >= embed_limit,
                    is_warning=embed_limit > 0 and embed_percent >= 80,
                ),
            ))

        return records


class PlatformKeyService:
    """Service for managing platform-level API keys"""

    # Default provider configuration for managed tiers
    DEFAULT_CONFIG = {
        "llm": {
            "provider": "cerebras",
            "model": "zai-glm-4.7",  # Cerebras GLM 4.7 (reasoning toggle enabled)
        },
        "stt": {
            "provider": "cartesia",
        },
        "tts": {
            "provider": "cartesia",
            "voice": "default",  # Will be set per agent
        },
        "embedding": {
            "provider": "siliconflow",
            "model": "Qwen/Qwen3-Embedding-4B",
        },
        "rerank": {
            "enabled": True,
            "provider": "siliconflow",
            "model": "Qwen/Qwen3-Reranker-2B",
        }
    }

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._initialized = False
        self._key_cache: Dict[str, str] = {}

    async def initialize(self, supabase_client=None):
        """Initialize with Supabase client"""
        if supabase_client:
            self.supabase = supabase_client
        if not self.supabase:
            from app.integrations.supabase_client import supabase_manager
            await supabase_manager.initialize()
            self.supabase = supabase_manager.admin_client
        self._initialized = True

    def _ensure_initialized(self):
        if not self._initialized or not self.supabase:
            raise RuntimeError("PlatformKeyService not initialized. Call initialize() first.")

    async def get_platform_key(self, key_name: str) -> Optional[str]:
        """Get a platform API key by name"""
        self._ensure_initialized()

        # Check cache first
        if key_name in self._key_cache:
            return self._key_cache[key_name]

        result = self.supabase.table("platform_api_keys").select("key_value").eq(
            "key_name", key_name
        ).eq("is_active", True).single().execute()

        if result.data:
            key_value = result.data.get("key_value")
            self._key_cache[key_name] = key_value

            # Update last_used_at
            self.supabase.table("platform_api_keys").update({
                "last_used_at": datetime.utcnow().isoformat(),
                "total_requests": self.supabase.table("platform_api_keys").select("total_requests").eq("key_name", key_name).single().execute().data.get("total_requests", 0) + 1
            }).eq("key_name", key_name).execute()

            return key_value
        return None

    async def should_use_platform_keys(self, client_id: str) -> bool:
        """Check if a client should use platform API keys"""
        self._ensure_initialized()

        # Get client's tier and uses_platform_keys override
        result = self.supabase.table("clients").select(
            "tier, uses_platform_keys"
        ).eq("id", client_id).single().execute()

        if not result.data:
            return False

        # If client has explicit override, use that
        if result.data.get("uses_platform_keys") is not None:
            return result.data["uses_platform_keys"]

        # Otherwise check tier defaults
        tier = result.data.get("tier", "adventurer")
        tier_result = self.supabase.table("tier_quotas").select(
            "uses_platform_keys"
        ).eq("tier", tier).single().execute()

        if tier_result.data:
            return tier_result.data.get("uses_platform_keys", True)

        # Default to True for adventurer
        return tier == "adventurer"

    async def get_api_keys_for_client(self, client_id: str) -> Dict[str, str]:
        """
        Get API keys for a client.
        Returns platform keys if client uses_platform_keys, otherwise returns client's own keys.
        """
        self._ensure_initialized()

        use_platform = await self.should_use_platform_keys(client_id)

        if use_platform:
            # Return platform keys
            keys = {}
            key_mappings = [
                ("cerebras_api_key", "cerebras_api_key"),
                ("cartesia_api_key", "cartesia_api_key"),
                ("siliconflow_api_key", "siliconflow_api_key"),
                ("deepgram_api_key", "deepgram_api_key"),  # Fallback STT
            ]

            for key_name, output_name in key_mappings:
                key_value = await self.get_platform_key(key_name)
                if key_value:
                    keys[output_name] = key_value

            return keys
        else:
            # Return client's own keys from their settings
            client_result = self.supabase.table("clients").select(
                "settings"
            ).eq("id", client_id).single().execute()

            if client_result.data:
                settings = client_result.data.get("settings", {})
                return settings.get("api_keys", {})

            return {}

    def get_default_config(self) -> Dict[str, Any]:
        """Get the default provider configuration for managed tiers"""
        return self.DEFAULT_CONFIG.copy()


# Singleton instances
usage_tracking_service = UsageTrackingService()
platform_key_service = PlatformKeyService()
