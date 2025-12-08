from __future__ import annotations

import logging
import re
import time
import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from collections import deque
import ast
import json
from typing import Any, Dict, List, Optional, Tuple, Deque

from livekit.agents.llm.tool_context import function_tool as lk_function_tool

from app.integrations.asana_client import AsanaAPIError, AsanaClient


logger = logging.getLogger(__name__)

# Global tool call deduplication tracker
# Format: {tool_slug: deque([(call_hash, timestamp, result), ...])}
_TOOL_CALL_HISTORY: Dict[str, Deque[Tuple[str, float, Any]]] = {}
_DEDUPE_WINDOW_SECONDS = 10.0  # Dedupe window: 10 seconds
_MAX_HISTORY_SIZE = 50  # Keep last 50 calls per tool


QUOTE_PATTERN = re.compile(r"[\"“”‘’']([^\"“”‘’']+)[\"“”‘’']")
ISO_DATE_PATTERN = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
SLASH_DATE_PATTERN = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?\b")
TASK_GID_PATTERN = re.compile(r"\b(\d{10,})\b")

DEFAULT_OPT_FIELDS = [
    "gid",
    "name",
    "completed",
    "due_on",
    "due_at",
    "permalink_url",
    "assignee.name",
    "memberships.project.name",
]

STRUCTURED_NAME_KEYS = ("task_name", "taskName", "title", "name", "task", "task_title", "taskTitle")
STRUCTURED_NOTES_KEYS = ("notes", "description", "details", "body", "comment")
STRUCTURED_DUE_KEYS = ("due_on", "dueOn", "due_date", "dueDate", "due")

_INQUIRY_CANDIDATE_KEYS = (
    "user_inquiry",
    "userInquiry",
    "latest_user_text",
    "latestUserText",
    "user_text",
    "userText",
    "last_user_message",
    "lastUserMessage",
    "user_message",
    "userMessage",
    "query",
    "question",
    "prompt",
    "text",
    "message",
    "transcript",
    "utterance",
    "final_transcript",
)


def _find_text_candidate(value: Any, *, depth: int = 0, max_depth: int = 3) -> Optional[str]:
    if depth > max_depth or value is None:
        return None

    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None

    if isinstance(value, dict):
        for key in _INQUIRY_CANDIDATE_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str):
                candidate = candidate.strip()
                if candidate:
                    return candidate
        for nested in value.values():
            candidate = _find_text_candidate(nested, depth=depth + 1, max_depth=max_depth)
            if candidate:
                return candidate
        return None

    if isinstance(value, list):
        for item in value:
            candidate = _find_text_candidate(item, depth=depth + 1, max_depth=max_depth)
            if candidate:
                return candidate
    return None


def _coalesce_user_inquiry(metadata: Optional[Dict[str, Any]], messages: Any) -> Optional[str]:
    candidate = None
    if isinstance(metadata, dict):
        candidate = _find_text_candidate(metadata)
        if candidate:
            return candidate

    if isinstance(messages, list):
        for entry in reversed(messages):
            if not isinstance(entry, dict):
                continue
            role = entry.get("role")
            if role not in {"user", "system"}:
                continue
            content = entry.get("content")
            if isinstance(content, str):
                candidate = content.strip()
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_value = part.get("text")
                        if isinstance(text_value, str) and text_value.strip():
                            candidate = text_value.strip()
                            break
            if candidate:
                return candidate
    return None


class AsanaAbilityConfigError(ValueError):
    """Raised when the Asana ability is misconfigured."""


@dataclass
class ProjectConfig:
    gid: str
    name: Optional[str] = None


