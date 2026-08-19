"""Conflict detection — finds contradictions between memories and suggests resolutions.

SYNTH:
    purpose: Detect when stored facts contradict each other (X=Y vs X=Z) and suggest deterministic resolutions (supersede, concurrent, or needs_review).
    axioms: [local_first, open_process, evidence_over_intuition, epistemic_boundary, honest_failure_over_fake_success, scientific_method]
    objective: Every pair of contradictory facts is detected, reported with evidence, and given a resolution suggestion — no contradiction goes unnoticed.
    anti_patterns:
        - Fabricating contradictions — only real value mismatches for the same subject count.
        - Silently resolving conflicts without evidence — every suggestion must be justified.
        - Ignoring temporal ordering — newer facts may supersede older ones.
        - Treating all contradictions as equal — some are concurrent truths, some are supersessions, some need human review.
        - Using an LLM to detect conflicts — deterministic pattern matching only.

Detects contradictions in the memory store by comparing fact values for the same
subject. When two facts about the same subject have different values, a conflict
is recorded. The resolution suggestion is deterministic: if one fact is newer and
more important, it supersedes the older one; if both are recent and equally
important, they may be concurrent truths; otherwise, human review is needed.

#C Inspired by PMB (Project Memory Bank) sleep engine
"""

# ┌─ synth ──────────────────────────────────────────────────────────────────┐
# @NCL{v=1.0;agent=builder;mod=conflicts;ts=2026-08-18Z;tier=L3}
# #C Inspired by PMB (Project Memory Bank) sleep engine
# #S{purpose="Detect contradictory facts (X=Y vs X=Z) in memory and suggest deterministic resolutions — supersede, concurrent, or needs_review"}
# #I{1="deterministic conflict detection — compares fact values for the same subject";2="three resolution types: supersede (newer replaces older), concurrent (both true), needs_review (human must decide)";3="temporal awareness — uses creation time and importance to determine resolution";4="evidence-based — every conflict includes the conflicting memory IDs and values"]
# #D{1="conflict"→="two or more facts with the same subject but different extracted values";2="supersede"→="the newer, higher-importance fact replaces the older one";3="concurrent"→="both facts may be simultaneously true (e.g. different contexts)";4="needs_review"→="evidence is ambiguous, human must decide"]
# #M{status=IMPLEMENTED;version=1.0.0;deps="nousclaww.memory.memory_manager"]
# #T{pass=0;fail=0;xfail=0}
# #W{1="value extraction is heuristic — facts without a clear 'subject=value' pattern may not be detected";2="concurrent resolution is conservative — only suggested when both facts are recent and important";3="needs_review is the safe fallback when evidence is ambiguous"]
# #L{lexicon→docs/NOUS_LEXICON.md}
# └──────────────────────────────────────────────────────────────────────────┘

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nousclaww.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# ── Resolution type constants ────────────────────────────────────────────────

SUPERSEDE = "supersede"
CONCURRENT = "concurrent"
NEEDS_REVIEW = "needs_review"

# ── Value extraction patterns ────────────────────────────────────────────────

# Matches "subject is value", "subject = value", "subject: value", "subject -> value"
# Captures the value part after the separator.
_VALUE_PATTERNS: list[re.Pattern[str]] = [
    # "X is Y" / "X was Y" / "X are Y" / "X has Y" / "X have Y"
    re.compile(
        r"(?:is|was|are|were|has|have|had)\s+(.+?)(?:\.|$)",
        re.IGNORECASE,
    ),
    # "X = Y" / "X := Y"
    re.compile(
        r"(?:=|:=)\s*(.+?)(?:\.|$)",
    ),
    # "X: Y" (colon separator)
    re.compile(
        r":\s*(.+?)(?:\.|$)",
    ),
    # "X -> Y" / "X => Y"
    re.compile(
        r"(?:->|=>)\s*(.+?)(?:\.|$)",
    ),
]

# Minimum importance difference to consider one fact clearly more authoritative
_IMPORTANCE_DIFF_THRESHOLD = 0.15

# Minimum age difference in seconds to consider one fact clearly newer
_AGE_DIFF_THRESHOLD_SECONDS = 3600  # 1 hour


