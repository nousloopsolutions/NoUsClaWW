"""Tools layer — community-side tool registry with control-flag gating.

This package provides a ``ToolRegistry`` that dispatches named tool calls
to registered handlers, with each tool gated behind one or more
``ControlFlag`` values from :mod:`nousclaww.control_state`. A tool call
is rejected with ``ControlStateError`` if the required control flag is
not enabled.

Built-in tools:
- ``file_read`` / ``file_write`` — file operations (gated by OBSERVE_ENABLED / STORE_ENABLED)
- ``shell_exec`` — shell command execution (gated by SHELL_ENABLED)

Additional tools (e.g. browser control) can be registered at runtime via
``ToolRegistry.register()``.
"""

from nousclaww.tools.registry import ToolRegistry, ToolResult
from nousclaww.tools.file_ops import FileReadTool, FileWriteTool
from nousclaww.tools.shell_ops import ShellExecTool

__all__ = [
    "ToolRegistry",
    "ToolResult",
    "FileReadTool",
    "FileWriteTool",
    "ShellExecTool",
]
