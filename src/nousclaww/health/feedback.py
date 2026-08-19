"""Recall feedback engine — real-user feedback drives importance adjustment.

When a user interacts with a recalled memory, they can verdict it as
``useful``, ``wrong``, or ``irrelevant``. This module stores that feedback
via the memory manager and then applies pending feedback to adjust the
importance of the affected memories:

- **useful** — boost importance (the memory helped).
- **wrong** — sharply reduce importance (the memory is incorrect).
- **irrelevant** — mildly reduce importance (the memory is not useful here).

Feedback is applied in two stages so that the record/apply split is
auditable: ``record()`` stores the verdict without changing importance,
and ``apply()`` processes all pending verdicts in a single batch. This
makes it possible to review what happened before it affects the memory
store.

SYNTH:
    purpose: Real-user feedback drives importance adjustment for recalled memories
    axioms: [evidence_over_intuition, iteration_is_progress, reversibility_awareness, open_process]
    objective: Every user verdict on a recalled memory produces a proportional importance delta,
        so memories that users find useful rise and memories that users find wrong or irrelevant fall
    anti_patterns:
        - Applying feedback immediately at record time without an explicit apply step
        - Using the same delta for "wrong" and "irrelevant" — they are different signals
        - Silently dropping feedback for missing memories instead of logging
        - Allowing importance to escape the [0.0, 1.0] range
        - Applying the same feedback entry twice
#C Inspired by PMB (Project Memory Bank) sleep engine
"""

from __future__ import annotations

import logging
from typing import Any

from nousclaww.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# Valid verdict strings.
VALID_VERDICTS: frozenset[str] = frozenset({"useful", "wrong", "irrelevant"})

# Importance deltas per verdict. "wrong" is a sharp penalty because an
# incorrect memory is actively harmful. "irrelevant" is a mild penalty
# because the memory may still be correct, just not useful in this context.
DELTAS: dict[str, float] = {
    "useful": +0.10,
    "wrong": -0.25,
    "irrelevant": -0.05,
}


class RecallFeedback:
    """Store and apply user feedback on recalled memories.

    This class is stateless — all data lives in the memory manager's SQLite
    database. The record/apply split ensures feedback is auditable before
    it affects importance.
    """

    def record(self, memory_id: str, verdict: str, memory_manager: MemoryManager) -> bool:
        """Store a user feedback verdict for a memory.

        The verdict is stored but not yet applied — call ``apply()`` to
        process pending feedback and adjust importance.

        Args:
            memory_id: The ID of the memory being verdicted.
            verdict: One of "useful", "wrong", "irrelevant".
            memory_manager: The memory manager to store feedback in.

        Returns:
            True if the memory exists and the verdict was valid and stored.
            False if the verdict is invalid or the memory does not exist.
        """
        if verdict not in VALID_VERDICTS:
            logger.warning(
                "RecallFeedback: invalid verdict '%s' for memory %s — rejected.",
                verdict,
                memory_id,
            )
            return False

        success = memory_manager.record_feedback(memory_id, verdict)
        if success:
            logger.info(
                "RecallFeedback: recorded verdict '%s' for memory %s.",
                verdict,
                memory_id,
            )
        else:
            logger.warning(
                "RecallFeedback: could not record verdict for memory %s "
                "(memory not found).",
                memory_id,
            )
        return success

    def apply(self, memory_manager: MemoryManager) -> int:
        """Apply all pending feedback to adjust memory importance.

        For each pending feedback entry, the importance delta from
        ``DELTAS`` is applied via ``memory_manager.update_importance``.
        Successfully applied entries are marked as applied so they are not
        processed again.

        Args:
            memory_manager: The memory manager holding feedback and memories.

        Returns:
            The number of feedback entries successfully applied (importance
            updated and entry marked as applied).
        """
        pending = memory_manager.get_pending_feedback()
        if not pending:
            logger.debug("RecallFeedback: no pending feedback to apply.")
            return 0

        applied_ids: list[str] = []
        applied_count = 0

        for entry in pending:
            feedback_id: str = entry["feedback_id"]
            memory_id: str = entry["memory_id"]
            verdict: str = entry["verdict"]

            delta = DELTAS.get(verdict)
            if delta is None:
                # This should not happen if record() validated the verdict,
                # but we guard against manually inserted bad data.
                logger.warning(
                    "RecallFeedback: unknown verdict '%s' in feedback %s — skipping.",
                    verdict,
                    feedback_id,
                )
                continue

            success = memory_manager.update_importance(memory_id, delta)
            if success:
                applied_count += 1
                applied_ids.append(feedback_id)
                logger.info(
                    "RecallFeedback: applied %+.2f to memory %s (verdict='%s').",
                    delta,
                    memory_id,
                    verdict,
                )
            else:
                # The memory may have been deleted between record and apply.
                # We still mark the feedback as applied so we don't retry
                # forever on a missing memory.
                applied_ids.append(feedback_id)
                logger.warning(
                    "RecallFeedback: memory %s not found for feedback %s — "
                    "marking applied to prevent retry.",
                    memory_id,
                    feedback_id,
                )

        # Mark all processed entries as applied in a single batch.
        if applied_ids:
            memory_manager.mark_feedback_applied(applied_ids)

        logger.info(
            "RecallFeedback: applied %d / %d pending feedback entries.",
            applied_count,
            len(pending),
        )
        return applied_count
