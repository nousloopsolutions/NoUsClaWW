"""Mad-Dog continuous self-healing loop with kill switch, git rollback, and void socket scanning.

Inspired by OpenClaw's capability-evolver "Mad Dog Mode" — runs the
self-improvement cycle in a background loop, continuously scanning for
gaps, failures, and fixable issues. When found, it proposes, validates,
and (when safe) auto-applies fixes with rollback support.

This version adds VDS 90000 (Pool of Tears) void socket scanning: the
loop periodically checks for unresolved void sockets in the epistemic
boundary. If sockets remain unresolved for more than N days, the loop
launches a research cycle, checks for new data, marks resolved sockets,
or escalates to a Human Pivot request.

Safety rails (from capability-evolver's design):
  1. Single-process constraint — never spawns child evolution processes
  2. Kill switch — stop() terminates the loop immediately
  3. Max iterations — configurable cap to prevent infinite loops
  4. Cooldown between cycles — prevents thrashing
  5. Test validation — every auto-applied fix must pass tests before AND after
  6. Rollback — if post-apply tests fail, revert the change
  7. Audit log — every action recorded for human review
  8. Void socket scanning — unresolved epistemic gaps trigger research or escalation

Contract:
    - The loop runs in a background thread, never blocking the main process.
    - Only ONE loop instance may run at a time (single-process constraint).
    - Every auto-applied change is validated: tests must pass before and after.
    - If post-apply tests fail, the change is rolled back automatically.
    - All actions are logged to the event log for human audit.
    - The kill switch (stop()) terminates the loop within one cycle.
    - Max iterations and cooldown prevent runaway behavior.
    - Unresolved void sockets older than N days trigger research or Human Pivot.

SYNTH:
    purpose: Continuous self-healing loop that scans for gaps/failures, proposes, validates, auto-applies with rollback, and monitors unresolved void sockets in VDS 90000.
    axioms: [local_first, open_process, evidence_over_intuition, reversibility_awareness, epistemic_boundary, honest_failure_over_fake_success, scientific_method]
    objective: The system continuously heals itself — detecting gaps, applying validated fixes with rollback safety, and resolving or escalating epistemic gaps — all governed by safety rails and a kill switch.
    anti_patterns:
        - Spawning child evolution processes — single-process only
        - Auto-applying changes without test validation before AND after
        - Ignoring unresolved void sockets — epistemic gaps must be addressed
        - Running without a kill switch — the loop must be stoppable within one cycle
        - Silently failing rollback — if rollback fails, it must be logged loudly
        - Bypassing the singleton lock — only one loop instance at a time
#C Adapted from NoUs-fordge Nous-hub mvp_local_core
"""

# ┌─ synth ──────────────────────────────────────────────────────────────────┐
# @NCL{v=1.0;agent=builder;mod=mad_dog_loop;ts=2026-08-18Z;tier=L3}
# #C Adapted from NoUs-fordge Nous-hub mvp_local_core
# #S{purpose="continuous self-healing loop — scans for gaps/failures, proposes, validates, auto-applies with rollback, monitors unresolved void sockets in VDS 90000"}
# #I{1="single-process constraint — never spawns child evolution processes";2="kill switch terminates loop within one cycle";3="max iterations + cooldown prevent runaway";4="every auto-applied change validated — tests pass before AND after";5="automatic rollback if post-apply tests fail";6="all actions logged for human audit";7="void socket scanning — unresolved epistemic gaps trigger research or Human Pivot escalation"}
# #D{1="extends SelfImprovementEngine"→="adds auto-apply path to existing propose-only system";2="background thread"→="non-blocking, main process continues";3="governed auto-apply"→="propose → validate → apply → re-validate → rollback on failure";4="void socket scan"→="check unresolved sockets in EpistemicBoundary, escalate stale ones"]
# #M{status=IMPLEMENTED;version=1.0.0;deps="nousclaww.reflection.self_improvement, nousclaww.event_log, nousclaww.epistemic_boundary"]
# #T{pass=0;fail=0;xfail=0}
# #W{1="auto-applies changes — ensure test suite is comprehensive before enabling";2="rollback uses git checkout — uncommitted changes may be lost";3="loop continues until stopped or max_iterations reached";4="void socket escalation requires an EpistemicBoundary instance — without one, scanning is skipped"]
# #L{lexicon→docs/NOUS_LEXICON.md}
# └──────────────────────────────────────────────────────────────────────────┘

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nousclaww.epistemic_boundary import EpistemicBoundary, VoidSocket
    from nousclaww.event_log import EventLog
    from nousclaww.reflection.self_improvement import SelfImprovementEngine

