"""Absence Detector — detects when something is MISSING, not just what is present.

Most systems find patterns in what IS there. This module also finds patterns
in what ISN'T there — to notice gaps, to detect when expected things are
absent, and to cultivate the transition from unknown-unknown to known-unknown.

SIX GAP TYPES:

  1. PATTERN     — An expected pattern is missing (e.g. high UNKNOWN rate).
  2. CONNECTION  — Two knowns that should be connected aren't (e.g. ingests
                   but no retrieves).
  3. TEMPORAL    — A time period with no data where data was expected.
  4. CATEGORY    — A category that should have entries but doesn't.
  5. CAPACITY    — A resource that should be used but isn't (e.g. low voxel
                   utilization).
  6. CONSISTENCY — Two related metrics that should agree but don't (e.g.
                   high similarity but low KNOWN rate).

PUZZLE PRINCIPLE:
  A gap is defined by what surrounds it. Each AbsenceGap includes a
  ``context`` dict providing the surrounding known information that makes
  the gap visible.

INVARIANTS:
  1. Absence detection is read-only — it observes, never modifies.
  2. Detected gaps are returned as structured data, not acted upon.
  3. Each gap includes surrounding context (Puzzle Principle).
  4. Gaps are classified by severity (INFO, WARNING, CRITICAL).
  5. The detector does NOT force resolution — it reports, the caller decides.

SYNTH:
    purpose: Detect six types of absence gaps (pattern, connection, temporal, category, capacity, consistency) from monitoring stats, implementing the Puzzle Principle.
    axioms: [epistemic_boundary, evidence_over_intuition, honest_failure_over_fake_success, scientific_method]
    objective: Given a stats dictionary from a monitoring module, return a sorted list of AbsenceGap objects that identify what is missing — each with surrounding context, expected vs. actual values, and a non-directive recommendation.
    anti_patterns:
        - Auto-correcting or modifying detected gaps (read-only invariant)
        - Importing internal nousclaww monitoring modules (duck-typed stats dict only)
        - Acting on gaps instead of reporting them (detection is not resolution)
        - Returning gaps without surrounding context (violates Puzzle Principle)
        - Using a fixed threshold that cannot be tuned via constructor parameters
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core core/absence_detector.py

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    "GapType",
    "GapSeverity",
    "AbsenceGap",
    "AbsenceDetector",
]

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GapSeverity(Enum):
    """Severity of a detected absence.

    - INFO:     Noteworthy but not concerning.
    - WARNING:  May indicate a problem.
    - CRITICAL: Likely indicates a problem.
    """

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class GapType(Enum):
    """Type of detected absence.

    - PATTERN:     Expected pattern is missing.
    - CONNECTION:  Expected connection between knowns is missing.
    - TEMPORAL:    Expected data in a time period is missing.
    - CATEGORY:    Expected entries in a category are missing.
    - CAPACITY:    Expected resource utilization is missing.
    - CONSISTENCY: Expected agreement between metrics is missing.
    """

    PATTERN = "PATTERN"
    CONNECTION = "CONNECTION"
    TEMPORAL = "TEMPORAL"
    CATEGORY = "CATEGORY"
    CAPACITY = "CAPACITY"
    CONSISTENCY = "CONSISTENCY"


# ---------------------------------------------------------------------------
# AbsenceGap dataclass
# ---------------------------------------------------------------------------


@dataclass
class AbsenceGap:
    """A detected absence — something that is missing.

    Implements the Puzzle Principle: the gap is defined by what surrounds
    it. The ``context`` field provides the surrounding known information
    that makes the gap visible.

    Attributes:
        gap_type: The type of absence (one of GapType).
        severity: The severity level (INFO, WARNING, CRITICAL).
        description: Human-readable description of the gap.
        context: Surrounding known information that defines the gap
            (Puzzle Principle).
        expected: What was expected.
        actual: What was found (or not found).
        recommendation: Suggested investigation (non-directive — the
            caller decides whether to act).
    """

    gap_type: GapType
    severity: GapSeverity
    description: str
    context: dict[str, Any] = field(default_factory=dict)
    expected: Any = None
    actual: Any = None
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary.

        Returns:
            A dictionary with all gap fields.
        """
        return {
            "gap_type": self.gap_type.value,
            "severity": self.severity.value,
            "description": self.description,
            "context": self.context,
            "expected": self.expected,
            "actual": self.actual,
            "recommendation": self.recommendation,
        }


