"""Background worker for provisioning new client Supabase projects."""
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


__all__ = ["ProvisioningWorker", "ProvisioningJob"]
