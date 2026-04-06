"""
Descript API Service

Provides a client for the Descript API to import media, apply AI-driven edits,
and monitor job completion.

API Base URL: https://descriptapi.com/v1
Docs: https://docs.descriptapi.com/
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DESCRIPT_API_BASE = "https://descriptapi.com/v1"


class DescriptAPIError(Exception):
    """Raised when the Descript API returns an error."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Descript API error {status_code}: {detail}")


class DescriptService:
    """Client for the Descript API."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Descript API key is required")
        self._api_key = api_key
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: float = 60.0,
    ) -> Dict[str, Any]:
        """Make an authenticated request to the Descript API."""
        url = f"{DESCRIPT_API_BASE}{path}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method,
                url,
                headers=self._headers,
                json=json_body,
            )
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "30")
                raise DescriptAPIError(
                    429,
                    f"Rate limited. Retry after {retry_after}s",
                )
            if resp.status_code >= 400:
                raise DescriptAPIError(resp.status_code, resp.text)
            return resp.json()

    # ------------------------------------------------------------------
    # Import media into a new project
    # ------------------------------------------------------------------
    async def import_media(
        self,
        media_url: str,
        filename: str,
        project_name: str,
        *,
        callback_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Import a media file (by URL) into a new Descript project.

        Returns dict with job_id, project_id, project_url.
        """
        body: Dict[str, Any] = {
            "project_name": project_name,
            "add_media": {
                filename: {"url": media_url},
            },
            "add_compositions": [
                {
                    "name": "Main",
                    "clips": [{"media": filename}],
                },
            ],
        }
        if callback_url:
            body["callback_url"] = callback_url

        logger.info(f"[descript] Importing media: {filename} -> project '{project_name}'")
        return await self._request("POST", "/jobs/import/project_media", json_body=body)

    # ------------------------------------------------------------------
    # Agent editing
    # ------------------------------------------------------------------
    async def agent_edit(
        self,
        project_id: str,
        prompt: str,
        *,
        callback_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Apply AI-driven edits to a Descript project using natural language.

        Returns dict with job_id, project_id, project_url.
        """
        body: Dict[str, Any] = {
            "project_id": project_id,
            "prompt": prompt,
        }
        if callback_url:
            body["callback_url"] = callback_url

        logger.info(f"[descript] Agent edit on project {project_id}: {prompt[:120]}")
        return await self._request("POST", "/jobs/agent", json_body=body)

    # ------------------------------------------------------------------
    # Job management
    # ------------------------------------------------------------------
    async def get_job(self, job_id: str) -> Dict[str, Any]:
        """Retrieve the status of a specific job."""
        return await self._request("GET", f"/jobs/{job_id}")

    async def list_jobs(self, *, limit: int = 20) -> Dict[str, Any]:
        """List recent jobs."""
        return await self._request("GET", f"/jobs?limit={limit}")

    async def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Cancel a running job."""
        return await self._request("DELETE", f"/jobs/{job_id}")

    # ------------------------------------------------------------------
    # Polling helper
    # ------------------------------------------------------------------
    async def poll_job_completion(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 600,
        poll_interval: float = 5.0,
    ) -> Dict[str, Any]:
        """
        Poll a job until it reaches 'stopped' state or timeout.

        Returns the final job status dict.
        """
        elapsed = 0.0
        while elapsed < timeout_seconds:
            job = await self.get_job(job_id)
            state = job.get("job_state", "")
            if state == "stopped":
                logger.info(f"[descript] Job {job_id} completed")
                return job
            logger.debug(f"[descript] Job {job_id} state={state}, waiting...")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise DescriptAPIError(
            408,
            f"Job {job_id} did not complete within {timeout_seconds}s",
        )

    # ------------------------------------------------------------------
    # Prompt builder
    # ------------------------------------------------------------------
    @staticmethod
    def build_edit_prompt(options: Dict[str, Any]) -> str:
        """
        Build a natural-language editing prompt from widget options.

        Options:
            remove_filler_words: bool
            remove_silences: bool
            studio_sound: bool
            generate_captions: bool
            create_clips: bool
            clip_count: int (1-5)
            clip_length_seconds: int
            clip_resolution: str (e.g. "1080p", "720p")
            custom_instructions: str
        """
        parts: List[str] = []

        if options.get("remove_filler_words"):
            parts.append("Remove all filler words from the transcript")

        if options.get("remove_silences"):
            parts.append("Remove long silences and dead air")

        if options.get("studio_sound"):
            parts.append("Apply Studio Sound to all clips for enhanced audio quality")

        if options.get("generate_captions"):
            parts.append("Generate and add captions")

        if options.get("create_clips"):
            count = min(int(options.get("clip_count", 1)), 5)
            length = int(options.get("clip_length_seconds", 30))
            resolution = options.get("clip_resolution", "1080p")
            parts.append(
                f"Create {count} highlight clip(s), each approximately {length} seconds long "
                f"at {resolution} resolution"
            )

        custom = (options.get("custom_instructions") or "").strip()
        if custom:
            parts.append(custom)

        if not parts:
            return "Improve the overall quality of this video"

        return ". ".join(parts) + "."
