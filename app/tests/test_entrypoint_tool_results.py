from __future__ import annotations

import types
from typing import Any, List, Tuple

import pytest

from .utils.agent_loader import load_entrypoint_module, load_tool_registry_module


@pytest.fixture(scope="module")
def agent_entrypoint():
    return load_entrypoint_module()


@pytest.fixture(scope="module")
def agent_tool_registry():
    return load_tool_registry_module()


class _FakeCallOutput:
    def __init__(self, call_id: str, output: str, is_error: bool = False) -> None:
        self.call_id = call_id
        self.output = output
        self.is_error = is_error

    def model_dump(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "output": self.output,
            "is_error": self.is_error,
        }


class _FakeEvent:
    def __init__(self, calls: List[Any], outputs: List[Any]) -> None:
        self.function_calls = calls
        self.function_call_outputs = outputs

    def zipped(self) -> List[Tuple[Any, Any]]:
        return list(zip(self.function_calls, self.function_call_outputs))


def _make_call(**kwargs: Any) -> Any:
    defaults = {
        "name": "asana_tasks",
        "call_id": "call-123",
        "tool": "function",
        "type": "function",
        "success": True,
        "status": "completed",
        "output": None,
        "response": None,
        "result": None,
        "tool_output": None,
        "error": None,
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


def test_collect_tool_results_parses_structured_payload(agent_entrypoint):
    call = _make_call()
    payload = {
        "summary": "Project Tasks 1. • First task gid 12345",
        "raw_text": "Project Tasks 1. • First task gid 12345",
        "slug": "asana_tasks",
        "text": "Project Tasks 1. • First task gid 12345",
    }
    call_output = _FakeCallOutput(call_id="call-123", output=str(payload))

    event = _FakeEvent([call], [call_output])
    summaries, results = agent_entrypoint.collect_tool_results_from_event(event, log=agent_entrypoint.logger)

    assert summaries == ["asana_tasks"]
    assert len(results) == 1
    entry = results[0]
    assert entry["slug"] == "asana_tasks"
    assert entry["success"] is True
    assert entry["output"] == str(payload)
    assert entry["structured_output"]["summary"] == payload["summary"]
    assert entry["structured_output"]["slug"] == payload["slug"]


def test_collect_tool_results_handles_missing_output_object(agent_entrypoint):
    text_output = "Created Asana task 'Write docs' (gid 999)."
    call = _make_call(output=text_output, call_id="call-999", success=True, tool_output=None)
    event = _FakeEvent([call], [])

    summaries, results = agent_entrypoint.collect_tool_results_from_event(event, log=agent_entrypoint.logger)

    assert summaries == ["asana_tasks"]
    entry = results[0]
    assert entry["output"] == text_output
    assert "structured_output" not in entry
    assert entry["success"] is True


@pytest.mark.asyncio
async def test_tool_registry_injects_runtime_context_for_asana(monkeypatch: pytest.MonkeyPatch, agent_tool_registry):
    captured_kwargs: dict[str, Any] = {}

    async def fake_tool(**kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        return {"ok": True}

    def fake_builder(tool_def: dict[str, Any], config: dict[str, Any], **_: Any) -> Any:
        return fake_tool

    monkeypatch.setattr(agent_tool_registry, "build_asana_tool", fake_builder, raising=False)

    registry = agent_tool_registry.ToolRegistry()
    tool_defs = [
        {
            "type": "asana",
            "slug": "asana_tasks",
            "config": {
                "projects": [{"gid": "123"}],
                "access_token": "token",
            },
        }
    ]

    tools = registry.build(tool_defs)
    assert tools, "Expected registry to return wrapped Asana tool"

    registry.update_runtime_context("asana_tasks", {"latest_user_text": "List my pending Asana tasks"})

    result = await tools[0](user_inquiry=None, metadata=None)
    assert result == {"ok": True}
    assert captured_kwargs["user_inquiry"] == "List my pending Asana tasks"
    assert isinstance(captured_kwargs["metadata"], dict)
    assert captured_kwargs["metadata"]["latest_user_text"] == "List my pending Asana tasks"
