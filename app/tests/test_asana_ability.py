from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.agent_modules.abilities.asana import AsanaToolHandler


class FakeOAuthService:
    def __init__(self, token: str = "fake-token") -> None:
        self.token = token
        self.requests: List[str] = []

    async def ensure_valid_token(self, client_id: str, force_refresh: bool = False):  # pragma: no cover - simple stub
        self.requests.append(f"{client_id}:{force_refresh}")

        class _Bundle:
            def __init__(self, value: str) -> None:
                self.access_token = value
                self.refresh_token = None
                self.token_type = "bearer"
                self.expires_at = None
                self.extra: Dict[str, Any] = {}

            @property
            def is_expired(self) -> bool:
                return False

        return _Bundle(self.token)


class FakeAsanaClient:
    def __init__(
        self,
        tasks_by_project: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        subtasks_by_parent: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> None:
        self.tasks_by_project: Dict[str, List[Dict[str, Any]]] = {}
        self.tasks_by_gid: Dict[str, Dict[str, Any]] = {}
        self.subtasks_by_parent: Dict[str, List[Dict[str, Any]]] = {}
        source = tasks_by_project or {}
        for project_gid, tasks in source.items():
            normalized: List[Dict[str, Any]] = []
            for task in tasks:
                task_copy = dict(task)
                task_copy.setdefault("memberships", [{"project": {"gid": project_gid}}])
                task_copy.setdefault("completed", False)
                task_copy.setdefault("name", task_copy.get("name", "Task"))
                self.tasks_by_gid[task_copy["gid"]] = task_copy
                normalized.append(task_copy)
            self.tasks_by_project[project_gid] = normalized
        if subtasks_by_parent:
            for parent_gid, subtasks in subtasks_by_parent.items():
                normalized: List[Dict[str, Any]] = []
                for subtask in subtasks:
                    subtask_copy = dict(subtask)
                    subtask_copy.setdefault("gid", f"subtask_{len(self.tasks_by_gid)+1}")
                    subtask_copy.setdefault("name", subtask_copy.get("name", "Subtask"))
                    subtask_copy.setdefault("completed", False)
                    self.tasks_by_gid[subtask_copy["gid"]] = subtask_copy
                    normalized.append(subtask_copy)
                self.subtasks_by_parent[parent_gid] = normalized
        self.calls: List[Tuple[str, Any]] = []

    async def list_project_tasks(
        self,
        project_gid: str,
        *,
        limit: int,
        include_completed: bool,
        opt_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        self.calls.append(("list_project_tasks", {"project_gid": project_gid, "limit": limit, "include_completed": include_completed}))
        tasks = self.tasks_by_project.get(project_gid, [])
        return tasks[:limit]

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
        payload = {
            "name": name,
            "workspace": workspace,
            "projects": projects,
            "assignee": assignee,
            "due_on": due_on,
            "notes": notes,
        }
        self.calls.append(("create_task", payload))
        gid = f"task_{len(self.tasks_by_gid)+1}"
        memberships = [{"project": {"gid": project}} for project in (projects or [])]
        task = {"gid": gid, "name": name, "completed": False, "memberships": memberships}
        if projects:
            for project in projects:
                self.tasks_by_project.setdefault(project, []).append(dict(task))
        self.tasks_by_gid[gid] = task
        return task

    async def create_subtask(
        self,
        parent_gid: str,
        *,
        name: str,
        assignee: Optional[str] = None,
        due_on: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "parent": parent_gid,
            "name": name,
            "assignee": assignee,
            "due_on": due_on,
            "notes": notes,
        }
        self.calls.append(("create_subtask", payload))
        gid = f"subtask_{len(self.tasks_by_gid)+1}"
        subtask = {"gid": gid, "name": name, "completed": False}
        self.tasks_by_gid[gid] = subtask
        self.subtasks_by_parent.setdefault(parent_gid, []).append(subtask)
        return subtask

    async def update_task(self, task_gid: str, *, fields: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append(("update_task", {"gid": task_gid, "fields": fields}))
        task = self.tasks_by_gid.get(task_gid, {"gid": task_gid})
        updated = dict(task)
        updated.update(fields)
        updated.setdefault("name", task.get("name", "Unnamed task"))
        self.tasks_by_gid[task_gid] = updated
        return updated

    async def delete_task(self, task_gid: str) -> None:
        self.calls.append(("delete_task", {"gid": task_gid}))
        self.tasks_by_gid.pop(task_gid, None)

    async def get_task(
        self,
        task_gid: str,
        *,
        opt_fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        self.calls.append(("get_task", {"gid": task_gid}))
        if task_gid not in self.tasks_by_gid:
            return {}
        return dict(self.tasks_by_gid[task_gid])

    async def list_task_subtasks(
        self,
        task_gid: str,
        *,
        limit: int = 50,
        include_completed: bool,
        opt_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        self.calls.append(("list_task_subtasks", {"task_gid": task_gid, "limit": limit, "include_completed": include_completed}))
        return list(self.subtasks_by_parent.get(task_gid, []))[:limit]


def build_handler(fake_client: FakeAsanaClient, projects: Optional[List[Dict[str, Any]]] = None) -> AsanaToolHandler:
    config = {
        "projects": projects
        or [
            {"gid": "P1", "name": "Inbox"},
        ],
        "workspace_gid": "workspace",
        "default_assignee": "assignee",
        "max_tasks_per_project": 5,
    }
    oauth_service = FakeOAuthService()
    return AsanaToolHandler(
        slug="asana",
        description="Test Asana ability",
        config=config,
        oauth_service=oauth_service,
        client_factory=lambda _token: fake_client,
    )


@pytest.mark.asyncio
async def test_list_tasks_formats_output() -> None:
    fake = FakeAsanaClient(
        tasks_by_project={
            "P1": [
                {
                    "gid": "T1",
                    "name": "Prepare report",
                    "completed": False,
                    "due_on": "2024-10-12",
                    "assignee": {"name": "Alex"},
                },
                {
                    "gid": "T2",
                    "name": "Review design",
                    "completed": True,
                    "due_on": None,
                    "assignee": {"name": "Sam"},
                },
            ]
        }
    )
    handler = build_handler(fake)

    result = await handler.invoke(user_inquiry="What tasks do I have coming up?", metadata={"client_id": "client-1"})
    assert "Project Inbox" in result
    assert "Prepare report" in result
    assert "gid T1" in result
    assert fake.calls and fake.calls[0][0] == "list_project_tasks"


@pytest.mark.asyncio
async def test_create_task_uses_due_date() -> None:
    fake = FakeAsanaClient()
    handler = build_handler(fake)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    await handler.invoke(user_inquiry='Add "Write spec" task for tomorrow', metadata={"client_id": "client-1"})

    create_calls = [call for call in fake.calls if call[0] == "create_task"]
    assert create_calls, "Expected create_task to be called"
    payload = create_calls[0][1]
    assert payload["name"] == "Write spec"
    assert payload["due_on"] == tomorrow
    assert payload["projects"] == ["P1"]


@pytest.mark.asyncio
async def test_complete_task_by_name_marks_completed() -> None:
    fake = FakeAsanaClient(
        tasks_by_project={
            "P1": [
                {
                    "gid": "T1",
                    "name": "Write spec",
                    "completed": False,
                }
            ]
        }
    )
    handler = build_handler(fake)

    await handler.invoke(user_inquiry='Please mark "Write spec" as done', metadata={"client_id": "client-1"})

    update_calls = [call for call in fake.calls if call[0] == "update_task"]
    assert update_calls, "Expected update_task to be called"
    assert update_calls[0][1]["fields"] == {"completed": True}


@pytest.mark.asyncio
async def test_delete_task_by_gid() -> None:
    fake = FakeAsanaClient()
    fake.tasks_by_gid["9999999999"] = {"gid": "9999999999", "name": "Legacy task", "memberships": [{"project": {"gid": "P1"}}]}
    handler = build_handler(fake)

    await handler.invoke(user_inquiry="Remove task 9999999999", metadata={"client_id": "client-1"})

    delete_calls = [call for call in fake.calls if call[0] == "delete_task"]
    assert delete_calls, "Expected delete_task to be called"
    assert delete_calls[0][1]["gid"] == "9999999999"


@pytest.mark.asyncio
async def test_update_task_due_date() -> None:
    fake = FakeAsanaClient(
        tasks_by_project={
            "P1": [
                {
                    "gid": "T5",
                    "name": "Follow up with client",
                    "completed": False,
                }
            ]
        }
    )
    handler = build_handler(fake)

    await handler.invoke(
        user_inquiry='Update "Follow up with client" task to be due on 2024-10-19',
        metadata={"client_id": "client-1"},
    )

    update_calls = [call for call in fake.calls if call[0] == "update_task"]
    assert update_calls, "Expected update_task to be called"
    update_payload = update_calls[0][1]
    assert update_payload["fields"] == {"due_on": "2024-10-19"}


@pytest.mark.asyncio
async def test_create_subtask_under_parent() -> None:
    fake = FakeAsanaClient(
        tasks_by_project={
            "P1": [
                {
                    "gid": "T10",
                    "name": "Prepare launch plan",
                }
            ]
        }
    )
    handler = build_handler(fake)

    await handler.invoke(
        user_inquiry='Add subtask "Draft slides" under "Prepare launch plan"',
        metadata={"client_id": "client-1"},
    )

    subtask_calls = [call for call in fake.calls if call[0] == "create_subtask"]
    assert subtask_calls, "Expected create_subtask to be called"
    payload = subtask_calls[0][1]
    assert payload["parent"] == "T10"
    assert payload["name"] == "Draft slides"
    assert payload["assignee"] == "assignee"


@pytest.mark.asyncio
async def test_update_subtask_due_date() -> None:
    fake = FakeAsanaClient(
        tasks_by_project={
            "P1": [
                {
                    "gid": "T20",
                    "name": "Prepare report",
                }
            ]
        },
        subtasks_by_parent={
            "T20": [
                {
                    "gid": "555666777888",
                    "name": "Compile data",
                }
            ]
        },
    )
    handler = build_handler(fake)

    await handler.invoke(
        user_inquiry='Update subtask "Compile data" under "Prepare report" to be due on 2024-11-05',
        metadata={"client_id": "client-1"},
    )

    update_calls = [call for call in fake.calls if call[0] == "update_task"]
    assert update_calls, "Expected update_task to be called"
    update_payload = update_calls[0][1]
    assert update_payload["gid"] == "555666777888"
    assert update_payload["fields"] == {"due_on": "2024-11-05"}


@pytest.mark.asyncio
async def test_delete_subtask_by_gid() -> None:
    fake = FakeAsanaClient(
        tasks_by_project={
            "P1": [
                {
                    "gid": "T30",
                    "name": "Ship new build",
                }
            ]
        },
        subtasks_by_parent={
            "T30": [
                {
                    "gid": "999888777666",
                    "name": "Clean up backlog",
                }
            ]
        },
    )
    handler = build_handler(fake)

    await handler.invoke(
        user_inquiry="Delete subtask 999888777666",
        metadata={"client_id": "client-1"},
    )

    delete_calls = [call for call in fake.calls if call[0] == "delete_task"]
    assert delete_calls, "Expected delete_task to be called"
    assert delete_calls[0][1]["gid"] == "999888777666"


@pytest.mark.asyncio
async def test_complete_subtask_marks_complete() -> None:
    fake = FakeAsanaClient(
        tasks_by_project={
            "P1": [
                {
                    "gid": "T40",
                    "name": "Client onboarding",
                }
            ]
        },
        subtasks_by_parent={
            "T40": [
                {
                    "gid": "333222111000",
                    "name": "Collect documents",
                }
            ]
        },
    )
    handler = build_handler(fake)

    await handler.invoke(
        user_inquiry='Complete subtask "Collect documents" under "Client onboarding"',
        metadata={"client_id": "client-1"},
    )

    update_calls = [call for call in fake.calls if call[0] == "update_task"]
    assert update_calls, "Expected update_task to be called"
    update_payload = update_calls[0][1]
    assert update_payload["gid"] == "333222111000"
    assert update_payload["fields"]["completed"] is True
