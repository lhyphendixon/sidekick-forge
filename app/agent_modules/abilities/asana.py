from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from livekit.agents.llm.tool_context import function_tool as lk_function_tool

from app.integrations.asana_client import AsanaAPIError, AsanaClient


logger = logging.getLogger(__name__)


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

    async def _acquire_client(self, metadata: Optional[Dict[str, Any]]) -> AsanaClient:
        token: Optional[str] = None

        if self._oauth_service is not None:
            client_id = None
            if isinstance(metadata, dict):
                client_id = metadata.get("client_id") or metadata.get("clientId")
            if not client_id:
                raise AsanaAbilityConfigError(
                    "Unable to determine client context for Asana ability. Please retry after selecting a client."
                )
            bundle = await self._oauth_service.ensure_valid_token(client_id)
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

        return self._client_factory(token)

    async def invoke(self, *, user_inquiry: str, metadata: Optional[Dict[str, Any]] = None, **_: Any) -> str:
        if not user_inquiry or not isinstance(user_inquiry, str):
            raise ValueError("Asana ability requires a user inquiry string.")

        action, details = self._interpret_intent(user_inquiry)
        logger.info("Asana ability intent resolved", extra={"slug": self.slug, "action": action, "details": details})

        try:
            client = await self._acquire_client(metadata)
        except AsanaAbilityConfigError as exc:
            logger.warning("Asana client acquisition failed", exc_info=False)
            return str(exc)
        except Exception as exc:
            logger.exception("Unexpected error acquiring Asana client")
            return f"Failed to acquire Asana client: {exc}"

        try:
            if action == "list":
                return await self._handle_list(client, details)
            if action == "create":
                return await self._handle_create(client, details)
            if action == "complete":
                return await self._handle_complete(client, details)
            if action == "delete":
                return await self._handle_delete(client, details)
            if action == "update":
                return await self._handle_update(client, details)
        except AsanaAPIError as exc:
            logger.error("Asana API call failed", exc_info=True)
            return f"Asana API error: {exc}"
        except Exception as exc:
            logger.exception("Asana ability execution failed")
            return f"Asana ability failed: {exc}"

        return "Asana ability is ready. Ask me to list tasks, create a task, update it, or remove it."

    # --- Intent parsing helpers -------------------------------------------------

    def _interpret_intent(self, message: str) -> Tuple[str, Dict[str, Any]]:
        lower = message.lower()

        # Determine action priority: delete > complete > update > create > list
        if any(word in lower for word in ("delete", "remove", "trash", "archive task", "archive the task")):
            action = "delete"
        elif ("complete" in lower) or ("finish" in lower) or ("mark" in lower and ("done" in lower or "complete" in lower)):
            action = "complete"
        elif ("update" in lower) or ("change" in lower) or ("modify" in lower) or ("set" in lower and "due" in lower):
            action = "update"
        elif any(word in lower for word in ("add", "create", "new task", "make a task", "open a task", "log a task")):
            action = "create"
        elif any(word in lower for word in ("list", "show", "what", "pending", "due", "where are we", "tasks on my list")):
            action = "list"
        else:
            action = self.default_action if self.default_action in {"list", "create", "update", "complete", "delete"} else "list"

        details: Dict[str, Any] = {
            "raw": message,
            "lower": lower,
        }

        project = self._extract_project(message)
        if project:
            details["project"] = project

        task_gid = self._extract_task_gid(message)
        if task_gid:
            details["task_gid"] = task_gid

        task_name = self._extract_task_name(message)
        if task_name:
            details["task_name"] = task_name

        due_date = self._extract_due_date(message)
        if due_date:
            details["due_on"] = due_date

        new_name = self._extract_new_name(message)
        if new_name:
            details["new_name"] = new_name

        notes = self._extract_notes(message)
        if notes:
            details["notes"] = notes

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
        if "note" in message.lower() or "details" in message.lower():
            return message.strip()
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
            tasks = await client.list_project_tasks(
                project_gid=project.gid,
                limit=self.max_tasks_per_project,
                include_completed=self.include_completed_in_lists,
                opt_fields=self.opt_fields,
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
                parts.append(f"gid {task.get('gid')}")
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
        task_name = details.get("task_name") or details.get("raw")
        if not task_name:
            raise ValueError("Unable to determine the task name to create.")

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

    async def _invoke(**kwargs: Any) -> str:
        metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
        user_inquiry = kwargs.get("user_inquiry")

        if not user_inquiry and isinstance(metadata, dict):
            for key in ("user_inquiry", "latest_user_text", "transcript", "message"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    user_inquiry = value.strip()
                    break

        if not user_inquiry or not isinstance(user_inquiry, str):
            return (
                "I wasn't able to determine what to look up in Asana. "
                "Please repeat the request in your own words."
            )

        return await handler.invoke(user_inquiry=user_inquiry, metadata=metadata)

    return lk_function_tool(
        raw_schema={
            "name": slug,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "user_inquiry": {
                        "type": "string",
                        "description": "Latest user request describing the desired Asana action.",
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
