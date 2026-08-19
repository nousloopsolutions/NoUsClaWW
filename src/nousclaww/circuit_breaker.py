"""Process-wide circuit breaker for flaky backends.

SYNTH:
    purpose: Stop calling flaky backends after N consecutive failures
    axioms: [local_first, honest_failure_over_fake_success, evidence_over_intuition, reversibility_awareness]
    objective: A circuit breaker that opens after consecutive failures exceed a threshold, blocks
        calls during a cooldown period, then enters half-open to test whether the backend has
        recovered before fully closing.
    anti_patterns:
        - Never silently swallow exceptions from the wrapped callable without recording the failure
        - Never allow calls through an OPEN breaker (except half-open test calls)
        - Never reset the failure counter on a non-success outcome
        - Never block forever without a cooldown timeout
        - Never mutate shared global state without holding the lock
"""
#C Inspired by PMB (Project Memory Bank) patterns

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """The three states of a circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(Exception):
    """Raised when a call is attempted while the breaker is OPEN."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        """
        Initialize the error.

        Args:
            message: Human-readable explanation.
            retry_after: Estimated seconds until the breaker enters HALF_OPEN.
        """
        super().__init__(message)
        self.retry_after = retry_after


class CircuitBreaker:
    """Process-wide circuit breaker for flaky backends.

    The breaker starts in CLOSED state, allowing all calls through. Each failure
    increments the consecutive-failure counter; each success resets it to zero.
    When consecutive failures reach ``failure_threshold``, the breaker transitions
    to OPEN and blocks all calls for ``cooldown_seconds``.

    After the cooldown elapses, the breaker transitions to HALF_OPEN, allowing up
    to ``half_open_max_calls`` concurrent test calls. If all test calls succeed,
    the breaker closes. If any test call fails, the breaker reopens immediately.

    This class is thread-safe. All state mutations are guarded by an internal lock.

    Usage:
        breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
        try:
            result = breaker.call(flaky_backend_fn, arg1, arg2, key="value")
        except CircuitBreakerOpenError:
            # Backend is down — use fallback or back off
            ...
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        half_open_max_calls: int = 3,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """
        Initialize the circuit breaker.

        Args:
            failure_threshold: Number of consecutive failures before the breaker opens.
                Must be >= 1.
            cooldown_seconds: Seconds the breaker stays OPEN before entering HALF_OPEN.
                Must be > 0.
            half_open_max_calls: Maximum concurrent test calls allowed in HALF_OPEN.
                Must be >= 1.
            clock: Optional callable returning the current monotonic time. Defaults to
                ``time.monotonic``. Injected for deterministic testing.

        Raises:
            ValueError: If any configuration argument is out of range.
        """
        if failure_threshold < 1:
            raise ValueError(
                f"failure_threshold must be >= 1, got {failure_threshold}"
            )
        if cooldown_seconds <= 0:
            raise ValueError(
                f"cooldown_seconds must be > 0, got {cooldown_seconds}"
            )
        if half_open_max_calls < 1:
            raise ValueError(
                f"half_open_max_calls must be >= 1, got {half_open_max_calls}"
            )

        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._half_open_max_calls = half_open_max_calls
        self._clock = clock if clock is not None else time.monotonic

        self._lock = threading.RLock()
        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._opened_at: float | None = None
        self._half_open_in_flight: int = 0

    # ── Public configuration properties ──────────────────────────────────

    @property
    def failure_threshold(self) -> int:
        """Consecutive failures required to open the breaker."""
        return self._failure_threshold

    @property
    def cooldown_seconds(self) -> float:
        """Seconds the breaker stays OPEN before entering HALF_OPEN."""
        return self._cooldown_seconds

    @property
    def half_open_max_calls(self) -> int:
        """Maximum test calls permitted in HALF_OPEN state."""
        return self._half_open_max_calls

    # ── State inspection ─────────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        """Current breaker state, transitioning OPEN→HALF_OPEN if cooldown elapsed."""
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    @property
    def failure_count(self) -> int:
        """Current consecutive failure count (reset on success or reset())."""
        with self._lock:
            return self._failure_count

    @property
    def is_call_permitted(self) -> bool:
        """Whether a call would be allowed right now without raising."""
        with self._lock:
            return self._acquire_call_slot(raise_if_closed=False) is not None

    def seconds_until_half_open(self) -> float:
        """
        Estimate seconds remaining until the breaker enters HALF_OPEN.

        Returns:
            Seconds remaining if OPEN, otherwise 0.0.
        """
        with self._lock:
            if self._state is not CircuitState.OPEN or self._opened_at is None:
                return 0.0
            elapsed = self._clock() - self._opened_at
            remaining = self._cooldown_seconds - elapsed
            return max(0.0, remaining)

    # ── Core call execution ──────────────────────────────────────────────

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute ``fn`` through the circuit breaker.

        If the breaker is OPEN and the cooldown has not elapsed, a
        :class:`CircuitBreakerOpenError` is raised without invoking ``fn``.
        If the breaker is HALF_OPEN and the test-call quota is exhausted,
        a :class:`CircuitBreakerOpenError` is raised.

        On success, :meth:`record_success` is called automatically.
        On exception, :meth:`record_failure` is called and the original
        exception is re-raised.

        Args:
            fn: The callable to execute.
            *args: Positional arguments forwarded to ``fn``.
            **kwargs: Keyword arguments forwarded to ``fn``.

        Returns:
            The return value of ``fn(*args, **kwargs)``.

        Raises:
            CircuitBreakerOpenError: If the breaker is OPEN or HALF_OPEN quota
                is exhausted.
            Exception: Any exception raised by ``fn`` is re-raised after the
                failure is recorded.
        """
        slot = self._acquire_call_slot(raise_if_closed=True)
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self._release_call_slot(slot, success=False)
            self.record_failure()
            raise
        else:
            self._release_call_slot(slot, success=True)
            self.record_success()
            return result

    # ── Manual state control ─────────────────────────────────────────────

    def record_success(self) -> None:
        """Record a successful call.

        In CLOSED state, resets the consecutive-failure counter.
        In HALF_OPEN state, increments the success counter; if enough
        successes accumulate, the breaker closes.
        """
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._success_count += 1
                logger.debug(
                    "CircuitBreaker HALF_OPEN success (%d/%d)",
                    self._success_count,
                    self._half_open_max_calls,
                )
                if self._success_count >= self._half_open_max_calls:
                    self._close()
            else:
                self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call.

        In CLOSED state, increments the consecutive-failure counter; if it
        reaches the threshold, the breaker opens.
        In HALF_OPEN state, the breaker reopens immediately — any failure
        during the test phase means the backend has not recovered.
        """
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                logger.warning(
                    "CircuitBreaker HALF_OPEN failure — reopening breaker"
                )
                self._open()
                return

            self._failure_count += 1
            logger.debug(
                "CircuitBreaker CLOSED failure (%d/%d)",
                self._failure_count,
                self._failure_threshold,
            )
            if self._failure_count >= self._failure_threshold:
                self._open()

    def reset(self) -> None:
        """Force the breaker back to CLOSED and clear all counters.

        This is an explicit override — use it when an operator knows the
        backend has recovered and wants to bypass the cooldown.
        """
        with self._lock:
            self._close()

    # ── Internal state transitions ───────────────────────────────────────

    def _open(self) -> None:
        """Transition to OPEN state and record the opening time."""
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        self._failure_count = 0
        self._success_count = 0
        self._half_open_in_flight = 0
        logger.warning(
            "CircuitBreaker OPEN — calls blocked for %.1fs", self._cooldown_seconds
        )

    def _close(self) -> None:
        """Transition to CLOSED state and clear all counters."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at = None
        self._half_open_in_flight = 0
        logger.info("CircuitBreaker CLOSED — calls permitted")

    def _maybe_transition_to_half_open(self) -> None:
        """If OPEN and cooldown has elapsed, transition to HALF_OPEN."""
        if self._state is not CircuitState.OPEN or self._opened_at is None:
            return
        elapsed = self._clock() - self._opened_at
        if elapsed >= self._cooldown_seconds:
            self._state = CircuitState.HALF_OPEN
            self._success_count = 0
            self._half_open_in_flight = 0
            logger.info(
                "CircuitBreaker HALF_OPEN — allowing up to %d test calls",
                self._half_open_max_calls,
            )

    # ── Call-slot management (the gate) ──────────────────────────────────

    def _acquire_call_slot(self, raise_if_closed: bool) -> bool | None:
        """
        Attempt to acquire permission to make a call.

        Args:
            raise_if_closed: If True, raise :class:`CircuitBreakerOpenError`
                when the call is not permitted. If False, return None.

        Returns:
            True if the call is permitted (slot acquired). None if not
            permitted and ``raise_if_closed`` is False.

        Raises:
            CircuitBreakerOpenError: If the call is not permitted and
                ``raise_if_closed`` is True.
        """
        with self._lock:
            self._maybe_transition_to_half_open()

            if self._state is CircuitState.CLOSED:
                return True

            if self._state is CircuitState.HALF_OPEN:
                if self._half_open_in_flight < self._half_open_max_calls:
                    self._half_open_in_flight += 1
                    return True
                # Quota exhausted — treat like OPEN for the caller
                if raise_if_closed:
                    raise CircuitBreakerOpenError(
                        "Circuit breaker is HALF_OPEN and test-call quota "
                        f"({self._half_open_max_calls}) is exhausted. "
                        "Wait for in-flight test calls to complete.",
                        retry_after=None,
                    )
                return None

            # OPEN
            if raise_if_closed:
                remaining = self.seconds_until_half_open()
                raise CircuitBreakerOpenError(
                    "Circuit breaker is OPEN — calls are blocked. "
                    f"Backend has failed {self._failure_threshold} consecutive times. "
                    f"Retry in ~{remaining:.1f}s.",
                    retry_after=remaining,
                )
            return None

    def _release_call_slot(self, slot: bool | None, success: bool) -> None:
        """Release a previously acquired call slot.

        Args:
            slot: The slot handle returned by ``_acquire_call_slot``.
            success: Whether the call succeeded (used only for bookkeeping
                in HALF_OPEN state).
        """
        if slot is None:
            return
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._half_open_in_flight = max(0, self._half_open_in_flight - 1)

    # ── Diagnostics ──────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """
        Return a point-in-time snapshot of breaker state for diagnostics.

        Returns:
            A dict with keys: ``state``, ``failure_count``, ``success_count``,
            ``failure_threshold``, ``cooldown_seconds``, ``half_open_max_calls``,
            ``half_open_in_flight``, ``seconds_until_half_open``.
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            return {
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "failure_threshold": self._failure_threshold,
                "cooldown_seconds": self._cooldown_seconds,
                "half_open_max_calls": self._half_open_max_calls,
                "half_open_in_flight": self._half_open_in_flight,
                "seconds_until_half_open": self.seconds_until_half_open(),
            }

    def __repr__(self) -> str:
        with self._lock:
            self._maybe_transition_to_half_open()
            return (
                f"CircuitBreaker(state={self._state.value}, "
                f"failures={self._failure_count}/{self._failure_threshold}, "
                f"half_open_in_flight={self._half_open_in_flight})"
            )
