"""Tests for the TaskLoop — multi-step autonomous task execution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sovereign_sockets.brain_interface import BrainResponse
from nousclaww.control_state import ControlFlag, ControlState
from nousclaww.tools.registry import ToolRegistry
from nousclaww.agent.task_loop import TaskLoop, TaskResult, StepResult


def _make_brain(responses: list[str]) -> MagicMock:
    """Create a mock brain that returns the given responses in sequence."""
    brain = MagicMock()
    brain.reason.side_effect = [
        BrainResponse(output=r, confidence=0.7, void_sockets=[], metadata={})
        for r in responses
    ]
    return brain


def _make_tools() -> ToolRegistry:
    """Create a ToolRegistry with OBSERVE and STORE enabled."""
    state = ControlState()
    state.enable(ControlFlag.OBSERVE_ENABLED)
    state.enable(ControlFlag.STORE_ENABLED)
    registry = ToolRegistry(state)
    registry.register(
        "echo",
        lambda **k: f"echoed: {k.get('msg', '')}",
        set(),
        "Echo tool",
    )
    return registry


def test_task_loop_immediate_done():
    """When the brain reports done in the first plan, the task completes immediately."""
    brain = _make_brain([
        '{"done": true, "summary": "Task already complete."}',
    ])
    tools = _make_tools()
    loop = TaskLoop(brain, tools, max_steps=5)

    result = loop.run("Do nothing")
    assert result.completed
    assert len(result.steps) == 1
    assert result.steps[0].done
    assert "complete" in result.summary.lower()


def test_task_loop_one_step_then_done():
    """The loop should plan, act, reflect, and then complete."""
    brain = _make_brain([
        '{"action": "echo", "args": {"msg": "hi"}, "done": false}',
        '{"done": true, "summary": "Echoed successfully."}',
    ])
    tools = _make_tools()
    loop = TaskLoop(brain, tools, max_steps=5)

    result = loop.run("Echo hi")
    assert result.completed
    assert len(result.steps) == 1
    assert result.steps[0].action == "echo"
    assert "echoed: hi" in result.steps[0].observation


def test_task_loop_max_steps():
    """The loop should terminate after max_steps with an error."""
    # Brain never reports done — keeps requesting the same action
    responses = []
    for i in range(20):
        responses.append('{"action": "echo", "args": {"msg": "loop"}, "done": false}')
        responses.append('{"done": false, "summary": "not done yet"}')
    brain = _make_brain(responses)
    tools = _make_tools()
    loop = TaskLoop(brain, tools, max_steps=3)

    result = loop.run("Never-ending task")
    assert not result.completed
    assert result.error == "max_steps_exceeded"
    assert len(result.steps) == 3


def test_task_loop_cancel():
    """Cancel should stop the loop and mark the result as cancelled."""
    responses = []
    for i in range(20):
        responses.append('{"action": "echo", "args": {"msg": "x"}, "done": false}')
        responses.append('{"done": false, "summary": "not done"}')
    brain = _make_brain(responses)
    tools = _make_tools()
    loop = TaskLoop(brain, tools, max_steps=10)

    # Cancel before the first step
    loop.cancel()
    result = loop.run("Cancelled task")
    assert result.cancelled
    assert not result.completed


def test_task_loop_unknown_tool():
    """When the brain requests an unknown tool, the observation should report an error."""
    brain = _make_brain([
        '{"action": "nonexistent", "args": {}, "done": false}',
        '{"done": true, "summary": "Done despite error."}',
    ])
    tools = _make_tools()
    loop = TaskLoop(brain, tools, max_steps=5)

    result = loop.run("Use unknown tool")
    assert result.completed
    assert "ERROR" in result.steps[0].observation


def test_task_loop_extract_json_direct():
    """_extract_json should parse a direct JSON string."""
    data = TaskLoop._extract_json('{"key": "value"}')
    assert data == {"key": "value"}


def test_task_loop_extract_json_embedded():
    """_extract_json should find JSON embedded in text."""
    data = TaskLoop._extract_json('Here is the plan:\n{"action": "test", "done": false}\nDone.')
    assert data is not None
    assert data["action"] == "test"


def test_task_loop_extract_json_no_json():
    """_extract_json should return None when there's no JSON."""
    data = TaskLoop._extract_json("no json here")
    assert data is None


def test_task_loop_result_dataclass():
    """TaskResult and StepResult should be constructible with defaults."""
    tr = TaskResult(task="test")
    assert tr.task == "test"
    assert tr.steps == []
    assert not tr.completed
    assert not tr.cancelled

    sr = StepResult(step=0, plan="plan")
    assert sr.step == 0
    assert sr.action is None
