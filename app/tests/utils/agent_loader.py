from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _ensure_turn_detector_stub() -> None:
    """
    Provide a tiny stub for livekit.plugins.turn_detector.english so that unit
    tests can import the agent entrypoint without requiring the optional plugin.
    """

    module_name = "livekit.plugins.turn_detector.english"
    if module_name in sys.modules:
        return

    try:
        importlib.import_module(module_name)
        return
    except ModuleNotFoundError:
        pass

    try:
        plugins_pkg = importlib.import_module("livekit.plugins")
    except ModuleNotFoundError:
        return

    turn_detector_pkg = ModuleType("livekit.plugins.turn_detector")
    english_pkg = ModuleType(module_name)

    class _StubEnglishModel:
        def __init__(self, *args, **kwargs):
            pass

    english_pkg.EnglishModel = _StubEnglishModel  # type: ignore[attr-defined]

    turn_detector_pkg.english = english_pkg  # type: ignore[attr-defined]
    setattr(plugins_pkg, "turn_detector", turn_detector_pkg)

    sys.modules["livekit.plugins.turn_detector"] = turn_detector_pkg
    sys.modules[module_name] = english_pkg


def load_agent_module(filename: str, module_name: str) -> ModuleType:
    agent_dir = Path(__file__).resolve().parents[3] / "docker" / "agent"
    module_path = agent_dir / filename
    if not module_path.exists():
        pytest.skip(f"Agent module {filename} is not available for import")

    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))

    _ensure_turn_detector_stub()

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        pytest.skip(f"Unable to import agent module {filename} for testing")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_entrypoint_module():
    return load_agent_module("entrypoint.py", "agent_entrypoint_for_tests")


def load_tool_registry_module():
    return load_agent_module("tool_registry.py", "agent_tool_registry_for_tests")
