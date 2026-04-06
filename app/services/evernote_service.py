"""
Evernote REST API client using httpx.

Wraps the Evernote Cloud API for note CRUD, notebook listing, and search.
Uses the NoteStore REST endpoints rather than the Thrift-based SDK.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

EVERNOTE_API_BASE = "https://www.evernote.com"
EVERNOTE_SANDBOX_API_BASE = "https://sandbox.evernote.com"


class EvernoteAPIError(Exception):
    """Raised when the Evernote API returns an error."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class EvernoteClient:
    """Async REST client for the Evernote Cloud API."""

    def __init__(
        self,
        access_token: str,
        *,
        sandbox: bool = False,
        timeout: float = 20.0,
    ) -> None:
        self._token = access_token
        self._base = EVERNOTE_SANDBOX_API_BASE if sandbox else EVERNOTE_API_BASE
        self._timeout = timeout
        self._note_store_url: Optional[str] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _thrift_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/x-thrift",
        }

    async def _get_note_store_url(self) -> str:
        """Retrieve the NoteStore URL for this user via the UserStore."""
        if self._note_store_url:
            return self._note_store_url

        url = f"{self._base}/edam/user"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            # Use the JSON API endpoint to get user info
            resp = await client.get(
                url,
                headers=self._headers(),
                params={"auth": self._token},
            )
            if resp.status_code >= 400:
                raise EvernoteAPIError(
                    f"Failed to get user info: {resp.status_code} {resp.text[:200]}",
                    status_code=resp.status_code,
                )

        # For Evernote, the NoteStore URL follows a pattern based on the shard
        # We can derive it from the API base + standard path
        # The token contains shard info: S=s1:U=... -> shard is s1
        shard = self._extract_shard(self._token)
        self._note_store_url = f"{self._base}/shard/{shard}/notestore"
        return self._note_store_url

    @staticmethod
    def _extract_shard(token: str) -> str:
        """Extract shard ID from Evernote OAuth token."""
        # OAuth tokens contain S=sX where X is the shard
        match = re.search(r"S=(\w+)", token)
        if match:
            return match.group(1)
        # Fallback for developer tokens or different formats
        return "s1"

    async def _api_call(
        self,
        method: str,
        endpoint: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Make an authenticated API call to the Evernote API."""
        base_params = {"auth": self._token}
        if params:
            base_params.update(params)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            if method.upper() == "GET":
                resp = await client.get(
                    endpoint,
                    headers=self._headers(),
                    params=base_params,
                )
            else:
                resp = await client.post(
                    endpoint,
                    headers=self._headers(),
                    params=base_params,
                    json=json_body,
                )

            if resp.status_code >= 400:
                raise EvernoteAPIError(
                    f"Evernote API error {resp.status_code}: {resp.text[:300]}",
                    status_code=resp.status_code,
                )

            try:
                return resp.json()
            except Exception:
                return resp.text

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def list_notebooks(self) -> List[Dict[str, Any]]:
        """List all notebooks for the authenticated user."""
        url = f"{self._base}/api/v1/notebooks"
        try:
            result = await self._api_call("GET", url)
            if isinstance(result, list):
                return result
            if isinstance(result, dict) and "notebooks" in result:
                return result["notebooks"]
            return result if isinstance(result, list) else []
        except EvernoteAPIError:
            raise
        except Exception as exc:
            raise EvernoteAPIError(f"Failed to list notebooks: {exc}") from exc

    async def search_notes(
        self,
        query: str,
        *,
        notebook_guid: Optional[str] = None,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search notes using Evernote search grammar."""
        url = f"{self._base}/api/v1/notes"
        search_query = query
        if notebook_guid:
            search_query = f"notebook:{notebook_guid} {query}"

        params = {
            "search": search_query,
            "maxNotes": min(max_results, 50),
        }

        try:
            result = await self._api_call("GET", url, params=params)
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("notes", [])
            return []
        except EvernoteAPIError:
            raise
        except Exception as exc:
            raise EvernoteAPIError(f"Failed to search notes: {exc}") from exc

    async def get_note(
        self,
        note_guid: str,
        *,
        include_content: bool = True,
    ) -> Dict[str, Any]:
        """Get a specific note by GUID."""
        url = f"{self._base}/api/v1/notes/{note_guid}"
        params = {"includeContent": str(include_content).lower()}

        try:
            return await self._api_call("GET", url, params=params)
        except EvernoteAPIError:
            raise
        except Exception as exc:
            raise EvernoteAPIError(f"Failed to get note: {exc}") from exc

    async def create_note(
        self,
        title: str,
        content: str,
        *,
        notebook_guid: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new note."""
        url = f"{self._base}/api/v1/notes"
        enml_content = self._text_to_enml(content)

        body: Dict[str, Any] = {
            "title": title,
            "content": enml_content,
        }
        if notebook_guid:
            body["notebookGuid"] = notebook_guid
        if tags:
            body["tagNames"] = tags

        try:
            return await self._api_call("POST", url, json_body=body)
        except EvernoteAPIError:
            raise
        except Exception as exc:
            raise EvernoteAPIError(f"Failed to create note: {exc}") from exc

    async def update_note(
        self,
        note_guid: str,
        *,
        title: Optional[str] = None,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update an existing note."""
        url = f"{self._base}/api/v1/notes/{note_guid}"
        body: Dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if content is not None:
            body["content"] = self._text_to_enml(content)
        if tags is not None:
            body["tagNames"] = tags

        if not body:
            raise EvernoteAPIError("Nothing to update — provide title, content, or tags.")

        try:
            return await self._api_call("POST", url, json_body=body)
        except EvernoteAPIError:
            raise
        except Exception as exc:
            raise EvernoteAPIError(f"Failed to update note: {exc}") from exc

    # ------------------------------------------------------------------
    # ENML helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _text_to_enml(text: str) -> str:
        """Convert plain text to ENML (Evernote Markup Language)."""
        # Escape XML special characters
        escaped = (
            text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        # Convert newlines to <br/> tags
        body = escaped.replace("\n", "<br/>")
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
            f"<en-note>{body}</en-note>"
        )

    @staticmethod
    def enml_to_text(enml: str) -> str:
        """Convert ENML content to plain text."""
        if not enml:
            return ""
        try:
            # Strip XML declaration and DOCTYPE
            clean = re.sub(r"<\?xml[^?]*\?>", "", enml)
            clean = re.sub(r"<!DOCTYPE[^>]*>", "", clean)
            # Parse as XML
            root = ET.fromstring(clean)
            # Extract text content
            return "".join(root.itertext()).strip()
        except ET.ParseError:
            # Fallback: strip all tags
            return re.sub(r"<[^>]+>", "", enml).strip()
