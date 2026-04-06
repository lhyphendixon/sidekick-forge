"""
Notion REST API client using httpx.

Wraps the Notion API v1 for database, page, block, and search operations.
Auth: Bearer token (per-client via OAuth).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionAPIError(Exception):
    """Raised when the Notion API returns an error."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class NotionClient:
    """Async REST client for the Notion API."""

    def __init__(self, access_token: str, *, timeout: float = 20.0) -> None:
        self._token = access_token
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{NOTION_API_BASE}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_body,
            )
            if resp.status_code >= 400:
                raise NotionAPIError(
                    f"Notion API error {resp.status_code}: {resp.text[:300]}",
                    status_code=resp.status_code,
                )
            if resp.status_code == 204:
                return {}
            try:
                return resp.json()
            except Exception:
                return resp.text

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str = "",
        *,
        filter_type: Optional[str] = None,
        page_size: int = 10,
    ) -> Dict[str, Any]:
        """Full-text search across the workspace.
        filter_type: 'database' or 'page' to narrow results.
        """
        body: Dict[str, Any] = {"page_size": min(page_size, 100)}
        if query:
            body["query"] = query
        if filter_type in ("database", "page"):
            body["filter"] = {"value": filter_type, "property": "object"}
        return await self._request("POST", "/search", json_body=body)

    # ------------------------------------------------------------------
    # Databases
    # ------------------------------------------------------------------

    async def list_databases(self, *, page_size: int = 20) -> List[Dict[str, Any]]:
        """List databases the integration has access to."""
        result = await self.search(filter_type="database", page_size=page_size)
        return result.get("results", []) if isinstance(result, dict) else []

    async def get_database(self, database_id: str) -> Dict[str, Any]:
        """Get database schema (properties)."""
        return await self._request("GET", f"/databases/{database_id}")

    async def query_database(
        self,
        database_id: str,
        *,
        filter: Optional[Dict[str, Any]] = None,
        sorts: Optional[List[Dict[str, Any]]] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """Query a database with optional filter and sort."""
        body: Dict[str, Any] = {"page_size": min(page_size, 100)}
        if filter:
            body["filter"] = filter
        if sorts:
            body["sorts"] = sorts
        return await self._request(
            "POST", f"/databases/{database_id}/query", json_body=body
        )

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    async def get_page(self, page_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/pages/{page_id}")

    async def create_page(
        self,
        parent_database_id: str,
        properties: Dict[str, Any],
        *,
        children: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "parent": {"database_id": parent_database_id},
            "properties": properties,
        }
        if children:
            body["children"] = children
        return await self._request("POST", "/pages", json_body=body)

    async def update_page(
        self, page_id: str, properties: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not properties:
            raise NotionAPIError("Nothing to update -- provide at least one property.")
        return await self._request(
            "PATCH", f"/pages/{page_id}", json_body={"properties": properties}
        )

    async def archive_page(self, page_id: str) -> Dict[str, Any]:
        return await self._request(
            "PATCH", f"/pages/{page_id}", json_body={"archived": True}
        )

    # ------------------------------------------------------------------
    # Blocks
    # ------------------------------------------------------------------

    async def get_block_children(
        self, block_id: str, *, page_size: int = 50
    ) -> Dict[str, Any]:
        return await self._request(
            "GET",
            f"/blocks/{block_id}/children",
            params={"page_size": min(page_size, 100)},
        )

    async def append_blocks(
        self, block_id: str, children: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return await self._request(
            "PATCH",
            f"/blocks/{block_id}/children",
            json_body={"children": children},
        )

    # ------------------------------------------------------------------
    # Users (for connection verification)
    # ------------------------------------------------------------------

    async def get_me(self) -> Dict[str, Any]:
        return await self._request("GET", "/users/me")
