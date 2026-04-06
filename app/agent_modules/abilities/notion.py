"""Notion ability — search, query, create, and manage Notion databases and pages."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple

from livekit.agents.llm.tool_context import function_tool as lk_function_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Deduplication (same pattern as Trello)
# ---------------------------------------------------------------------------

_TOOL_CALL_HISTORY: Dict[str, Deque] = {}
_DEDUP_WINDOW = 10

QUOTE_PATTERN = re.compile(r"""["'\u201c\u201d\u2018\u2019](.+?)["'\u201c\u201d\u2018\u2019]""")

_INQUIRY_CANDIDATE_KEYS = (
    "user_inquiry", "latest_user_text", "user_text", "text",
    "query", "message", "input", "prompt",
)


def _compute_call_hash(slug: str, user_inquiry: str) -> str:
    return hashlib.md5(f"{slug}:{user_inquiry.strip().lower()}".encode()).hexdigest()


def _check_duplicate_call(slug: str, call_hash: str) -> Optional[Dict[str, Any]]:
    history = _TOOL_CALL_HISTORY.get(slug)
    if not history:
        return None
    now = time.time()
    for h, ts, result in history:
        if h == call_hash and (now - ts) < _DEDUP_WINDOW:
            logger.info("Notion dedup hit for %s (hash=%s)", slug, call_hash[:8])
            return result
    return None


def _record_call(slug: str, call_hash: str, result: Dict[str, Any]) -> None:
    history = _TOOL_CALL_HISTORY.setdefault(slug, deque(maxlen=20))
    history.append((call_hash, time.time(), result))


def _coalesce_user_inquiry(metadata: Dict[str, Any], fallback: Any = None) -> Optional[str]:
    for key in _INQUIRY_CANDIDATE_KEYS:
        val = metadata.get(key) if isinstance(metadata, dict) else None
        if isinstance(val, str) and val.strip():
            return val.strip()
    if isinstance(fallback, dict):
        for key in _INQUIRY_CANDIDATE_KEYS:
            val = fallback.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


# ---------------------------------------------------------------------------
# Exceptions & config
# ---------------------------------------------------------------------------

class NotionAbilityConfigError(ValueError):
    pass


@dataclass
class DatabaseConfig:
    id: str
    name: Optional[str] = None


# ---------------------------------------------------------------------------
# Notion property helpers
# ---------------------------------------------------------------------------

def _extract_title(page: Dict[str, Any]) -> str:
    """Extract the title string from a Notion page's properties."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            title_arr = prop.get("title", [])
            return "".join(t.get("plain_text", "") for t in title_arr)
    return "Untitled"


def _format_property(prop: Dict[str, Any]) -> str:
    """Format a Notion property value to a readable string."""
    ptype = prop.get("type", "")
    if ptype == "title":
        return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    elif ptype == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
    elif ptype == "number":
        val = prop.get("number")
        return str(val) if val is not None else ""
    elif ptype == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    elif ptype == "multi_select":
        return ", ".join(s.get("name", "") for s in prop.get("multi_select", []))
    elif ptype == "date":
        date = prop.get("date")
        if not date:
            return ""
        start = date.get("start", "")
        end = date.get("end")
        return f"{start} - {end}" if end else start
    elif ptype == "checkbox":
        return "Yes" if prop.get("checkbox") else "No"
    elif ptype == "url":
        return prop.get("url", "") or ""
    elif ptype == "email":
        return prop.get("email", "") or ""
    elif ptype == "phone_number":
        return prop.get("phone_number", "") or ""
    elif ptype == "status":
        status = prop.get("status")
        return status.get("name", "") if status else ""
    elif ptype == "people":
        return ", ".join(p.get("name", "Unknown") for p in prop.get("people", []))
    elif ptype == "relation":
        return f"({len(prop.get('relation', []))} linked)"
    elif ptype == "formula":
        formula = prop.get("formula", {})
        ftype = formula.get("type", "")
        return str(formula.get(ftype, ""))
    elif ptype == "rollup":
        rollup = prop.get("rollup", {})
        rtype = rollup.get("type", "")
        return str(rollup.get(rtype, ""))
    return ""


def _format_page_summary(page: Dict[str, Any], *, max_props: int = 6) -> str:
    """One-line summary of a Notion page with key properties."""
    title = _extract_title(page)
    props = page.get("properties", {})
    parts = [f"**{title}**"]
    count = 0
    for name, prop in props.items():
        if prop.get("type") == "title":
            continue
        val = _format_property(prop)
        if val:
            parts.append(f"{name}: {val}")
            count += 1
            if count >= max_props:
                break
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------

class NotionToolHandler:
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

        self.databases: List[DatabaseConfig] = []
        self.database_by_id: Dict[str, DatabaseConfig] = {}
        raw_dbs = config.get("databases") or []
        if isinstance(raw_dbs, list):
            for entry in raw_dbs:
                if isinstance(entry, str):
                    dc = DatabaseConfig(id=entry)
                elif isinstance(entry, dict):
                    dc = DatabaseConfig(id=entry.get("id", ""), name=entry.get("name"))
                else:
                    continue
                if dc.id:
                    self.databases.append(dc)
                    self.database_by_id[dc.id] = dc

    # ------------------------------------------------------------------
    # Client acquisition
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_client_id(metadata: Optional[Dict[str, Any]]) -> str:
        if not isinstance(metadata, dict):
            raise NotionAbilityConfigError("Unable to determine client context for Notion ability.")
        candidates = [metadata.get("client_id"), metadata.get("clientId")]
        client_obj = metadata.get("client")
        if isinstance(client_obj, dict):
            candidates.append(client_obj.get("id"))
        for c in candidates:
            if isinstance(c, str) and c.strip():
                return c.strip()
        raise NotionAbilityConfigError("Unable to determine client context for Notion ability.")

    async def _acquire_client(self, metadata: Optional[Dict[str, Any]]):
        from app.services.notion_service import NotionClient
        if self._auth_service is not None:
            client_id = self._extract_client_id(metadata)
            bundle = self._auth_service.get_token_bundle(client_id)
            if not bundle or not bundle.access_token:
                raise NotionAbilityConfigError(
                    "Notion is not connected for this client. "
                    "Ask an administrator to connect Notion in Settings."
                )
            return NotionClient(bundle.access_token), client_id

        access_token = self.config.get("access_token", "")
        if not access_token:
            raise NotionAbilityConfigError(
                "Notion ability requires an access token. "
                "Connect Notion for this client in Settings."
            )
        return NotionClient(access_token), None

    # ------------------------------------------------------------------
    # Intent parsing
    # ------------------------------------------------------------------

    def _interpret_intent(self, message: str) -> Tuple[str, Dict[str, Any]]:
        lower = message.lower()

        notion_keywords = (
            "notion", "database", "databases", "page", "pages",
            "workspace", "entry", "entries",
        )
        is_notion_related = any(kw in lower for kw in notion_keywords)
        if not is_notion_related:
            return ("skip", {"raw": message, "lower": lower})

        if any(w in lower for w in ("search", "find", "look up", "look for", "search for")):
            action = "search"
        elif any(w in lower for w in ("archive", "trash", "delete page")):
            action = "archive_page"
        elif any(w in lower for w in ("update", "edit", "change", "modify", "set status", "mark as", "set property")):
            action = "update_page"
        elif any(w in lower for w in ("create", "add entry", "new page", "add page", "add to database", "new entry", "add a")):
            action = "create_page"
        elif any(w in lower for w in ("append", "add content", "write to", "add to page")):
            action = "add_content"
        elif any(w in lower for w in ("show databases", "list databases", "my databases", "all databases")):
            action = "list_databases"
        elif any(w in lower for w in ("query", "filter", "show entries", "list entries", "what's in", "whats in")):
            action = "query_database"
        elif any(w in lower for w in ("show page", "read page", "get page", "open page")):
            action = "get_page"
        elif any(w in lower for w in ("check", "status", "overview", "summary", "what do i have", "what's on", "whats on")):
            action = "overview"
        else:
            action = "overview" if self.databases else "list_databases"

        details: Dict[str, Any] = {"raw": message, "lower": lower}

        quoted = QUOTE_PATTERN.findall(message)
        if quoted:
            details["quoted_text"] = quoted[0].strip()
            if len(quoted) > 1:
                details["quoted_texts"] = [q.strip() for q in quoted]

        db_match = re.search(
            r"(?:in|from|on|to)\s+(?:the\s+)?(?:database\s+)?[\"']?([A-Za-z][\w\s\-]+?)[\"']?\s*(?:database|$)",
            lower,
        )
        if db_match:
            details["database_name"] = db_match.group(1).strip()

        title_match = re.search(
            r"(?:called|named|titled)\s+[\"']?(.+?)(?:[\"']|\s*$)", message, re.IGNORECASE
        )
        if title_match:
            details["page_title"] = title_match.group(1).strip().rstrip(".")

        return action, details

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    async def _handle_overview(self, client, details: Dict[str, Any]) -> str:
        requested_db_name = details.get("database_name", "").lower().strip()
        all_databases = await client.list_databases(page_size=20)

        if requested_db_name:
            matching = [d for d in all_databases if requested_db_name in _extract_title(d).lower()]
            databases = matching if matching else all_databases[:10]
        else:
            databases = all_databases[:10]

        if not databases:
            return "No databases found in your Notion workspace."

        lines = ["Here's a summary of your Notion databases:\n"]
        for db in databases:
            title = _extract_title(db)
            db_id = db.get("id", "")
            try:
                result = await client.query_database(db_id, page_size=5)
                pages = result.get("results", [])
                lines.append(f"**{title}** ({len(pages)} recent entries):")
                for page in pages[:5]:
                    lines.append(f"  - {_format_page_summary(page)}")
            except Exception:
                lines.append(f"**{title}** (unable to query)")
            lines.append("")
        return "\n".join(lines)

    async def _handle_list_databases(self, client, details: Dict[str, Any]) -> str:
        databases = await client.list_databases(page_size=20)
        if not databases:
            return "No databases found in your Notion workspace."
        lines = ["Your Notion databases:\n"]
        for db in databases:
            title = _extract_title(db)
            url = db.get("url", "")
            lines.append(f"- **{title}**" + (f" ({url})" if url else ""))
        return "\n".join(lines)

    async def _handle_search(self, client, details: Dict[str, Any]) -> str:
        query = details.get("quoted_text") or details.get("raw", "")
        query = re.sub(r"(?i)search\s+(notion\s+)?(for\s+)?", "", query).strip()
        if not query:
            return "Please specify what to search for in Notion."
        result = await client.search(query, page_size=10)
        items = result.get("results", [])
        if not items:
            return f'No results found in Notion for "{query}".'
        lines = [f'Notion search results for "{query}":\n']
        for item in items[:10]:
            obj_type = item.get("object", "page")
            title = _extract_title(item)
            url = item.get("url", "")
            lines.append(f"- [{obj_type.capitalize()}] **{title}**" + (f" ({url})" if url else ""))
        return "\n".join(lines)

    async def _handle_query_database(self, client, details: Dict[str, Any]) -> str:
        db_name = details.get("database_name", "").lower().strip()
        if not db_name:
            return "Please specify which database to query (e.g., 'show entries in Tasks database')."
        all_databases = await client.list_databases(page_size=20)
        matching = [d for d in all_databases if db_name in _extract_title(d).lower()]
        if not matching:
            available = ", ".join(_extract_title(d) for d in all_databases[:10])
            return f'Database "{db_name}" not found. Available databases: {available}'
        db = matching[0]
        db_title = _extract_title(db)
        result = await client.query_database(db["id"], page_size=20)
        pages = result.get("results", [])
        if not pages:
            return f'No entries found in "{db_title}".'
        lines = [f'Entries in **{db_title}** ({len(pages)} shown):\n']
        for page in pages:
            lines.append(f"  - {_format_page_summary(page)}")
        return "\n".join(lines)

    async def _handle_create_page(self, client, details: Dict[str, Any]) -> str:
        db_name = details.get("database_name", "").lower().strip()
        page_title = details.get("page_title") or details.get("quoted_text") or ""
        if not db_name:
            return "Please specify which database to create an entry in (e.g., 'create page called Meeting Notes in Tasks database')."
        if not page_title:
            return "Please specify a title for the new page (e.g., 'create page called Meeting Notes')."
        all_databases = await client.list_databases(page_size=20)
        matching = [d for d in all_databases if db_name in _extract_title(d).lower()]
        if not matching:
            return f'Database "{db_name}" not found.'
        db = matching[0]
        db_schema = await client.get_database(db["id"])
        title_prop_name = None
        for name, prop in db_schema.get("properties", {}).items():
            if prop.get("type") == "title":
                title_prop_name = name
                break
        if not title_prop_name:
            return "Could not find a title property in this database."
        properties = {
            title_prop_name: {"title": [{"text": {"content": page_title}}]}
        }
        result = await client.create_page(db["id"], properties)
        url = result.get("url", "")
        return f'Created page **{page_title}** in {_extract_title(db)}.' + (f" ({url})" if url else "")

    async def _handle_update_page(self, client, details: Dict[str, Any]) -> str:
        return "To update a Notion page, I need the page name and what property to change. Please be more specific (e.g., 'set status to Done on page Meeting Notes')."

    async def _handle_get_page(self, client, details: Dict[str, Any]) -> str:
        page_title = details.get("page_title") or details.get("quoted_text") or ""
        if not page_title:
            return "Please specify which page to read (e.g., 'show page Meeting Notes')."
        result = await client.search(page_title, filter_type="page", page_size=5)
        items = result.get("results", [])
        if not items:
            return f'No page found matching "{page_title}".'
        page = items[0]
        title = _extract_title(page)
        lines = [f"**{title}**\n"]
        props = page.get("properties", {})
        for name, prop in props.items():
            if prop.get("type") == "title":
                continue
            val = _format_property(prop)
            if val:
                lines.append(f"- **{name}**: {val}")
        return "\n".join(lines)

    async def _handle_archive_page(self, client, details: Dict[str, Any]) -> str:
        page_title = details.get("page_title") or details.get("quoted_text") or ""
        if not page_title:
            return "Please specify which page to archive (e.g., 'archive page Meeting Notes')."
        result = await client.search(page_title, filter_type="page", page_size=5)
        items = result.get("results", [])
        if not items:
            return f'No page found matching "{page_title}".'
        page = items[0]
        title = _extract_title(page)
        await client.archive_page(page["id"])
        return f'Archived page **{title}**.'

    async def _handle_add_content(self, client, details: Dict[str, Any]) -> str:
        return "To add content to a Notion page, please specify the page and what to add (e.g., 'add a paragraph to page Meeting Notes saying: Action items discussed')."

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------

    async def invoke(self, *, user_inquiry: str, metadata: Optional[Dict[str, Any]] = None, **_: Any) -> str:
        if not user_inquiry or not isinstance(user_inquiry, str):
            raise ValueError("Notion ability requires a user inquiry string.")

        action, details = self._interpret_intent(user_inquiry)
        logger.info("Notion intent resolved", extra={"slug": self.slug, "action": action, "details": details})

        if action == "skip":
            return "This inquiry doesn't appear to be related to Notion. No action was taken."

        try:
            client, client_id = await self._acquire_client(metadata)
        except NotionAbilityConfigError as exc:
            logger.warning("Notion client acquisition failed", exc_info=False)
            return str(exc)
        except Exception as exc:
            logger.exception("Unexpected error acquiring Notion client")
            return f"Failed to connect to Notion: {exc}"

        handler_map = {
            "overview": self._handle_overview,
            "list_databases": self._handle_list_databases,
            "search": self._handle_search,
            "query_database": self._handle_query_database,
            "create_page": self._handle_create_page,
            "update_page": self._handle_update_page,
            "get_page": self._handle_get_page,
            "archive_page": self._handle_archive_page,
            "add_content": self._handle_add_content,
        }

        try:
            handler = handler_map.get(action)
            if handler:
                return await handler(client, details)
            return "Notion ability is ready. Ask me to list databases, search, query, create, or archive pages."
        except Exception as exc:
            from app.services.notion_service import NotionAPIError
            if isinstance(exc, NotionAPIError):
                status = getattr(exc, "status_code", None)
                logger.error("Notion API error: status=%s, message=%s", status, exc)
                if status == 429:
                    return "Notion rate limit reached. Please wait a moment and try again."
                if status in {401, 403}:
                    return "Notion connection was rejected. Please reconnect Notion from your dashboard."
                return f"Notion API error: {exc}"
            logger.exception("Unexpected error in Notion action %s", action)
            return f"An error occurred while accessing Notion: {exc}"


# ---------------------------------------------------------------------------
# Builder (called from ToolRegistry)
# ---------------------------------------------------------------------------

def build_notion_tool(
    tool_def: Dict[str, Any],
    config: Dict[str, Any],
    *,
    auth_service: Any = None,
) -> Any:
    slug = tool_def.get("slug") or tool_def.get("name") or "notion_workspace"
    description = tool_def.get("description") or (
        "Access and manage Notion databases and pages. "
        "Use this when the user asks to search, query, create, update, or archive Notion content. "
        "Read the tool's complete output to the user."
    )

    handler = NotionToolHandler(
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
            return "I wasn't able to determine what to do with Notion. Please repeat the request."

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
                            "For example: 'search Notion for meeting notes', "
                            "'show entries in Tasks database', "
                            "'create page called Launch Plan in Projects database'. REQUIRED."
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
