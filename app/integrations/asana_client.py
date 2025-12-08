from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp


logger = logging.getLogger(__name__)


class AsanaAPIError(RuntimeError):
    """Raised when the Asana API returns an error response."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, body: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class AsanaClient:
    """Lightweight async client for a subset of the Asana REST API."""

    BASE_URL = "https://app.asana.com/api/1.0"

    def __init__(
        self,
        access_token: str,
        timeout: float = 15.0,
    ) -> None:
        if not access_token or not isinstance(access_token, str):
            raise ValueError("Asana access token is required")
        self._access_token = access_token.strip()
        self._timeout = timeout

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                url,
                params=params,
                data=json.dumps(payload) if payload is not None else None,
                headers=headers,
            ) as response:
                text = await response.text()
                if response.status // 100 != 2:
                    logger.error(
                        "Asana API error",
                        extra={
                            "status": response.status,
                            "url": url,
                            "params": params,
                            "payload": payload,
                            "body": text,
                        },
                    )
                    raise AsanaAPIError(
                        f"Asana API request failed ({response.status}): {text or 'no response body'}",
                        status_code=response.status,
                        body=text,
                    )
                if not text:
                    return {}
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise AsanaAPIError("Failed to parse Asana API response as JSON") from exc

    async def list_project_tasks(
        self,
        project_gid: str,
        *,
        limit: int = 20,
        include_completed: bool = False,
        opt_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "limit": min(max(limit, 1), 100),
        }
        if not include_completed:
            params["completed_since"] = "now"
        if opt_fields:
            params["opt_fields"] = ",".join(opt_fields)

        tasks: List[Dict[str, Any]] = []
        next_offset: Optional[str] = None
        while len(tasks) < limit:
            if next_offset:
                params["offset"] = next_offset
            data = await self._request("GET", f"/projects/{project_gid}/tasks", params=params)
            page_tasks = data.get("data") or []
            tasks.extend(page_tasks)
            next_page = data.get("next_page") or {}
            next_offset = next_page.get("offset")
            if not next_offset:
                break

        return tasks[:limit]

    async def get_task(
        self,
        task_gid: str,
        *,
        opt_fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if opt_fields:
            params["opt_fields"] = ",".join(opt_fields)
        data = await self._request("GET", f"/tasks/{task_gid}", params=params)
        return data.get("data") or {}

    async def create_task(
        self,
        *,
        name: str,
        workspace: Optional[str] = None,
        projects: Optional[List[str]] = None,
        assignee: Optional[str] = None,
        due_on: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "data": {
                "name": name,
            }
        }
        if workspace:
            payload["data"]["workspace"] = workspace
        if projects:
            payload["data"]["projects"] = projects
        if assignee:
            payload["data"]["assignee"] = assignee
        if due_on:
            payload["data"]["due_on"] = due_on
        if notes:
            payload["data"]["notes"] = notes

        data = await self._request("POST", "/tasks", payload=payload)
        return data.get("data") or {}

    async def create_subtask(
        self,
        parent_gid: str,
        *,
        name: str,
        assignee: Optional[str] = None,
        due_on: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "data": {
                "name": name,
            }
        }
        if assignee:
            payload["data"]["assignee"] = assignee
        if due_on:
            payload["data"]["due_on"] = due_on
        if notes:
            payload["data"]["notes"] = notes

        data = await self._request("POST", f"/tasks/{parent_gid}/subtasks", payload=payload)
        return data.get("data") or {}

    async def update_task(
        self,
        task_gid: str,
        *,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload = {"data": fields}
        data = await self._request("PUT", f"/tasks/{task_gid}", payload=payload)
        return data.get("data") or {}

    async def list_task_subtasks(
        self,
        task_gid: str,
        *,
        limit: int = 50,
        include_completed: bool = True,
        opt_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "limit": min(max(limit, 1), 100),
        }
        if not include_completed:
            params["completed_since"] = "now"
        if opt_fields:
            params["opt_fields"] = ",".join(opt_fields)

        data = await self._request("GET", f"/tasks/{task_gid}/subtasks", params=params)
        return data.get("data") or []

    async def delete_task(self, task_gid: str) -> None:
        await self._request("DELETE", f"/tasks/{task_gid}")
