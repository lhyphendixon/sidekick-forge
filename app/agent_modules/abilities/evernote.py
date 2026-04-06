"""Evernote ability — access and manage Evernote notes and notebooks."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from livekit.agents.llm.tool_context import function_tool as lk_function_tool

from app.services.evernote_service import EvernoteAPIError, EvernoteClient

logger = logging.getLogger(__name__)

# Deduplication (mirrors Asana pattern)
_TOOL_CALL_HISTORY: Dict[str, Deque[Tuple[str, float, Any]]] = {}
_DEDUPE_WINDOW_SECONDS = 10.0
_MAX_HISTORY_SIZE = 50

QUOTE_PATTERN = re.compile(r"[\"\u201c\u201d\u2018\u2019\u0027]([^\"\u201c\u201d\u2018\u2019\u0027]+)[\"\u201c\u201d\u2018\u2019\u0027]")

_INQUIRY_CANDIDATE_KEYS = (
    "user_inquiry",
    "userInquiry",
    "latest_user_text",
    "latestUserText",
    "query",
    "question",
    "prompt",
    "text",
    "message",
    "transcript",
)


class EvernoteAbilityConfigError(ValueError):
    """Raised when the Evernote ability is misconfigured."""


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
            logger.info(
                "Duplicate Evernote call detected: slug=%s, hash=%s, age=%.1fs",
                slug, call_hash[:8], now - timestamp,
            )
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


class EvernoteToolHandler:
    """Handles Evernote intent parsing and action execution."""

    def __init__(
        self,
        slug: str,
        description: str,
        config: Dict[str, Any],
        *,
        oauth_service: Any = None,
    ) -> None:
        self.slug = slug
        self.description = description
        self.config = config
        self._oauth_service = oauth_service
        self._sandbox = bool(config.get("sandbox", False))

    # ------------------------------------------------------------------
    # Client acquisition
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_client_id(metadata: Optional[Dict[str, Any]]) -> str:
        if not isinstance(metadata, dict):
            raise EvernoteAbilityConfigError(
                "Unable to determine client context for Evernote ability."
            )
        candidates = [
            metadata.get("client_id"),
            metadata.get("clientId"),
        ]
        client_obj = metadata.get("client")
        if isinstance(client_obj, dict):
            candidates.append(client_obj.get("id"))
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        raise EvernoteAbilityConfigError(
            "Unable to determine client context for Evernote ability."
        )

    async def _acquire_client(
        self,
        metadata: Optional[Dict[str, Any]],
        *,
        force_refresh: bool = False,
    ) -> Tuple[EvernoteClient, Optional[str]]:
        token: Optional[str] = None
        resolved_client_id: Optional[str] = None

        if self._oauth_service is not None:
            resolved_client_id = self._extract_client_id(metadata)
            bundle = await self._oauth_service.ensure_valid_token(
                resolved_client_id, force_refresh=force_refresh
            )
            if not bundle or not bundle.access_token:
                raise EvernoteAbilityConfigError(
                    "Evernote is not connected for this client yet. "
                    "Ask an administrator to complete the OAuth connection in Settings."
                )
            token = bundle.access_token
        else:
            token = str(self.config.get("access_token") or "").strip()
            if not token:
                raise EvernoteAbilityConfigError(
                    "Evernote ability requires an OAuth connection. "
                    "Ask an administrator to connect Evernote for this client."
                )

        return EvernoteClient(token, sandbox=self._sandbox), resolved_client_id

    # ------------------------------------------------------------------
    # Intent parsing
    # ------------------------------------------------------------------

    def _interpret_intent(self, message: str) -> Tuple[str, Dict[str, Any]]:
        lower = message.lower()

        # Check if message is Evernote-related at all
        evernote_keywords = (
            "evernote", "note", "notes", "notebook", "notebooks",
            "write a note", "save a note", "read my note", "check my note",
            "jot down", "memo", "update note", "create note", "find note",
            "search note", "look up note", "my notes",
        )
        is_evernote_related = any(kw in lower for kw in evernote_keywords)

        if not is_evernote_related:
            return ("skip", {"raw": message, "lower": lower})

        # Determine action priority
        if any(w in lower for w in ("search", "find", "look up", "look for", "search for")):
            action = "search_notes"
        elif any(w in lower for w in ("read", "open", "show", "view", "get", "fetch", "check")):
            # Could be reading a note or listing notebooks
            if any(w in lower for w in ("notebook", "notebooks")):
                action = "list_notebooks"
            else:
                action = "read_note"
        elif any(w in lower for w in ("list", "all notebooks", "my notebooks", "show notebooks")):
            action = "list_notebooks"
        elif any(w in lower for w in ("update", "edit", "change", "modify", "append")):
            action = "update_note"
        elif any(w in lower for w in ("create", "new note", "write", "add note", "jot", "save", "make a note")):
            action = "create_note"
        elif "notebook" in lower:
            action = "list_notebooks"
        elif "note" in lower:
            # Default to search if they mention notes generically
            action = "search_notes"
        else:
            action = "search_notes"

        details: Dict[str, Any] = {
            "raw": message,
            "lower": lower,
        }

        # Extract quoted strings (note titles, search queries)
        quoted = QUOTE_PATTERN.findall(message)
        if quoted:
            details["quoted_text"] = quoted[0].strip()

        # Extract notebook name from "in <notebook>" pattern
        notebook_match = re.search(r"(?:in|from|to)\s+(?:the\s+)?[\"']?(\w[\w\s]+?)[\"']?\s*(?:notebook|$)", lower)
        if notebook_match:
            details["notebook_name"] = notebook_match.group(1).strip()

        # Extract note title from "called/named/titled <X>" pattern
        title_match = re.search(r"(?:called|named|titled|about)\s+[\"']?(.+?)(?:[\"']|\s*$)", message, re.IGNORECASE)
        if title_match and "quoted_text" not in details:
            details["note_title"] = title_match.group(1).strip().rstrip(".")

        # Extract content for create/update — text after "saying/with content/containing"
        content_match = re.search(
            r"(?:saying|with content|containing|that says|body)\s*[:\-]?\s*(.+)",
            message,
            re.IGNORECASE | re.DOTALL,
        )
        if content_match:
            details["content"] = content_match.group(1).strip()

        return action, details

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    async def _handle_list_notebooks(self, client: EvernoteClient, details: Dict[str, Any]) -> str:
        notebooks = await client.list_notebooks()
        if not notebooks:
            return "You don't have any notebooks in Evernote."

        lines = [f"Found {len(notebooks)} notebook(s) in Evernote:\n"]
        for nb in notebooks:
            name = nb.get("name", "Untitled")
            guid = nb.get("guid", "")
            lines.append(f"  - {name} (id: {guid})")
        return "\n".join(lines)

    async def _handle_search_notes(self, client: EvernoteClient, details: Dict[str, Any]) -> str:
        query = details.get("quoted_text") or details.get("note_title") or details.get("raw", "")
        # Clean up the query — remove common prefixes
        for prefix in ("search evernote for", "find note", "search notes for", "find in evernote", "look up"):
            if query.lower().startswith(prefix):
                query = query[len(prefix):].strip()

        if not query.strip():
            return "Please specify what to search for in Evernote."

        notes = await client.search_notes(query, max_results=10)
        if not notes:
            return f"No notes found matching '{query}'."

        lines = [f"Found {len(notes)} note(s) matching '{query}':\n"]
        for note in notes:
            title = note.get("title", "Untitled")
            guid = note.get("guid", "")
            updated = note.get("updated", "")
            lines.append(f"  - {title} (id: {guid}, updated: {updated})")
        return "\n".join(lines)

    async def _handle_read_note(self, client: EvernoteClient, details: Dict[str, Any]) -> str:
        # Try to find the note by title search
        title = details.get("quoted_text") or details.get("note_title") or ""
        if not title:
            # Try to extract from raw message
            raw = details.get("raw", "")
            for prefix in ("read note", "show note", "open note", "get note", "check note", "view note"):
                if raw.lower().startswith(prefix):
                    title = raw[len(prefix):].strip()
                    break
            if not title:
                title = raw

        if not title.strip():
            return "Please specify which note to read."

        notes = await client.search_notes(title, max_results=5)
        if not notes:
            return f"No note found matching '{title}'."

        # Get the first match with content
        note_guid = notes[0].get("guid")
        if not note_guid:
            return f"Found a note matching '{title}' but couldn't retrieve it."

        note = await client.get_note(note_guid, include_content=True)
        note_title = note.get("title", "Untitled")
        content = note.get("content", "")
        plain_text = EvernoteClient.enml_to_text(content) if content else "(empty)"

        return f"**{note_title}**\n\n{plain_text}"

    async def _handle_create_note(self, client: EvernoteClient, details: Dict[str, Any]) -> str:
        title = details.get("quoted_text") or details.get("note_title") or "Untitled Note"
        content = details.get("content", "")

        if not content:
            # Use the raw message minus the command parts as content
            raw = details.get("raw", "")
            content = raw

        result = await client.create_note(title, content)
        note_title = result.get("title", title)
        guid = result.get("guid", "")
        return f"Created note '{note_title}' (id: {guid}) in Evernote."

    async def _handle_update_note(self, client: EvernoteClient, details: Dict[str, Any]) -> str:
        title = details.get("quoted_text") or details.get("note_title") or ""
        if not title:
            return "Please specify which note to update."

        # Find the note first
        notes = await client.search_notes(title, max_results=5)
        if not notes:
            return f"No note found matching '{title}' to update."

        note_guid = notes[0].get("guid")
        if not note_guid:
            return f"Found a note matching '{title}' but couldn't retrieve it."

        new_content = details.get("content")
        new_title = None

        # Check if user wants to rename
        rename_match = re.search(r"rename\s+to\s+[\"']?(.+?)[\"']?\s*$", details.get("raw", ""), re.IGNORECASE)
        if rename_match:
            new_title = rename_match.group(1).strip()

        if not new_content and not new_title:
            return f"Found note '{title}' but no new content or title was provided. What would you like to change?"

        result = await client.update_note(
            note_guid,
            title=new_title,
            content=new_content,
        )
        updated_title = result.get("title", title)
        return f"Updated note '{updated_title}' in Evernote."

    # ------------------------------------------------------------------
    # Main invoke
    # ------------------------------------------------------------------

    async def _run_action(self, client: EvernoteClient, action: str, details: Dict[str, Any]) -> str:
        if action == "skip":
            return ""
        if action == "list_notebooks":
            return await self._handle_list_notebooks(client, details)
        if action == "search_notes":
            return await self._handle_search_notes(client, details)
        if action == "read_note":
            return await self._handle_read_note(client, details)
        if action == "create_note":
            return await self._handle_create_note(client, details)
        if action == "update_note":
            return await self._handle_update_note(client, details)
        return "Evernote ability is ready. Ask me to list notebooks, search notes, read a note, create or update one."

    async def invoke(self, *, user_inquiry: str, metadata: Optional[Dict[str, Any]] = None, **_: Any) -> str:
        if not user_inquiry or not isinstance(user_inquiry, str):
            raise ValueError("Evernote ability requires a user inquiry string.")

        action, details = self._interpret_intent(user_inquiry)
        logger.info("Evernote intent resolved", extra={"slug": self.slug, "action": action, "details": details})

        if action == "skip":
            return "This inquiry doesn't appear to be related to Evernote. No action was taken."

        try:
            client, client_id = await self._acquire_client(metadata)
        except EvernoteAbilityConfigError as exc:
            logger.warning("Evernote client acquisition failed", exc_info=False)
            return str(exc)
        except Exception as exc:
            logger.exception("Unexpected error acquiring Evernote client")
            return f"Failed to connect to Evernote: {exc}"

        refresh_attempted = False
        while True:
            try:
                return await self._run_action(client, action, details)
            except EvernoteAPIError as exc:
                status = getattr(exc, "status_code", None)
                if (
                    self._oauth_service
                    and client_id
                    and not refresh_attempted
                    and status in {401, 403}
                ):
                    refresh_attempted = True
                    logger.info("Evernote token rejected with status %s; forcing refresh", status)
                    try:
                        client, _ = await self._acquire_client(metadata, force_refresh=True)
                        continue
                    except EvernoteAbilityConfigError:
                        return "Evernote connection expired. Please reconnect Evernote from your dashboard."
                    except Exception:
                        logger.exception("Forced Evernote token refresh failed")
                        return "Evernote connection could not be refreshed. Please reconnect."
                logger.error("Evernote API call failed", exc_info=True)
                return f"Evernote API error: {exc}"
            except Exception as exc:
                logger.exception("Evernote ability execution failed")
                return f"Evernote ability failed: {exc}"


# ------------------------------------------------------------------
# Build function (called from ToolRegistry)
# ------------------------------------------------------------------

def build_evernote_tool(
    tool_def: Dict[str, Any],
    config: Dict[str, Any],
    *,
    oauth_service: Any = None,
) -> Any:
    slug = tool_def.get("slug") or tool_def.get("name") or "evernote_notes"
    description = tool_def.get("description") or (
        "Access and manage Evernote notes and notebooks. "
        "Use this when the user explicitly asks to check, read, search, create, or update Evernote notes or notebooks. "
        "Read the tool's complete output to the user."
    )

    handler = EvernoteToolHandler(
        slug=slug,
        description=description,
        config=config,
        oauth_service=oauth_service,
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
            return "I wasn't able to determine what to do with Evernote. Please repeat the request."

        # Check for duplicate call
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
                            "For example: 'search my Evernote notes for meeting minutes', "
                            "'create a note called Project Plan with content...', "
                            "'list my Evernote notebooks'. REQUIRED."
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
