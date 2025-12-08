from __future__ import annotations

import types

import pytest

from .utils.agent_loader import load_entrypoint_module


@pytest.fixture(scope="module")
def agent_entrypoint():
    return load_entrypoint_module()


def test_normalize_transcript_text_collapses_whitespace(agent_entrypoint):
    normalize = agent_entrypoint._normalize_transcript_text  # type: ignore[attr-defined]
    assert normalize("  Hello   world  ") == "hello world"
    assert normalize("Line one\nLine   two") == "line one line two"
    assert normalize(None) == ""


def test_should_skip_user_commit_only_with_active_turn(agent_entrypoint):
    should_skip = agent_entrypoint._should_skip_user_commit  # type: ignore[attr-defined]
    agent = types.SimpleNamespace(
        _last_user_commit="Check my Asana tasks please",
        _pending_user_commit=False,
        _current_turn_id=None,
    )

    # No active turn or pending commit => do not skip even if text matches
    assert should_skip(agent, "Check my Asana tasks please") is False

    # Pending commit should trigger dedupe
    agent._pending_user_commit = True
    assert should_skip(agent, "Check my   Asana    tasks  please") is True

    # Clearing pending flag but keeping active turn should still dedupe
    agent._pending_user_commit = False
    agent._current_turn_id = "turn-123"
    assert should_skip(agent, "Check my Asana tasks please") is True

    # Different text within same turn should not dedupe
    assert should_skip(agent, "Add this to Asana") is False