class ConflictDetector:
    """Detects contradictions in the memory store and suggests resolutions.

    Scans all fact-type memories grouped by subject. When two or more
    facts about the same subject have different extracted values, a
    conflict is recorded. The resolution suggestion is deterministic:

    - **supersede**: One fact is newer AND more important than the
      other. The newer, higher-importance fact should replace the older
      one.
    - **concurrent**: Both facts are recent (within the age threshold of
      each other) and have similar importance. They may be
      simultaneously true (e.g. valid in different contexts).
    - **needs_review**: The evidence is ambiguous — similar importance,
      unclear temporal ordering, or more than two conflicting values.
      Human review is required.

    Usage:
        detector = ConflictDetector()
        conflicts = detector.detect(memory_manager)
        for c in conflicts:
            print(c["subject"], c["resolution"], c["values"])
            # -> "auth_method", "supersede", ["JWT", "session cookies"]
    """

    def __init__(self) -> None:
        """Initialize the ConflictDetector."""
        self._value_patterns = _VALUE_PATTERNS
        self._importance_diff_threshold = _IMPORTANCE_DIFF_THRESHOLD
        self._age_diff_threshold = _AGE_DIFF_THRESHOLD_SECONDS

    def detect(self, memory_manager: "MemoryManager") -> list[dict[str, Any]]:
        """Detect all contradictions in the memory store.

        Groups all fact-type memories by their subject field, then
        compares the extracted values within each group. When values
        differ, a conflict dict is created with the subject, the
        conflicting memories, their values, and a resolution suggestion.

        Args:
            memory_manager: The MemoryManager instance to scan.

        Returns:
            A list of conflict dicts, each with keys:
            - ``subject``: The subject of the conflicting facts.
            - ``memories``: List of conflicting memory dicts.
            - ``values``: List of extracted values (one per memory).
            - ``resolution``: The suggested resolution (supersede,
              concurrent, or needs_review).
            - ``rationale``: Human-readable explanation of the
              suggestion.
            - ``conflict_id``: A unique identifier for this conflict.

            Returns an empty list if no contradictions are found.
        """
        try:
            subject_groups = memory_manager.get_facts_by_subject()
        except Exception as e:
            logger.warning(f"ConflictDetector: failed to get facts by subject: {e}")
            return []

        conflicts: list[dict[str, Any]] = []

        for subject, facts in subject_groups.items():
            if len(facts) < 2:
                continue

            # Extract values from each fact
            fact_values: list[tuple[dict[str, Any], str | None]] = []
            for fact in facts:
                value = self._extract_value(fact)
                fact_values.append((fact, value))

            # Filter out facts where no value could be extracted
            with_values = [
                (f, v) for f, v in fact_values if v is not None
            ]
            if len(with_values) < 2:
                continue

            # Group by unique values
            value_groups: dict[str, list[tuple[dict[str, Any], str]]] = {}
            for fact, value in with_values:
                normalized = self._normalize_value(value)
                value_groups.setdefault(normalized, []).append((fact, value))

            # If all facts have the same value, no conflict
            if len(value_groups) <= 1:
                continue

            # We have a conflict — different values for the same subject
            conflict = self._build_conflict(subject, with_values, value_groups)
            conflicts.append(conflict)

        logger.info(
            f"ConflictDetector: found {len(conflicts)} conflicts "
            f"across {len(subject_groups)} subjects"
        )
        return conflicts

    def suggest_resolution(self, conflict: dict[str, Any]) -> str:
        """Suggest a resolution for a conflict.

        Determines the resolution type deterministically based on the
        conflicting memories' importance scores and creation times:

        1. If one memory is clearly newer (by more than the age
           threshold) AND more important (by more than the importance
           threshold), suggest SUPERSEDE.
        2. If all memories are within the age threshold of each other
           AND have similar importance (within the importance threshold),
           suggest CONCURRENT.
        3. Otherwise, suggest NEEDS_REVIEW.

        Args:
            conflict: A conflict dict as returned by ``detect()``.

        Returns:
            One of ``SUPERSEDE``, ``CONCURRENT``, or ``NEEDS_REVIEW``.
        """
        memories = conflict.get("memories", [])
        if len(memories) < 2:
            return NEEDS_REVIEW

        # More than two distinct values — always needs review
        values = conflict.get("values", [])
        unique_values = set(self._normalize_value(v) for v in values if v)
        if len(unique_values) > 2:
            return NEEDS_REVIEW

        # Extract importance and creation time for each memory
        importance_times = []
        for mem in memories:
            importance = mem.get("importance", 0.5)
            created_at = mem.get("created_at", 0.0)
            importance_times.append((importance, created_at))

        if len(importance_times) < 2:
            return NEEDS_REVIEW

        # Sort by creation time (oldest first)
        importance_times.sort(key=lambda x: x[1])

        oldest_imp, oldest_time = importance_times[0]
        newest_imp, newest_time = importance_times[-1]

        age_diff = newest_time - oldest_time
        importance_diff = abs(newest_imp - oldest_imp)

        # Check for supersede: newer is more important by a clear margin
        if (
            age_diff > self._age_diff_threshold
            and newest_imp > oldest_imp
            and importance_diff >= self._importance_diff_threshold
        ):
            return SUPERSEDE

        # Check for concurrent: similar age and similar importance
        if (
            age_diff <= self._age_diff_threshold
            and importance_diff < self._importance_diff_threshold
        ):
            return CONCURRENT

        # Ambiguous — needs human review
        return NEEDS_REVIEW

    # ── Internal helpers ──────────────────────────────────────────────────

    def _extract_value(self, fact: dict[str, Any]) -> str | None:
        """Extract the value from a fact memory.

        Attempts to extract the value from the fact's content using
        pattern matching. The content is expected to be in a form like
        "subject is value" or "subject = value".

        Args:
            fact: The fact memory dict.

        Returns:
            The extracted value string, or None if no value could be
            extracted.
        """
        content = fact.get("content", "")
        if not content:
            return None

        # Try each value pattern
        for pattern in self._value_patterns:
            match = pattern.search(content)
            if match:
                value = match.group(1).strip()
                # Clean up trailing punctuation
                value = value.rstrip(".,;:!?")
                if value:
                    return value

        # If no pattern matched, try using the metadata
        metadata = fact.get("metadata", {})
        if isinstance(metadata, dict):
            for key in ("value", "fact_value", "data"):
                if key in metadata and isinstance(metadata[key], str):
                    return metadata[key].strip()

        return None

    def _normalize_value(self, value: str) -> str:
        """Normalize a value for comparison.

        Converts to lowercase, strips whitespace, and collapses internal
        whitespace. This ensures that "JWT" and "jwt " are treated as
        the same value.

        Args:
            value: The value to normalize.

        Returns:
            The normalized value string.
        """
        cleaned = value.strip().lower()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned

    def _build_conflict(
        self,
        subject: str,
        with_values: list[tuple[dict[str, Any], str | None]],
        value_groups: dict[str, list[tuple[dict[str, Any], str]]],
    ) -> dict[str, Any]:
        """Build a conflict dict from the detected contradiction.

        Args:
            subject: The subject of the conflicting facts.
            with_values: List of (fact, value) tuples for all facts
                with extractable values.
            value_groups: Dict mapping normalized values to lists of
                (fact, original_value) tuples.

        Returns:
            A conflict dict with all details and a resolution
            suggestion.
        """
        memories = [f for f, _ in with_values]
        values = [v for _, v in with_values if v is not None]

        # Build a summary of the distinct values and their supporting facts
        distinct_values: list[dict[str, Any]] = []
        for norm_value, fact_list in value_groups.items():
            distinct_values.append({
                "value": fact_list[0][1],  # Use the original (non-normalized) value
                "normalized": norm_value,
                "supporting_memory_ids": [
                    f.get("memory_id", "") for f, _ in fact_list
                ],
                "supporting_count": len(fact_list),
                "max_importance": max(
                    f.get("importance", 0.5) for f, _ in fact_list
                ),
                "newest_created_at": max(
                    f.get("created_at", 0.0) for f, _ in fact_list
                ),
            })

        # Sort distinct values by newest first (most recent evidence)
        distinct_values.sort(key=lambda d: d["newest_created_at"], reverse=True)

        conflict_id = f"conflict-{subject}-{len(with_values)}"

        conflict: dict[str, Any] = {
            "conflict_id": conflict_id,
            "subject": subject,
            "memories": memories,
            "values": values,
            "distinct_values": distinct_values,
            "resolution": "",  # Filled in below
            "rationale": "",
        }

        # Determine the resolution
        resolution = self.suggest_resolution(conflict)
        conflict["resolution"] = resolution
        conflict["rationale"] = self._build_rationale(
            resolution, subject, distinct_values
        )

        return conflict

    def _build_rationale(
        self,
        resolution: str,
        subject: str,
        distinct_values: list[dict[str, Any]],
    ) -> str:
        """Build a human-readable rationale for a resolution suggestion.

        Args:
            resolution: The resolution type.
            subject: The subject of the conflict.
            distinct_values: The distinct values in the conflict.

        Returns:
            A rationale string explaining the suggestion.
        """
        if resolution == SUPERSEDE:
            newest = distinct_values[0]
            oldest = distinct_values[-1]
            return (
                f"SUPERSEDE: The newest fact for '{subject}' "
                f"(value: '{newest['value']}', importance: "
                f"{newest['max_importance']:.2f}) is more recent and "
                f"more important than the oldest "
                f"(value: '{oldest['value']}', importance: "
                f"{oldest['max_importance']:.2f}). The newer fact "
                f"should replace the older one."
            )
        elif resolution == CONCURRENT:
            values_str = ", ".join(
                f"'{d['value']}'" for d in distinct_values
            )
            return (
                f"CONCURRENT: The facts for '{subject}' ({values_str}) "
                f"are similarly recent and similarly important. They "
                f"may be simultaneously true in different contexts. "
                f"Both should be retained."
            )
        else:
            values_str = ", ".join(
                f"'{d['value']}'" for d in distinct_values
            )
            return (
                f"NEEDS_REVIEW: The facts for '{subject}' ({values_str}) "
                f"have conflicting values with ambiguous temporal or "
                f"importance ordering. Human review is required to "
                f"determine the correct resolution."
            )
