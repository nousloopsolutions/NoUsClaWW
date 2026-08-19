"""Adaptive importance engine — learn from recall failures by boosting importance.

When the self-test engine probes memory recall, some memories fail to surface.
Those failures are not ignored: this module boosts the importance of the
underlying memories so they are more likely to be recalled next time. The
boost is proportional to how badly recall failed (a complete miss gets a
larger boost than a partial recall) and is always clamped to [0.0, 1.0] by
the memory manager.

The self_test_results dict is expected to carry one of two shapes:

Shape A (explicit failure list)::

    {
        "failures": [
            {"memory_id": "mem-1", "severity": 0.8},
            {"memory_id": "mem-2", "severity": 0.3},
        ],
        "passes": ["mem-3", "mem-4"],
        "total": 4,
    }

Shape B (flat results list with per-item verdicts)::

    {
        "total": 10,
        "passed": 7,
        "failed": 3,
        "results": [
            {"memory_id": "mem-1", "verdict": "pass"},
            {"memory_id": "mem-2", "verdict": "fail", "severity": 0.6},
            {"memory_id": "mem-3", "verdict": "fail"},
        ],
    }

``severity`` is optional and defaults to ``DEFAULT_SEVERITY``. It is
interpreted as "how bad was the recall failure" on a 0.0-1.0 scale where
1.0 means the memory was completely absent from recall.

SYNTH:
    purpose: Learn from recall failures by boosting importance of memories that failed self-test recall
    axioms: [evidence_over_intuition, iteration_is_progress, reversibility_awareness, scientific_method]
    objective: Every memory that fails recall receives a measurable importance boost so it surfaces
        more reliably next time, with the boost size proportional to failure severity
    anti_patterns:
        - Boosting importance without evidence of a recall failure
        - Boosting importance above 1.0 or below 0.0
        - Applying the same boost to every failure regardless of severity
        - Silently skipping memories that no longer exist instead of logging
        - Modifying the self_test_results dict in place
#C Inspired by PMB (Project Memory Bank) sleep engine
"""

from __future__ import annotations

import logging

from nousclaww.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# Default severity when a failure entry omits the field.
DEFAULT_SEVERITY: float = 0.5

# Base importance boost applied for a recall failure.
# The actual boost is BASE_BOOST * severity, so a complete miss (severity=1.0)
# gets the full base boost while a partial failure gets less.
BASE_BOOST: float = 0.15

# Verdict strings that count as a failure.
FAILURE_VERDICTS: frozenset[str] = frozenset({"fail", "failed", "miss", "absent"})


class AdaptiveImportance:
    """Boost importance of memories that failed self-test recall.

    The boost is proportional to failure severity and always clamped to
    [0.0, 1.0] by the memory manager. This class is stateless — all state
    lives in the memory manager's SQLite database.
    """

    @staticmethod
    def _extract_failures(self_test_results: dict) -> list[tuple[str, float]]:
        """Extract (memory_id, severity) pairs from a self-test results dict.

        Handles both the explicit ``failures`` list shape and the flat
        ``results`` list shape. Unknown shapes produce an empty list.

        Args:
            self_test_results: The results dict produced by the self-test engine.

        Returns:
            List of (memory_id, severity) tuples for every recall failure.
        """
        failures: list[tuple[str, float]] = []

        # Shape A: explicit "failures" key with per-entry severity.
        raw_failures = self_test_results.get("failures")
        if isinstance(raw_failures, list):
            for entry in raw_failures:
                if isinstance(entry, dict):
                    mid = entry.get("memory_id")
                    if mid is None:
                        continue
                    severity = entry.get("severity", DEFAULT_SEVERITY)
                    failures.append((str(mid), float(severity)))
                elif isinstance(entry, str):
                    failures.append((entry, DEFAULT_SEVERITY))

        # Shape B: flat "results" list with per-item verdicts.
        raw_results = self_test_results.get("results")
        if isinstance(raw_results, list):
            for entry in raw_results:
                if not isinstance(entry, dict):
                    continue
                verdict = str(entry.get("verdict", "")).lower()
                if verdict in FAILURE_VERDICTS:
                    mid = entry.get("memory_id")
                    if mid is None:
                        continue
                    severity = entry.get("severity", DEFAULT_SEVERITY)
                    failures.append((str(mid), float(severity)))

        return failures

    def adjust(self, memory_manager: MemoryManager, self_test_results: dict) -> int:
        """Boost importance of every memory that failed recall.

        For each failure, the importance delta is ``BASE_BOOST * severity``,
        clamped to [0.0, 1.0] by the memory manager. Memories that no longer
        exist are logged and skipped (they are not counted as adjusted).

        Args:
            memory_manager: The memory manager holding the memories to adjust.
            self_test_results: Results dict from the self-test engine. See
                module docstring for the expected shapes.

        Returns:
            The number of memories whose importance was successfully boosted.
        """
        failures = self._extract_failures(self_test_results)
        if not failures:
            logger.debug("AdaptiveImportance: no recall failures to process.")
            return 0

        adjusted = 0
        for memory_id, severity in failures:
            # Clamp severity to a sane range before computing the boost.
            clamped_severity = max(0.0, min(1.0, severity))
            boost = BASE_BOOST * clamped_severity
            if boost <= 0.0:
                logger.debug(
                    "AdaptiveImportance: skipping %s — zero boost (severity=%.3f).",
                    memory_id,
                    clamped_severity,
                )
                continue

            success = memory_manager.update_importance(memory_id, boost)
            if success:
                adjusted += 1
                logger.info(
                    "AdaptiveImportance: boosted %s by +%.4f (severity=%.3f).",
                    memory_id,
                    boost,
                    clamped_severity,
                )
            else:
                logger.warning(
                    "AdaptiveImportance: memory %s not found — skipped.",
                    memory_id,
                )

        logger.info(
            "AdaptiveImportance: adjusted %d / %d failed memories.",
            adjusted,
            len(failures),
        )
        return adjusted
