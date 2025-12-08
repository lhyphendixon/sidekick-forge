from __future__ import annotations

import os
from typing import Any, Dict, List

import pytest

# Ensure required environment variables exist before importing settings-backed modules
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")
os.environ.setdefault("DOMAIN_NAME", "example.com")

from app.agent_modules.abilities.asana import AsanaAbilityConfigError, build_asana_tool  # noqa: E402
from app.agent_modules.tool_registry import ToolRegistry  # noqa: E402


@pytest.mark.asyncio
async def test_asana_tool_registry_uses_agent_override_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Dict[str, Any]] = {}

    async def _fake_tool(**_: Any) -> str:
        return "ok"

    def fake_builder(tool_def: Dict[str, Any], config: Dict[str, Any], **__: Any) -> Any:
        captured["config"] = config
        return _fake_tool

    monkeypatch.setattr("app.agent_modules.tool_registry.build_asana_tool", fake_builder)

    registry = ToolRegistry(
        tools_config={
            "tool-123": {
                "projects": [{"gid": "OVERRIDE"}],
                "max_tasks_per_project": 7,
            }
        },
    )

    tool_defs: List[Dict[str, Any]] = [
        {
            "id": "tool-123",
            "slug": "asana_tasks",
            "type": "asana",
            "config": {
                "projects": [{"gid": "DEFAULT"}],
                "workspace_gid": "workspace",
            },
        }
    ]

    tools = registry.build(tool_defs)

    assert tools, "Expected the registry to return a tool entry"
    merged_config = captured.get("config")
    assert merged_config is not None, "Merged config should be captured"
    assert merged_config["projects"] == [{"gid": "OVERRIDE"}]
    assert merged_config["workspace_gid"] == "workspace"
    assert merged_config["max_tasks_per_project"] == 7


@pytest.mark.asyncio
async def test_asana_tool_registry_returns_stub_when_builder_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_builder(*_: Any, **__: Any) -> Any:
        raise AsanaAbilityConfigError("missing projects")

    monkeypatch.setattr("app.agent_modules.tool_registry.build_asana_tool", failing_builder)

    registry = ToolRegistry()
    tool_defs = [
        {
            "id": "tool-456",
            "slug": "asana_tasks",
            "type": "asana",
            "config": {},
        }
    ]

    tools = registry.build(tool_defs)
    assert tools, "Registry should return fallback tool even when misconfigured"

    result = await tools[0](user_inquiry="List my tasks", metadata={"client_id": "client-1"})
    assert isinstance(result, dict)
    assert "Asana ability is not ready" in result.get("error", "")
    assert "missing projects" in result.get("error", "")


@pytest.mark.asyncio
async def test_asana_tool_returns_structured_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_invoke(self, *, user_inquiry: str, metadata: Dict[str, Any] | None = None, **_: Any) -> str:
        assert user_inquiry == "List tasks"
        return "Project Alpha: 1. • Task one gid 123"

    monkeypatch.setattr("app.agent_modules.abilities.asana.AsanaToolHandler.invoke", fake_invoke)

    tool = build_asana_tool(
        {"slug": "asana_tasks", "description": "List Asana tasks."},
        {"projects": [{"gid": "111"}], "access_token": "token"},
    )

    result = await tool(user_inquiry="List tasks", metadata={})
    assert isinstance(result, dict)
    assert result["slug"] == "asana_tasks"
    assert result["summary"].startswith("Project Alpha")
    assert result["text"] == result["summary"]


@pytest.mark.asyncio
async def test_asana_tool_derives_user_inquiry_from_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_inquiry: Dict[str, Any] = {}

    async def fake_invoke(self, *, user_inquiry: str, metadata: Dict[str, Any] | None = None, **_: Any) -> str:
        captured_inquiry["value"] = user_inquiry
        return f"Echo:{user_inquiry}"

    monkeypatch.setattr("app.agent_modules.abilities.asana.AsanaToolHandler.invoke", fake_invoke)

    tool = build_asana_tool(
        {"slug": "asana_tasks", "description": "List Asana tasks."},
        {"projects": [{"gid": "111"}], "access_token": "token"},
    )

    metadata_payload = {
        "context": {
            "latest_user_text": "List the open Asana tasks for today."
        }
    }

    result = await tool(user_inquiry=None, metadata=metadata_payload)
    assert captured_inquiry["value"] == "List the open Asana tasks for today."
    assert result["summary"] == "Echo:List the open Asana tasks for today."
