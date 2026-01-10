"""Background worker for provisioning new client Supabase projects.

Supports tiered provisioning:
- Adventurer (shared): No project creation, just mark as ready
- Champion (dedicated): Create Supabase project + apply schema
- Paragon (dedicated): Same as Champion with additional setup
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from app.services.client_connection_manager import get_connection_manager
from app.services.schema_sync import apply_schema, project_ref_from_url
from app.services.onboarding.supabase_management import (
    SupabaseManagementError,
    create_project,
)
from app.services.tier_features import ClientTier, HostingType, get_tier_features

logger = logging.getLogger(__name__)


@dataclass
class ProvisioningJob:
    id: str
    client_id: str
    job_type: str
    attempts: int
    claimed_at: Optional[str]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProvisioningJob":
        return cls(
            id=data["id"],
            client_id=data["client_id"],
            job_type=data["job_type"],
            attempts=data.get("attempts", 0),
            claimed_at=data.get("claimed_at"),
        )


class ProvisioningWorker:
    """Polls provisioning jobs and orchestrates onboarding steps."""

    def __init__(
        self,
        management_token: Optional[str] = None,
        poll_interval: float = 5.0,
    ) -> None:
        self.connection_manager = get_connection_manager()
        self.platform_db = self.connection_manager.platform_client
        self.management_token = management_token or os.getenv("SUPABASE_ACCESS_TOKEN")
        if not self.management_token:
            raise RuntimeError("SUPABASE_ACCESS_TOKEN must be configured for provisioning worker")
        self.poll_interval = poll_interval
        self._shutdown = asyncio.Event()

    async def run(self) -> None:
        """Start the provisioning loop until shutdown is requested."""
        logger.info("ProvisioningWorker started")
        try:
            while not self._shutdown.is_set():
                job = await self._claim_next_job()
                if not job:
                    await asyncio.sleep(self.poll_interval)
                    continue

                try:
                    await self._process_job(job)
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.exception("Provisioning job %s failed: %s", job.id, exc)
                    await self._record_failure(job, str(exc))
                    await asyncio.sleep(self.poll_interval)
        finally:
            logger.info("ProvisioningWorker shutting down")

    def stop(self) -> None:
        self._shutdown.set()

    async def _claim_next_job(self) -> Optional[ProvisioningJob]:
        def _claim() -> Optional[ProvisioningJob]:
            result = (
                self.platform_db
                .table("client_provisioning_jobs")
                .select("*")
                .is_("claimed_at", None)
                .order("created_at")
                .limit(1)
                .execute()
            )
            if not result.data:
                return None

            job_row = result.data[0]
            now_iso = datetime.utcnow().isoformat()
            confirm = (
                self.platform_db
                .table("client_provisioning_jobs")
                .update({"claimed_at": now_iso})
                .eq("id", job_row["id"])
                .is_("claimed_at", None)
                .execute()
            )
            if not confirm.data:
                return None
            return ProvisioningJob.from_dict(confirm.data[0])

        return await asyncio.to_thread(_claim)

    async def _process_job(self, job: ProvisioningJob) -> None:
        if job.job_type == "supabase_project":
            await self._process_supabase_project(job)
        elif job.job_type == "schema_sync":
            await self._process_schema_sync(job)
        elif job.job_type == "shared_pool_setup":
            await self._process_shared_pool_setup(job)
        else:
            logger.warning("Unknown provisioning job type '%s' for job %s", job.job_type, job.id)
            await self._delete_job(job.id)

    async def _process_supabase_project(self, job: ProvisioningJob) -> None:
        logger.info("Processing supabase_project job for client %s", job.client_id)
        await self._update_client(job.client_id, {
            "provisioning_status": "creating_project",
            "provisioning_error": None,
            "provisioning_started_at": datetime.utcnow().isoformat(),
        })

        try:
            project_info = await self._create_supabase_project(job.client_id)
        except SupabaseManagementError as exc:
            await self._record_failure(job, str(exc))
            return
        except NotImplementedError as exc:
            logger.warning("Supabase project creation not implemented: %s", exc)
            await self._record_failure(job, str(exc))
            return

        if not project_info:
            await self._record_failure(job, "_create_supabase_project returned no data")
            return

        project_ref = project_info.get("project_ref")
        supabase_url = project_info.get("supabase_url")
        service_key = project_info.get("service_role_key")
        anon_key = project_info.get("anon_key")

        if not all([project_ref, supabase_url, service_key, anon_key]):
            await self._record_failure(job, "Provisioning response missing required keys")
            return

        await self._update_client(job.client_id, {
            "supabase_project_ref": project_ref,
            "supabase_url": supabase_url,
            "supabase_service_role_key": service_key,
            "supabase_anon_key": anon_key,
            "provisioning_status": "schema_syncing",
            "provisioning_error": None,
        })

        await self._enqueue_job(job.client_id, "schema_sync")
        await self._delete_job(job.id)

    async def _process_schema_sync(self, job: ProvisioningJob) -> None:
        logger.info("Processing schema_sync job for client %s", job.client_id)

        client = await self._fetch_client(job.client_id)
        if not client:
            await self._record_failure(job, "Client not found for schema sync")
            return

        supabase_url = client.get("supabase_url")
        if not supabase_url:
            await self._record_failure(job, "Client missing supabase_url for schema sync")
            return

        project_ref = project_ref_from_url(supabase_url)
        results = await asyncio.to_thread(apply_schema, project_ref, self.management_token, True)

        failures = [detail for _, ok, detail in results if not ok]
        if failures:
            await self._record_failure(job, "; ".join(failures))
            return

        await self._update_client(job.client_id, {
            "provisioning_status": "ready",
            "provisioning_completed_at": datetime.utcnow().isoformat(),
            "provisioning_error": None,
        })
        await self._delete_job(job.id)
        logger.info("Client %s provisioning complete", job.client_id)

    async def _create_supabase_project(self, client_id: str) -> Dict[str, Any]:
        def _create() -> Dict[str, Any]:
            org_id = os.getenv("SUPABASE_ORG_ID")
            if not org_id:
                raise SupabaseManagementError("SUPABASE_ORG_ID environment variable not set")

            region = os.getenv("SUPABASE_DEFAULT_REGION", "us-east-1")
            plan = os.getenv("SUPABASE_DEFAULT_PLAN", "free")

            client = (
                self.platform_db
                .table("clients")
                .select("name")
                .eq("id", client_id)
                .single()
                .execute()
            )
            client_name = client.data.get("name") if client and client.data else client_id

            return create_project(
                token=self.management_token,
                org_id=org_id,
                name=str(client_name),
                client_id=client_id,
                region=region,
                plan=plan,
            )

        return await asyncio.to_thread(_create)

    async def _update_client(self, client_id: str, data: Dict[str, Any]) -> None:
        def _update() -> None:
            self.platform_db.table("clients").update(data).eq("id", client_id).execute()

        await asyncio.to_thread(_update)

    async def _fetch_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        def _fetch() -> Optional[Dict[str, Any]]:
            result = (
                self.platform_db
                .table("clients")
                .select("*")
                .eq("id", client_id)
                .single()
                .execute()
            )
            return result.data

        return await asyncio.to_thread(_fetch)

    async def _enqueue_job(self, client_id: str, job_type: str) -> None:
        def _enqueue() -> None:
            self.platform_db.table("client_provisioning_jobs").upsert({
                "client_id": client_id,
                "job_type": job_type,
                "attempts": 0,
                "claimed_at": None,
                "last_error": None,
            }, on_conflict="client_id,job_type").execute()

        await asyncio.to_thread(_enqueue)

    async def _delete_job(self, job_id: str) -> None:
        def _delete() -> None:
            self.platform_db.table("client_provisioning_jobs").delete().eq("id", job_id).execute()

        await asyncio.to_thread(_delete)

    async def _record_failure(self, job: ProvisioningJob, message: str) -> None:
        logger.error("Provisioning job %s failed: %s", job.id, message)

        def _update() -> None:
            self.platform_db.table("client_provisioning_jobs").update({
                "attempts": job.attempts + 1,
                "last_error": message,
                "claimed_at": datetime.utcnow().isoformat(),
            }).eq("id", job.id).execute()

            self.platform_db.table("clients").update({
                "provisioning_status": "failed",
                "provisioning_error": message,
            }).eq("id", job.client_id).execute()

        await asyncio.to_thread(_update)

    async def _process_shared_pool_setup(self, job: ProvisioningJob) -> None:
        """
        Process Adventurer tier setup (shared pool).

        For shared hosting, we don't create a Supabase project.
        We set up default API configuration with platform keys and mark as ready.
        """
        logger.info("Processing shared_pool_setup job for client %s (Adventurer tier)", job.client_id)

        await self._update_client(job.client_id, {
            "provisioning_status": "configuring_shared",
            "provisioning_error": None,
            "provisioning_started_at": datetime.utcnow().isoformat(),
        })

        # Verify shared pool is available (optional - may not have table yet)
        def _verify_pool() -> bool:
            try:
                result = (
                    self.platform_db
                    .table("shared_pool_config")
                    .select("id, pool_name, current_client_count, max_clients")
                    .eq("is_active", True)
                    .eq("pool_name", "adventurer_pool")
                    .single()
                    .execute()
                )
                if not result.data:
                    # No pool config found - allow anyway for now
                    logger.warning("No shared_pool_config found, proceeding with default setup")
                    return True

                # Check if pool has capacity
                current = result.data.get("current_client_count", 0)
                max_clients = result.data.get("max_clients", 1000)
                return current < max_clients
            except Exception as e:
                # Table may not exist yet - allow provisioning to continue
                logger.warning(f"shared_pool_config check failed: {e}, proceeding anyway")
                return True

        pool_available = await asyncio.to_thread(_verify_pool)

        if not pool_available:
            await self._record_failure(
                job,
                "Shared pool not available or at capacity. Cannot provision Adventurer tier."
            )
            return

        # Set default API configuration for Adventurer tier
        # These are the platform-managed defaults - user can override with BYOK
        default_api_config = {
            "llm": {
                "provider": "cerebras",
                "model": "glm-4-9b-chat",  # GLM 4.6
            },
            "stt": {
                "provider": "cartesia",
            },
            "tts": {
                "provider": "cartesia",
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

        # Initialize usage tracking record for this client
        def _init_usage() -> None:
            try:
                period_start = datetime.utcnow().replace(day=1).date().isoformat()
                self.platform_db.table("client_usage").upsert({
                    "client_id": job.client_id,
                    "period_start": period_start,
                    "voice_seconds_used": 0,
                    "voice_seconds_limit": 6000,  # 100 minutes
                    "text_messages_used": 0,
                    "text_messages_limit": 1000,
                    "embedding_chunks_used": 0,
                    "embedding_chunks_limit": 10000,
                }, on_conflict="client_id,period_start").execute()
            except Exception as e:
                logger.warning(f"Failed to initialize usage tracking: {e}")

        await asyncio.to_thread(_init_usage)

        # Mark client as ready with shared hosting and default config
        await self._update_client(job.client_id, {
            "tier": "adventurer",
            "hosting_type": "shared",
            "max_sidekicks": 1,
            "uses_platform_keys": True,  # Use platform API keys by default
            "default_api_config": default_api_config,
            "supabase_url": None,  # Uses shared pool
            "supabase_service_role_key": None,
            "provisioning_status": "completed",
            "provisioning_completed_at": datetime.utcnow().isoformat(),
            "provisioning_error": None,
        })

        await self._delete_job(job.id)
        logger.info("Client %s provisioning complete (Adventurer tier, shared pool, platform keys)", job.client_id)


async def provision_client_by_tier(
    client_id: str,
    tier: str = "champion",
    platform_db=None,
) -> None:
    """
    Enqueue the appropriate provisioning job based on tier.

    Args:
        client_id: The client UUID
        tier: 'adventurer', 'champion', or 'paragon'
        platform_db: Platform Supabase client (optional, will use default)
    """
    if platform_db is None:
        platform_db = get_connection_manager().platform_client

    tier_features = get_tier_features(tier)
    hosting_type = tier_features.get("hosting_type", HostingType.DEDICATED)

    if hosting_type == HostingType.SHARED:
        # Adventurer tier: Use shared pool setup
        job_type = "shared_pool_setup"
        logger.info(f"Enqueueing shared_pool_setup for client {client_id} (Adventurer tier)")
    else:
        # Champion/Paragon tier: Create dedicated Supabase project
        job_type = "supabase_project"
        logger.info(f"Enqueueing supabase_project for client {client_id} ({tier} tier)")

    # Update client with tier info
    platform_db.table("clients").update({
        "tier": tier,
        "hosting_type": str(hosting_type.value) if isinstance(hosting_type, HostingType) else hosting_type,
        "max_sidekicks": tier_features.get("max_sidekicks"),
        "provisioning_status": "queued",
    }).eq("id", client_id).execute()

    # Enqueue the job
    platform_db.table("client_provisioning_jobs").upsert({
        "client_id": client_id,
        "job_type": job_type,
        "attempts": 0,
        "claimed_at": None,
        "last_error": None,
    }, on_conflict="client_id,job_type").execute()


__all__ = ["ProvisioningWorker", "ProvisioningJob", "provision_client_by_tier"]
