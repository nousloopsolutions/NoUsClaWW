"""Tests for the tool registry and built-in tools."""

from __future__ import annotations

import pytest

from nousclaww.control_state import ControlFlag, ControlState, ControlStateError
from nousclaww.tools.registry import ToolRegistry, ToolResult
from nousclaww.tools.file_ops import FileReadTool, FileWriteTool
from nousclaww.tools.shell_ops import ShellExecTool


# ── ToolRegistry ──────────────────────────────────────────────────────────


def test_tool_registry_dispatch_success():
    """A registered tool should dispatch successfully when flags are enabled."""
    state = ControlState()
    state.enable(ControlFlag.OBSERVE_ENABLED)
    registry = ToolRegistry(state)

    def echo(**kwargs):
        return f"echo: {kwargs.get('msg', '')}"

    registry.register("echo", echo, {ControlFlag.OBSERVE_ENABLED}, "Echo tool")
    result = registry.dispatch("echo", msg="hello")
    assert result.success
    assert result.output == "echo: hello"
    assert result.tool == "echo"


def test_tool_registry_dispatch_blocked_by_flag():
    """A tool should raise ControlStateError when a required flag is disabled."""
    state = ControlState()  # all disabled
    registry = ToolRegistry(state)

    def noop(**kwargs):
        return "should not reach"

    registry.register("noop", noop, {ControlFlag.OBSERVE_ENABLED})
    with pytest.raises(ControlStateError, match="OBSERVE_ENABLED"):
        registry.dispatch("noop")


def test_tool_registry_unknown_tool():
    """Dispatching an unknown tool should return a failed ToolResult."""
    state = ControlState()
    registry = ToolRegistry(state)
    result = registry.dispatch("nonexistent")
    assert not result.success
    assert "Unknown tool" in result.error


def test_tool_registry_handler_error():
    """A handler that raises should return a failed ToolResult."""
    state = ControlState()
    state.enable(ControlFlag.OBSERVE_ENABLED)
    registry = ToolRegistry(state)

    def boom(**kwargs):
        raise ValueError("kaboom")

    registry.register("boom", boom, {ControlFlag.OBSERVE_ENABLED})
    result = registry.dispatch("boom")
    assert not result.success
    assert "kaboom" in result.error


def test_tool_registry_list_tools():
    """list_tools should return all registered tools."""
    state = ControlState()
    registry = ToolRegistry(state)
    registry.register("a", lambda **k: "a", set(), "A tool")
    registry.register("b", lambda **k: "b", set(), "B tool")
    tools = registry.list_tools()
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"a", "b"}


def test_tool_registry_has_tool():
    """has_tool should check if a tool is registered."""
    state = ControlState()
    registry = ToolRegistry(state)
    registry.register("exists", lambda **k: "", set())
    assert registry.has_tool("exists")
    assert not registry.has_tool("nope")


def test_tool_registry_multiple_flags():
    """A tool requiring multiple flags should need all of them enabled."""
    state = ControlState()
    state.enable(ControlFlag.OBSERVE_ENABLED)
    # Don't enable STORE_ENABLED
    registry = ToolRegistry(state)
    registry.register(
        "multi",
        lambda **k: "ok",
        {ControlFlag.OBSERVE_ENABLED, ControlFlag.STORE_ENABLED},
    )
    with pytest.raises(ControlStateError, match="STORE_ENABLED"):
        registry.dispatch("multi")

    state.enable(ControlFlag.STORE_ENABLED)
    result = registry.dispatch("multi")
    assert result.success


# ── FileReadTool ──────────────────────────────────────────────────────────


def test_file_read_tool_success(tmp_path):
    """FileReadTool should read a file and return its contents."""
    f = tmp_path / "test.txt"
    f.write_text("hello world", encoding="utf-8")
    tool = FileReadTool()
    result = tool(path=str(f))
    assert result == "hello world"


def test_file_read_tool_not_found():
    """FileReadTool should return an error for a nonexistent file."""
    tool = FileReadTool()
    result = tool(path="/nonexistent/file.txt")
    assert "ERROR" in result
    assert "not found" in result


def test_file_read_tool_truncation(tmp_path):
    """FileReadTool should truncate files larger than max_bytes."""
    f = tmp_path / "big.txt"
    f.write_bytes(b"x" * 1000)
    tool = FileReadTool()
    result = tool(path=str(f), max_bytes=100)
    assert len(result) == 100


# ── FileWriteTool ─────────────────────────────────────────────────────────


def test_file_write_tool_success(tmp_path):
    """FileWriteTool should write content to a file."""
    f = tmp_path / "output.txt"
    tool = FileWriteTool()
    result = tool(path=str(f), content="test content")
    assert "OK" in result
    assert f.read_text(encoding="utf-8") == "test content"


def test_file_write_tool_append(tmp_path):
    """FileWriteTool should append when append=True."""
    f = tmp_path / "append.txt"
    f.write_text("line1\n", encoding="utf-8")
    tool = FileWriteTool()
    tool(path=str(f), content="line2\n", append=True)
    assert f.read_text(encoding="utf-8") == "line1\nline2\n"


def test_file_write_tool_creates_parent_dirs(tmp_path):
    """FileWriteTool should create parent directories."""
    f = tmp_path / "subdir" / "output.txt"
    tool = FileWriteTool()
    tool(path=str(f), content="test")
    assert f.exists()
    assert f.read_text(encoding="utf-8") == "test"


# ── ShellExecTool ─────────────────────────────────────────────────────────


def test_shell_exec_tool_success():
    """ShellExecTool should run a command and return output."""
    tool = ShellExecTool()
    result = tool(command="echo hello")
    assert "hello" in result


def test_shell_exec_tool_error():
    """ShellExecTool should report non-zero exit codes."""
    tool = ShellExecTool()
    result = tool(command="python -c \"import sys; sys.exit(1)\"")
    assert "exit code" in result


def test_shell_exec_tool_timeout():
    """ShellExecTool should timeout and report it."""
    import sys
    if sys.platform == "win32":
        pytest.skip("subprocess.run timeout with shell=True hangs on Windows due to cmd.exe wrapper")
    tool = ShellExecTool()
    result = tool(command="sleep 999", timeout=1)
    # The tool should report a timeout or error
    assert "ERROR" in result or "timed out" in result.lower()
