"""
Trello REST API client using httpx.

Wraps the Trello API v1 for board, list, card, checklist, and search operations.
Auth: API Key (platform-wide) + User Token (per-client via authorize redirect).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

TRELLO_API_BASE = "https://api.trello.com/1"


class TrelloAPIError(Exception):
    """Raised when the Trello API returns an error."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TrelloClient:
    """Async REST client for the Trello API."""

    def __init__(
        self,
        api_key: str,
        token: str,
        *,
        timeout: float = 20.0,
    ) -> None:
        self._key = api_key
        self._token = token
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_params(self) -> Dict[str, str]:
        return {"key": self._key, "token": self._token}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{TRELLO_API_BASE}{path}"
        merged_params = self._auth_params()
        if params:
            merged_params.update(params)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.request(
                method,
                url,
                params=merged_params,
                json=json_body,
            )
            if resp.status_code >= 400:
                raise TrelloAPIError(
                    f"Trello API error {resp.status_code}: {resp.text[:300]}",
                    status_code=resp.status_code,
                )
            if resp.status_code == 204:
                return {}
            try:
                return resp.json()
            except Exception:
                return resp.text

    # ------------------------------------------------------------------
    # Boards
    # ------------------------------------------------------------------

    async def list_boards(self, *, fields: str = "id,name,url,closed") -> List[Dict[str, Any]]:
        """List all open boards for the authenticated member."""
        result = await self._request(
            "GET", "/members/me/boards",
            params={"filter": "open", "fields": fields},
        )
        return result if isinstance(result, list) else []

    async def get_board(self, board_id: str, *, fields: str = "id,name,desc,url") -> Dict[str, Any]:
        return await self._request("GET", f"/boards/{board_id}", params={"fields": fields})

    async def get_board_lists(
        self, board_id: str, *, filter: str = "open", fields: str = "id,name,pos"
    ) -> List[Dict[str, Any]]:
        result = await self._request(
            "GET", f"/boards/{board_id}/lists",
            params={"filter": filter, "fields": fields},
        )
        return result if isinstance(result, list) else []

    async def get_board_labels(self, board_id: str) -> List[Dict[str, Any]]:
        result = await self._request("GET", f"/boards/{board_id}/labels")
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # Lists
    # ------------------------------------------------------------------

    async def get_list_cards(
        self,
        list_id: str,
        *,
        fields: str = "id,name,desc,due,dueComplete,url,labels,idList,pos",
    ) -> List[Dict[str, Any]]:
        result = await self._request(
            "GET", f"/lists/{list_id}/cards",
            params={"fields": fields},
        )
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # Cards
    # ------------------------------------------------------------------

    async def get_card(
        self,
        card_id: str,
        *,
        fields: str = "id,name,desc,due,dueComplete,url,labels,idList,idBoard,pos",
    ) -> Dict[str, Any]:
        return await self._request("GET", f"/cards/{card_id}", params={"fields": fields})

    async def create_card(
        self,
        list_id: str,
        name: str,
        *,
        desc: Optional[str] = None,
        due: Optional[str] = None,
        label_ids: Optional[List[str]] = None,
        pos: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"idList": list_id, "name": name}
        if desc:
            body["desc"] = desc
        if due:
            body["due"] = due
        if label_ids:
            body["idLabels"] = ",".join(label_ids)
        if pos:
            body["pos"] = pos
        return await self._request("POST", "/cards", json_body=body)

    async def update_card(
        self,
        card_id: str,
        *,
        name: Optional[str] = None,
        desc: Optional[str] = None,
        due: Optional[str] = None,
        due_complete: Optional[bool] = None,
        list_id: Optional[str] = None,
        closed: Optional[bool] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if desc is not None:
            body["desc"] = desc
        if due is not None:
            body["due"] = due
        if due_complete is not None:
            body["dueComplete"] = due_complete
        if list_id is not None:
            body["idList"] = list_id
        if closed is not None:
            body["closed"] = closed
        if not body:
            raise TrelloAPIError("Nothing to update — provide at least one field.")
        return await self._request("PUT", f"/cards/{card_id}", json_body=body)

    async def delete_card(self, card_id: str) -> Dict[str, Any]:
        return await self._request("DELETE", f"/cards/{card_id}")

    async def archive_card(self, card_id: str) -> Dict[str, Any]:
        return await self.update_card(card_id, closed=True)

    async def add_comment(self, card_id: str, text: str) -> Dict[str, Any]:
        return await self._request(
            "POST", f"/cards/{card_id}/actions/comments",
            json_body={"text": text},
        )

    # ------------------------------------------------------------------
    # Checklists
    # ------------------------------------------------------------------

    async def create_checklist(
        self, card_id: str, name: str = "Checklist"
    ) -> Dict[str, Any]:
        return await self._request(
            "POST", "/checklists",
            json_body={"idCard": card_id, "name": name},
        )

    async def add_checklist_item(
        self, checklist_id: str, name: str, *, checked: bool = False
    ) -> Dict[str, Any]:
        return await self._request(
            "POST", f"/checklists/{checklist_id}/checkItems",
            json_body={"name": name, "checked": str(checked).lower()},
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        board_ids: Optional[List[str]] = None,
        model_types: str = "cards,boards",
        cards_limit: int = 10,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "query": query,
            "modelTypes": model_types,
            "cards_limit": min(cards_limit, 50),
            "card_fields": "id,name,desc,due,url,idBoard,idList,labels",
            "board_fields": "id,name,url",
        }
        if board_ids:
            params["idBoards"] = ",".join(board_ids)
        return await self._request("GET", "/search", params=params)

    # ------------------------------------------------------------------
    # Members (for connection verification)
    # ------------------------------------------------------------------

    async def get_me(self, *, fields: str = "id,fullName,username") -> Dict[str, Any]:
        return await self._request("GET", "/members/me", params={"fields": fields})
