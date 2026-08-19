"""
Four-Control State Machine — User Authority Controls.

Implements the four independent user controls required by P1.7:
  - OBSERVE_ENABLED:  Can the system observe/ingest sensory input?
  - STORE_ENABLED:    Can the system commit engrams to canon/voxel storage?
  - INFER_ENABLED:    Can the system run hypothesis lenses and inference?
  - OUTPUT_ENABLED:   Can the system produce output to the user?

CONTRACT (P1.7):
  - No combined flag may substitute for these four controls.
  - Default is ALL DISABLED — no background observation until the user explicitly enables it.
  - State transitions are explicit and testable.
  - Each control is independent — enabling OBSERVE does not enable STORE.

Socket Pattern: This module imports ONLY typing and enum. No framework deps.

SYNTH:
    purpose: Four-control state machine providing independent OBSERVE/STORE/INFER/OUTPUT user authority controls
    axioms: [local_first, open_process, epistemic_boundary, reversibility_awareness]
    objective: User has granular, independent control over each cognitive operation; default is all-disabled; every transition is explicit and testable
    anti_patterns:
        - Combining multiple controls into a single flag
        - Defaulting any control to enabled
        - Silently enabling one control when another is enabled
        - Allowing operations without checking the required control flag
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ControlFlag(Enum):
    """The four independent user authority controls."""
    OBSERVE_ENABLED = "OBSERVE_ENABLED"
    STORE_ENABLED = "STORE_ENABLED"
    INFER_ENABLED = "INFER_ENABLED"
    OUTPUT_ENABLED = "OUTPUT_ENABLED"


# Default state: ALL DISABLED (no background observation until user enables)
DEFAULT_CONTROLS: dict[ControlFlag, bool] = {
    ControlFlag.OBSERVE_ENABLED: False,
    ControlFlag.STORE_ENABLED: False,
    ControlFlag.INFER_ENABLED: False,
    ControlFlag.OUTPUT_ENABLED: False,
}


class ControlStateError(Exception):
    """Raised when an operation is attempted without the required control enabled."""


@dataclass
class ControlState:
    """
    Four-control state machine for user authority.

    Each control is independent. The default is ALL DISABLED.
    State transitions are explicit via enable()/disable().
    Operations check via can_*() methods or require_*() methods.

    Usage:
        state = ControlState()  # All disabled by default
        state.enable(ControlFlag.OBSERVE_ENABLED)
        state.enable(ControlFlag.STORE_ENABLED)
        if state.can_observe():
            memory.ingest(...)
        state.disable(ControlFlag.OBSERVE_ENABLED)  # Stop observing
    """

    _flags: dict[ControlFlag, bool] = field(default_factory=lambda: dict(DEFAULT_CONTROLS))

    def __post_init__(self):
        """Validate that all four controls are present."""
        for flag in ControlFlag:
            if flag not in self._flags:
                raise ValueError(f"Missing control flag: {flag}")

    def enable(self, flag: ControlFlag) -> None:
        """Enable a control flag."""
        if not isinstance(flag, ControlFlag):
            raise TypeError(f"flag must be ControlFlag, got {type(flag)}")
        self._flags[flag] = True

    def disable(self, flag: ControlFlag) -> None:
        """Disable a control flag."""
        if not isinstance(flag, ControlFlag):
            raise TypeError(f"flag must be ControlFlag, got {type(flag)}")
        self._flags[flag] = False

    def is_enabled(self, flag: ControlFlag) -> bool:
        """Check if a control flag is enabled."""
        return self._flags.get(flag, False)

    # -- Convenience methods ------------------------------------------------

    def can_observe(self) -> bool:
        """Can the system observe/ingest sensory input?"""
        return self.is_enabled(ControlFlag.OBSERVE_ENABLED)

    def can_store(self) -> bool:
        """Can the system commit engrams to storage?"""
        return self.is_enabled(ControlFlag.STORE_ENABLED)

    def can_infer(self) -> bool:
        """Can the system run hypothesis lenses and inference?"""
        return self.is_enabled(ControlFlag.INFER_ENABLED)

    def can_output(self) -> bool:
        """Can the system produce output to the user?"""
        return self.is_enabled(ControlFlag.OUTPUT_ENABLED)

    # -- Require methods (raise if not enabled) -----------------------------

    def require_observe(self) -> None:
        """Raise ControlStateError if OBSERVE_ENABLED is not set."""
        if not self.can_observe():
            raise ControlStateError(
                "OBSERVE_ENABLED is not set — cannot observe or ingest sensory input. "
                "Use state.enable(ControlFlag.OBSERVE_ENABLED) to enable."
            )

    def require_store(self) -> None:
        """Raise ControlStateError if STORE_ENABLED is not set."""
        if not self.can_store():
            raise ControlStateError(
                "STORE_ENABLED is not set — cannot commit engrams to storage. "
                "Use state.enable(ControlFlag.STORE_ENABLED) to enable."
            )

    def require_infer(self) -> None:
        """Raise ControlStateError if INFER_ENABLED is not set."""
        if not self.can_infer():
            raise ControlStateError(
                "INFER_ENABLED is not set — cannot run hypothesis lenses or inference. "
                "Use state.enable(ControlFlag.INFER_ENABLED) to enable."
            )

    def require_output(self) -> None:
        """Raise ControlStateError if OUTPUT_ENABLED is not set."""
        if not self.can_output():
            raise ControlStateError(
                "OUTPUT_ENABLED is not set — cannot produce output to user. "
                "Use state.enable(ControlFlag.OUTPUT_ENABLED) to enable."
            )

    # -- State inspection ---------------------------------------------------

    def get_state(self) -> dict[str, bool]:
        """Return a dict of flag_name -> enabled for all four controls."""
        return {flag.value: self._flags[flag] for flag in ControlFlag}

    def enabled_flags(self) -> set[ControlFlag]:
        """Return the set of currently enabled flags."""
        return {flag for flag in ControlFlag if self._flags[flag]}

    def disabled_flags(self) -> set[ControlFlag]:
        """Return the set of currently disabled flags."""
        return {flag for flag in ControlFlag if not self._flags[flag]}

    def all_disabled(self) -> bool:
        """Check if all controls are disabled (default state)."""
        return all(not self._flags[flag] for flag in ControlFlag)

    def all_enabled(self) -> bool:
        """Check if all controls are enabled."""
        return all(self._flags[flag] for flag in ControlFlag)

    def __repr__(self) -> str:
        flags_str = ", ".join(
            f"{flag.value}={'ON' if self._flags[flag] else 'OFF'}"
            for flag in ControlFlag
        )
        return f"ControlState({flags_str})"
