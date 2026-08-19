"""Rehearsal engine — prevent decay of important memories via spaced repetition.

Important memories that haven't been accessed recently are at risk of
being "forgotten" by the retrieval system (which ranks by importance and
access recency). The rehearser periodically finds these idle-but-important
memories and re-activates them by bumping their importance and access
count, simulating the cognitive process of rehearsal that keeps critical
knowledge fresh.

This implements a spaced-repetition strategy:
    - Memories with high importance that haven't been accessed in N days
      are candidates for rehearsal.
    - Rehearsal bumps access_count and gives a small importance boost
      (preventing decay without unbounded growth).
    - The importance boost decays with each rehearsal (diminishing
      returns) to prevent runaway importance inflation.

Contract:
    - Only memories above min_importance are rehearsed.
    - Only memories idle for >= days_idle are rehearsed.
    - Importance is clamped to [0.0, 1.0] after each bump.
    - The access_count is always incremented by exactly 1 per rehearsal.
    - Returns the number of memories rehearsed.

SYNTH:
    purpose: Prevent decay of important memories by re-activating idle high-importance memories via spaced repetition
    axioms: [evidence_over_intuition, iteration_is_progress, reversibility_awareness, completion_assumption, local_first]
    objective: Keep critical memories from fading due to access-recency ranking by periodically rehearsing them
    anti_patterns:
        - Rehearsing low-importance memories (wastes effort, inflates noise)
        - Bumping importance without bounds (runaway scores)
        - Rehearsing memories that were accessed recently (unnecessary)
        - Incrementing access_count by more than 1 per rehearsal cycle
        - Rehearsing without checking that the memory still exists

#C Inspired by PMB (Project Memory Bank) sleep engine
"""

from __future__ import annotations

import logging
import time
from typing import Any

