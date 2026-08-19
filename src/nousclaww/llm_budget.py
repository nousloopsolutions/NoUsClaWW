"""Wall-clock and call-count budget for offline LLM passes.

SYNTH:
    purpose: Bound offline LLM passes so they never run forever
    axioms: [local_first, epistemic_boundary, honest_failure_over_fake_success, completion_assumption]
    objective: A budget tracker that enforces both a wall-clock deadline and a maximum call count,
        so consolidation/distillation offline passes terminate predictably even if the LLM is slow
        or looping.
    anti_patterns:
        - Never allow a call after the budget is exhausted without an explicit reset()
        - Never silently reset the budget when a limit is hit
        - Never hide the fact that the budget was exhausted from the caller
        - Never use wall-clock time as the sole limit — call count is independent and required
"""
#C Inspired by PMB (Project Memory Bank) patterns

from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)


class LLMBudget:
    """Wall-clock and call-count budget for offline LLM passes.

    The budget bounds an offline LLM pass along two independent axes:

    1. **Wall-clock time** — ``wall_clock_seconds`` from the moment the budget
       is started (lazily on first :meth:`can_call` / :meth:`record_call`).
    2. **Call count** — at most ``max_calls`` individual LLM invocations.

    Either limit, once exceeded, causes :meth:`can_call` to return ``False``
    and the :attr:`exhausted` property to return ``True``.

    This class is NOT thread-safe by design — offline passes are expected to
    run on a single thread. If concurrent use is needed, wrap calls in an
    external lock.

    Usage:
        budget = LLMBudget(wall_clock_seconds=300, max_calls=50)
        while budget.can_call():
            response = llm.generate(prompt)
            budget.record_call(duration_seconds=elapsed)
        if budget.exhausted:
            logger.info("LLM pass stopped: budget exhausted")
    """

    def __init__(
        self,
        wall_clock_seconds: float,
        max_calls: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """
        Initialize the budget.

        The clock starts lazily — ``start_time`` is set on the first call to
        :meth:`can_call` or :meth:`record_call`, not at construction time.
        This allows a budget to be created ahead of time and activated only
        when the pass actually begins.

        Args:
            wall_clock_seconds: Maximum wall-clock duration. Must be > 0.
            max_calls: Maximum number of LLM calls. Must be >= 1.
            clock: Optional callable returning monotonic time. Defaults to
                ``time.monotonic``. Injected for deterministic testing.

        Raises:
            ValueError: If ``wall_clock_seconds`` or ``max_calls`` is out of range.
        """
        if wall_clock_seconds <= 0:
            raise ValueError(
                f"wall_clock_seconds must be > 0, got {wall_clock_seconds}"
            )
        if max_calls < 1:
            raise ValueError(f"max_calls must be >= 1, got {max_calls}")

        self._wall_clock_seconds = wall_clock_seconds
        self._max_calls = max_calls
        self._clock = clock if clock is not None else time.monotonic

        self._calls_made: int = 0
        self._start_time: float | None = None
        self._elapsed: float = 0.0

    # ── Configuration properties ─────────────────────────────────────────

    @property
    def wall_clock_seconds(self) -> float:
        """The configured wall-clock budget in seconds."""
        return self._wall_clock_seconds

    @property
    def max_calls(self) -> int:
        """The configured maximum number of calls."""
        return self._max_calls

    # ── Live state properties ────────────────────────────────────────────

    @property
    def calls_made(self) -> int:
        """Number of calls recorded so far."""
        return self._calls_made

    @property
    def start_time(self) -> float | None:
        """Monotonic timestamp when the budget was first activated, or None."""
        return self._start_time

    @property
    def elapsed_time(self) -> float:
        """Wall-clock seconds elapsed since the budget started.

        Returns 0.0 if the budget has not been activated yet.
        """
        if self._start_time is None:
            return 0.0
        return self._clock() - self._start_time

    def remaining_time(self) -> float:
        """Seconds remaining in the wall-clock budget (never negative)."""
        if self._start_time is None:
            return self._wall_clock_seconds
        return max(0.0, self._wall_clock_seconds - self.elapsed_time)

    def remaining_calls(self) -> int:
        """Number of calls still permitted before the call-count limit."""
        return max(0, self._max_calls - self._calls_made)

    @property
    def exhausted(self) -> bool:
        """True if either the wall-clock or call-count budget is exhausted."""
        if self._calls_made >= self._max_calls:
            return True
        if self._start_time is not None and self.elapsed_time >= self._wall_clock_seconds:
            return True
        return False

    @property
    def exhausted_reason(self) -> str | None:
        """Human-readable reason for exhaustion, or None if not exhausted."""
        if self._calls_made >= self._max_calls:
            return (
                f"Call count exhausted: {self._calls_made}/{self._max_calls} calls made"
            )
        if self._start_time is not None and self.elapsed_time >= self._wall_clock_seconds:
            return (
                f"Wall-clock exhausted: {self.elapsed_time:.1f}s >= "
                f"{self._wall_clock_seconds:.1f}s budget"
            )
        return None

    # ── Core operations ──────────────────────────────────────────────────

    def can_call(self) -> bool:
        """Check whether another LLM call is permitted.

        Activates the budget (sets ``start_time``) on first invocation.

        Returns:
            True if both the call-count and wall-clock budgets have remaining
            capacity. False otherwise.
        """
        self._ensure_started()
        if self._calls_made >= self._max_calls:
            return False
        if self.elapsed_time >= self._wall_clock_seconds:
            return False
        return True

    def record_call(self, duration_seconds: float) -> None:
        """Record a completed LLM call.

        Activates the budget (sets ``start_time``) on first invocation.
        The ``duration_seconds`` parameter is accepted for bookkeeping and
        future per-call analysis, but the wall-clock limit is measured from
        ``start_time`` to the current clock, not by summing durations. This
        avoids drift when there is idle time between calls.

        Args:
            duration_seconds: How long the individual call took. Must be >= 0.

        Raises:
            ValueError: If ``duration_seconds`` is negative.
        """
        if duration_seconds < 0:
            raise ValueError(
                f"duration_seconds must be >= 0, got {duration_seconds}"
            )
        self._ensure_started()
        self._calls_made += 1
        self._elapsed = self._clock() - self._start_time  # type: ignore[assignment]
        logger.debug(
            "LLMBudget: call %d/%d recorded (%.2fs), elapsed %.1fs/%.1fs",
            self._calls_made,
            self._max_calls,
            duration_seconds,
            self.elapsed_time,
            self._wall_clock_seconds,
        )

    def reset(self) -> None:
        """Reset the budget to its initial (un-activated) state.

        Clears ``calls_made``, ``start_time``, and ``elapsed``. The configured
        limits (``wall_clock_seconds``, ``max_calls``) are preserved.
        """
        self._calls_made = 0
        self._start_time = None
        self._elapsed = 0.0
        logger.debug("LLMBudget: reset to initial state")

    # ── Internal helpers ─────────────────────────────────────────────────

    def _ensure_started(self) -> None:
        """Lazily activate the budget on first use."""
        if self._start_time is None:
            self._start_time = self._clock()
            logger.debug(
                "LLMBudget: activated at %.2f (budget: %.1fs, %d calls)",
                self._start_time,
                self._wall_clock_seconds,
                self._max_calls,
            )

    # ── Diagnostics ──────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, float | int | str | None]:
        """Return a point-in-time snapshot for diagnostics.

        Returns:
            A dict with keys: ``wall_clock_seconds``, ``max_calls``,
            ``calls_made``, ``start_time``, ``elapsed_time``,
            ``remaining_time``, ``remaining_calls``, ``exhausted``,
            ``exhausted_reason``.
        """
        return {
            "wall_clock_seconds": self._wall_clock_seconds,
            "max_calls": self._max_calls,
            "calls_made": self._calls_made,
            "start_time": self._start_time,
            "elapsed_time": self.elapsed_time,
            "remaining_time": self.remaining_time(),
            "remaining_calls": self.remaining_calls(),
            "exhausted": self.exhausted,
            "exhausted_reason": self.exhausted_reason,
        }

    def __repr__(self) -> str:
        return (
            f"LLMBudget(calls={self._calls_made}/{self._max_calls}, "
            f"elapsed={self.elapsed_time:.1f}s/{self._wall_clock_seconds:.1f}s, "
            f"exhausted={self.exhausted})"
        )
