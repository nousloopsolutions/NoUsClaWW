"""Earned memory engine — measure whether lessons actually help.

A lesson is only "earned" if it produces measurable improvement. This module
computes effectiveness metrics for each lesson stored in the memory manager:

- **success_rate** — fraction of recorded outcomes that were successes.
- **lift** — how much the lesson's success rate exceeds the global average
  across all lessons. Positive lift means the lesson helps; negative lift
  means it hurts.
- **churn** — instability of outcomes over time, measured as the fraction of
  consecutive outcome pairs that flip (success→failure or failure→success).
  High churn means the lesson's effectiveness is volatile.
- **wilson_ci** — Wilson score interval [lower, upper] for the success rate
  at 95% confidence. Unlike a naive proportion, the Wilson interval remains
  sane for small sample sizes (even 0 out of 1).

All metrics are computed from the ``lesson_outcomes`` table via the memory
manager's API. No external services are required.

SYNTH:
    purpose: Measure if lessons actually help by computing success_rate, lift, churn, and Wilson CI
    axioms: [evidence_over_intuition, scientific_method, honest_failure_over_fake_success, epistemic_boundary]
    objective: Every lesson has quantified effectiveness metrics with confidence intervals so the agent
        can decide which lessons to keep, revise, or retire based on data rather than intuition
    anti_patterns:
        - Reporting a success rate without a confidence interval
        - Treating a 1/1 success as proof the lesson works
        - Hiding lessons with poor effectiveness
        - Computing lift without a global baseline
        - Using a naive normal approximation for small sample sizes
#C Inspired by PMB (Project Memory Bank) sleep engine
"""

from __future__ import annotations

import logging
import math
from typing import Any

from nousclaww.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# Z-score for a 95% two-sided confidence interval.
Z_95: float = 1.96

# Minimum number of outcomes before lift is considered meaningful.
# Below this threshold lift is reported but flagged as low_confidence.
MIN_SAMPLE_FOR_LIFT: int = 5


