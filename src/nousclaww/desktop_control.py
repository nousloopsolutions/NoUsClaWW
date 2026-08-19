"""
Desktop control via cua-driver — background computer-use for Windows/macOS/Linux.

Wraps the open-source cua-driver (https://github.com/trycua/cua) to provide
background desktop control without stealing cursor focus. Works with any
tool-capable model — local Ollama models, cloud models, or the hybrid LLMRouter.

Gates on existing ControlFlags:
  - OBSERVE_ENABLED required for all read operations (list_apps, list_windows,
    get_window_state, get_desktop_state, read_terminal)
  - OUTPUT_ENABLED required for all input operations (click, type_text,
    press_key, scroll, drag, launch_app, terminate_app)

Contract:
    - Never moves the user's physical cursor or steals keyboard focus.
    - All read operations require OBSERVE_ENABLED.
    - All input operations require OBSERVE_ENABLED + OUTPUT_ENABLED.
    - cua-driver must be installed: pip install cua-driver or irm install.
    - Graceful degradation when cua-driver is not installed — returns
      DesktopControlError with install instructions, never crashes.
    - All operations logged to event log when available.

SYNTH:
    purpose: Background desktop control via cua-driver wrapper — no focus steal, cross-platform, model-agnostic
    axioms: [local_first, llm_agnostic, epistemic_boundary, reversibility_awareness, honest_failure_over_fake_success]
    objective: Provide read and input desktop operations gated by ControlState flags, with graceful degradation when cua-driver is unavailable
    anti_patterns:
        - Moving the user's physical cursor or stealing keyboard focus
        - Performing read operations without OBSERVE_ENABLED
        - Performing input operations without OBSERVE_ENABLED and OUTPUT_ENABLED
        - Crashing when cua-driver is not installed instead of returning a clear error
        - Sending desktop state or screenshots to any remote service
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nousclaww.control_state import ControlStateError

logger = logging.getLogger(__name__)


class DesktopControlError(Exception):
    """Raised when desktop control fails or is unavailable."""


@dataclass
class DesktopControlConfig:
    """Configuration for the desktop control wrapper."""
    # CLI binary name — resolved via PATH
    binary: str = "cua-driver"
    # Screenshot output directory (None = embed in response)
    screenshot_dir: str | None = None
    # Default capture mode: "som" (screenshot+overlays), "vision" (plain), "ax" (tree only)
    default_mode: str = "ax"
    # Max elements in AX tree walks (prevents context blowup on Electron apps)
    max_elements: int = 2000
    # Max depth in AX tree walks
    max_depth: int = 25
    # Timeout for CLI calls in seconds
    cli_timeout: float = 30.0
    # Whether to use the Python SDK if available (faster, in-process)
    prefer_sdk: bool = True

    def __post_init__(self):
        if self.default_mode not in ("som", "vision", "ax"):
            raise ValueError(f"default_mode must be 'som', 'vision', or 'ax', got '{self.default_mode}'")
        if self.max_elements < 1:
            raise ValueError("max_elements must be >= 1")
        if self.max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        if self.cli_timeout <= 0:
            raise ValueError("cli_timeout must be positive")


@dataclass
class ActionResult:
    """Result of a desktop control action."""
    ok: bool
    tool: str
    summary: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    screenshot_path: str | None = None
    error: str | None = None
    duration_ms: float = 0.0


class DesktopControl:
    """Background desktop control via cua-driver.

    Wraps the cua-driver CLI/SDK to provide:
    - Window/app inspection (list_apps, list_windows, get_window_state)
    - Screenshot capture (get_desktop_state, get_window_state with screenshot)
    - Background input (click, type_text, press_key, scroll, drag)
    - App management (launch_app, terminate_app)
    - Terminal reading (read_terminal via get_window_state on terminal windows)

    All operations are gated by ControlState:
    - Reads require OBSERVE_ENABLED
    - Inputs require OBSERVE_ENABLED + OUTPUT_ENABLED

    Usage:
        from nousclaww.control_state import ControlFlag, ControlState
        from nousclaww.desktop_control import DesktopControl

        state = ControlState()
        state.enable(ControlFlag.OBSERVE_ENABLED)
        state.enable(ControlFlag.OUTPUT_ENABLED)

        dc = DesktopControl(control_state=state)
        windows = dc.list_windows()
        dc.click(pid=1234, window_id=5678, element_index=3)
    """

    def __init__(
        self,
        control_state=None,
        config: DesktopControlConfig | None = None,
        event_log=None,
    ):
        self.control_state = control_state
        self.config = config or DesktopControlConfig()
        self.event_log = event_log
        self._sdk = None
        self._sdk_checked = False
        self._binary_path: str | None = None
        self._binary_checked = False

    # -- Availability checks ------------------------------------------------

    def is_available(self) -> bool:
        """Check if cua-driver is available (SDK or CLI)."""
        return self._get_sdk() is not None or self._get_binary() is not None

    def _get_sdk(self):
        """Try to import the cua-driver Python SDK. Returns None if unavailable."""
        if self._sdk_checked:
            return self._sdk
        self._sdk_checked = True
        if not self.config.prefer_sdk:
            return None
        try:
            from cua_driver import CuaDriver  # type: ignore
            self._sdk = CuaDriver.create()
            logger.info("cua-driver Python SDK loaded")
        except ImportError:
            logger.debug("cua-driver Python SDK not installed — will use CLI")
        except Exception as e:
            logger.debug(f"cua-driver SDK init failed: {e}")
        return self._sdk

    def _get_binary(self) -> str | None:
        """Find the cua-driver CLI binary. Returns None if not found."""
        if self._binary_checked:
            return self._binary_path
        self._binary_checked = True
        self._binary_path = shutil.which(self.config.binary)
        if self._binary_path:
            logger.debug(f"cua-driver CLI found at {self._binary_path}")
        else:
            logger.debug("cua-driver CLI not found in PATH")
        return self._binary_path

    def _ensure_available(self) -> None:
        """Raise DesktopControlError if cua-driver is not available."""
        if not self.is_available():
            raise DesktopControlError(
                "cua-driver is not installed. Install with one of:\n"
                "  pip install cua-driver\n"
                "  irm https://cua.ai/driver/install.ps1 | iex   (Windows PowerShell)\n"
                "  curl -fsSL https://cua.ai/driver/install.sh | bash   (macOS/Linux)"
            )

    # -- Control state gating ------------------------------------------------

    def _require_observe(self) -> None:
        """Raise if OBSERVE_ENABLED is not set."""
        if self.control_state is None:
            return  # No control state = unrestricted (testing)
        if not self.control_state.can_observe():
            raise ControlStateError(
                "OBSERVE_ENABLED is not set — cannot read desktop state. "
                "Use state.enable(ControlFlag.OBSERVE_ENABLED) to enable."
            )

    def _require_output(self) -> None:
        """Raise if OUTPUT_ENABLED is not set (also requires OBSERVE)."""
        if self.control_state is None:
            return
        self._require_observe()
        if not self.control_state.can_output():
            raise ControlStateError(
                "OUTPUT_ENABLED is not set — cannot send desktop input. "
                "Use state.enable(ControlFlag.OUTPUT_ENABLED) to enable."
            )

    # -- CLI dispatch --------------------------------------------------------

    def _call_cli(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a cua-driver tool via the CLI."""
        self._ensure_available()
        binary = self._get_binary()
        if not binary:
            raise DesktopControlError("No cua-driver binary available")

        cmd = [binary, "call", tool]
        if args:
            cmd.append(json.dumps(args))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.cli_timeout,
            )
            if proc.returncode != 0:
                raise DesktopControlError(
                    f"cua-driver {tool} failed (exit {proc.returncode}): {proc.stderr.strip()}"
                )
            # CLI returns text content; try to parse as JSON
            output = proc.stdout.strip()
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                return {"raw_output": output}
        except subprocess.TimeoutExpired:
            raise DesktopControlError(
                f"cua-driver {tool} timed out after {self.config.cli_timeout}s"
            )

    def _call_tool(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a cua-driver tool via SDK (preferred) or CLI (fallback)."""
        sdk = self._get_sdk()
        if sdk is not None:
            try:
                result = sdk.call_tool(tool, args or {})
                return result if isinstance(result, dict) else {"raw_output": str(result)}
            except Exception as e:
                logger.warning(f"SDK call failed for {tool}, falling back to CLI: {e}")
        return self._call_cli(tool, args)

    def _log(self, event_type: str, tool: str, inputs: dict, outputs: dict, duration_ms: float, status: str = "completed"):
        """Log to event log if available."""
        if self.event_log:
            self.event_log.log_operation(
                event_type=event_type,
                module="desktop_control",
                operation=tool,
                inputs=inputs,
                outputs=outputs,
                status=status,
                duration_ms=duration_ms,
            )

    # -- Inspection tools (require OBSERVE_ENABLED) --------------------------

    def list_apps(self) -> ActionResult:
        """List running and installed desktop apps."""
        self._require_observe()
        start = time.time()
        try:
            data = self._call_tool("list_apps")
            result = ActionResult(
                ok=True, tool="list_apps",
                summary=f"Found {len(data.get('apps', []))} apps",
                data=data,
                duration_ms=(time.time() - start) * 1000,
            )
            self._log("observe", "list_apps", {}, data, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="list_apps", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def list_windows(self, on_screen_only: bool = False) -> ActionResult:
        """List all top-level desktop windows."""
        self._require_observe()
        start = time.time()
        args = {"on_screen_only": on_screen_only}
        try:
            data = self._call_tool("list_windows", args)
            result = ActionResult(
                ok=True, tool="list_windows",
                summary=f"Found {len(data.get('windows', []))} windows",
                data=data, duration_ms=(time.time() - start) * 1000,
            )
            self._log("observe", "list_windows", args, data, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="list_windows", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def get_window_state(
        self,
        pid: int,
        window_id: int,
        include_screenshot: bool = True,
        query: str | None = None,
    ) -> ActionResult:
        """Get the accessibility tree and optional screenshot for a window.

        This is the primary tool for understanding what's on screen.
        Returns element indices that can be passed to click(), type_text(), etc.
        """
        self._require_observe()
        start = time.time()
        args: dict[str, Any] = {
            "pid": pid,
            "window_id": window_id,
            "include_screenshot": include_screenshot,
            "max_elements": self.config.max_elements,
            "max_depth": self.config.max_depth,
        }
        if query:
            args["query"] = query

        # Handle screenshot output to file if configured
        screenshot_path = None
        if include_screenshot and self.config.screenshot_dir:
            screenshot_path = str(
                Path(self.config.screenshot_dir) / f"window_{window_id}_{int(time.time())}.png"
            )
            args["screenshot_out_file"] = screenshot_path

        try:
            data = self._call_tool("get_window_state", args)
            element_count = data.get("element_count", 0)
            result = ActionResult(
                ok=True, tool="get_window_state",
                summary=f"Window {window_id}: {element_count} elements",
                data=data,
                screenshot_path=screenshot_path,
                duration_ms=(time.time() - start) * 1000,
            )
            self._log("observe", "get_window_state", args, data, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="get_window_state", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def get_desktop_state(self) -> ActionResult:
        """Capture the full desktop screenshot in native resolution."""
        self._require_observe()
        start = time.time()
        screenshot_path = None
        if self.config.screenshot_dir:
            screenshot_path = str(
                Path(self.config.screenshot_dir) / f"desktop_{int(time.time())}.png"
            )
        args: dict[str, Any] = {}
        if screenshot_path:
            args["screenshot_out_file"] = screenshot_path
        try:
            data = self._call_tool("get_desktop_state", args)
            result = ActionResult(
                ok=True, tool="get_desktop_state",
                summary="Desktop captured",
                data=data,
                screenshot_path=screenshot_path,
                duration_ms=(time.time() - start) * 1000,
            )
            self._log("observe", "get_desktop_state", args, data, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="get_desktop_state", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def get_accessibility_tree(self) -> ActionResult:
        """Get a lightweight snapshot of the desktop — running apps and visible windows."""
        self._require_observe()
        start = time.time()
        try:
            data = self._call_tool("get_accessibility_tree")
            result = ActionResult(
                ok=True, tool="get_accessibility_tree",
                summary="Desktop AX tree snapshot",
                data=data, duration_ms=(time.time() - start) * 1000,
            )
            self._log("observe", "get_accessibility_tree", {}, data, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="get_accessibility_tree", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    # -- Terminal reading (specialized window inspection) --------------------

    def read_terminal(self, pid: int, window_id: int) -> ActionResult:
        """Read the text content of a terminal window.

        Uses get_window_state with include_screenshot=False to get the AX tree,
        then extracts text content from terminal elements. Works with
        Windows Terminal, PowerShell, cmd.exe, and any terminal that exposes
        accessibility text.
        """
        self._require_observe()
        start = time.time()
        try:
            state = self.get_window_state(
                pid=pid, window_id=window_id,
                include_screenshot=False,
            )
            if not state.ok:
                return state

            # Extract text from the AX tree elements
            elements = state.data.get("structuredContent", {}).get("elements", [])
            text_lines = []
            for el in elements:
                value = el.get("value", "")
                label = el.get("label", "")
                role = el.get("role", "")
                # Terminal text typically shows up in text elements with values
                if value and role in ("text", "textarea", "document", "generic"):
                    text_lines.append(value)
                elif label and role in ("text", "textarea", "document", "generic"):
                    text_lines.append(label)

            terminal_text = "\n".join(text_lines)
            result = ActionResult(
                ok=True, tool="read_terminal",
                summary=f"Read {len(text_lines)} text blocks from terminal",
                data={
                    "text": terminal_text,
                    "line_count": len(text_lines),
                    "raw_elements": len(elements),
                },
                duration_ms=(time.time() - start) * 1000,
            )
            self._log("observe", "read_terminal",
                      {"pid": pid, "window_id": window_id},
                      {"line_count": len(text_lines)}, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="read_terminal", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    # -- Input actions (require OBSERVE + OUTPUT) ----------------------------

    def click(
        self,
        pid: int,
        window_id: int,
        element_index: int | None = None,
        x: float | None = None,
        y: float | None = None,
        button: str = "left",
        double: bool = False,
    ) -> ActionResult:
        """Click an element by index or pixel coordinates."""
        self._require_output()
        start = time.time()
        args: dict[str, Any] = {
            "pid": pid,
            "window_id": window_id,
            "button": button,
            "double": double,
        }
        if element_index is not None:
            args["element_index"] = element_index
        elif x is not None and y is not None:
            args["x"] = x
            args["y"] = y
        else:
            return ActionResult(
                ok=False, tool="click",
                error="Must provide element_index or (x, y) coordinates",
                duration_ms=0.0,
            )
        try:
            data = self._call_tool("click", args)
            result = ActionResult(
                ok=True, tool="click",
                summary=f"Clicked {'element' if element_index is not None else 'pixel'} target",
                data=data, duration_ms=(time.time() - start) * 1000,
            )
            self._log("output", "click", args, data, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="click", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def type_text(
        self,
        pid: int,
        window_id: int,
        text: str,
        element_index: int | None = None,
    ) -> ActionResult:
        """Type text into an element or the focused window."""
        self._require_output()
        start = time.time()
        args: dict[str, Any] = {
            "pid": pid,
            "window_id": window_id,
            "text": text,
        }
        if element_index is not None:
            args["element_index"] = element_index
        try:
            data = self._call_tool("type_text", args)
            result = ActionResult(
                ok=True, tool="type_text",
                summary=f"Typed {len(text)} chars",
                data=data, duration_ms=(time.time() - start) * 1000,
            )
            self._log("output", "type_text", args, data, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="type_text", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def press_key(
        self,
        pid: int,
        window_id: int,
        key: str,
        modifiers: list[str] | None = None,
    ) -> ActionResult:
        """Press a key (with optional modifiers like ['ctrl', 'shift'])."""
        self._require_output()
        start = time.time()
        args: dict[str, Any] = {
            "pid": pid,
            "window_id": window_id,
            "key": key,
        }
        if modifiers:
            args["modifiers"] = modifiers
        try:
            data = self._call_tool("press_key", args)
            result = ActionResult(
                ok=True, tool="press_key",
                summary=f"Pressed {key}" + (f" + {modifiers}" if modifiers else ""),
                data=data, duration_ms=(time.time() - start) * 1000,
            )
            self._log("output", "press_key", args, data, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="press_key", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def scroll(
        self,
        pid: int,
        window_id: int,
        dx: float = 0.0,
        dy: float = 0.0,
        element_index: int | None = None,
    ) -> ActionResult:
        """Scroll horizontally (dx) and/or vertically (dy)."""
        self._require_output()
        start = time.time()
        args: dict[str, Any] = {
            "pid": pid,
            "window_id": window_id,
            "dx": dx,
            "dy": dy,
        }
        if element_index is not None:
            args["element_index"] = element_index
        try:
            data = self._call_tool("scroll", args)
            result = ActionResult(
                ok=True, tool="scroll",
                summary=f"Scrolled dx={dx}, dy={dy}",
                data=data, duration_ms=(time.time() - start) * 1000,
            )
            self._log("output", "scroll", args, data, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="scroll", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    # -- App management (require OUTPUT) -------------------------------------

    def launch_app(self, app_name: str | None = None, bundle_id: str | None = None) -> ActionResult:
        """Launch a desktop application."""
        self._require_output()
        start = time.time()
        args: dict[str, Any] = {}
        if bundle_id:
            args["bundle_id"] = bundle_id
        elif app_name:
            args["app_name"] = app_name
        else:
            return ActionResult(
                ok=False, tool="launch_app",
                error="Must provide app_name or bundle_id",
                duration_ms=0.0,
            )
        try:
            data = self._call_tool("launch_app", args)
            result = ActionResult(
                ok=True, tool="launch_app",
                summary=f"Launched {app_name or bundle_id}",
                data=data, duration_ms=(time.time() - start) * 1000,
            )
            self._log("output", "launch_app", args, data, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="launch_app", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def terminate_app(self, pid: int) -> ActionResult:
        """Terminate a running application by PID."""
        self._require_output()
        start = time.time()
        args = {"pid": pid}
        try:
            data = self._call_tool("terminate_app", args)
            result = ActionResult(
                ok=True, tool="terminate_app",
                summary=f"Terminated PID {pid}",
                data=data, duration_ms=(time.time() - start) * 1000,
            )
            self._log("output", "terminate_app", args, data, result.duration_ms)
            return result
        except Exception as e:
            return ActionResult(
                ok=False, tool="terminate_app", error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    # -- Diagnostics ---------------------------------------------------------

    def doctor(self) -> dict[str, Any]:
        """Run cua-driver diagnostic checks. Returns probe results."""
        self._ensure_available()
        binary = self._get_binary()
        if not binary:
            return {"available": False, "error": "No binary found"}
        try:
            proc = subprocess.run(
                [binary, "doctor", "--json"],
                capture_output=True, text=True,
                timeout=10.0,
            )
            if proc.returncode == 0:
                return json.loads(proc.stdout.strip())
            return {
                "available": True,
                "healthy": False,
                "exit_code": proc.returncode,
                "stderr": proc.stderr.strip(),
            }
        except Exception as e:
            return {"available": True, "healthy": False, "error": str(e)}

    def status(self) -> dict[str, Any]:
        """Check if the cua-driver daemon is running."""
        self._ensure_available()
        binary = self._get_binary()
        if not binary:
            return {"available": False}
        try:
            proc = subprocess.run(
                [binary, "status"],
                capture_output=True, text=True,
                timeout=5.0,
            )
            return {
                "available": True,
                "running": proc.returncode == 0,
                "output": proc.stdout.strip(),
            }
        except Exception as e:
            return {"available": True, "running": False, "error": str(e)}
