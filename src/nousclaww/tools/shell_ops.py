"""Shell execution tool — run shell commands with control-flag gating.

Gated by ``SHELL_ENABLED`` (added to ControlState by this integration).
The tool runs commands via ``subprocess`` with a timeout and captures
stdout/stderr. It is deliberately simple: the agent provides a command
string, the tool returns the combined output.

Safety:
- The tool does NOT run unless SHELL_ENABLED is true (enforced by the
  ToolRegistry's control-flag check).
- A default timeout of 30 seconds prevents hangs.
- The tool does not run interactive commands (no TTY).

SYNTH:
    purpose: Shell command execution tool gated by SHELL_ENABLED.
    axioms: [local_first, reversibility_awareness, honest_failure_over_fake_success]
    objective: Allow the agent to run shell commands when the user has
        explicitly enabled shell access, with timeout and honest error
        reporting.
    anti_patterns:
        - Never run commands without SHELL_ENABLED
        - Never run interactive commands
        - Never hide command failures
        - Never run commands with no timeout

#C Adapted from the tool registry pattern and nousclaww.control_state
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class ShellExecTool:
    """Run a shell command and return its output.

    Handler signature: ``__call__(command: str, timeout: int = 30, cwd: str | None = None) -> str``
    """

    def __call__(
        self,
        command: str,
        timeout: int = 30,
        cwd: str | None = None,
        **_: Any,
    ) -> str:
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            output = result.stdout
            if result.stderr:
                output = f"{output}\n[stderr]\n{result.stderr}" if output else result.stderr
            if result.returncode != 0:
                output = f"{output}\n[exit code: {result.returncode}]" if output else f"[exit code: {result.returncode}]"
            return output.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            logger.warning("ShellExecTool: command timed out after %ds: %s", timeout, command)
            return f"ERROR: command timed out after {timeout}s"
        except Exception as exc:
            logger.error("ShellExecTool failed: %s", exc)
            return f"ERROR: {exc}"