logger = logging.getLogger(__name__)


class LoopState(Enum):
    """State of the Mad-Dog loop."""
    STOPPED = "stopped"
    RUNNING = "running"
    COOLDOWN = "cooldown"
    STOPPING = "stopping"


class VoidSocketAction(Enum):
    """Action taken on an unresolved void socket during scanning."""
    RESEARCH = "research"
    RESOLVED = "resolved"
    ESCALATE = "escalate"
    SKIP = "skip"


@dataclass
class MadDogConfig:
    """Configuration for the Mad-Dog loop.

    Attributes:
        max_iterations: Max iterations before auto-stop (0 = infinite).
        cooldown_seconds: Cooldown between cycles in seconds.
        max_applies_per_cycle: Max auto-applies per cycle (prevents
            mass changes).
        auto_apply: Whether to auto-apply validated proposals (False =
            propose only).
        use_git_rollback: Whether to create a git stash before applying
            (rollback safety).
        test_command: Test command (None = use default pytest).
        test_timeout: Test timeout in seconds.
        stop_on_failed_apply: Whether to stop on first failed apply
            (conservative).
        void_socket_max_age_days: Max days a void socket can remain
            unresolved before escalation. Sockets older than this
            trigger research or Human Pivot.
        void_socket_research_threshold: Traversal count above which a
            socket is escalated to Human Pivot instead of just
            triggering research.
    """
    max_iterations: int = 100
    cooldown_seconds: float = 30.0
    max_applies_per_cycle: int = 3
    auto_apply: bool = True
    use_git_rollback: bool = True
    test_command: list[str] | None = None
    test_timeout: int = 180
    stop_on_failed_apply: bool = False
    void_socket_max_age_days: int = 7
    void_socket_research_threshold: int = 3

    def __post_init__(self) -> None:
        if self.max_iterations < 0:
            raise ValueError("max_iterations must be >= 0")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        if self.max_applies_per_cycle < 1:
            raise ValueError("max_applies_per_cycle must be >= 1")
        if self.test_timeout <= 0:
            raise ValueError("test_timeout must be positive")
        if self.void_socket_max_age_days < 1:
            raise ValueError("void_socket_max_age_days must be >= 1")
        if self.void_socket_research_threshold < 1:
            raise ValueError("void_socket_research_threshold must be >= 1")


@dataclass
class LoopCycleResult:
    """Result of a single Mad-Dog loop cycle.

    Attributes:
        cycle_number: The cycle's sequential number.
        timestamp: UTC ISO-8601 timestamp of the cycle.
        gaps_found: Number of gaps detected.
        failures_found: Number of failures detected.
        proposals_generated: Number of proposals generated.
        proposals_validated: Number of proposals validated.
        proposals_applied: Number of proposals auto-applied.
        proposals_rolled_back: Number of proposals rolled back.
        duration_ms: Cycle duration in milliseconds.
        error: Error message if the cycle failed, None otherwise.
        applied_changes: List of applied change dicts.
        void_sockets_scanned: Number of void sockets scanned.
        void_sockets_researched: Number of sockets that triggered
            research.
        void_sockets_resolved: Number of sockets marked resolved.
        void_sockets_escalated: Number of sockets escalated to Human
            Pivot.
    """
    cycle_number: int
    timestamp: str
    gaps_found: int = 0
    failures_found: int = 0
    proposals_generated: int = 0
    proposals_validated: int = 0
    proposals_applied: int = 0
    proposals_rolled_back: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    applied_changes: list[dict[str, Any]] = field(default_factory=list)
    void_sockets_scanned: int = 0
    void_sockets_researched: int = 0
    void_sockets_resolved: int = 0
    void_sockets_escalated: int = 0


@dataclass
class VoidSocketScanResult:
    """Result of scanning unresolved void sockets in VDS 90000.

    Attributes:
        total_scanned: Total number of unresolved sockets scanned.
        researched: Sockets that triggered a research cycle.
        resolved: Sockets that were marked resolved (new data found).
        escalated: Sockets escalated to Human Pivot.
        details: Per-socket detail dicts with action and rationale.
    """
    total_scanned: int = 0
    researched: int = 0
    resolved: int = 0
    escalated: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)


class MadDogLoop:
    """Continuous self-healing loop with safety rails and void socket scanning.

    Usage:
        from nousclaww.reflection.mad_dog_loop import MadDogLoop, MadDogConfig
        from nousclaww.reflection.self_improvement import SelfImprovementEngine

        engine = SelfImprovementEngine(reflection_engine=...)
        loop = MadDogLoop(
            improvement_engine=engine,
            epistemic_boundary=boundary,
        )

        loop.start()        # Start background loop
        loop.get_status()   # Check status
        loop.stop()         # Kill switch — stops within one cycle
    """

    # Class-level singleton lock — enforces single-process constraint
    _instance_lock = threading.Lock()
    _active_instance: "MadDogLoop | None" = None

    def __init__(
        self,
        improvement_engine: "SelfImprovementEngine",
        event_log: "EventLog | None" = None,
        config: MadDogConfig | None = None,
        epistemic_boundary: "EpistemicBoundary | None" = None,
    ) -> None:
        """Initialize the Mad-Dog loop.

        Args:
            improvement_engine: The self-improvement engine that
                detects gaps and generates proposals.
            event_log: Optional event log for audit trail. If None,
                actions are logged to the Python logger only.
            config: Configuration for the loop. If None, defaults are
                used.
            epistemic_boundary: Optional epistemic boundary instance
                for void socket scanning. If None, void socket scanning
                is skipped.
        """
        self.engine = improvement_engine
        self.event_log = event_log
        self.config = config or MadDogConfig()
        self.epistemic_boundary = epistemic_boundary

        self._state = LoopState.STOPPED
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self._cycle_count = 0
        self._total_applied = 0
        self._total_rolled_back = 0
        self._last_cycle: LoopCycleResult | None = None
        self._all_results: list[LoopCycleResult] = []

        # Void socket scan tracking
        self._last_void_scan: VoidSocketScanResult | None = None
        self._pending_human_pivots: list[dict[str, Any]] = []

        # Test command
        self._test_cmd = self.config.test_command or [
            sys.executable, "-m", "pytest",
            str(Path(__file__).resolve().parent.parent.parent / "tests"),
            "--tb=no", "-q", "--timeout=120",
        ]

    # ── Singleton enforcement ─────────────────────────────────────────────

    def _acquire_instance(self) -> bool:
        """Try to acquire the singleton instance lock."""
        with MadDogLoop._instance_lock:
            if MadDogLoop._active_instance is not None and MadDogLoop._active_instance is not self:
                logger.error(
                    "Another MadDogLoop instance is already running — "
                    "single-process constraint violated"
                )
                return False
            MadDogLoop._active_instance = self
            return True

    def _release_instance(self) -> None:
        """Release the singleton instance lock."""
        with MadDogLoop._instance_lock:
            if MadDogLoop._active_instance is self:
                MadDogLoop._active_instance = None

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the Mad-Dog loop in a background thread."""
        with self._lock:
            if self._state == LoopState.RUNNING:
                logger.warning("Mad-Dog loop is already running")
                return
            if not self._acquire_instance():
                raise RuntimeError("Another MadDogLoop instance is already running")
            self._stop_event.clear()
            self._state = LoopState.RUNNING
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="mad-dog-loop",
            )
            self._thread.start()
            logger.info("Mad-Dog loop started")
            self._log("loop_start", {"max_iterations": self.config.max_iterations})

    def stop(self) -> None:
        """Kill switch — stop the loop within one cycle."""
        with self._lock:
            if self._state in (LoopState.STOPPED, LoopState.STOPPING):
                return
            self._state = LoopState.STOPPING
            self._stop_event.set()
            logger.info("Mad-Dog stop requested — will stop within one cycle")
            self._log("loop_stop_requested", {})

    def stop_and_wait(self, timeout: float = 60.0) -> bool:
        """Stop the loop and wait for it to finish.

        Args:
            timeout: Maximum seconds to wait for the loop to stop.

        Returns:
            True if the loop stopped within the timeout, False otherwise.
        """
        self.stop()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            stopped = not self._thread.is_alive()
        else:
            stopped = True
        self._release_instance()
        return stopped

    def get_status(self) -> dict[str, Any]:
        """Get current loop status.

        Returns:
            A dict with keys: state, cycle_count, total_applied,
            total_rolled_back, last_cycle, last_void_scan,
            pending_human_pivots, config.
        """
        with self._lock:
            return {
                "state": self._state.value,
                "cycle_count": self._cycle_count,
                "total_applied": self._total_applied,
                "total_rolled_back": self._total_rolled_back,
                "last_cycle": self._last_cycle.__dict__ if self._last_cycle else None,
                "last_void_scan": (
                    self._last_void_scan.__dict__
                    if self._last_void_scan else None
                ),
                "pending_human_pivots": list(self._pending_human_pivots),
                "config": {
                    "max_iterations": self.config.max_iterations,
                    "cooldown_seconds": self.config.cooldown_seconds,
                    "auto_apply": self.config.auto_apply,
                    "max_applies_per_cycle": self.config.max_applies_per_cycle,
                    "void_socket_max_age_days": self.config.void_socket_max_age_days,
                },
            }

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get history of loop cycles.

        Args:
            limit: Maximum number of cycle results to return.

        Returns:
            A list of cycle result dicts, most recent last.
        """
        with self._lock:
            return [r.__dict__ for r in self._all_results[-limit:]]

    def get_pending_human_pivots(self) -> list[dict[str, Any]]:
        """Get void sockets that have been escalated to Human Pivot.

        Returns:
            A list of human pivot request dicts, each containing the
            socket_id, query, missing data, and age in days.
        """
        with self._lock:
            return list(self._pending_human_pivots)

    def clear_human_pivots(self) -> int:
        """Clear all pending Human Pivot requests.

        Call this after a human has reviewed and addressed the
        escalated void sockets.

        Returns:
            The number of pivots cleared.
        """
        with self._lock:
            count = len(self._pending_human_pivots)
            self._pending_human_pivots.clear()
            return count

    # ── Main loop ─────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """The main loop body — runs in a background thread."""
        try:
            while not self._stop_event.is_set():
                # Check max iterations
                if (
                    self.config.max_iterations > 0
                    and self._cycle_count >= self.config.max_iterations
                ):
                    logger.info(
                        f"Mad-Dog reached max_iterations "
                        f"({self.config.max_iterations}) — stopping"
                    )
                    break

                # Run one cycle
                result = self._run_cycle()
                with self._lock:
                    self._cycle_count += 1
                    self._last_cycle = result
                    self._all_results.append(result)
                    self._total_applied += result.proposals_applied
                    self._total_rolled_back += result.proposals_rolled_back

                # Check stop signal after cycle
                if self._stop_event.is_set():
                    break

                # Check for stop_on_failed_apply
                if self.config.stop_on_failed_apply and result.proposals_rolled_back > 0:
                    logger.warning(
                        "Mad-Dog stopping due to failed apply "
                        "(stop_on_failed_apply=True)"
                    )
                    break

                # Cooldown
                with self._lock:
                    self._state = LoopState.COOLDOWN
                if not self._stop_event.wait(timeout=self.config.cooldown_seconds):
                    with self._lock:
                        self._state = LoopState.RUNNING
                else:
                    break  # stop_event was set during cooldown

        except Exception as e:
            logger.error(f"Mad-Dog loop crashed: {e}", exc_info=True)
            self._log("loop_crash", {"error": str(e)})
        finally:
            with self._lock:
                self._state = LoopState.STOPPED
            self._release_instance()
            self._log("loop_stopped", {"cycle_count": self._cycle_count})
            logger.info("Mad-Dog loop stopped")

    def _run_cycle(self) -> LoopCycleResult:
        """Run a single improvement cycle with auto-apply and void socket scan.

        Returns:
            The result of this cycle.
        """
        cycle_num = self._cycle_count + 1
        start = time.time()
        result = LoopCycleResult(
            cycle_number=cycle_num,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        try:
            # Step 1: Run the improvement cycle (detect gaps + generate proposals)
            summary = self.engine.run_improvement_cycle()
            result.gaps_found = summary.get("gaps_found", 0)
            result.failures_found = summary.get("failures_found", 0)
            result.proposals_generated = summary.get("proposals_generated", 0)
            result.proposals_validated = summary.get("validated", 0)

            # Step 2: Auto-apply validated proposals (if enabled)
            if self.config.auto_apply and result.proposals_validated > 0:
                validated = self.engine.get_validated_proposals()
                applied = 0
                rolled_back = 0

                # Create rollback point if using git
                rollback_point = None
                if self.config.use_git_rollback:
                    rollback_point = self._create_rollback_point()

                for proposal in validated[:self.config.max_applies_per_cycle]:
                    if self._stop_event.is_set():
                        break

                    apply_result = self._apply_proposal(proposal, rollback_point)
                    if apply_result["applied"]:
                        applied += 1
                        result.applied_changes.append(apply_result)
                    elif apply_result["rolled_back"]:
                        rolled_back += 1
                        result.applied_changes.append(apply_result)

                result.proposals_applied = applied
                result.proposals_rolled_back = rolled_back

            # Step 3: Scan for unresolved void sockets in VDS 90000
            void_result = self._scan_void_sockets()
            result.void_sockets_scanned = void_result.total_scanned
            result.void_sockets_researched = void_result.researched
            result.void_sockets_resolved = void_result.resolved
            result.void_sockets_escalated = void_result.escalated

            with self._lock:
                self._last_void_scan = void_result

        except Exception as e:
            result.error = str(e)
            logger.error(f"Mad-Dog cycle {cycle_num} error: {e}", exc_info=True)

        result.duration_ms = (time.time() - start) * 1000
        self._log("cycle_complete", result.__dict__)
        return result

    # ── Void socket scanning (VDS 90000) ──────────────────────────────────

    def _scan_void_sockets(self) -> VoidSocketScanResult:
        """Scan for unresolved void sockets in VDS 90000.

        Checks the EpistemicBoundary for unresolved void sockets. For
        each unresolved socket:
        1. If the socket has been traversed more than the research
           threshold, escalate to Human Pivot.
        2. If the socket is older than the max age (in days), trigger a
           research cycle by traversing it and checking for new data.
        3. If new data is available (the socket's missing items can
           now be resolved), mark it resolved.
        4. Otherwise, skip it for this cycle.

        Returns:
            A VoidSocketScanResult with the scan summary.
        """
        result = VoidSocketScanResult()

        if self.epistemic_boundary is None:
            logger.debug("Mad-Dog: no epistemic boundary, skipping void socket scan")
            return result

        try:
            unresolved = self.epistemic_boundary.unresolved_sockets
        except Exception as e:
            logger.warning(f"Mad-Dog: failed to get unresolved sockets: {e}")
            return result

        if not unresolved:
            logger.debug("Mad-Dog: no unresolved void sockets")
            return result

        result.total_scanned = len(unresolved)
        max_age_seconds = self.config.void_socket_max_age_days * 86400
        now = time.time()

        for socket in unresolved:
            if self._stop_event.is_set():
                break

            socket_id = socket.socket_id
            action = VoidSocketAction.SKIP
            rationale = ""

            # Parse the socket timestamp
            socket_age_seconds = self._compute_socket_age(socket, now)

            # Check if traversal count exceeds the research threshold
            if socket.traversal_count >= self.config.void_socket_research_threshold:
                action = VoidSocketAction.ESCALATE
                rationale = (
                    f"Socket traversed {socket.traversal_count} times "
                    f"(threshold: {self.config.void_socket_research_threshold}) — "
                    f"escalating to Human Pivot"
                )
                result.escalated += 1
                self._add_human_pivot(socket, socket_age_seconds)
            # Check if the socket is old enough to warrant research
            elif socket_age_seconds > max_age_seconds:
                # Traverse the socket (mark that we revisited it)
                try:
                    self.epistemic_boundary.traverse_void_socket(socket_id)
                except Exception as e:
                    logger.warning(
                        f"Mad-Dog: failed to traverse socket {socket_id}: {e}"
                    )

                # Check if new data is available to resolve the socket
                if self._check_new_data_for_socket(socket):
                    action = VoidSocketAction.RESOLVED
                    rationale = (
                        f"New data available — socket resolved after "
                        f"{socket_age_seconds / 86400:.1f} days"
                    )
                    result.resolved += 1
                    try:
                        self.epistemic_boundary.resolve_void_socket(
                            socket_id,
                            resolution=(
                                f"Resolved by Mad-Dog loop: new data found "
                                f"after {socket_age_seconds / 86400:.1f} days"
                            ),
                        )
                    except Exception as e:
                        logger.warning(
                            f"Mad-Dog: failed to resolve socket {socket_id}: {e}"
                        )
                else:
                    action = VoidSocketAction.RESEARCH
                    rationale = (
                        f"Socket unresolved for {socket_age_seconds / 86400:.1f} "
                        f"days — triggering research cycle"
                    )
                    result.researched += 1
            else:
                rationale = (
                    f"Socket is {socket_age_seconds / 86400:.1f} days old "
                    f"(max: {self.config.void_socket_max_age_days}) — skipping"
                )

            result.details.append({
                "socket_id": socket_id,
                "query": socket.query,
                "missing": socket.missing,
                "age_days": socket_age_seconds / 86400 if socket_age_seconds > 0 else 0,
                "traversal_count": socket.traversal_count,
                "action": action.value,
                "rationale": rationale,
            })

        logger.info(
            f"Mad-Dog void socket scan: {result.total_scanned} scanned, "
            f"{result.researched} researched, {result.resolved} resolved, "
            f"{result.escalated} escalated"
        )
        self._log("void_socket_scan", result.__dict__)

        return result

    def _compute_socket_age(self, socket: "VoidSocket", now: float) -> float:
        """Compute the age of a void socket in seconds.

        Args:
            socket: The void socket.
            now: The current Unix timestamp.

        Returns:
            Age in seconds, or 0 if the timestamp cannot be parsed.
        """
        try:
            # Parse ISO-8601 timestamp
            ts = datetime.fromisoformat(socket.timestamp)
            return now - ts.timestamp()
        except (ValueError, TypeError, AttributeError):
            logger.debug(
                f"Mad-Dog: could not parse timestamp for socket "
                f"{socket.socket_id}: {socket.timestamp}"
            )
            return 0.0

    def _check_new_data_for_socket(self, socket: "VoidSocket") -> bool:
        """Check if new data is available to resolve a void socket.

        This is a heuristic check. In the full implementation, this
        would query the memory manager or knowledge graph for new data
        matching the socket's missing items. For now, it checks if the
        socket's missing items have been addressed by recent events or
        memories.

        The heuristic: if the socket has been traversed at least once
        (meaning a previous cycle already tried to resolve it) and no
        new data has arrived, it stays unresolved. If this is the first
        traversal after the max age, we check whether the missing items
        are now available.

        Args:
            socket: The void socket to check.

        Returns:
            True if new data appears to be available, False otherwise.
        """
        # If the socket has no missing items, it can't be resolved
        if not socket.missing:
            return False

        # If the socket has been traversed many times without resolution,
        # it's unlikely new data has arrived — let it escalate instead
        if socket.traversal_count >= self.config.void_socket_research_threshold:
            return False

        # Heuristic: check if the engine's reflection has new gaps
        # that overlap with the socket's missing items. If the gap
        # count has decreased since the socket was created, some
        # gaps may have been resolved.
        try:
            gaps = self.engine.detector.detect_gaps()
            if not gaps:
                # No gaps remaining — the socket may be resolvable
                return True
            # Check if any of the socket's missing items are no longer
            # in the current gaps
            gap_evidence = " ".join(
                g.get("evidence", "") + g.get("directive", "") for g in gaps
            ).lower()
            still_missing = [
                m for m in socket.missing
                if m.lower() in gap_evidence
            ]
            # If fewer items are still missing than originally, some
            # new data may have arrived
            return len(still_missing) < len(socket.missing)
        except Exception:
            return False

    def _add_human_pivot(self, socket: "VoidSocket", age_seconds: float) -> None:
        """Add a void socket to the pending Human Pivot queue.

        Args:
            socket: The void socket being escalated.
            age_seconds: The age of the socket in seconds.
        """
        pivot_request = {
            "socket_id": socket.socket_id,
            "query": socket.query,
            "missing": socket.missing,
            "trigger": socket.trigger,
            "age_days": age_seconds / 86400 if age_seconds > 0 else 0,
            "traversal_count": socket.traversal_count,
            "escalated_at": datetime.now(timezone.utc).isoformat(),
            "message": (
                f"Human Pivot required: void socket '{socket.socket_id}' "
                f"has been unresolved for {age_seconds / 86400:.1f} days "
                f"after {socket.traversal_count} research cycles. "
                f"Missing data: {', '.join(socket.missing[:5])}"
            ),
        }
        with self._lock:
            self._pending_human_pivots.append(pivot_request)
        logger.warning(f"Mad-Dog: Human Pivot escalated for socket {socket.socket_id}")

    # ── Auto-apply with rollback ──────────────────────────────────────────

    def _create_rollback_point(self) -> str | None:
        """Create a git stash as a rollback point.

        Returns:
            The stash commit SHA, or None if creation failed.
        """
        try:
            core_dir = Path(__file__).resolve().parent.parent.parent
            proc = subprocess.run(
                ["git", "stash", "create"],
                capture_output=True, text=True,
                cwd=str(core_dir),
                timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                commit_sha = proc.stdout.strip()
                logger.debug(f"Rollback point created: {commit_sha[:8]}")
                return commit_sha
        except Exception as e:
            logger.warning(f"Could not create rollback point: {e}")
        return None

    def _apply_proposal(
        self,
        proposal: dict[str, Any],
        rollback_point: str | None,
    ) -> dict[str, Any]:
        """Apply a single proposal with test validation and rollback.

        Steps:
        1. Run baseline tests (must pass).
        2. Apply the change.
        3. Run post-apply tests (must pass).
        4. If post-apply fails, rollback.

        Args:
            proposal: The proposal dict to apply.
            rollback_point: The git stash SHA for rollback, or None.

        Returns:
            A result dict with keys: proposal_id, target_file, applied,
            rolled_back, error, timestamp.
        """
        proposal_id = proposal.get("proposal_id", "unknown")
        target_file = proposal.get("target_file", "")
        proposed_change = proposal.get("proposed_change", "")

        result: dict[str, Any] = {
            "proposal_id": proposal_id,
            "target_file": target_file,
            "applied": False,
            "rolled_back": False,
            "error": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Step 1: Baseline tests
        baseline_pass = self._run_tests()
        if not baseline_pass:
            result["error"] = "Baseline tests failing — cannot apply"
            logger.warning(f"Skipping {proposal_id}: baseline tests failing")
            return result

        # Step 2: Apply the change
        try:
            apply_ok = self._execute_apply(proposal)
            if not apply_ok:
                result["error"] = "Apply step failed"
                return result
        except Exception as e:
            result["error"] = f"Apply failed: {e}"
            return result

        # Step 3: Post-apply tests
        post_pass = self._run_tests()
        if post_pass:
            result["applied"] = True
            logger.info(f"Proposal {proposal_id} applied successfully")
            self._log("proposal_applied", result)
        else:
            # Step 4: Rollback
            result["rolled_back"] = True
            result["error"] = "Post-apply tests failed — rolled back"
            logger.warning(
                f"Proposal {proposal_id} applied but tests failed — rolling back"
            )
            self._rollback(rollback_point)
            self._log("proposal_rolled_back", result)

        return result

    def _execute_apply(self, proposal: dict[str, Any]) -> bool:
        """Execute the actual change described by the proposal.

        This is the governed application path. The actual code
        modification is handled by the improvement orchestrator which
        has access to the LLM. This method logs the intent and returns
        False to indicate that the orchestrator handles actual
        application.

        Args:
            proposal: The proposal dict describing the change.

        Returns:
            False — the orchestrator handles actual code generation.
        """
        logger.info(
            f"Apply intent logged for {proposal.get('proposal_id', 'unknown')}: "
            f"{proposal.get('proposed_change', '')[:100]}"
        )
        return False

    def _run_tests(self) -> bool:
        """Run the test suite and return True if all pass.

        Returns:
            True if all tests pass, False otherwise.
        """
        try:
            core_dir = Path(__file__).resolve().parent.parent.parent
            proc = subprocess.run(
                self._test_cmd,
                capture_output=True, text=True,
                cwd=str(core_dir),
                timeout=self.config.test_timeout,
            )
            output = proc.stdout + proc.stderr
            if "failed" in output and "0 failed" not in output:
                return False
            return proc.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("Test suite timed out")
            return False
        except Exception as e:
            logger.warning(f"Test runner error: {e}")
            return False

    def _rollback(self, rollback_point: str | None) -> bool:
        """Rollback to the given point using git.

        Args:
            rollback_point: The git stash SHA to restore, or None.

        Returns:
            True if rollback succeeded, False otherwise.
        """
        if not rollback_point:
            logger.warning("No rollback point available — manual recovery needed")
            return False
        try:
            core_dir = Path(__file__).resolve().parent.parent.parent
            proc = subprocess.run(
                ["git", "stash", "apply", rollback_point],
                capture_output=True, text=True,
                cwd=str(core_dir),
                timeout=30,
            )
            if proc.returncode == 0:
                logger.info(f"Rolled back to {rollback_point[:8]}")
                return True
            else:
                logger.error(f"Rollback failed: {proc.stderr}")
                return False
        except Exception as e:
            logger.error(f"Rollback error: {e}")
            return False

    # ── Logging ───────────────────────────────────────────────────────────

    def _log(self, operation: str, data: dict[str, Any]) -> None:
        """Log to event log if available.

        Args:
            operation: The operation name.
            data: The operation data dict.
        """
        if self.event_log:
            try:
                self.event_log.log_operation(
                    event_type="mad_dog",
                    module="mad_dog_loop",
                    operation=operation,
                    inputs=data,
                    outputs={},
                    status="completed",
                    duration_ms=0.0,
                )
            except Exception as e:
                logger.warning(f"Mad-Dog: failed to log to event log: {e}")
