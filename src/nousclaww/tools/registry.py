"""ToolRegistry — dispatch named tool calls with control-flag gating.

Each tool is registered with a name, a callable handler, and a set of
required ``ControlFlag`` values. When ``dispatch()`` is called, the
registry checks that all required flags are enabled in the provided
``ControlState`` before invoking the handler. If any flag is disabled,
a ``ControlStateError`` is raised.

This enforces the four-control model (AGENTS.md, P1.7) at the tool
dispatch layer: the agent cannot read files unless OBSERVE_ENABLED,
cannot write files unless STORE_ENABLED, cannot run shell commands
unless SHELL_ENABLED, etc.

SYNTH:
    purpose: Dispatch named tool calls to handlers, gated by ControlState flags.
    axioms: [local_first, epistemic_boundary, reversibility_awareness, honest_failure_over_fake_success]
    objective: Every tool call is explicitly authorised by the user's control
        flags before execution — no tool runs without permission.
    anti_patterns:
        - Never dispatch a tool without checking its required control flags
        - Never register a tool without declaring its required flags
        - Never silently bypass a disabled flag

#C Adapted from nousclaww.control_state and the sovereign socket pattern
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from nousclaww.control_state import ControlFlag, ControlState, ControlStateError

logger = logging.getLogger(__name__)


class ToolHandler(Protocol):
    """Protocol for tool handler callables."""

    def __call__(self, **kwargs: Any) -> str: ...


@dataclass
class ToolRegistration:
    """A registered tool with its handler and required control flags."""

    name: str
    handler: ToolHandler
    required_flags: set[ControlFlag]
    description: str = ""


@dataclass
class ToolResult:
    """The outcome of a tool dispatch.

    Attributes:
        tool: The name of the tool that was dispatched.
        output: The tool's output string (empty on error).
        error: Error message if the tool failed, else None.
        success: True if the tool executed without error.
    """

    tool: str
    output: str = ""
    error: str | None = None
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """Registry of named tools with control-flag gating.

    Usage::

        registry = ToolRegistry(control_state)
        registry.register("file_read", FileReadTool(), {ControlFlag.OBSERVE_ENABLED})
        result = registry.dispatch("file_read", path="/tmp/foo.txt")
    """

    def __init__(self, control_state: ControlState) -> None:
        self._control_state = control_state
        self._tools: dict[str, ToolRegistration] = {}

    def register(
        self,
        name: str,
        handler: ToolHandler,
        required_flags: set[ControlFlag],
        description: str = "",
    ) -> None:
        """Register a tool with its required control flags.

        Args:
            name: Unique tool name (e.g. ``"file_read"``).
            handler: Callable that accepts keyword arguments and returns a string.
            required_flags: Control flags that must be enabled before dispatch.
            description: Human-readable description of the tool.
        """
        if name in self._tools:
            logger.warning("ToolRegistry: overwriting existing tool '%s'", name)
        self._tools[name] = ToolRegistration(
            name=name,
            handler=handler,
            required_flags=required_flags,
            description=description,
        )
        logger.debug("ToolRegistry: registered '%s' (flags=%s)", name, required_flags)

    def dispatch(self, name: str, **kwargs: Any) -> ToolResult:
        """Dispatch a tool call after checking control flags.

        Args:
            name: The registered tool name.
            **kwargs: Arguments passed to the tool handler.

        Returns:
            ToolResult with the tool's output or an error message.

        Raises:
            ControlStateError: If a required control flag is not enabled.
            KeyError: If the tool name is not registered.
        """
        reg = self._tools.get(name)
        if reg is None:
            return ToolResult(
                tool=name,
                output="",
                error=f"Unknown tool: '{name}'",
                success=False,
            )

        # Check all required control flags
        for flag in reg.required_flags:
            if not self._control_state.can(flag):
                raise ControlStateError(
                    f"Tool '{name}' requires {flag.value} but it is disabled"
                )

        try:
            output = reg.handler(**kwargs)
            return ToolResult(tool=name, output=output, success=True)
        except ControlStateError:
            raise
        except Exception as exc:
            logger.error("Tool '%s' failed: %s", name, exc)
            return ToolResult(
                tool=name,
                output="",
                error=str(exc),
                success=False,
            )

    def list_tools(self) -> list[ToolRegistration]:
        """Return all registered tools."""
        return list(self._tools.values())

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools
