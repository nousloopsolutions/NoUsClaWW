"""Observability metrics aggregator — derives diagnostics from the event log.

Aggregates metrics from the EventLog (the single source of truth) and produces
structured diagnostics. This makes the agent's behavior visible: what's working,
what's failing, how fast, and what the quality looks like.

The event log is the only data source — no separate metrics storage. This
avoids sync issues and ensures metrics are always consistent with the audit
trail.

Architecture:
  1. MetricsAggregator — derives aggregate stats, health checks, and quality
     metrics from the event log
  2. HealthCheck — result of a subsystem health check

Invariants:
  1. Metrics are derived from the event log — no separate storage
  2. All metrics are 100% local — no data leaves the machine
  3. Metrics include health, quality, performance, and activity
  4. Metrics are queryable for diagnostics

SYNTH:
    purpose: Derive observability metrics (health, quality, performance, activity) from the event log as a single source of truth
    axioms: [local_first, evidence_over_intuition, honest_failure_over_fake_success, open_process, epistemic_boundary]
    objective: Any consumer can call aggregate(), health_check(), or quality_metrics() to get a structured snapshot of agent health and behavior; all data comes from the event log
    anti_patterns:
        - Maintaining separate metrics storage (event log is the single source of truth)
        - Sending metrics data to any remote service
        - Hiding failures in quality metrics (null results must be reported)
        - Inventing metrics not derivable from the event log
        - Modifying the event log during aggregation (read-only)
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core observability/metrics.py

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from nousclaww.event_log import EventLog, EventStatus


# ── Constants ───────────────────────────────────────────────────────────────

SLOW_OPERATION_THRESHOLD_MS = 1000.0
DEFAULT_QUERY_LIMIT = 10000
HEALTH_CHECK_RECENT_LIMIT = 50
ACTIVITY_RECENT_LIMIT = 10
SLOW_OPS_LIMIT = 10
FAILURE_DETAILS_LIMIT = 10


# ── Health Check ────────────────────────────────────────────────────────────

@dataclass
class HealthCheck:
    """Result of a health check on a subsystem.

    Attributes:
        name: Subsystem name
        healthy: Whether the subsystem is healthy
        message: Human-readable status message
        details: Additional structured details
    """

    name: str
    healthy: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for JSON output."""
        return {
            "name": self.name,
            "healthy": self.healthy,
            "message": self.message,
            "details": self.details,
        }


# ── Metrics Aggregator ──────────────────────────────────────────────────────

class MetricsAggregator:
    """Aggregates observability metrics from the event log.

    The event log is the single source of truth. This class derives all
    metrics — health, quality, performance, activity — from it without
    maintaining any separate storage.

    Usage::

        from nousclaww.event_log import EventLog
        from nousclaww.metrics import MetricsAggregator

        log = EventLog()
        aggregator = MetricsAggregator(log)
        health = aggregator.health_check()
        quality = aggregator.quality_metrics()
        full = aggregator.aggregate()
    """

    def __init__(self, event_log: EventLog) -> None:
        """Initialize the aggregator.

        Args:
            event_log: The EventLog to derive metrics from
        """
        self.event_log = event_log

    # ── Full Aggregation ───────────────────────────────────────────────

    def aggregate(self) -> dict[str, Any]:
        """Aggregate all metrics into a single snapshot.

        Returns:
            Dict with timestamp, health, performance, quality, and activity
        """
        return {
            "timestamp": time.time(),
            "health": self.health_check(),
            "performance": self._performance_metrics(),
            "quality": self.quality_metrics(),
            "activity": self._activity_summary(),
        }

    # ── Health Checks ──────────────────────────────────────────────────

    def health_check(self) -> dict[str, Any]:
        """Run health checks on all subsystems.

        Checks:
          - Event log health (is it accessible, how many events)
          - Pipeline health (any recent failures)

        Returns:
            Dict with overall status and per-subsystem checks
        """
        checks: list[HealthCheck] = []

        # Event log health
        stats = self.event_log.get_stats()
        checks.append(HealthCheck(
            name="event_log",
            healthy=stats["total_events"] >= 0,
            message=f"{stats['total_events']} events logged",
            details=stats,
        ))

        # Pipeline health (based on recent events)
        recent = self.event_log.get_recent(limit=HEALTH_CHECK_RECENT_LIMIT)
        has_failures = any(
            e["status"] == EventStatus.FAILED.value for e in recent
        )
        checks.append(HealthCheck(
            name="pipeline",
            healthy=not has_failures,
            message=(
                "All recent operations succeeded"
                if not has_failures
                else "Recent failures detected"
            ),
            details={"recent_count": len(recent)},
        ))

        # Overall health
        all_healthy = all(c.healthy for c in checks)

        return {
            "healthy": all_healthy,
            "checks": [c.to_dict() for c in checks],
            "checked_at": time.time(),
        }

    # ── Quality Metrics ────────────────────────────────────────────────

    def quality_metrics(self) -> dict[str, Any]:
        """Get quality metrics — success rate, abstention rate, failure details.

        Returns:
            Dict with success_rate, abstention_rate, failure_count,
            and recent_failures
        """
        return {
            "success_rate": self._success_rate(),
            "abstention_rate": self._abstention_rate(),
            "failure_count": len(self._failure_details(limit=FAILURE_DETAILS_LIMIT)),
            "recent_failures": self._failure_details(limit=5),
        }

    # ── Performance Metrics ────────────────────────────────────────────

    def _performance_metrics(self) -> dict[str, Any]:
        """Get performance metrics — avg duration, per-type breakdown, slow ops."""
        stats = self.event_log.get_stats()
        tracked_types = [
            "import", "chunk", "embed", "retrieve",
            "generate", "query", "reflect", "improve",
        ]
        by_type: dict[str, dict[str, Any]] = {}
        for etype in tracked_types:
            by_type[etype] = {
                "avg_ms": self._avg_duration(etype),
                "count": stats["by_type"].get(etype, 0),
            }

        return {
            "avg_duration_ms": self._avg_duration(),
            "by_type": by_type,
            "slow_operations": self._slow_operations(),
        }

    # ── Activity Summary ───────────────────────────────────────────────

    def _activity_summary(self) -> dict[str, Any]:
        """Get summary of recent activity — total events, by module, by status."""
        stats = self.event_log.get_stats()
        recent = self.event_log.get_recent(limit=ACTIVITY_RECENT_LIMIT)
        return {
            "total_events": stats["total_events"],
            "by_module": stats["by_module"],
            "by_status": stats["by_status"],
            "recent_events": [
                {
                    "timestamp": e["timestamp"],
                    "type": e["event_type"],
                    "module": e["module"],
                    "status": e["status"],
                    "duration_ms": e["duration_ms"],
                }
                for e in recent
            ],
        }

    # ── Internal Metric Calculations ───────────────────────────────────

    def _operation_counts(self) -> dict[str, int]:
        """Get count of each operation type from the event log."""
        stats = self.event_log.get_stats()
        return stats["by_type"]

    def _success_rate(self, event_type: str | None = None) -> float:
        """Compute success rate (0.0 to 1.0) for all or specific operation types.

        Args:
            event_type: Optional event type filter

        Returns:
            Success rate as a float (0.0-1.0). Returns 1.0 if no events
            (no events = no failures).
        """
        events = self.event_log.query(
            event_type=event_type, limit=DEFAULT_QUERY_LIMIT
        )
        if not events:
            return 1.0
        completed = sum(
            1 for e in events if e["status"] == EventStatus.COMPLETED.value
        )
        failed = sum(
            1 for e in events if e["status"] == EventStatus.FAILED.value
        )
        total = completed + failed
        if total == 0:
            return 1.0
        return completed / total

    def _abstention_rate(self, event_type: str | None = None) -> float:
        """Compute rate of UNKNOWN/abstention responses (0.0 to 1.0).

        Args:
            event_type: Optional event type filter (defaults to "generate")

        Returns:
            Abstention rate as a float (0.0-1.0)
        """
        events = self.event_log.query(
            event_type=event_type or "generate", limit=DEFAULT_QUERY_LIMIT
        )
        if not events:
            return 0.0
        unknown = sum(
            1 for e in events if e["status"] == EventStatus.UNKNOWN.value
        )
        return unknown / len(events)

    def _avg_duration(self, event_type: str | None = None) -> float:
        """Compute average duration in ms for all or specific operation types.

        Args:
            event_type: Optional event type filter

        Returns:
            Average duration in milliseconds, or 0.0 if no events
        """
        events = self.event_log.query(
            event_type=event_type, limit=DEFAULT_QUERY_LIMIT
        )
        if not events:
            return 0.0
        durations = [e["duration_ms"] for e in events if e["duration_ms"] > 0]
        if not durations:
            return 0.0
        return sum(durations) / len(durations)

    def _slow_operations(
        self,
        threshold_ms: float = SLOW_OPERATION_THRESHOLD_MS,
        limit: int = SLOW_OPS_LIMIT,
    ) -> list[dict[str, Any]]:
        """Get operations that took longer than a threshold.

        Args:
            threshold_ms: Duration threshold in milliseconds
            limit: Maximum number of slow operations to return

        Returns:
            List of event dicts, sorted by duration (longest first)
        """
        events = self.event_log.query(limit=DEFAULT_QUERY_LIMIT)
        slow = [e for e in events if e["duration_ms"] > threshold_ms]
        slow.sort(key=lambda e: e["duration_ms"], reverse=True)
        return slow[:limit]

    def _failure_details(self, limit: int = FAILURE_DETAILS_LIMIT) -> list[dict[str, Any]]:
        """Get details of recent failures.

        Args:
            limit: Maximum number of failures to return

        Returns:
            List of failed event dicts
        """
        return self.event_log.query(
            status=EventStatus.FAILED.value, limit=limit
        )

    # ── Representation ─────────────────────────────────────────────────

    def __repr__(self) -> str:
        stats = self.event_log.get_stats()
        return f"MetricsAggregator(events={stats['total_events']})"
