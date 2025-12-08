from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.agent_modules.abilities.asana import AsanaToolHandler
from app.integrations.asana_client import AsanaAPIError
from app.services.asana_oauth_service import AsanaTokenBundle


class _StubOAuthService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def ensure_valid_token(self, client_id: str, force_refresh: bool = False) -> AsanaTokenBundle:
        self.calls.append((client_id, force_refresh))
        access_token = "refreshed-token" if force_refresh else "stale-token"
        return AsanaTokenBundle(
            access_token=access_token,
            refresh_token="refresh-token",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc),
            extra={},
        )


class _ScenarioClientFactory:
    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self, token: str):
        return _ScenarioClient(self)


class _ScenarioClient:
    def __init__(self, parent: _ScenarioClientFactory) -> None:
        self._parent = parent

    async def list_project_tasks(self, *_, **__):
        self._parent.call_count += 1
        if self._parent.call_count == 1:
            raise AsanaAPIError("Unauthorized", status_code=401)
        return [
            {
                "gid": "task-1",
                "name": "Test task",
                "completed": False,
                "permalink_url": "https://app.asana.com/0/123/456",
                "memberships": [{"project": {"name": "Project Alpha"}}],
            }
        ]


@pytest.mark.asyncio
async def test_asana_ability_refreshes_token_on_unauthorized_response():
    handler = AsanaToolHandler(
        slug="asana_tasks",
        description="List Asana tasks",
        config={"projects": [{"gid": "123", "name": "Project Alpha"}]},
        oauth_service=_StubOAuthService(),
        client_factory=_ScenarioClientFactory(),
    )

    summary = await handler.invoke(user_inquiry="List my tasks", metadata={"client_id": "client-1"})

    assert "Project Alpha" in summary
    assert handler._oauth_service.calls == [("client-1", False), ("client-1", True)]  # type: ignore[attr-defined]
