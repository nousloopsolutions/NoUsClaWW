"""Memory tiers — STM/MTM/LTM lifecycle, decay, and compaction.

Implements the Phase 6 memory-quality model over the MemoryManager store:

    Tiers
        - STM (short-term): new or rarely-accessed memories.
        - MTM (mid-term): memories accessed at least PROMOTE_TO_MTM times.
        - LTM (long-term): heavily accessed, high-importance memories.

    Decay
        Importance erodes linearly with idle time (days since last access)
        at DECAY_RATE_PER_DAY, floored at zero. Memories with access_count
        >= LTM_ACCESS_THRESHOLD are exempt (consolidated knowledge resists
        forgetting).

    Compaction
        Stale memories whose decayed importance falls below a threshold are
        removed. This is the storage-side of forgetting.

All operations are deterministic and local (SQLite only).

SYNTH:
    purpose: Classify stored memories into STM/MTM/LTM tiers, apply idle-time importance decay, promote on access, and compact forgotten entries.
    axioms: [local_first, scientific_method, evidence_over_intuition, reversibility_awareness]
    objective: Tier assignment, decay, promotion, and compaction behave deterministically from stored timestamps and counts, never producing out-of-range scores.
    anti_patterns:
        - Never delete a memory whose importance is at or above the threshold
        - Never let decay produce negative or >1.0 scores
        - Never decay consolidated (LTM) knowledge
        - Never fabricate timestamps — always derive from the store

#C Tier model per NoUsClaWW Phase 6 roadmap; decay via linear erosion with LTM exemption.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── Tunables (documented constants, not magic numbers) ────────────────────

#: Accesses required to leave short-term memory.
PROMOTE_TO_MTM = 5
#: Accesses required to consolidate into long-term memory.
LTM_ACCESS_THRESHOLD = 20
#: Importance required (in addition to accesses) for LTM consolidation.
LTM_IMPORTANCE_FLOOR = 0.8
#: Importance lost per idle day (0.01/day ≈ fully-faded after 100 idle days).
DECAY_RATE_PER_DAY = 0.01
#: Minimum age in days before a low-scoring memory is eligible for removal.
STALE_DAYS = 90


@dataclass(frozen=True)
class TierRules:
    """Parameters governing tier classification, decay, and compaction."""

    promote_to_mtm: int = PROMOTE_TO_MTM
    ltm_access_threshold: int = LTM_ACCESS_THRESHOLD
    ltm_importance_floor: float = LTM_IMPORTANCE_FLOOR
    decay_rate_per_day: float = DECAY_RATE_PER_DAY
    stale_days: int = STALE_DAYS


class MemoryTiers:
    """Tier lifecycle engine operating directly on a MemoryManager DB."""

    def __init__(self, db_path: str, rules: TierRules | None = None) -> None:
        """Bind the engine to a memory database.

        Args:
            db_path: Path to the SQLite database used by MemoryManager.
            rules: Optional rule overrides.
        """
        self.db_path = db_path
        self.rules = rules or TierRules()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Classification ────────────────────────────────────────────────────

    def classify(
        self, access_count: int, importance: float, created_at: float,
        now: float,
    ) -> str:
        """Return the tier for one memory's stored statistics.

        Args:
            access_count: Number of times the memory was recalled.
            importance: Current importance score [0.0, 1.0].
            created_at: Creation timestamp (epoch seconds).
            now: Reference "current" timestamp (epoch seconds).

        Returns:
            One of ``"STM"``, ``"MTM"``, ``"LTM"``.
        """
        r = self.rules
        age_days = max(0.0, (now - created_at)) / 86400.0
        if (
            access_count >= r.ltm_access_threshold
            and importance >= r.ltm_importance_floor
            and age_days >= 7.0
        ):
            return "LTM"
        if access_count >= r.promote_to_mtm:
            return "MTM"
        return "STM"

    def get_tiers(self, now_offset_days: float = 0.0) -> dict[str, dict[str, Any]]:
        """Return tier info for every stored memory.

        Args:
            now_offset_days: Shift the reference clock forward by this many
                days (for testing decay/promotion without sleeping).

        Returns:
            Mapping of memory_id → {tier, importance, content, access_count}.
        """
        now = time.time() + now_offset_days * 86400.0
        result: dict[str, dict[str, Any]] = {}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT memory_id, content, importance, access_count, "
                "created_at FROM memories"
            ).fetchall()
        for row in rows:
            result[row["memory_id"]] = {
                "tier": self.classify(
                    row["access_count"], row["importance"],
                    row["created_at"], now,
                ),
                "importance": float(row["importance"]),
                "content": row["content"],
                "access_count": int(row["access_count"]),
            }
        return result

    # ── Decay ─────────────────────────────────────────────────────────────

    def apply_decay(self, now_offset_days: float = 0.0) -> int:
        """Erode importance of idle memories; returns count decayed.

        Importance decreases by ``decay_rate_per_day`` per idle day since
        last access (or creation), clamped to [0.0, 1.0]. Memories whose
        access_count reaches the LTM threshold are exempt.
        """
        r = self.rules
        now = time.time() + now_offset_days * 86400.0
        decayed = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT memory_id, importance, access_count, created_at, "
                "last_accessed FROM memories"
            ).fetchall()
            for row in rows:
                if row["access_count"] >= r.ltm_access_threshold:
                    continue  # consolidated knowledge resists decay
                reference = row["last_accessed"] or row["created_at"]
                idle_days = max(0.0, (now - reference) / 86400.0)
                if idle_days <= 0:
                    continue
                new_imp = max(
                    0.0, min(1.0, row["importance"] - r.decay_rate_per_day * idle_days)
                )
                if new_imp != row["importance"]:
                    conn.execute(
                        "UPDATE memories SET importance = ? WHERE memory_id = ?",
                        (new_imp, row["memory_id"]),
                    )
                    decayed += 1
        logger.info("Memory decay applied to %d memories", decayed)
        return decayed

    # ── Compaction ────────────────────────────────────────────────────────

    def compact(
        self, min_importance: float, now_offset_days: float = 0.0,
    ) -> dict[str, int]:
        """Remove stale memories whose importance fell below *min_importance*.

        A memory is removed only when BOTH conditions hold:
          - its current importance is below ``min_importance``, and
          - it has been idle longer than ``stale_days``.

        Args:
            min_importance: Survival threshold for importance.
            now_offset_days: Shift the reference clock forward (testing).

        Returns:
            Report dict: {"compacted": <n>, "survived": <m>}.
        """
        r = self.rules
        now = time.time() + now_offset_days * 86400.0
        removed = 0
        survived = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT memory_id, importance, access_count, created_at, "
                "last_accessed FROM memories"
            ).fetchall()
            for row in rows:
                reference = row["last_accessed"] or row["created_at"]
                idle_days = max(0.0, (now - reference) / 86400.0)
                if (
                    row["importance"] < min_importance
                    and idle_days >= r.stale_days
                    and row["access_count"] < r.ltm_access_threshold
                ):
                    conn.execute(
                        "DELETE FROM memories WHERE memory_id = ?",
                        (row["memory_id"],),
                    )
                    removed += 1
                else:
                    survived += 1
        logger.info("Compaction removed %d memories (%d survived)", removed, survived)
        return {"compacted": removed, "survived": survived}