class AsanaToolHandler:
    def __init__(
        self,
        *,
        slug: str,
        description: Optional[str],
        config: Dict[str, Any],
        oauth_service: Any = None,
        client_factory: Optional[Any] = None,
    ) -> None:
        self.slug = slug
        self.description = description or "Interact with Asana tasks."
        self.config = config
        self._oauth_service = oauth_service
        self._timeout = self._coerce_float(config.get("timeout"), default=15.0)

        if client_factory is not None:
            self._client_factory = client_factory
        else:
            self._client_factory = lambda token: AsanaClient(access_token=token, timeout=self._timeout)

        self.workspace_gid: Optional[str] = self._coerce_optional_str(config.get("workspace_gid"))
        self.default_assignee: Optional[str] = self._coerce_optional_str(config.get("default_assignee"))
        self.max_tasks_per_project: int = self._coerce_int(config.get("max_tasks_per_project"), default=10, minimum=1, maximum=50)
        self.lookup_limit: int = self._coerce_int(config.get("lookup_limit"), default=max(self.max_tasks_per_project, 25), minimum=5, maximum=100)
        self.include_completed_in_lists: bool = self._coerce_bool(config.get("list_include_completed"), default=False)
        self.default_action: str = str(config.get("default_action") or "list").lower()

        projects_raw = config.get("projects") or config.get("project_gids")
        self.projects: List[ProjectConfig] = self._normalize_projects(projects_raw)
        if not self.projects:
            raise AsanaAbilityConfigError(
                "Asana ability requires at least one project gid in config.projects"
            )
        self.default_project = self.projects[0]
        self.project_lookup = {self._safe_lower(p.name): p for p in self.projects if p.name}
        self.project_by_gid = {p.gid: p for p in self.projects}

        opt_fields = config.get("opt_fields")
        if isinstance(opt_fields, list) and opt_fields:
            self.opt_fields = [str(f) for f in opt_fields]
        else:
            self.opt_fields = DEFAULT_OPT_FIELDS

    @staticmethod
    def _coerce_float(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_optional_str(value: Any) -> Optional[str]:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            trimmed = value.strip().lower()
            if trimmed in {"true", "1", "yes", "on"}:
                return True
            if trimmed in {"false", "0", "no", "off"}:
                return False
        return bool(value)

    @staticmethod
    def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            result = default
        if result < minimum:
            return minimum
        if result > maximum:
            return maximum
        return result

    @staticmethod
    def _normalize_projects(value: Any) -> List[ProjectConfig]:
        projects: List[ProjectConfig] = []
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, str):
                    gid = entry.strip()
                    if gid:
                        projects.append(ProjectConfig(gid=gid))
                elif isinstance(entry, dict):
                    gid = str(entry.get("gid") or entry.get("id") or "").strip()
                    if gid:
                        name = entry.get("name")
                        if isinstance(name, str):
                            name = name.strip()
                        else:
                            name = None
                        projects.append(ProjectConfig(gid=gid, name=name or None))
        elif isinstance(value, dict):
            for gid, name in value.items():
                gid_str = str(gid).strip()
                if gid_str:
                    name_str = str(name).strip() if name else None
                    projects.append(ProjectConfig(gid=gid_str, name=name_str or None))
        elif isinstance(value, str) and value.strip():
            projects.append(ProjectConfig(gid=value.strip()))
        return projects

    @staticmethod
    def _safe_lower(value: Optional[str]) -> Optional[str]:
        return value.lower() if isinstance(value, str) else None

    @staticmethod
    def _extract_client_id(metadata: Optional[Dict[str, Any]]) -> str:
        if not isinstance(metadata, dict):
            raise AsanaAbilityConfigError(
                "Unable to determine client context for Asana ability. Please retry after selecting a client."
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
        raise AsanaAbilityConfigError(
            "Unable to determine client context for Asana ability. Please retry after selecting a client."
        )

    async def _acquire_client(
        self,
        metadata: Optional[Dict[str, Any]],
        *,
        force_refresh: bool = False,
    ) -> Tuple[AsanaClient, Optional[str]]:
        token: Optional[str] = None
        resolved_client_id: Optional[str] = None

        if self._oauth_service is not None:
            resolved_client_id = self._extract_client_id(metadata)
            bundle = await self._oauth_service.ensure_valid_token(resolved_client_id, force_refresh=force_refresh)
            if not bundle or not bundle.access_token:
                raise AsanaAbilityConfigError(
                    "Asana is not connected for this client yet. Ask an administrator to complete the OAuth connection."
                )
            token = bundle.access_token
        else:
            token = str(self.config.get("access_token") or "").strip()
            if not token:
                raise AsanaAbilityConfigError(
                    "Asana ability requires an OAuth connection. Ask an administrator to connect Asana for this client."
                )

        return self._client_factory(token), resolved_client_id

    async def _run_action(self, client: AsanaClient, action: str, details: Dict[str, Any]) -> str:
        # Handle skip action for non-Asana messages (text mode optimization)
        if action == "skip":
            return ""
        if details.get("is_subtask"):
            if action == "create":
                return await self._handle_create_subtask(client, details)
            if action == "update":
                return await self._handle_update_subtask(client, details)
            if action == "delete":
                return await self._handle_delete_subtask(client, details)
            if action == "complete":
                return await self._handle_complete_subtask(client, details)
        if action == "list":
            return await self._handle_list(client, details)
        if action == "create":
            return await self._handle_create(client, details)
        if action == "complete":
            return await self._handle_complete(client, details)
        if action == "delete_all":
            return await self._handle_delete_all(client, details)
        if action == "delete":
            return await self._handle_delete(client, details)
        if action == "update":
            return await self._handle_update(client, details)
        return "Asana ability is ready. Ask me to list tasks, create a task, update it, or remove it."

    async def invoke(self, *, user_inquiry: str, metadata: Optional[Dict[str, Any]] = None, **_: Any) -> str:
        if not user_inquiry or not isinstance(user_inquiry, str):
            raise ValueError("Asana ability requires a user inquiry string.")

        action, details = self._interpret_intent(user_inquiry)
        logger.info("Asana ability intent resolved", extra={"slug": self.slug, "action": action, "details": details})

        # Skip Asana client acquisition for non-Asana messages (text mode optimization)
        if action == "skip":
            # Return explicit message to prevent LLM hallucination
            # The LLM should understand this inquiry wasn't Asana-related and not make up results
            return "This inquiry doesn't appear to be related to Asana tasks. No action was taken."

        try:
            client, client_id = await self._acquire_client(metadata)
        except AsanaAbilityConfigError as exc:
            logger.warning("Asana client acquisition failed", exc_info=False)
            return str(exc)
        except Exception as exc:
            logger.exception("Unexpected error acquiring Asana client")
            return f"Failed to acquire Asana client: {exc}"

        refresh_attempted = False
        while True:
            try:
                return await self._run_action(client, action, details)
            except AsanaAPIError as exc:
                status = getattr(exc, "status_code", None)
                if (
                    self._oauth_service
                    and client_id
                    and not refresh_attempted
                    and status in {401, 403}
                ):
                    refresh_attempted = True
                    logger.info("Asana token rejected with status %s; forcing refresh", status)
                    try:
                        client, _ = await self._acquire_client(metadata, force_refresh=True)
                        continue
                    except AsanaAbilityConfigError:
                        return "Asana connection expired. Please reconnect Asana from your dashboard."
                    except Exception:
                        logger.exception("Forced Asana token refresh failed")
                        return "Asana connection could not be refreshed. Please reconnect Asana."
                logger.error("Asana API call failed", exc_info=True)
                return f"Asana API error: {exc}"
            except Exception as exc:
                logger.exception("Asana ability execution failed")
                return f"Asana ability failed: {exc}"

    # --- Intent parsing helpers -------------------------------------------------

    def _interpret_intent(self, message: str) -> Tuple[str, Dict[str, Any]]:
        lower = message.lower()

        # Check if message is Asana-related at all
        asana_keywords = (
            "asana", "task", "tasks", "todo", "to-do", "project", "assignment", 
            "assign", "due date", "complete", "finish", "delete", "remove", "create",
            "add task", "list task", "show task", "my task", "the task"
        )
        is_asana_related = any(keyword in lower for keyword in asana_keywords)
        
        # If clearly not Asana-related, return None action to skip execution
        if not is_asana_related:
            return ("skip", {"raw": message, "lower": lower})

        # Determine action priority: delete > complete > update > create > list
        if any(word in lower for word in ("delete", "remove", "trash", "archive task", "archive the task")):
            # Check if user wants to delete ALL tasks
            if any(phrase in lower for phrase in ("delete all", "remove all", "delete them all", "delete everything", "delete every task", "all the tasks", "all tasks")):
                action = "delete_all"
            else:
                action = "delete"
        elif ("complete" in lower) or ("finish" in lower) or ("mark" in lower and ("done" in lower or "complete" in lower)):
            action = "complete"
        elif ("update" in lower) or ("change" in lower) or ("modify" in lower) or ("set" in lower and "due" in lower):
            action = "update"
        elif any(word in lower for word in ("add", "create", "new task", "make a task", "open a task", "log a task")):
            action = "create"
        # Recognize implicit task creation from "I need to..." statements
        elif any(phrase in lower for phrase in ("i need to", "we need to", "i have to", "we have to", "i must", "we must", "i should", "we should")):
            action = "create"
        elif any(word in lower for word in ("list", "show", "what", "pending", "due", "where are we", "tasks on my list")):
            action = "list"
        elif ("check" in lower or "see" in lower or "view" in lower or "look at" in lower) and "task" in lower:
            action = "list"
        else:
            # If no clear query words and message seems like a task statement, default to create
            has_query_words = any(word in lower for word in ("what", "which", "how many", "show me", "tell me about"))
            if not has_query_words and len(lower.split()) > 3:  # Substantive statement, not a question
                action = "create"
            else:
                action = self.default_action if self.default_action in {"list", "create", "update", "complete", "delete"} else "list"

        details: Dict[str, Any] = {
            "raw": message,
            "lower": lower,
        }

        structured_details = self._extract_structured_details(message)
        for key, value in structured_details.items():
            if value and key not in details:
                details[key] = value

        quoted_names = QUOTE_PATTERN.findall(message)
        if quoted_names:
            details["task_name"] = quoted_names[-1].strip()

        project = self._extract_project(message)
        if project:
            details["project"] = project

        task_gid = self._extract_task_gid(message)
        if task_gid:
            details["task_gid"] = task_gid

        task_gids = TASK_GID_PATTERN.findall(message)
        if task_gids and "task_gid" not in details:
            details["task_gid"] = task_gids[0]

        if "task_name" not in details:
            task_name = self._extract_task_name(message)
            if task_name:
                details["task_name"] = task_name

        if "due_on" not in details:
            due_date = self._extract_due_date(message)
            if due_date:
                details["due_on"] = due_date

        new_name = self._extract_new_name(message)
        if new_name:
            details["new_name"] = new_name

        if "notes" not in details:
            notes = self._extract_notes(message)
            if notes:
                details["notes"] = notes

        if "subtask" in lower:
            details["is_subtask"] = True
            if quoted_names:
                details["subtask_name"] = quoted_names[0].strip()
                if len(quoted_names) > 1:
                    details["parent_task_name"] = quoted_names[-1].strip()
            if task_gids:
                details["task_gid"] = task_gids[0]
                if len(task_gids) > 1:
                    details["parent_task_gid"] = task_gids[1]
            if details.get("subtask_name"):
                details["task_name"] = details["subtask_name"]

        return action, details

    def _extract_project(self, message: str) -> Optional[ProjectConfig]:
        lower = message.lower()
        for project in self.projects:
            if project.name and project.name.lower() in lower:
                return project
        # Look for explicit gid mention
        for gid_match in TASK_GID_PATTERN.findall(message):
            project = self.project_by_gid.get(gid_match)
            if project:
                return project
        return None

    @staticmethod
    def _extract_task_gid(message: str) -> Optional[str]:
        match = TASK_GID_PATTERN.search(message)
        if match:
            return match.group(1)
        return None

    def _extract_structured_details(self, message: str) -> Dict[str, Any]:
        parsed = self._parse_structured_payload(message)
        if not parsed:
            parsed = self._parse_key_value_payload(message)
        if not parsed:
            return {}

        details: Dict[str, Any] = {}
        name_value = self._find_structured_value(parsed, STRUCTURED_NAME_KEYS)
        if name_value:
            sanitized = self._sanitize_task_name(name_value)
            if sanitized:
                details["task_name"] = sanitized

        notes_value = self._find_structured_value(parsed, STRUCTURED_NOTES_KEYS)
        if isinstance(notes_value, str) and notes_value.strip():
            details["notes"] = notes_value.strip()

        due_value = self._find_structured_value(parsed, STRUCTURED_DUE_KEYS)
        if isinstance(due_value, (str, int, float)):
            coerced = self._coerce_due_string(str(due_value))
            if coerced:
                details["due_on"] = coerced

        return details

    @staticmethod
    def _parse_structured_payload(message: str) -> Optional[Dict[str, Any]]:
        if not message:
            return None
        trimmed = message.strip()
        if not trimmed:
            return None
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(trimmed)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _parse_key_value_payload(message: str) -> Optional[Dict[str, Any]]:
        if not message:
            return None
        pairs: Dict[str, Any] = {}
        for raw_line in message.splitlines():
            line = raw_line.strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if key and value:
                pairs[key] = value
        return pairs or None

    def _find_structured_value(self, payload: Any, keys: Tuple[str, ...]) -> Optional[str]:
        key_set = {key.lower(): key for key in keys}

        def _walker(obj: Any) -> Optional[str]:
            if isinstance(obj, dict):
                for raw_key, value in obj.items():
                    lower_key = raw_key.lower()
                    if lower_key in key_set and isinstance(value, (str, int, float)):
                        return str(value)
                    found = _walker(value)
                    if found:
                        return found
            elif isinstance(obj, list):
                for item in obj:
                    found = _walker(item)
                    if found:
                        return found
            return None

        return _walker(payload)

    @staticmethod
    def _sanitize_task_name(value: Any) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        cleaned = re.sub(r"\s+", " ", value).strip().strip("\"'`")
        if not cleaned:
            return None
        if len(cleaned) > 200:
            cleaned = cleaned[:200].rstrip()
        return cleaned

    def _coerce_due_string(self, value: str) -> Optional[str]:
        candidate = value.strip()
        if not candidate:
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            return candidate
        synthetic = f"due {candidate}"
        return self._extract_due_date(synthetic)

    @staticmethod
    def _extract_task_name(message: str) -> Optional[str]:
        quoted = QUOTE_PATTERN.findall(message)
        if quoted:
            return quoted[-1].strip()
        # Fallback: extract after the first verb
        lowered = message.lower()
        for keyword in ("add", "create", "new task", "update", "complete", "delete", "remove", "mark"):
            idx = lowered.find(keyword)
            if idx != -1:
                candidate = message[idx + len(keyword) :].strip()
                if candidate:
                    # Remove leading stop words
                    candidate = re.sub(r"^(the|a|an|task)\s+", "", candidate, flags=re.IGNORECASE)
                    return candidate[:120].strip()
        return None

    @staticmethod
    def _extract_new_name(message: str) -> Optional[str]:
        lowered = message.lower()
        if "rename" not in lowered and "call it" not in lowered and "change it to" not in lowered:
            return None
        quoted = QUOTE_PATTERN.findall(message)
        if len(quoted) >= 2:
            return quoted[-1].strip()
        match = re.search(r"(?:call it|rename it|change it to)\s+(.+)", message, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _extract_notes(message: str) -> Optional[str]:
        match = re.search(r"(?:note|details)\s*[:\-]\s*(.+)", message, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    def _extract_due_date(self, message: str) -> Optional[str]:
        lower = message.lower()
        today = date.today()

        if "today" in lower:
            return today.isoformat()
        if "tomorrow" in lower:
            return (today + timedelta(days=1)).isoformat()
        if "next week" in lower:
            return (today + timedelta(days=7)).isoformat()

        weekday_map = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        for name, idx in weekday_map.items():
            token = f"next {name}"
            if token in lower:
                current = today.weekday()
                delta = (idx - current) % 7
                if delta == 0:
                    delta = 7
                return (today + timedelta(days=delta)).isoformat()

        iso_match = ISO_DATE_PATTERN.search(message)
        if iso_match:
            return iso_match.group(1)

        slash_match = SLASH_DATE_PATTERN.search(message)
        if slash_match:
            month = int(slash_match.group(1))
            day = int(slash_match.group(2))
            year = int(slash_match.group(3)) if slash_match.group(3) else today.year
            try:
                parsed = date(year, month, day)
                if parsed < today and slash_match.group(3) is None:
                    parsed = date(year + 1, month, day)
                return parsed.isoformat()
            except ValueError:
                return None

        return None

    # --- Action handlers -------------------------------------------------------

    async def _handle_list(self, client: AsanaClient, details: Dict[str, Any]) -> str:
        target_projects = [details["project"]] if details.get("project") else self.projects
        summaries: List[str] = []
        tasks_found = 0

        for project in target_projects:
            logger.info(
                "Asana list: fetching tasks",
                extra={"project_gid": project.gid, "project_name": project.name},
            )
            tasks = await client.list_project_tasks(
                project_gid=project.gid,
                limit=self.max_tasks_per_project,
                include_completed=self.include_completed_in_lists,
                opt_fields=self.opt_fields,
            )
            logger.info(
                "Asana list: fetched %s tasks",
                len(tasks or []),
                extra={"project_gid": project.gid, "project_name": project.name},
            )
            if not tasks:
                summaries.append(f"Project {self._project_label(project)}: no {'tasks' if not self.include_completed_in_lists else 'recent tasks'} found.")
                continue

            formatted = []
            for index, task in enumerate(tasks, start=1):
                status = "✓" if task.get("completed") else "•"
                name = task.get("name") or "(no title)"
                due_on = task.get("due_on") or task.get("due_at")
                assignee = None
                assignee_field = task.get("assignee") or {}
                if isinstance(assignee_field, dict):
                    assignee = assignee_field.get("name")
                parts = [f"{index}. {status} {name}"]
                if assignee:
                    parts.append(f"(assignee: {assignee})")
                if due_on:
                    parts.append(f"due {due_on}")
                # Store gid in internal reference but don't include in spoken output
                formatted.append(" ".join(parts))
            tasks_found += len(tasks)
            summaries.append(f"Project {self._project_label(project)}:\n" + "\n".join(formatted))

        if tasks_found == 0:
            return "No matching Asana tasks were found in the configured projects."

        return "\n\n".join(summaries)

    async def _handle_create(self, client: AsanaClient, details: Dict[str, Any]) -> str:
        project = details.get("project") or self.default_project
        if not project:
            raise AsanaAbilityConfigError(
                "No Asana project configured. Provide a project in the request or configure projects for this ability."
            )
        task_name = self._sanitize_task_name(details.get("task_name"))
        if not task_name:
            return (
                "I couldn't determine a concise Asana task title from that request. "
                "Please specify the task name explicitly, for example: "
                "\"Create a task called 'Prepare Q4 forecast' due next Tuesday.\""
            )

        payload: Dict[str, Any] = {
            "name": task_name.strip(),
        }
        if self.workspace_gid:
            payload["workspace"] = self.workspace_gid
        payload["projects"] = [project.gid]
        if self.default_assignee:
            payload["assignee"] = self.default_assignee
        if details.get("due_on"):
            payload["due_on"] = details["due_on"]
        if details.get("notes"):
            payload["notes"] = details["notes"]

        task = await client.create_task(**payload)
        return (
            f"Created Asana task '{task.get('name')}' in project {self._project_label(project)} "
            f"(gid {task.get('gid')})."
        )

    async def _handle_complete(self, client: AsanaClient, details: Dict[str, Any]) -> str:
        task, project = await self._locate_task(client, details, include_completed=True)
        if not task:
            return self._unable_to_find_task(details)

        if task.get("completed"):
            return f"Task '{task.get('name')}' (gid {task.get('gid')}) is already completed."

        updated = await client.update_task(task["gid"], fields={"completed": True})
        return f"Marked Asana task '{updated.get('name')}' (gid {updated.get('gid')}) as complete."

    async def _handle_delete(self, client: AsanaClient, details: Dict[str, Any]) -> str:
        task, project = await self._locate_task(client, details, include_completed=True)
        if not task:
            return self._unable_to_find_task(details)

        await client.delete_task(task["gid"])
        project_label = self._project_label(project) if project else "configured project"
        return f"Deleted Asana task '{task.get('name')}' (gid {task.get('gid')}) from {project_label}."

    async def _handle_delete_all(self, client: AsanaClient, details: Dict[str, Any]) -> str:
        """Delete all tasks from specified project(s)."""
        target_projects = [details["project"]] if details.get("project") else self.projects
        
        if not target_projects:
            return "No project specified. Please specify which project's tasks you want to delete."
        
        deleted_count = 0
        errors = []
        
        for project in target_projects:
            try:
                # Fetch all tasks in the project (including completed)
                tasks = await client.list_project_tasks(
                    project_gid=project.gid,
                    limit=100,  # Delete up to 100 tasks
                    include_completed=True,
                    opt_fields=["gid", "name"],
                )
                
                if not tasks:
                    continue
                
                # Delete each task
                for task in tasks:
                    try:
                        await client.delete_task(task["gid"])
                        deleted_count += 1
                    except Exception as e:
                        errors.append(f"Failed to delete '{task.get('name')}': {str(e)}")
                        
            except Exception as e:
                errors.append(f"Failed to fetch tasks from {self._project_label(project)}: {str(e)}")
        
        # Build response message
        project_labels = ", ".join([self._project_label(p) for p in target_projects])
        
        if deleted_count == 0:
            return f"No tasks were found to delete in {project_labels}."
        
        message = f"Successfully deleted {deleted_count} task(s) from {project_labels}."
        
        if errors:
            message += f" However, encountered {len(errors)} error(s): " + "; ".join(errors[:3])
            if len(errors) > 3:
                message += f" (and {len(errors) - 3} more)"
        
        return message

    async def _handle_update(self, client: AsanaClient, details: Dict[str, Any]) -> str:
        task, _project = await self._locate_task(client, details, include_completed=True)
        if not task:
            return self._unable_to_find_task(details)

        fields: Dict[str, Any] = {}
        if details.get("due_on"):
            fields["due_on"] = details["due_on"]
        if details.get("new_name"):
            fields["name"] = details["new_name"]
        if not fields and "complete" in details.get("lower", ""):
            fields["completed"] = True

        if not fields:
            return "No updates identified for the target task. Specify a due date, new name, or completion instruction."

        updated = await client.update_task(task["gid"], fields=fields)
        changes = []
        if "due_on" in fields:
            changes.append(f"due date → {fields['due_on']}")
        if "name" in fields:
            changes.append(f"name → {fields['name']}")
        if "completed" in fields and fields["completed"]:
            changes.append("marked complete")
        return f"Updated Asana task '{updated.get('name')}' (gid {updated.get('gid')}): {', '.join(changes)}."

    async def _handle_create_subtask(self, client: AsanaClient, details: Dict[str, Any]) -> str:
        parent_task, _ = await self._resolve_parent_task(client, details)
        if not parent_task:
            return "Unable to determine which parent task to attach this subtask to. Please specify the parent task by name or gid."

        subtask_name = details.get("subtask_name") or details.get("task_name") or details.get("raw")
        if not subtask_name:
            return "I couldn't determine a name for the subtask you'd like me to create."

        payload: Dict[str, Any] = {
            "name": subtask_name.strip(),
        }
        if self.default_assignee:
            payload["assignee"] = self.default_assignee
        if details.get("due_on"):
            payload["due_on"] = details["due_on"]
        if details.get("notes"):
            payload["notes"] = details["notes"]

        subtask = await client.create_subtask(parent_task["gid"], **payload)
        return (
            f"Created Asana subtask '{subtask.get('name')}' (gid {subtask.get('gid')}) under "
            f"'{parent_task.get('name')}'."
        )

    async def _handle_update_subtask(self, client: AsanaClient, details: Dict[str, Any]) -> str:
        subtask, parent_task = await self._locate_subtask(client, details, include_completed=True)
        if not subtask:
            return "I couldn't find the target subtask. Provide its name with the parent task or include the subtask gid."

        fields: Dict[str, Any] = {}
        if details.get("due_on"):
            fields["due_on"] = details["due_on"]
        if details.get("new_name"):
            fields["name"] = details["new_name"]
        if "complete" in details.get("lower", ""):
            fields["completed"] = True

        if not fields:
            return "No changes were detected for the subtask. Provide an updated name, due date, or completion instruction."

        updated = await client.update_task(subtask["gid"], fields=fields)
        parts = []
        if "name" in fields:
            parts.append(f"name → {fields['name']}")
        if "due_on" in fields:
            parts.append(f"due date → {fields['due_on']}")
        if fields.get("completed"):
            parts.append("marked complete")

        parent_label = parent_task.get("name") if parent_task else "its parent task"
        return (
            f"Updated Asana subtask '{updated.get('name')}' (gid {updated.get('gid')}) under {parent_label}: "
            f"{', '.join(parts)}."
        )

    async def _handle_delete_subtask(self, client: AsanaClient, details: Dict[str, Any]) -> str:
        subtask, parent_task = await self._locate_subtask(client, details, include_completed=True)
        if not subtask:
            return "Unable to find the subtask you want to delete. Please reference it by name along with its parent or provide the subtask gid."

        await client.delete_task(subtask["gid"])
        parent_label = parent_task.get("name") if parent_task else "its parent task"
        return f"Deleted Asana subtask '{subtask.get('name')}' (gid {subtask.get('gid')}) under {parent_label}."

    async def _handle_complete_subtask(self, client: AsanaClient, details: Dict[str, Any]) -> str:
        details = dict(details)
        details["lower"] = details.get("lower", "") + " complete"
        details.pop("new_name", None)
        return await self._handle_update_subtask(client, details)

    # --- Task discovery helpers ------------------------------------------------

    async def _locate_task(
        self,
        client: AsanaClient,
        details: Dict[str, Any],
        *,
        include_completed: bool,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[ProjectConfig]]:
        if details.get("task_gid"):
            task = await client.get_task(details["task_gid"], opt_fields=self.opt_fields)
            if not task:
                return None, None
            project = None
            memberships = task.get("memberships") or []
            for membership in memberships:
                project_data = membership.get("project") or {}
                gid = project_data.get("gid")
                if gid and gid in self.project_by_gid:
                    project = self.project_by_gid[gid]
                    break
            return task, project

        task_name = details.get("task_name")
        if not task_name:
            return None, None

        project_hint = details.get("project")
        candidate_projects = [project_hint] if project_hint else self.projects

        search_tasks: List[Tuple[Dict[str, Any], ProjectConfig]] = []
        for project in candidate_projects:
            tasks = await client.list_project_tasks(
                project_gid=project.gid,
                limit=self.lookup_limit,
                include_completed=include_completed,
                opt_fields=self.opt_fields,
            )
            for task in tasks:
                search_tasks.append((task, project))

        if not search_tasks:
            return None, None

        target = self._find_best_task_match(task_name, search_tasks)
        if target is None:
            return None, None
        return target

    @staticmethod
    def _find_best_task_match(
        task_name: str,
        candidates: List[Tuple[Dict[str, Any], ProjectConfig]],
    ) -> Optional[Tuple[Dict[str, Any], ProjectConfig]]:
        normalized = task_name.lower().strip()
        best_match: Optional[Tuple[Dict[str, Any], ProjectConfig]] = None
        best_score = 0.0

        for task, project in candidates:
            name = (task.get("name") or "").lower().strip()
            if not name:
                continue
            if name == normalized:
                return task, project
            if normalized in name or name in normalized:
                score = len(normalized) / len(name)
                if score > best_score:
                    best_score = score
                    best_match = (task, project)
            else:
                # Simple token overlap score
                target_tokens = set(normalized.split())
                name_tokens = set(name.split())
                overlap = target_tokens & name_tokens
                if overlap:
                    score = len(overlap) / max(len(target_tokens), 1)
                    if score > best_score:
                        best_score = score
                        best_match = (task, project)

        return best_match

    async def _resolve_parent_task(
        self,
        client: AsanaClient,
        details: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[ProjectConfig]]:
        lookup: Dict[str, Any] = {}
        if details.get("parent_task_name"):
            lookup["task_name"] = details["parent_task_name"]
        if details.get("parent_task_gid"):
            lookup["task_gid"] = details["parent_task_gid"]
        if details.get("project"):
            lookup["project"] = details["project"]
        if not lookup:
            return None, None
        return await self._locate_task(client, lookup, include_completed=True)

    async def _locate_subtask(
        self,
        client: AsanaClient,
        details: Dict[str, Any],
        *,
        include_completed: bool,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if details.get("task_gid"):
            task = await client.get_task(details["task_gid"], opt_fields=self.opt_fields)
            if not task:
                return None, None
            parent_task, _ = await self._resolve_parent_task(client, details)
            return task, parent_task

        parent_task, parent_project = await self._resolve_parent_task(client, details)
        if not parent_task:
            return None, None

        subtask_name = details.get("subtask_name") or details.get("task_name")
        if not subtask_name:
            return None, parent_task

        subtasks = await client.list_task_subtasks(
            parent_task["gid"],
            include_completed=include_completed,
            opt_fields=self.opt_fields,
        )
        if not subtasks:
            return None, parent_task

        project_ctx = parent_project or self.project_by_gid.get(parent_task.get("gid")) or self.default_project
        match = self._find_best_task_match(
            subtask_name,
            [(task, project_ctx) for task in subtasks],
        )
        if not match:
            return None, parent_task
        subtask, _ = match
        return subtask, parent_task

    # --- Formatting helpers ----------------------------------------------------

    @staticmethod
    def _project_label(project: ProjectConfig) -> str:
        return project.name or f"project {project.gid}"

    @staticmethod
    def _unable_to_find_task(details: Dict[str, Any]) -> str:
        if details.get("task_name"):
            return f"Unable to locate a task matching '{details['task_name']}' in the configured Asana projects."
        if details.get("task_gid"):
            return f"No Asana task found with gid {details['task_gid']}."
        return "Unable to determine which Asana task to target."


def _compute_call_hash(slug: str, user_inquiry: str) -> str:
    """Compute a hash of the tool call to detect duplicates."""
    # Normalize the inquiry by lowercasing and removing extra whitespace
    normalized = re.sub(r'\s+', ' ', user_inquiry.lower().strip())
    # Remove trailing punctuation for comparison
    normalized = re.sub(r'[?.!]+$', '', normalized)
    
    # Create hash from slug + normalized inquiry
    content = f"{slug}:{normalized}"
    return hashlib.md5(content.encode()).hexdigest()


def _check_duplicate_call(slug: str, call_hash: str) -> Optional[Any]:
    """
    Check if this call is a duplicate of a recent call.
    Returns the cached result if found within the dedupe window, else None.
    """
    now = time.time()
    
    if slug not in _TOOL_CALL_HISTORY:
        _TOOL_CALL_HISTORY[slug] = deque(maxlen=_MAX_HISTORY_SIZE)
        return None
    
    history = _TOOL_CALL_HISTORY[slug]
    
    # Clean up old entries outside the dedupe window
    while history and (now - history[0][1]) > _DEDUPE_WINDOW_SECONDS:
        history.popleft()
    
    # Check for duplicate
    for stored_hash, timestamp, result in history:
        if stored_hash == call_hash and (now - timestamp) <= _DEDUPE_WINDOW_SECONDS:
            logger.info(
                f"🔄 Duplicate tool call detected: slug={slug}, hash={call_hash[:8]}, "
                f"age={now - timestamp:.1f}s - returning cached result"
            )
            return result
    
    return None


def _record_call(slug: str, call_hash: str, result: Any) -> None:
    """Record a successful tool call in the history."""
    if slug not in _TOOL_CALL_HISTORY:
        _TOOL_CALL_HISTORY[slug] = deque(maxlen=_MAX_HISTORY_SIZE)
    
    _TOOL_CALL_HISTORY[slug].append((call_hash, time.time(), result))


def build_asana_tool(
    tool_def: Dict[str, Any],
    config: Dict[str, Any],
    *,
    oauth_service: Any = None,
    client_factory: Optional[Any] = None,
) -> Any:
    slug = tool_def.get("slug") or tool_def.get("name") or "asana_tasks"
    description = tool_def.get("description") or "Manage tasks in Asana."

    handler = AsanaToolHandler(
        slug=slug,
        description=description,
        config=config,
        oauth_service=oauth_service,
        client_factory=client_factory,
    )

    async def _invoke(**kwargs: Any) -> Dict[str, Any]:
        metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
        user_inquiry = kwargs.get("user_inquiry")
        if not isinstance(user_inquiry, str) or not user_inquiry.strip():
            auto_fallback = _coalesce_user_inquiry(metadata, kwargs.get("messages"))
            if auto_fallback:
                user_inquiry = auto_fallback

        if not user_inquiry and isinstance(metadata, dict):
            for key in ("user_inquiry", "latest_user_text", "transcript", "message"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    user_inquiry = value.strip()
                    break

        if not user_inquiry or not isinstance(user_inquiry, str):
            message = (
                "I wasn't able to determine what to look up in Asana. "
                "Please repeat the request in your own words."
            )
            return message

        # Check for duplicate call
        call_hash = _compute_call_hash(slug, user_inquiry)
        cached_result = _check_duplicate_call(slug, call_hash)
        if cached_result is not None:
            return cached_result

        # Execute the tool
        summary = await handler.invoke(user_inquiry=user_inquiry, metadata=metadata)
        if not isinstance(summary, str):
            summary = str(summary)

        result = {
            "slug": slug,
            "summary": summary,
            "text": summary,
            "raw_text": summary,
        }
        
        # Record successful call
        _record_call(slug, call_hash, result)
        
        return result

    # Enhanced description to ensure LLM reads tool output verbatim
    # Keep it simple - complex instructions can confuse the LLM's tool calling
    enhanced_description = f"{description} Read the tool's complete output to the user."
    
    return lk_function_tool(
        raw_schema={
            "name": slug,
            "description": enhanced_description,
            "parameters": {
                "type": "object",
                "properties": {
                    "user_inquiry": {
                        "type": "string",
                        "description": "Pass the COMPLETE user request VERBATIM including the action verb. For example: if user says 'delete all tasks in L-Dixon project', pass EXACTLY 'delete all tasks in L-Dixon project' - DO NOT extract only parameters. REQUIRED: Must start with action verb (list/delete/create/update/complete/remove).",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Additional session metadata (unused).",
                        "additionalProperties": True,
                    },
                },
                "required": ["user_inquiry"],
            },
        }
    )(_invoke)