class EarnedMemory:
    """Compute effectiveness metrics for lessons stored in the memory manager.

    This class is stateless — all data lives in the memory manager's SQLite
    database. Each method opens a short-lived read transaction and returns
    plain dicts.
    """

    @staticmethod
    def _wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
        """Compute the Wilson score interval for a binomial proportion.

        The Wilson interval is preferred over the naive normal approximation
        because it behaves correctly for small samples and edge cases
        (0/n, n/n). See: Wilson, E. B. (1927), "Probable Inference."

        Args:
            successes: Number of observed successes.
            total: Total number of trials.
            z: Z-score for the desired confidence level (default 1.96 for 95%).

        Returns:
            A (lower, upper) tuple of floats in [0.0, 1.0].
        """
        if total <= 0:
            return 0.0, 0.0

        n = total
        p_hat = successes / n
        z2 = z * z
        denominator = 1.0 + z2 / n
        center = (p_hat + z2 / (2 * n)) / denominator
        margin = (z / denominator) * math.sqrt(
            (p_hat * (1.0 - p_hat) / n) + (z2 / (4 * n * n))
        )
        lower = max(0.0, center - margin)
        upper = min(1.0, center + margin)
        return lower, upper

    @staticmethod
    def _compute_churn(outcomes: list[dict[str, Any]]) -> float:
        """Compute outcome churn — the fraction of consecutive pairs that flip.

        Churn measures instability: if outcomes alternate between success and
        failure, churn is 1.0. If outcomes are constant, churn is 0.0.

        Args:
            outcomes: List of outcome dicts sorted by timestamp ascending.
                Each dict must have a ``success`` key (1 or 0).

        Returns:
            Float in [0.0, 1.0]. Returns 0.0 if fewer than 2 outcomes.
        """
        if len(outcomes) < 2:
            return 0.0

        flips = 0
        pairs = 0
        prev: int | None = None
        for o in outcomes:
            curr = int(o.get("success", 0))
            if prev is not None:
                pairs += 1
                if curr != prev:
                    flips += 1
            prev = curr

        if pairs == 0:
            return 0.0
        return flips / pairs

    @staticmethod
    def _get_ordered_outcomes(
        memory_manager: MemoryManager, lesson_id: str
    ) -> list[dict[str, Any]]:
        """Fetch raw lesson outcomes ordered by timestamp.

        The memory manager's ``get_lesson_outcomes`` returns aggregated counts,
        but for churn we need the ordered sequence. This helper queries the
        ``lesson_outcomes`` table directly through the manager's connection.

        Args:
            memory_manager: The memory manager to query.
            lesson_id: The lesson whose outcomes are needed.

        Returns:
            List of outcome dicts with ``success`` and ``timestamp`` keys,
            ordered by timestamp ascending.
        """
        conn = memory_manager._get_conn()
        try:
            rows = conn.execute(
                "SELECT success, timestamp FROM lesson_outcomes "
                "WHERE lesson_id = ? ORDER BY timestamp ASC",
                (lesson_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _compute_global_baseline(memory_manager: MemoryManager) -> float:
        """Compute the global average success rate across all lessons.

        This is the baseline against which per-lesson lift is measured.

        Args:
            memory_manager: The memory manager to query.

        Returns:
            Float in [0.0, 1.0] representing the overall success rate. If no
            outcomes exist, returns 0.0.
        """
        conn = memory_manager._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as total, SUM(success) as successes "
                "FROM lesson_outcomes"
            ).fetchone()
            total = row["total"] if row else 0
            successes = row["successes"] if row and row["successes"] is not None else 0
            if total <= 0:
                return 0.0
            return successes / total
        finally:
            conn.close()

    def measure(self, lesson_id: str, memory_manager: MemoryManager) -> dict[str, Any]:
        """Compute effectiveness metrics for a single lesson.

        Metrics returned:
            - lesson_id: The ID of the measured lesson.
            - total: Total number of recorded outcomes.
            - successes: Number of successful outcomes.
            - failures: Number of failed outcomes.
            - success_rate: successes / total (0.0 if no outcomes).
            - lift: success_rate minus the global average success rate.
            - churn: Fraction of consecutive outcome pairs that flip.
            - wilson_lower: Lower bound of the 95% Wilson CI.
            - wilson_upper: Upper bound of the 95% Wilson CI.
            - low_confidence: True if total < MIN_SAMPLE_FOR_LIFT.

        Args:
            lesson_id: The memory_id of the lesson to measure.
            memory_manager: The memory manager holding lesson outcomes.

        Returns:
            A dict with the metrics listed above. If the lesson does not
            exist, returns a dict with total=0 and zeroed metrics.
        """
        # Verify the lesson exists.
        lesson = memory_manager.get_memory(lesson_id)
        if lesson is None:
            logger.warning("EarnedMemory: lesson %s not found.", lesson_id)
            return {
                "lesson_id": lesson_id,
                "total": 0,
                "successes": 0,
                "failures": 0,
                "success_rate": 0.0,
                "lift": 0.0,
                "churn": 0.0,
                "wilson_lower": 0.0,
                "wilson_upper": 0.0,
                "low_confidence": True,
            }

        # Aggregated counts from the memory manager.
        agg = memory_manager.get_lesson_outcomes(lesson_id)
        total = agg["total"]
        successes = agg["successes"]
        failures = agg["failures"]
        success_rate = agg["success_rate"]

        # Wilson confidence interval.
        wilson_lower, wilson_upper = self._wilson_interval(successes, total)

        # Global baseline for lift computation.
        baseline = self._compute_global_baseline(memory_manager)
        lift = success_rate - baseline

        # Churn from ordered outcomes.
        ordered = self._get_ordered_outcomes(memory_manager, lesson_id)
        churn = self._compute_churn(ordered)

        low_confidence = total < MIN_SAMPLE_FOR_LIFT

        logger.debug(
            "EarnedMemory: lesson %s — rate=%.3f, lift=%+.3f, churn=%.3f, "
            "wilson=[%.3f, %.3f], n=%d.",
            lesson_id,
            success_rate,
            lift,
            churn,
            wilson_lower,
            wilson_upper,
            total,
        )

        return {
            "lesson_id": lesson_id,
            "total": total,
            "successes": successes,
            "failures": failures,
            "success_rate": success_rate,
            "lift": lift,
            "churn": churn,
            "wilson_lower": wilson_lower,
            "wilson_upper": wilson_upper,
            "low_confidence": low_confidence,
        }

    def report(self, memory_manager: MemoryManager) -> list[dict[str, Any]]:
        """Compute effectiveness metrics for every lesson in the memory store.

        Lessons are sorted by lift descending — the most effective lessons
        appear first. Lessons with no recorded outcomes are included with
        zeroed metrics so the report is complete.

        Args:
            memory_manager: The memory manager holding lessons and outcomes.

        Returns:
            List of metric dicts (one per lesson), sorted by lift descending.
        """
        lessons = memory_manager.get_all_lessons()
        if not lessons:
            logger.info("EarnedMemory: no lessons found — empty report.")
            return []

        results: list[dict[str, Any]] = []
        for lesson in lessons:
            lesson_id = lesson["memory_id"]
            metrics = self.measure(lesson_id, memory_manager)
            # Include the lesson content for context in the report.
            metrics["content"] = lesson.get("content", "")
            metrics["importance"] = lesson.get("importance", 0.0)
            results.append(metrics)

        # Sort by lift descending — most effective lessons first.
        results.sort(key=lambda m: m["lift"], reverse=True)

        logger.info(
            "EarnedMemory: report generated for %d lessons.", len(results)
        )
        return results