from nousclaww.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class Rehearser:
    """Rehearse idle but important memories to prevent decay.

    Usage:
        rehearser = Rehearser()
        candidates = rehearser.find_idle_important(memory_manager, days_idle=7)
        n = rehearser.rehearse(memory_manager)
        print(f"Rehearsed {n} memories")
    """

    # Importance boost per rehearsal. This is the maximum boost applied
    # on the first rehearsal; subsequent rehearsals get diminishing boosts.
    BASE_IMPORTANCE_BOOST = 0.05

    # Minimum importance boost (floor for the diminishing-returns curve).
    MIN_IMPORTANCE_BOOST = 0.01

    # Decay factor applied to the boost for each prior rehearsal.
    # boost = BASE * (DECAY ^ prior_rehearsal_count)
    BOOST_DECAY_FACTOR = 0.7

    def __init__(
        self,
        importance_boost: float = BASE_IMPORTANCE_BOOST,
        boost_decay: float = BOOST_DECAY_FACTOR,
        min_boost: float = MIN_IMPORTANCE_BOOST,
        max_importance: float = 1.0,
    ) -> None:
        """Initialize the rehearser.

        Args:
            importance_boost: Base importance boost applied on first
                rehearsal of a memory. Default 0.05.
            boost_decay: Multiplicative decay factor for the boost on
                subsequent rehearsals. Default 0.7 (each rehearsal adds
                70% of the previous boost).
            min_boost: Floor for the importance boost after decay.
                Default 0.01.
            max_importance: Maximum importance after boosting. Default 1.0.
        """
        self.importance_boost = float(importance_boost)
        self.boost_decay = float(boost_decay)
        self.min_boost = float(min_boost)
        self.max_importance = float(max_importance)

    # ── Public API ─────────────────────────────────────────────────────────

    def find_idle_important(
        self,
        memory_manager: MemoryManager,
        days_idle: int = 7,
        min_importance: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Find memories that are important but haven't been accessed recently.

        This is a thin wrapper around memory_manager.get_idle_important()
        that exists to provide a clear rehearsal-specific interface and
        allow future filtering logic (e.g., excluding memories already
        rehearsed recently).

        Args:
            memory_manager: The MemoryManager to search.
            days_idle: Minimum days since last access to consider a
                memory idle. Default 7.
            min_importance: Minimum importance threshold. Default 0.5.

        Returns:
            List of memory dicts meeting both criteria, ordered by
            importance (highest first).
        """
        candidates = memory_manager.get_idle_important(
            days_idle=days_idle,
            min_importance=min_importance,
        )
        logger.info(
            f"Rehearser: found {len(candidates)} idle important memories "
            f"(idle >= {days_idle}d, importance >= {min_importance})"
        )
        return candidates

    def rehearse(self, memory_manager: MemoryManager) -> int:
        """Re-activate idle important memories by bumping importance and access count.

        Finds memories idle for >= 7 days with importance >= 0.5 (default
        thresholds), then for each:
            1. Increments access_count by 1 and updates last_accessed.
            2. Applies a diminishing-returns importance boost based on
               how many times the memory has been accessed previously.

        Args:
            memory_manager: The MemoryManager to rehearse memories in.

        Returns:
            The number of memories successfully rehearsed.
        """
        candidates = self.find_idle_important(memory_manager)

        if not candidates:
            logger.info("Rehearser: no memories to rehearse")
            return 0

        rehearsed = 0
        for memory in candidates:
            memory_id = memory.get("memory_id")
            if not memory_id:
                logger.warning(
                    f"Rehearser: memory missing memory_id, skipping: {memory}"
                )
                continue

            # Calculate the importance boost with diminishing returns.
            # The more a memory has been accessed (rehearsed), the smaller
            # the boost — preventing runaway importance inflation.
            current_access_count = memory.get("access_count", 0)
            boost = self._calculate_boost(current_access_count)

            # Bump access count (updates last_accessed to now)
            try:
                bumped = memory_manager.bump_access(memory_id)
            except Exception as exc:
                logger.warning(
                    f"Rehearser: failed to bump access for {memory_id}: {exc}"
                )
                continue

            if not bumped:
                logger.warning(
                    f"Rehearser: memory {memory_id} not found during bump, "
                    "may have been deleted"
                )
                continue

            # Apply importance boost
            if boost > 0:
                try:
                    memory_manager.update_importance(memory_id, boost)
                except Exception as exc:
                    logger.warning(
                        f"Rehearser: failed to update importance for "
                        f"{memory_id}: {exc}"
                    )
                    # Access was still bumped, so count this as rehearsed

            rehearsed += 1

        logger.info(f"Rehearser: rehearsed {rehearsed} memories")
        return rehearsed

    # ── Internal helpers ───────────────────────────────────────────────────

    def _calculate_boost(self, prior_access_count: int) -> float:
        """Calculate the importance boost with diminishing returns.

        The boost decays exponentially with the number of prior accesses
        (rehearsals), floor at min_boost::

            boost = max(min_boost, base_boost * decay^prior_access_count)

        Args:
            prior_access_count: The current access_count of the memory
                before this rehearsal.

        Returns:
            The importance boost to apply (always positive, <= base_boost).
        """
        if prior_access_count < 0:
            prior_access_count = 0

        # Exponential decay: each prior access reduces the boost
        decayed = self.importance_boost * (
            self.boost_decay ** prior_access_count
        )
        return max(self.min_boost, min(self.importance_boost, decayed))

    # ── Diagnostics ─────────────────────────────────────────────────────────

    def get_rehearsal_summary(
        self,
        memory_manager: MemoryManager,
        days_idle: int = 7,
        min_importance: float = 0.5,
    ) -> dict[str, Any]:
        """Get a summary of what would be rehearsed without actually rehearsing.

        Useful for dry-run diagnostics and reporting.

        Args:
            memory_manager: The MemoryManager to inspect.
            days_idle: Minimum days since last access.
            min_importance: Minimum importance threshold.

        Returns:
            A dict with:
                - 'candidate_count': number of memories that would be rehearsed
                - 'candidates': list of memory dicts (id, content, importance,
                  access_count, last_accessed)
                - 'total_boost': sum of importance boosts that would be applied
                - 'avg_importance': average importance of candidates
        """
        candidates = self.find_idle_important(
            memory_manager, days_idle=days_idle, min_importance=min_importance,
        )

        total_boost = 0.0
        summary_candidates: list[dict[str, Any]] = []
        for mem in candidates:
            boost = self._calculate_boost(mem.get("access_count", 0))
            total_boost += boost
            summary_candidates.append({
                "memory_id": mem.get("memory_id"),
                "content": mem.get("content", "")[:100],
                "importance": mem.get("importance", 0.0),
                "access_count": mem.get("access_count", 0),
                "last_accessed": mem.get("last_accessed"),
                "planned_boost": boost,
            })

        avg_importance = (
            sum(m.get("importance", 0.0) for m in candidates) / len(candidates)
            if candidates else 0.0
        )

        return {
            "candidate_count": len(candidates),
            "candidates": summary_candidates,
            "total_boost": total_boost,
            "avg_importance": avg_importance,
        }
