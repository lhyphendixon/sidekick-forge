"""Trello ability — full CRUD for boards, lists, and cards via the Trello API."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple

from livekit.agents.llm.tool_context import function_tool as lk_function_tool

from app.services.trello_service import TrelloAPIError, TrelloClient

logger = logging.getLogger(__name__)

# Deduplication
_TOOL_CALL_HISTORY: Dict[str, Deque[Tuple[str, float, Any]]] = {}
_DEDUPE_WINDOW_SECONDS = 10.0
_MAX_HISTORY_SIZE = 50

QUOTE_PATTERN = re.compile(r"[\"\u201c\u201d\u2018\u2019\u0027]([^\"\u201c\u201d\u2018\u2019\u0027]+)[\"\u201c\u201d\u2018\u2019\u0027]")

_INQUIRY_CANDIDATE_KEYS = (
    "user_inquiry", "userInquiry", "latest_user_text", "latestUserText",
    "query", "question", "prompt", "text", "message", "transcript",
)


class TrelloAbilityConfigError(ValueError):
    """Raised when the Trello ability is misconfigured."""


@dataclass
class BoardConfig:
    id: str
    name: Optional[str] = None


def _compute_call_hash(slug: str, user_inquiry: str) -> str:
    content = f"{slug}:{user_inquiry.lower().strip()}"
    return hashlib.md5(content.encode()).hexdigest()


def _check_duplicate_call(slug: str, call_hash: str) -> Optional[Any]:
    now = time.time()
    if slug not in _TOOL_CALL_HISTORY:
        _TOOL_CALL_HISTORY[slug] = deque(maxlen=_MAX_HISTORY_SIZE)
        return None
    history = _TOOL_CALL_HISTORY[slug]
    while history and (now - history[0][1]) > _DEDUPE_WINDOW_SECONDS:
        history.popleft()
    for stored_hash, timestamp, result in history:
        if stored_hash == call_hash and (now - timestamp) <= _DEDUPE_WINDOW_SECONDS:
            return result
    return None


def _record_call(slug: str, call_hash: str, result: Any) -> None:
    if slug not in _TOOL_CALL_HISTORY:
        _TOOL_CALL_HISTORY[slug] = deque(maxlen=_MAX_HISTORY_SIZE)
    _TOOL_CALL_HISTORY[slug].append((call_hash, time.time(), result))


def _coalesce_user_inquiry(metadata: Optional[Dict[str, Any]], kwargs: Dict[str, Any]) -> Optional[str]:
    for source in (kwargs, metadata or {}):
        if not isinstance(source, dict):
            continue
        for key in _INQUIRY_CANDIDATE_KEYS:
            val = source.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


class TrelloToolHandler:
    """Handles Trello intent parsing and action execution."""

    def __init__(
        self,
        slug: str,
        description: str,
        config: Dict[str, Any],
        *,
        auth_service: Any = None,
    ) -> None:
        self.slug = slug
        self.description = description
        self.config = config
        self._auth_service = auth_service

        # Parse scoped boards from config
        self.boards: List[BoardConfig] = []
        self.board_by_id: Dict[str, BoardConfig] = {}
        raw_boards = config.get("boards") or []
        if isinstance(raw_boards, list):
            for entry in raw_boards:
                if isinstance(entry, str):
                    bc = BoardConfig(id=entry)
                elif isinstance(entry, dict):
                    bc = BoardConfig(id=entry.get("id", ""), name=entry.get("name"))
                else:
                    continue
                if bc.id:
                    self.boards.append(bc)
                    self.board_by_id[bc.id] = bc

        self.default_action = config.get("default_action", "list")

    # ------------------------------------------------------------------
    # Client acquisition
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_client_id(metadata: Optional[Dict[str, Any]]) -> str:
        if not isinstance(metadata, dict):
            raise TrelloAbilityConfigError("Unable to determine client context for Trello ability.")
        candidates = [metadata.get("client_id"), metadata.get("clientId")]
        client_obj = metadata.get("client")
        if isinstance(client_obj, dict):
            candidates.append(client_obj.get("id"))
        for c in candidates:
            if isinstance(c, str) and c.strip():
                return c.strip()
        raise TrelloAbilityConfigError("Unable to determine client context for Trello ability.")

    async def _acquire_client(self, metadata: Optional[Dict[str, Any]]) -> Tuple[TrelloClient, Optional[str]]:
        if self._auth_service is not None:
            client_id = self._extract_client_id(metadata)
            bundle = self._auth_service.get_token_bundle(client_id)
            if not bundle or not bundle.token or not bundle.api_key:
                raise TrelloAbilityConfigError(
                    "Trello is not connected for this client. "
                    "Ask an administrator to connect Trello in Settings."
                )
            return TrelloClient(bundle.api_key, bundle.token), client_id

        api_key = self.config.get("api_key", "")
        token = self.config.get("token", "")
        if not api_key or not token:
            raise TrelloAbilityConfigError(
                "Trello ability requires an API key and token. "
                "Connect Trello for this client in Settings."
            )
        return TrelloClient(api_key, token), None

    # ------------------------------------------------------------------
    # Intent parsing
    # ------------------------------------------------------------------

    def _interpret_intent(self, message: str) -> Tuple[str, Dict[str, Any]]:
        lower = message.lower()

        trello_keywords = (
            "trello", "board", "boards", "card", "cards", "list", "lists",
            "checklist", "label", "column", "kanban", "backlog", "sprint",
        )
        is_trello_related = any(kw in lower for kw in trello_keywords)
        if not is_trello_related:
            return ("skip", {"raw": message, "lower": lower})

        # Determine action
        if any(w in lower for w in ("search", "find", "look up", "look for", "search for")):
            action = "search"
        elif any(w in lower for w in ("delete", "remove", "trash")):
            action = "delete_card"
        elif any(w in lower for w in ("archive",)):
            action = "archive_card"
        elif any(w in lower for w in ("complete", "done", "finish", "mark done", "mark complete")):
            action = "complete_card"
        elif any(w in lower for w in ("comment", "add comment", "note on")):
            action = "add_comment"
        elif any(w in lower for w in ("checklist", "add checklist")):
            action = "create_checklist"
        elif any(w in lower for w in ("move", "move card")):
            action = "move_card"
        elif any(w in lower for w in ("update", "edit", "change", "modify", "rename", "set due")):
            action = "update_card"
        elif any(w in lower for w in ("create", "add card", "new card", "add a card", "make a card")):
            action = "create_card"
        elif any(w in lower for w in ("show boards", "list boards", "my boards", "all boards")):
            action = "list_boards"
        elif any(w in lower for w in ("show cards", "list cards", "cards on", "cards in", "what cards")):
            action = "list_cards"
        elif any(w in lower for w in ("show lists", "list lists", "columns on", "lists on")):
            action = "list_lists"
        elif any(w in lower for w in ("show labels", "list labels", "labels on")):
            action = "list_labels"
        elif "board" in lower and any(w in lower for w in ("show", "list", "view", "open", "what")):
            action = "list_boards"
        elif "card" in lower and any(w in lower for w in ("show", "list", "view", "read", "what")):
            action = "list_cards"
        elif any(w in lower for w in ("check", "status", "overview", "summary", "what do i have", "what's on", "whats on")):
            action = "overview"
        else:
            # Default: if scoped boards exist, show overview; otherwise list boards
            action = "overview" if self.boards else "list_boards"

        details: Dict[str, Any] = {"raw": message, "lower": lower}

        # Extract quoted strings
        quoted = QUOTE_PATTERN.findall(message)
        if quoted:
            details["quoted_text"] = quoted[0].strip()
            if len(quoted) > 1:
                details["quoted_texts"] = [q.strip() for q in quoted]

        # Extract board name from "on <board>" pattern
        board_match = re.search(r"(?:on|in|from|to)\s+(?:the\s+)?(?:board\s+)?[\"']?([A-Za-z][\w\s\-]+?)[\"']?\s*(?:board|$)", lower)
        if board_match:
            details["board_name"] = board_match.group(1).strip()

        # Match against scoped boards
        board = self._match_board(message)
        if board:
            details["board"] = board

        # Extract list/column name
        list_match = re.search(r"(?:list|column|in)\s+[\"']?([A-Za-z][\w\s\-]+?)[\"']?\s*(?:list|column|$)", lower)
        if list_match:
            details["list_name"] = list_match.group(1).strip()

        # Extract card name from "called/named/titled" or use quoted text
        title_match = re.search(r"(?:called|named|titled)\s+[\"']?(.+?)(?:[\"']|\s*$)", message, re.IGNORECASE)
        if title_match:
            details["card_name"] = title_match.group(1).strip().rstrip(".")

        # Extract due date
        due_match = re.search(r"due\s+(?:on\s+|by\s+)?(\d{4}-\d{2}-\d{2})", message)
        if due_match:
            details["due"] = due_match.group(1)

        # Extract description/comment content
        content_match = re.search(
            r"(?:saying|with description|with content|description|comment)\s*[:\-]?\s*(.+)",
            message, re.IGNORECASE | re.DOTALL,
        )
        if content_match:
            details["content"] = content_match.group(1).strip()

        return action, details

    def _match_board(self, message: str) -> Optional[BoardConfig]:
        lower = message.lower()
        for board in self.boards:
            if board.name and board.name.lower() in lower:
                return board
            if board.id in message:
                return board
        return None

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    async def _handle_overview(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        """Provide a high-level summary of boards and recent cards."""
        target_boards = []
        requested_board_name = details.get("board_name", "").lower().strip()

        if self.boards:
            # Use scoped boards
            for b in self.boards:
                try:
                    cards = await client.get_board_cards(b.id)
                    target_boards.append({
                        "name": b.name or b.id,
                        "id": b.id,
                        "cards": cards[:10],  # Limit to 10 most recent
                    })
                except Exception as exc:
                    logger.warning("Failed to fetch cards for board %s: %s", b.id, exc)
                    target_boards.append({"name": b.name or b.id, "id": b.id, "cards": [], "error": str(exc)})
        else:
            # Fetch all boards and top cards from each
            all_boards = await client.list_boards()

            # If a board name was mentioned, filter to matching boards first
            if requested_board_name:
                matching = [b for b in all_boards if requested_board_name in b.get("name", "").lower()]
                boards = matching if matching else all_boards[:15]
            else:
                boards = all_boards[:15]

            for b in boards:
                try:
                    cards = await client.get_board_cards(b["id"])
                    target_boards.append({
                        "name": b.get("name", "Untitled"),
                        "id": b["id"],
                        "cards": cards[:10],
                    })
                except Exception:
                    target_boards.append({
                        "name": b.get("name", "Untitled"),
                        "id": b["id"],
                        "cards": [],
                    })

        if not target_boards:
            return "No boards found in your Trello account."

        lines = ["Here's a summary of your Trello boards:\n"]
        for board in target_boards:
            lines.append(f"**{board['name']}**")
            cards = board.get("cards", [])
            if not cards:
                lines.append("  (no cards)")
            else:
                for card in cards:
                    name = card.get("name", "Untitled")
                    due = card.get("due", "")
                    due_str = f" (due: {due[:10]})" if due else ""
                    labels = ", ".join(l.get("name", "") for l in card.get("labels", []) if l.get("name"))
                    label_str = f" [{labels}]" if labels else ""
                    list_name = card.get("list", {}).get("name", "")
                    list_str = f" — {list_name}" if list_name else ""
                    lines.append(f"  - {name}{due_str}{label_str}{list_str}")
            lines.append("")

        total_cards = sum(len(b.get("cards", [])) for b in target_boards)
        lines.append(f"Total: {len(target_boards)} board(s), {total_cards} card(s)")
        return "\n".join(lines)

    async def _handle_list_boards(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        if self.boards:
            lines = [f"Scoped to {len(self.boards)} board(s):\n"]
            for b in self.boards:
                label = b.name or b.id
                lines.append(f"  - {label} (id: {b.id})")
            return "\n".join(lines)

        boards = await client.list_boards()
        if not boards:
            return "No open boards found in your Trello account."
        lines = [f"Found {len(boards)} open board(s):\n"]
        for b in boards:
            lines.append(f"  - {b.get('name', 'Untitled')} (id: {b.get('id', '')}) {b.get('url', '')}")
        return "\n".join(lines)

    async def _resolve_board_id(self, client: TrelloClient, details: Dict[str, Any]) -> Optional[str]:
        board_cfg = details.get("board")
        if isinstance(board_cfg, BoardConfig):
            return board_cfg.id

        board_name = details.get("board_name", "")
        if board_name:
            # Search scoped boards first
            for b in self.boards:
                if b.name and b.name.lower() == board_name.lower():
                    return b.id

            # Search Trello
            boards = await client.list_boards()
            for b in boards:
                if b.get("name", "").lower() == board_name.lower():
                    return b.get("id")

        # Default to first scoped board
        if self.boards:
            return self.boards[0].id
        return None

    async def _resolve_list_id(self, client: TrelloClient, board_id: str, details: Dict[str, Any]) -> Optional[str]:
        list_name = details.get("list_name", "")
        lists = await client.get_board_lists(board_id)
        if list_name:
            for lst in lists:
                if lst.get("name", "").lower() == list_name.lower():
                    return lst.get("id")
        # Default to first list
        if lists:
            return lists[0].get("id")
        return None

    async def _handle_list_lists(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        board_id = await self._resolve_board_id(client, details)
        if not board_id:
            return "Please specify which board to view lists for."
        lists = await client.get_board_lists(board_id)
        if not lists:
            return "No lists found on this board."
        lines = [f"Lists on board:\n"]
        for lst in lists:
            lines.append(f"  - {lst.get('name', 'Untitled')} (id: {lst.get('id', '')})")
        return "\n".join(lines)

    async def _handle_list_cards(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        board_id = await self._resolve_board_id(client, details)
        if not board_id:
            return "Please specify which board to view cards for."

        list_name = details.get("list_name", "")
        lists = await client.get_board_lists(board_id)

        if list_name:
            target_lists = [l for l in lists if l.get("name", "").lower() == list_name.lower()]
        else:
            target_lists = lists

        if not target_lists:
            return f"No list matching '{list_name}' found on this board."

        all_lines = []
        for lst in target_lists:
            cards = await client.get_list_cards(lst["id"])
            list_label = lst.get("name", "Untitled")
            if not cards:
                all_lines.append(f"**{list_label}**: (empty)")
                continue
            all_lines.append(f"**{list_label}** ({len(cards)} card{'s' if len(cards) != 1 else ''}):")
            for c in cards:
                due = f" (due: {c['due'][:10]})" if c.get("due") else ""
                done = " [DONE]" if c.get("dueComplete") else ""
                all_lines.append(f"  - {c.get('name', 'Untitled')}{due}{done}")

        return "\n".join(all_lines)

    async def _handle_list_labels(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        board_id = await self._resolve_board_id(client, details)
        if not board_id:
            return "Please specify which board to view labels for."
        labels = await client.get_board_labels(board_id)
        if not labels:
            return "No labels found on this board."
        lines = [f"Labels:\n"]
        for lb in labels:
            name = lb.get("name") or "(unnamed)"
            color = lb.get("color") or "none"
            lines.append(f"  - {name} ({color})")
        return "\n".join(lines)

    async def _handle_search(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        query = details.get("quoted_text") or details.get("card_name") or details.get("raw", "")
        for prefix in ("search trello for", "find card", "search cards for", "find in trello", "look up"):
            if query.lower().startswith(prefix):
                query = query[len(prefix):].strip()
        if not query.strip():
            return "Please specify what to search for in Trello."

        board_ids = [b.id for b in self.boards] if self.boards else None
        result = await client.search(query, board_ids=board_ids)
        cards = result.get("cards", [])
        boards = result.get("boards", [])

        lines = []
        if cards:
            lines.append(f"Found {len(cards)} card(s):\n")
            for c in cards:
                due = f" (due: {c['due'][:10]})" if c.get("due") else ""
                lines.append(f"  - {c.get('name', 'Untitled')}{due} — {c.get('url', '')}")
        if boards:
            lines.append(f"\nFound {len(boards)} board(s):\n")
            for b in boards:
                lines.append(f"  - {b.get('name', 'Untitled')} — {b.get('url', '')}")
        if not lines:
            return f"No results found for '{query}'."
        return "\n".join(lines)

    async def _handle_create_card(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        card_name = details.get("quoted_text") or details.get("card_name") or "Untitled Card"
        board_id = await self._resolve_board_id(client, details)
        if not board_id:
            return "Please specify which board to create the card on."
        list_id = await self._resolve_list_id(client, board_id, details)
        if not list_id:
            return "No list found to add the card to."

        desc = details.get("content", "")
        due = details.get("due")
        result = await client.create_card(list_id, card_name, desc=desc or None, due=due)
        return f"Created card '{result.get('name', card_name)}' — {result.get('url', '')}"

    async def _handle_update_card(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        card_name = details.get("quoted_text") or details.get("card_name") or ""
        if not card_name:
            return "Please specify which card to update."

        board_ids = [b.id for b in self.boards] if self.boards else None
        search_result = await client.search(card_name, board_ids=board_ids, model_types="cards", cards_limit=5)
        cards = search_result.get("cards", [])
        if not cards:
            return f"No card found matching '{card_name}'."

        card_id = cards[0].get("id")
        kwargs: Dict[str, Any] = {}
        if details.get("content"):
            kwargs["desc"] = details["content"]
        if details.get("due"):
            kwargs["due"] = details["due"]

        rename_match = re.search(r"rename\s+to\s+[\"']?(.+?)[\"']?\s*$", details.get("raw", ""), re.IGNORECASE)
        if rename_match:
            kwargs["name"] = rename_match.group(1).strip()

        if not kwargs:
            return f"Found card '{cards[0].get('name')}' but no changes specified."

        result = await client.update_card(card_id, **kwargs)
        return f"Updated card '{result.get('name', card_name)}'."

    async def _handle_move_card(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        card_name = details.get("quoted_text") or details.get("card_name") or ""
        target_list = details.get("list_name") or ""
        if not card_name:
            return "Please specify which card to move."
        if not target_list:
            return "Please specify which list to move the card to."

        board_ids = [b.id for b in self.boards] if self.boards else None
        search_result = await client.search(card_name, board_ids=board_ids, model_types="cards", cards_limit=5)
        cards = search_result.get("cards", [])
        if not cards:
            return f"No card found matching '{card_name}'."

        card = cards[0]
        board_id = card.get("idBoard")
        if not board_id:
            return "Could not determine the board for this card."

        lists = await client.get_board_lists(board_id)
        target = None
        for lst in lists:
            if lst.get("name", "").lower() == target_list.lower():
                target = lst
                break
        if not target:
            return f"No list named '{target_list}' found on this board."

        await client.update_card(card["id"], list_id=target["id"])
        return f"Moved card '{card.get('name')}' to list '{target.get('name')}'."

    async def _handle_complete_card(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        card_name = details.get("quoted_text") or details.get("card_name") or ""
        if not card_name:
            return "Please specify which card to mark complete."

        board_ids = [b.id for b in self.boards] if self.boards else None
        search_result = await client.search(card_name, board_ids=board_ids, model_types="cards", cards_limit=5)
        cards = search_result.get("cards", [])
        if not cards:
            return f"No card found matching '{card_name}'."

        await client.update_card(cards[0]["id"], due_complete=True)
        return f"Marked card '{cards[0].get('name')}' as complete."

    async def _handle_delete_card(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        card_name = details.get("quoted_text") or details.get("card_name") or ""
        if not card_name:
            return "Please specify which card to delete."

        board_ids = [b.id for b in self.boards] if self.boards else None
        search_result = await client.search(card_name, board_ids=board_ids, model_types="cards", cards_limit=5)
        cards = search_result.get("cards", [])
        if not cards:
            return f"No card found matching '{card_name}'."

        await client.delete_card(cards[0]["id"])
        return f"Deleted card '{cards[0].get('name')}'."

    async def _handle_archive_card(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        card_name = details.get("quoted_text") or details.get("card_name") or ""
        if not card_name:
            return "Please specify which card to archive."

        board_ids = [b.id for b in self.boards] if self.boards else None
        search_result = await client.search(card_name, board_ids=board_ids, model_types="cards", cards_limit=5)
        cards = search_result.get("cards", [])
        if not cards:
            return f"No card found matching '{card_name}'."

        await client.archive_card(cards[0]["id"])
        return f"Archived card '{cards[0].get('name')}'."

    async def _handle_add_comment(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        card_name = details.get("quoted_text") or details.get("card_name") or ""
        comment_text = details.get("content") or ""
        if not card_name:
            return "Please specify which card to comment on."
        if not comment_text:
            return "Please specify the comment text."

        board_ids = [b.id for b in self.boards] if self.boards else None
        search_result = await client.search(card_name, board_ids=board_ids, model_types="cards", cards_limit=5)
        cards = search_result.get("cards", [])
        if not cards:
            return f"No card found matching '{card_name}'."

        await client.add_comment(cards[0]["id"], comment_text)
        return f"Added comment to card '{cards[0].get('name')}'."

    async def _handle_create_checklist(self, client: TrelloClient, details: Dict[str, Any]) -> str:
        card_name = details.get("quoted_text") or details.get("card_name") or ""
        if not card_name:
            return "Please specify which card to add a checklist to."

        board_ids = [b.id for b in self.boards] if self.boards else None
        search_result = await client.search(card_name, board_ids=board_ids, model_types="cards", cards_limit=5)
        cards = search_result.get("cards", [])
        if not cards:
            return f"No card found matching '{card_name}'."

        checklist_name = "Checklist"
        # Check for custom checklist name
        name_match = re.search(r"checklist\s+(?:called|named)\s+[\"']?(.+?)[\"']?\s*$", details.get("raw", ""), re.IGNORECASE)
        if name_match:
            checklist_name = name_match.group(1).strip()

        result = await client.create_checklist(cards[0]["id"], checklist_name)
        return f"Created checklist '{result.get('name', checklist_name)}' on card '{cards[0].get('name')}'."

    # ------------------------------------------------------------------
    # Main invoke
    # ------------------------------------------------------------------

    async def _run_action(self, client: TrelloClient, action: str, details: Dict[str, Any]) -> str:
        if action == "skip":
            return ""
        handler_map = {
            "overview": self._handle_overview,
            "list_boards": self._handle_list_boards,
            "list_lists": self._handle_list_lists,
            "list_cards": self._handle_list_cards,
            "list_labels": self._handle_list_labels,
            "search": self._handle_search,
            "create_card": self._handle_create_card,
            "update_card": self._handle_update_card,
            "move_card": self._handle_move_card,
            "complete_card": self._handle_complete_card,
            "delete_card": self._handle_delete_card,
            "archive_card": self._handle_archive_card,
            "add_comment": self._handle_add_comment,
            "create_checklist": self._handle_create_checklist,
        }
        handler = handler_map.get(action)
        if handler:
            return await handler(client, details)
        return "Trello ability is ready. Ask me to list boards, show cards, create/update/delete cards, add comments, or search."

    async def invoke(self, *, user_inquiry: str, metadata: Optional[Dict[str, Any]] = None, **_: Any) -> str:
        if not user_inquiry or not isinstance(user_inquiry, str):
            raise ValueError("Trello ability requires a user inquiry string.")

        action, details = self._interpret_intent(user_inquiry)
        logger.info("Trello intent resolved", extra={"slug": self.slug, "action": action, "details": details})

        if action == "skip":
            return "This inquiry doesn't appear to be related to Trello. No action was taken."

        try:
            client, client_id = await self._acquire_client(metadata)
        except TrelloAbilityConfigError as exc:
            logger.warning("Trello client acquisition failed", exc_info=False)
            return str(exc)
        except Exception as exc:
            logger.exception("Unexpected error acquiring Trello client")
            return f"Failed to connect to Trello: {exc}"

        try:
            return await self._run_action(client, action, details)
        except TrelloAPIError as exc:
            status = getattr(exc, "status_code", None)
            logger.error("Trello API error: status=%s, message=%s", status, exc)
            if status == 429:
                return "Trello rate limit reached. Please wait a moment and try again."
            if status in {401, 403}:
                return "Trello connection was rejected. Please reconnect Trello from your dashboard."
            return f"Trello API error: {exc}"
        except Exception as exc:
            logger.exception("Trello ability execution failed")
            return f"Trello ability failed: {exc}"


# ------------------------------------------------------------------
# Build function (called from ToolRegistry)
# ------------------------------------------------------------------

def build_trello_tool(
    tool_def: Dict[str, Any],
    config: Dict[str, Any],
    *,
    auth_service: Any = None,
) -> Any:
    slug = tool_def.get("slug") or tool_def.get("name") or "trello_boards"
    description = tool_def.get("description") or (
        "Access and manage Trello boards, lists, and cards. "
        "Use this when the user explicitly asks to check, search, create, update, move, or delete Trello cards, "
        "view boards or lists, add comments, or create checklists. "
        "Read the tool's complete output to the user."
    )

    handler = TrelloToolHandler(
        slug=slug,
        description=description,
        config=config,
        auth_service=auth_service,
    )

    async def _invoke(**kwargs: Any) -> Dict[str, Any]:
        # v1.5.0 RawFunctionTool passes all LLM args inside a single 'raw_arguments' dict
        raw_args = kwargs.get("raw_arguments")
        if isinstance(raw_args, dict):
            kwargs = {**kwargs, **raw_args}
        metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
        user_inquiry = kwargs.get("user_inquiry")
        if not isinstance(user_inquiry, str) or not user_inquiry.strip():
            user_inquiry = _coalesce_user_inquiry(metadata, kwargs)

        if not user_inquiry or not isinstance(user_inquiry, str):
            return "I wasn't able to determine what to do with Trello. Please repeat the request."

        call_hash = _compute_call_hash(slug, user_inquiry)
        cached_result = _check_duplicate_call(slug, call_hash)
        if cached_result is not None:
            return cached_result

        summary = await handler.invoke(user_inquiry=user_inquiry, metadata=metadata)
        if not isinstance(summary, str):
            summary = str(summary)

        result = {
            "slug": slug,
            "summary": summary,
            "text": summary,
            "raw_text": summary,
        }
        _record_call(slug, call_hash, result)
        return result

    return lk_function_tool(
        raw_schema={
            "name": slug,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "user_inquiry": {
                        "type": "string",
                        "description": (
                            "Pass the COMPLETE user request VERBATIM. "
                            "For example: 'show cards on my Marketing board', "
                            "'create a card called Launch Plan on the To Do list', "
                            "'search trello for bug reports'. REQUIRED."
                        ),
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Additional session metadata.",
                        "additionalProperties": True,
                    },
                },
                "required": ["user_inquiry"],
            },
        }
    )(_invoke)