# ---------------------------------------------------------------------------
# AbsenceDetector
# ---------------------------------------------------------------------------


class AbsenceDetector:
    """Detects absences in system monitoring stats.

    Reads a stats dictionary (duck-typed — any module that produces a
    compatible stats dict works) and identifies gaps: things that are
    missing that should be present.

    The detector is read-only: it observes and reports, never modifies.
    The caller decides what to do with detected gaps.

    Usage::

        detector = AbsenceDetector()
        gaps = detector.detect_gaps(monitor.get_stats())
        for gap in gaps:
            print(f"{gap.severity.value}: {gap.description}")
    """

    def __init__(
        self,
        empty_category_warning: int = 0,
        low_category_warning: int = 3,
        low_utilization_warning: float = 0.05,
        low_utilization_info: float = 0.20,
        max_snapshot_gap_seconds: float = 3600.0,
        high_unknown_rate_warning: float = 0.5,
        high_unknown_rate_critical: float = 0.8,
        low_known_rate_warning: float = 0.3,
        high_invalid_telemetry_warning: float = 0.3,
        high_invalid_telemetry_critical: float = 0.5,
        expected_categories: Optional[list[str]] = None,
        min_commits_for_category_check: int = 5,
        min_commits_for_capacity_check: int = 10,
        min_operations_for_pattern_check: int = 5,
        min_operations_for_consistency_check: int = 10,
    ) -> None:
        """Initialize the detector with configurable thresholds.

        All thresholds have sensible defaults but can be tuned per
        deployment without code changes.

        Args:
            empty_category_warning: Count below which a category is
                considered empty (0 = truly empty triggers WARNING).
            low_category_warning: Count below which a category is
                considered underused (triggers INFO).
            low_utilization_warning: Utilization fraction below which
                capacity is critically underused (WARNING).
            low_utilization_info: Utilization fraction below which
                capacity is underused (INFO).
            max_snapshot_gap_seconds: Maximum seconds allowed between
                snapshots before a temporal gap is detected.
            high_unknown_rate_warning: UNKNOWN rate above which a
                pattern gap is detected (WARNING).
            high_unknown_rate_critical: UNKNOWN rate above which a
                pattern gap is detected (CRITICAL).
            low_known_rate_warning: KNOWN rate below which a pattern
                gap is detected (WARNING).
            high_invalid_telemetry_warning: Invalid telemetry rate
                above which a pattern gap is detected (WARNING).
            high_invalid_telemetry_critical: Invalid telemetry rate
                above which a pattern gap is detected (CRITICAL).
            expected_categories: Categories that should have entries
                in a healthy system. Defaults to a standard set.
            min_commits_for_category_check: Minimum commits before
                category gaps are checked.
            min_commits_for_capacity_check: Minimum commits before
                capacity gaps are checked.
            min_operations_for_pattern_check: Minimum operations
                before pattern gaps are checked.
            min_operations_for_consistency_check: Minimum operations
                before consistency gaps are checked.
        """
        self.empty_category_warning = empty_category_warning
        self.low_category_warning = low_category_warning
        self.low_utilization_warning = low_utilization_warning
        self.low_utilization_info = low_utilization_info
        self.max_snapshot_gap_seconds = max_snapshot_gap_seconds
        self.high_unknown_rate_warning = high_unknown_rate_warning
        self.high_unknown_rate_critical = high_unknown_rate_critical
        self.low_known_rate_warning = low_known_rate_warning
        self.high_invalid_telemetry_warning = high_invalid_telemetry_warning
        self.high_invalid_telemetry_critical = high_invalid_telemetry_critical
        self.expected_categories = expected_categories or [
            "ENGRAM",
            "LABEL",
            "VOXEL",
        ]
        self.min_commits_for_category_check = min_commits_for_category_check
        self.min_commits_for_capacity_check = min_commits_for_capacity_check
        self.min_operations_for_pattern_check = min_operations_for_pattern_check
        self.min_operations_for_consistency_check = (
            min_operations_for_consistency_check
        )

    def detect_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect all absence types in the given monitoring stats.

        Args:
            stats: A dictionary of monitoring statistics. Expected to
                contain sub-dicts like ``storage``, ``retrieval``,
                ``intake``, and ``health`` — but missing keys are
                handled gracefully (no gap detected for absent sections).

        Returns:
            A list of AbsenceGap objects, sorted by severity (CRITICAL
            first, then WARNING, then INFO).
        """
        gaps: list[AbsenceGap] = []

        gaps.extend(self._detect_category_gaps(stats))
        gaps.extend(self._detect_capacity_gaps(stats))
        gaps.extend(self._detect_retrieval_gaps(stats))
        gaps.extend(self._detect_intake_gaps(stats))
        gaps.extend(self._detect_temporal_gaps(stats))
        gaps.extend(self._detect_connection_gaps(stats))
        gaps.extend(self._detect_consistency_gaps(stats))

        # Sort by severity: CRITICAL > WARNING > INFO.
        severity_order = {
            GapSeverity.CRITICAL: 0,
            GapSeverity.WARNING: 1,
            GapSeverity.INFO: 2,
        }
        gaps.sort(key=lambda g: severity_order.get(g.severity, 3))
        return gaps

    def _detect_category_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect categories that should have entries but don't.

        Args:
            stats: Monitoring stats dictionary.

        Returns:
            List of CATEGORY gaps.
        """
        gaps: list[AbsenceGap] = []
        storage = stats.get("storage", {})
        if not isinstance(storage, dict):
            return gaps
        cat_dist = storage.get("category_distribution", {})
        if not isinstance(cat_dist, dict):
            return gaps
        total_commits = storage.get("total_commits", 0)

        if total_commits < self.min_commits_for_category_check:
            return gaps

        for cat in self.expected_categories:
            count = cat_dist.get(cat, 0)
            if count <= self.empty_category_warning:
                gaps.append(
                    AbsenceGap(
                        gap_type=GapType.CATEGORY,
                        severity=GapSeverity.WARNING,
                        description=(
                            f"Category {cat} has {count} entries after "
                            f"{total_commits} commits"
                        ),
                        context={
                            "total_commits": total_commits,
                            "categories_with_entries": [
                                c for c in cat_dist if cat_dist[c] > 0
                            ],
                            "category_distribution": dict(cat_dist),
                        },
                        expected=(
                            f"{cat} should have entries in a healthy system"
                        ),
                        actual=f"{cat} has {count} entries",
                        recommendation=(
                            f"Investigate why no {cat} entries are being "
                            f"created — check ingest pipeline and category "
                            f"assignment"
                        ),
                    )
                )
            elif count < self.low_category_warning:
                gaps.append(
                    AbsenceGap(
                        gap_type=GapType.CATEGORY,
                        severity=GapSeverity.INFO,
                        description=(
                            f"Category {cat} has only {count} entries after "
                            f"{total_commits} commits"
                        ),
                        context={
                            "total_commits": total_commits,
                            "category_distribution": dict(cat_dist),
                        },
                        expected=f"{cat} should have more entries by now",
                        actual=f"{cat} has {count} entries",
                        recommendation=(
                            f"Monitor {cat} category — if count stays low, "
                            f"investigate ingest pipeline"
                        ),
                    )
                )

        return gaps

    def _detect_capacity_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect underutilized resources.

        Args:
            stats: Monitoring stats dictionary.

        Returns:
            List of CAPACITY gaps.
        """
        gaps: list[AbsenceGap] = []
        storage = stats.get("storage", {})
        if not isinstance(storage, dict):
            return gaps
        voxel = storage.get("voxel_occupancy", {})
        if not isinstance(voxel, dict):
            return gaps
        occupied = voxel.get("occupied", 0)
        total = voxel.get("total", 64)
        utilization = voxel.get("utilization", 0.0)
        total_commits = storage.get("total_commits", 0)

        if total_commits < self.min_commits_for_capacity_check:
            return gaps

        if utilization < self.low_utilization_warning:
            gaps.append(
                AbsenceGap(
                    gap_type=GapType.CAPACITY,
                    severity=GapSeverity.WARNING,
                    description=(
                        f"Voxel utilization is {utilization:.1%} "
                        f"({occupied}/{total}) after {total_commits} commits"
                    ),
                    context={
                        "total_commits": total_commits,
                        "occupied_voxels": occupied,
                        "total_voxels": total,
                        "hotspots": voxel.get("hotspots", {}),
                    },
                    expected=(
                        f"Voxel utilization should be "
                        f">{self.low_utilization_warning:.0%} after "
                        f"{total_commits} commits"
                    ),
                    actual=f"Voxel utilization is {utilization:.1%}",
                    recommendation=(
                        "Check if all ingests are going to the same voxel — "
                        "may indicate context/time/state parameters are not "
                        "being varied"
                    ),
                )
            )
        elif utilization < self.low_utilization_info:
            gaps.append(
                AbsenceGap(
                    gap_type=GapType.CAPACITY,
                    severity=GapSeverity.INFO,
                    description=(
                        f"Voxel utilization is {utilization:.1%} — "
                        f"most cells are empty"
                    ),
                    context={
                        "total_commits": total_commits,
                        "occupied_voxels": occupied,
                        "total_voxels": total,
                    },
                    expected=(
                        f"Higher utilization expected after "
                        f"{total_commits} commits"
                    ),
                    actual=f"Voxel utilization is {utilization:.1%}",
                    recommendation=(
                        "Consider varying context, time_slot, and state "
                        "parameters during ingest"
                    ),
                )
            )

        return gaps

    def _detect_retrieval_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect retrieval health issues.

        Args:
            stats: Monitoring stats dictionary.

        Returns:
            List of PATTERN gaps related to retrieval.
        """
        gaps: list[AbsenceGap] = []
        retrieval = stats.get("retrieval", {})
        if not isinstance(retrieval, dict):
            return gaps
        total = retrieval.get("total_retrieves", 0)

        if total < self.min_operations_for_pattern_check:
            return gaps

        state_rates = retrieval.get("state_rates", {})
        if not isinstance(state_rates, dict):
            return gaps
        unknown_rate = state_rates.get("UNKNOWN", 0.0)
        known_rate = state_rates.get("KNOWN", 0.0)
        failure_modes = retrieval.get("failure_modes", {})

        surrounding = {
            "total_retrieves": total,
            "state_rates": dict(state_rates),
            "failure_modes": dict(failure_modes) if isinstance(failure_modes, dict) else {},
            "similarity_distribution": retrieval.get("similarity_distribution", {}),
        }

        if unknown_rate >= self.high_unknown_rate_critical:
            gaps.append(
                AbsenceGap(
                    gap_type=GapType.PATTERN,
                    severity=GapSeverity.CRITICAL,
                    description=(
                        f"{unknown_rate:.1%} of retrievals return UNKNOWN — "
                        f"system is failing to recall"
                    ),
                    context=surrounding,
                    expected=(
                        f"UNKNOWN rate should be "
                        f"<{self.high_unknown_rate_warning:.0%}"
                    ),
                    actual=f"UNKNOWN rate is {unknown_rate:.1%}",
                    recommendation=(
                        "Check if memories are being stored correctly — "
                        "high UNKNOWN after ingest suggests storage or "
                        "retrieval pipeline failure"
                    ),
                )
            )
        elif unknown_rate >= self.high_unknown_rate_warning:
            gaps.append(
                AbsenceGap(
                    gap_type=GapType.PATTERN,
                    severity=GapSeverity.WARNING,
                    description=(
                        f"{unknown_rate:.1%} of retrievals return UNKNOWN"
                    ),
                    context=surrounding,
                    expected=(
                        f"UNKNOWN rate should be "
                        f"<{self.high_unknown_rate_warning:.0%}"
                    ),
                    actual=f"UNKNOWN rate is {unknown_rate:.1%}",
                    recommendation=(
                        "Investigate failure modes — check if "
                        "label_not_found or low_similarity dominates"
                    ),
                )
            )

        if known_rate < self.low_known_rate_warning and total >= 10:
            gaps.append(
                AbsenceGap(
                    gap_type=GapType.PATTERN,
                    severity=GapSeverity.WARNING,
                    description=(
                        f"Only {known_rate:.1%} of retrievals return KNOWN — "
                        f"recall accuracy is low"
                    ),
                    context=surrounding,
                    expected=(
                        f"KNOWN rate should be "
                        f">{self.low_known_rate_warning:.0%}"
                    ),
                    actual=f"KNOWN rate is {known_rate:.1%}",
                    recommendation=(
                        "Check similarity distribution — if mean similarity "
                        "is low, may indicate encoding or binding issues"
                    ),
                )
            )

        return gaps

    def _detect_intake_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect intake health issues.

        Args:
            stats: Monitoring stats dictionary.

        Returns:
            List of PATTERN gaps related to intake.
        """
        gaps: list[AbsenceGap] = []
        intake = stats.get("intake", {})
        if not isinstance(intake, dict):
            return gaps
        total = intake.get("total_ingests", 0)

        if total < self.min_operations_for_pattern_check:
            return gaps

        telemetry = intake.get("telemetry_validity", {})
        if not isinstance(telemetry, dict):
            return gaps
        total_telemetry = (
            telemetry.get("valid", 0)
            + telemetry.get("invalid", 0)
            + telemetry.get("none", 0)
        )
        invalid_rate = (
            telemetry.get("invalid", 0) / total_telemetry
            if total_telemetry > 0
            else 0.0
        )

        surrounding = {
            "total_ingests": total,
            "telemetry_validity": dict(telemetry),
            "sensor_usage": intake.get("sensor_usage", {}),
        }

        if invalid_rate >= self.high_invalid_telemetry_critical:
            gaps.append(
                AbsenceGap(
                    gap_type=GapType.PATTERN,
                    severity=GapSeverity.CRITICAL,
                    description=(
                        f"{invalid_rate:.1%} of ingests have invalid "
                        f"telemetry — sensor values are out of range"
                    ),
                    context=surrounding,
                    expected=(
                        f"Invalid telemetry rate should be "
                        f"<{self.high_invalid_telemetry_warning:.0%}"
                    ),
                    actual=f"Invalid telemetry rate is {invalid_rate:.1%}",
                    recommendation=(
                        "Check sensor schema — values may be outside "
                        "declared ranges. Update ranges or fix sensor "
                        "data source"
                    ),
                )
            )
        elif invalid_rate >= self.high_invalid_telemetry_warning:
            gaps.append(
                AbsenceGap(
                    gap_type=GapType.PATTERN,
                    severity=GapSeverity.WARNING,
                    description=(
                        f"{invalid_rate:.1%} of ingests have invalid "
                        f"telemetry"
                    ),
                    context=surrounding,
                    expected=(
                        f"Invalid telemetry rate should be "
                        f"<{self.high_invalid_telemetry_warning:.0%}"
                    ),
                    actual=f"Invalid telemetry rate is {invalid_rate:.1%}",
                    recommendation=(
                        "Monitor telemetry validity — if trend continues, "
                        "investigate sensor configuration"
                    ),
                )
            )

        return gaps

    def _detect_temporal_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect temporal gaps in monitoring data.

        Args:
            stats: Monitoring stats dictionary.

        Returns:
            List of TEMPORAL gaps.
        """
        gaps: list[AbsenceGap] = []
        health = stats.get("health", {})
        if not isinstance(health, dict):
            return gaps
        ts_entries = health.get("time_series_entries", 0)
        last_snapshot = health.get("last_snapshot_time", 0.0)

        if ts_entries < 2:
            if ts_entries == 0:
                gaps.append(
                    AbsenceGap(
                        gap_type=GapType.TEMPORAL,
                        severity=GapSeverity.INFO,
                        description=(
                            "No time-series snapshots have been taken — "
                            "trend analysis is not possible"
                        ),
                        context={
                            "time_series_entries": ts_entries,
                            "uptime_seconds": health.get("uptime_seconds", 0),
                        },
                        expected="Periodic snapshots for trend analysis",
                        actual="No snapshots exist",
                        recommendation=(
                            "Call monitor.snapshot() periodically to "
                            "enable trend analysis"
                        ),
                    )
                )
            return gaps

        if last_snapshot > 0:
            now = time.time()
            time_since_last = now - last_snapshot
            if time_since_last > self.max_snapshot_gap_seconds:
                gaps.append(
                    AbsenceGap(
                        gap_type=GapType.TEMPORAL,
                        severity=GapSeverity.WARNING,
                        description=(
                            f"No snapshot in {time_since_last / 3600:.1f} "
                            f"hours — monitoring may have stopped"
                        ),
                        context={
                            "time_series_entries": ts_entries,
                            "last_snapshot_time": last_snapshot,
                            "time_since_last_hours": time_since_last / 3600,
                        },
                        expected=(
                            f"Snapshots at least every "
                            f"{self.max_snapshot_gap_seconds / 3600:.0f} "
                            f"hour(s)"
                        ),
                        actual=(
                            f"Last snapshot was "
                            f"{time_since_last / 3600:.1f} hours ago"
                        ),
                        recommendation=(
                            "Check if the snapshot process is running — "
                            "it may have crashed or been stopped"
                        ),
                    )
                )

        return gaps

    def _detect_connection_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect missing connections between related metrics.

        A connection gap is when two things that should be linked aren't.
        For example: if ingests are happening but retrieves are not, there's
        a missing connection between the intake and retrieval pipelines.

        Args:
            stats: Monitoring stats dictionary.

        Returns:
            List of CONNECTION gaps.
        """
        gaps: list[AbsenceGap] = []
        intake = stats.get("intake", {})
        retrieval = stats.get("retrieval", {})
        storage = stats.get("storage", {})

        if not isinstance(intake, dict):
            intake = {}
        if not isinstance(retrieval, dict):
            retrieval = {}
        if not isinstance(storage, dict):
            storage = {}

        total_ingests = intake.get("total_ingests", 0)
        total_retrieves = retrieval.get("total_retrieves", 0)
        total_commits = storage.get("total_commits", 0)

        # Connection gap: ingests happening but no retrieves.
        if total_ingests >= 10 and total_retrieves == 0:
            gaps.append(
                AbsenceGap(
                    gap_type=GapType.CONNECTION,
                    severity=GapSeverity.WARNING,
                    description=(
                        f"{total_ingests} ingests but 0 retrieves — "
                        f"intake and retrieval pipelines are disconnected"
                    ),
                    context={
                        "total_ingests": total_ingests,
                        "total_retrieves": total_retrieves,
                        "total_commits": total_commits,
                    },
                    expected=(
                        "Retrieval should occur after ingest to verify "
                        "storage"
                    ),
                    actual="No retrievals have been performed",
                    recommendation=(
                        "Perform test retrievals after ingest to verify "
                        "the memory pipeline is connected end-to-end"
                    ),
                )
            )

        # Connection gap: commits stored but retrieves return UNKNOWN.
        if total_commits >= 10 and total_retrieves >= 10:
            state_rates = retrieval.get("state_rates", {})
            if isinstance(state_rates, dict):
                unknown_rate = state_rates.get("UNKNOWN", 0.0)
                if unknown_rate >= 0.8:
                    gaps.append(
                        AbsenceGap(
                            gap_type=GapType.CONNECTION,
                            severity=GapSeverity.CRITICAL,
                            description=(
                                f"{total_commits} commits stored but "
                                f"{unknown_rate:.0%} of retrieves return "
                                f"UNKNOWN — storage and retrieval are "
                                f"disconnected"
                            ),
                            context={
                                "total_commits": total_commits,
                                "total_retrieves": total_retrieves,
                                "unknown_rate": unknown_rate,
                                "state_rates": dict(state_rates),
                            },
                            expected=(
                                "Stored memories should be retrievable "
                                "(low UNKNOWN rate)"
                            ),
                            actual=(
                                f"{unknown_rate:.0%} of retrieves return "
                                f"UNKNOWN despite {total_commits} commits"
                            ),
                            recommendation=(
                                "Check if LABEL category vectors are being "
                                "registered during ingest — retrieval "
                                "returns UNKNOWN without clean-up vocabulary"
                            ),
                        )
                    )

        return gaps

    def _detect_consistency_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect disagreements between related metrics.

        A consistency gap is when two metrics that should agree don't.
        For example: high mean similarity but low KNOWN rate suggests
        the epistemic gate threshold is miscalibrated.

        Args:
            stats: Monitoring stats dictionary.

        Returns:
            List of CONSISTENCY gaps.
        """
        gaps: list[AbsenceGap] = []
        retrieval = stats.get("retrieval", {})
        if not isinstance(retrieval, dict):
            return gaps
        total = retrieval.get("total_retrieves", 0)

        if total < self.min_operations_for_consistency_check:
            return gaps

        sim_dist = retrieval.get("similarity_distribution", {})
        state_rates = retrieval.get("state_rates", {})
        if not isinstance(sim_dist, dict):
            sim_dist = {}
        if not isinstance(state_rates, dict):
            state_rates = {}

        mean_sim = sim_dist.get("mean", 0.0)
        known_rate = state_rates.get("KNOWN", 0.0)

        # High similarity but low KNOWN rate — threshold may be too high.
        if mean_sim > 0.6 and known_rate < 0.3:
            gaps.append(
                AbsenceGap(
                    gap_type=GapType.CONSISTENCY,
                    severity=GapSeverity.WARNING,
                    description=(
                        f"Mean similarity is {mean_sim:.2f} but KNOWN rate "
                        f"is only {known_rate:.1%} — metrics disagree"
                    ),
                    context={
                        "mean_similarity": mean_sim,
                        "known_rate": known_rate,
                        "similarity_distribution": dict(sim_dist),
                        "state_rates": dict(state_rates),
                    },
                    expected=(
                        "High mean similarity should correlate with "
                        "high KNOWN rate"
                    ),
                    actual=(
                        f"Mean sim={mean_sim:.2f}, "
                        f"KNOWN rate={known_rate:.1%}"
                    ),
                    recommendation=(
                        "Check epistemic gate threshold (tau) — it may be "
                        "set too high for the current similarity distribution"
                    ),
                )
            )

        # Low similarity but high KNOWN rate — threshold may be too low.
        if mean_sim < 0.3 and known_rate > 0.7:
            gaps.append(
                AbsenceGap(
                    gap_type=GapType.CONSISTENCY,
                    severity=GapSeverity.WARNING,
                    description=(
                        f"Mean similarity is only {mean_sim:.2f} but KNOWN "
                        f"rate is {known_rate:.1%} — metrics disagree"
                    ),
                    context={
                        "mean_similarity": mean_sim,
                        "known_rate": known_rate,
                        "similarity_distribution": dict(sim_dist),
                        "state_rates": dict(state_rates),
                    },
                    expected=(
                        "Low mean similarity should correlate with "
                        "low KNOWN rate"
                    ),
                    actual=(
                        f"Mean sim={mean_sim:.2f}, "
                        f"KNOWN rate={known_rate:.1%}"
                    ),
                    recommendation=(
                        "Check if similarity is being computed correctly — "
                        "or if gate threshold is too low"
                    ),
                )
            )

        return gaps
