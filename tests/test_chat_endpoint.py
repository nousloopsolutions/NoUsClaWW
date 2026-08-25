"""Tests for the socket-plugin sidecar endpoints — /chat, /task, /tools, /visage/frame."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sovereign_sockets.brain_interface import BrainResponse
from nousclaww.control_state import ControlFlag, ControlState
from nousclaww.tools.registry import ToolRegistry


def _make_service_with_brain():
    """Create a SidecarService with a mock brain and tool registry."""
    from nousclaww.sidecar.server import SidecarConfig, SidecarService

    config = SidecarConfig(
        db_path=":memory:",
        event_db_path=":memory:",
        enable_mad_dog_loop=False,
        enable_desktop_control=False,
    )
    service = SidecarService(config)

    # Inject a mock brain
    mock_brain = MagicMock()
    mock_brain.reason.return_value = BrainResponse(
        output="Hello from the brain.",
        confidence=0.8,
        void_sockets=[],
        metadata={"model": "test"},
    )
    service.brain = mock_brain

    # Inject a tool registry
    state = ControlState()
    state.enable(ControlFlag.OBSERVE_ENABLED)
    state.enable(ControlFlag.STORE_ENABLED)
    state.enable(ControlFlag.SHELL_ENABLED)
    registry = ToolRegistry(state)
    registry.register(
        "echo",
        lambda **k: f"echo: {k.get('msg', '')}",
        set(),
        "Echo tool",
    )
    service.tool_registry = registry

    # Inject a task loop
    from nousclaww.agent.task_loop import TaskLoop
    service.task_loop = TaskLoop(mock_brain, registry, max_steps=3)

    # Inject a visage HUD
    from nousclaww.visage.visage_hud import VisageHUD
    service.visage = VisageHUD()

    return service


def test_chat_endpoint_success():
    """/chat should return the brain's response."""
    service = _make_service_with_brain()
    resp = service.chat("Hello")
    assert resp.ok
    data = resp.data
    assert data["output"] == "Hello from the brain."
    assert data["confidence"] == 0.8


def test_chat_endpoint_no_brain():
    """/chat should return an error when no brain is available."""
    from nousclaww.sidecar.server import SidecarConfig, SidecarService

    config = SidecarConfig(
        db_path=":memory:",
        event_db_path=":memory:",
        enable_mad_dog_loop=False,
        enable_desktop_control=False,
    )
    service = SidecarService(config)
    service.brain = None  # explicitly no brain

    resp = service.chat("Hello")
    assert not resp.ok
    assert "Brain not available" in resp.error


def test_task_endpoint_success():
    """/task should run the task loop and return a result."""
    service = _make_service_with_brain()
    # Make the brain report done immediately
    service.brain.reason.return_value = BrainResponse(
        output='{"done": true, "summary": "Task complete."}',
        confidence=0.9,
        void_sockets=[],
        metadata={},
    )
    resp = service.task("Simple task")
    assert resp.ok
    data = resp.data
    assert data["completed"]
    assert data["step_count"] >= 1


def test_task_endpoint_no_task_loop():
    """/task should return an error when no task loop is available."""
    from nousclaww.sidecar.server import SidecarConfig, SidecarService

    config = SidecarConfig(
        db_path=":memory:",
        event_db_path=":memory:",
        enable_mad_dog_loop=False,
        enable_desktop_control=False,
    )
    service = SidecarService(config)
    service.task_loop = None

    resp = service.task("test")
    assert not resp.ok
    assert "Task loop not available" in resp.error


def test_tools_list_endpoint():
    """/tools should list registered tools."""
    service = _make_service_with_brain()
    resp = service.tools_list()
    assert resp.ok
    tools = resp.data
    assert any(t["name"] == "echo" for t in tools)


def test_tools_dispatch_endpoint():
    """/tools/dispatch should dispatch a tool call."""
    service = _make_service_with_brain()
    resp = service.tools_dispatch("echo", {"msg": "test"})
    assert resp.ok
    assert "echo: test" in resp.data["output"]


def test_visage_frame_endpoint():
    """/visage/frame should return the Visage HUD state."""
    service = _make_service_with_brain()
    resp = service.visage_frame()
    assert resp.ok
    data = resp.data
    assert "render" in data
    assert "face" in data
    assert "affect" in data


def test_visage_frame_no_visage():
    """/visage/frame should return an error when no visage is available."""
    from nousclaww.sidecar.server import SidecarConfig, SidecarService

    config = SidecarConfig(
        db_path=":memory:",
        event_db_path=":memory:",
        enable_mad_dog_loop=False,
        enable_desktop_control=False,
    )
    service = SidecarService(config)
    service.visage = None

    resp = service.visage_frame()
    assert not resp.ok
    assert "Visage HUD not available" in resp.error
