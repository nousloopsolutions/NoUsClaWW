"""Auto-consolidation trigger — sleep cycle automation for memory.

Monitors the event queue and automatically triggers consolidation when
either an event-count threshold or a time-since-last-consolidation
threshold is exceeded. This is the "sleep cycle" of the memory system:
the agent periodically reviews accumulated events and distills them into
durable facts without requiring explicit user invocation.

Contract:
    - Triggers on EITHER event count OR time threshold (whichever fires
      first). Both are configurable.
    - Never consolidates an empty event queue.
    - Logs every consolidation run via memory_manager.log_consolidation().
    - Returns the number of events actually consolidated (0 if nothing
      to do).
    - Honors a max_events_per_cycle limit to avoid unbounded LLM calls
      in a single run.

SYNTH:
    purpose: Automatically trigger memory consolidation when event count or time thresholds are exceeded, like sleep cycles
    axioms: [local_first, evidence_over_intuition, iteration_is_progress, completion_assumption, honest_failure_over_fake_success]
    objective: Keep the event queue from growing unbounded by periodically consolidating events into durable facts without manual intervention
    anti_patterns:
        - Triggering consolidation when there are no unconsolidated events
        - Running consolidation on every single event (no batching)
        - Skipping the consolidation log entry
        - Processing more events than max_events_per_cycle in one run
        - Silently swallowing LLM errors without reporting them

#C Inspired by PMB (Project Memory Bank) sleep engine
"""

from __future__ import annotations

import logging
import time
from typing import Any

from nousclaww.memory.memory_manager import MemoryManager
from nousclaww.llm_router import LLMRouter
from nousclaww.health.consolidate import Consolidator

logger = logging.getLogger(__name__)


class AutoConsolidate:
    """Automatic consolidation trigger based on event count and time thresholds.

    Usage:
        auto = AutoConsolidate(event_threshold=50, time_threshold_hours=6)
        if auto.should_trigger(memory_manager):
            n = auto.run(memory_manager, llm_router)
            print(f"Consolidated {n} events")
    """

    def __init__(
        self,
        event_threshold: int = 50,
        time_threshold_hours: float = 6.0,
        max_events_per_cycle: int = 200,
        consolidator: Consolidator | None = None,
    ) -> None:
        """Initialize the auto-consolidation trigger.

        Args:
            event_threshold: Minimum number of unconsolidated events to
                trigger consolidation. Default 50.
            time_threshold_hours: Maximum hours since last consolidation
                before triggering regardless of event count. Default 6.0.
            max_events_per_cycle: Maximum events to process in a single
                run. Prevents unbounded LLM calls. Default 200.
            consolidator: Optional custom Consolidator instance. If not
                provided, a default one is created on first use.
        """
        self.event_threshold = int(event_threshold)
        self.time_threshold_seconds = float(time_threshold_hours) * 3600.0
        self.max_events_per_cycle = int(max_events_per_cycle)
        self._consolidator = consolidator

    # ── Public API ─────────────────────────────────────────────────────────

    def should_trigger(self, memory_manager: MemoryManager) -> bool:
        """Check whether consolidation should be triggered now.

        Returns True if EITHER:
            - The unconsolidated event count >= event_threshold, OR
            - The time since last consolidation >= time_threshold AND
              there is at least one unconsolidated event.

        Args:
            memory_manager: The MemoryManager instance to check.

        Returns:
            True if consolidation should run, False otherwise.
        """
        unconsolidated_count = memory_manager.get_event_count(
            unconsolidated_only=True,
        )

        # No events to consolidate — never trigger
        if unconsolidated_count == 0:
            return False

        # Event count threshold met
        if unconsolidated_count >= self.event_threshold:
            logger.info(
                f"Auto-consolidate triggered by event count: "
                f"{unconsolidated_count} >= {self.event_threshold}"
            )
            return True

        # Time threshold met (but only if there are events to process)
        last_consolidation = memory_manager.get_last_consolidation_time()
        now = time.time()
        if last_consolidation is None:
            # Never consolidated — trigger if there are events
            logger.info(
                "Auto-consolidate triggered: no prior consolidation "
                f"and {unconsolidated_count} events pending"
            )
            return True

        elapsed = now - last_consolidation
        if elapsed >= self.time_threshold_seconds:
            logger.info(
                f"Auto-consolidate triggered by time: "
                f"{elapsed / 3600:.1f}h >= "
                f"{self.time_threshold_seconds / 3600:.1f}h "
                f"({unconsolidated_count} events pending)"
            )
            return True

        return False

    def run(
        self,
        memory_manager: MemoryManager,
        llm_router: LLMRouter,
    ) -> int:
        """Execute a consolidation cycle.

        Fetches unconsolidated events (up to max_events_per_cycle), runs
        the Consolidator, stores the resulting facts, marks events as
        consolidated, and logs the run.

        Args:
            memory_manager: The MemoryManager to read events from and
                store facts into.
            llm_router: The LLMRouter for fact extraction.

        Returns:
            The number of events consolidated (0 if nothing to process
            or if an error occurred before any events were processed).
        """
        events = memory_manager.get_events(
            unconsolidated_only=True,
            limit=self.max_events_per_cycle,
        )

        if not events:
            logger.info("Auto-consolidate: no unconsolidated events to process")
            return 0

        # Get or create consolidator
        consolidator = self._consolidator or Consolidator()

        logger.info(
            f"Auto-consolidate: processing {len(events)} events "
            f"(max {self.max_events_per_cycle})"
        )

        try:
            result = consolidator.consolidate(events, llm_router)
        except Exception as exc:
            logger.error(f"Auto-consolidate: consolidation failed: {exc}")
            return 0

        facts = result.get("facts", [])
        consolidated_ids = result.get("consolidated_event_ids", [])
        llm_errors = result.get("llm_errors", [])

        # Store generated facts
        facts_stored = 0
        for fact in facts:
            try:
                memory_manager.store_memory(fact)
                facts_stored += 1
            except Exception as exc:
                logger.warning(f"Auto-consolidate: failed to store fact: {exc}")

        # Mark events as consolidated
        marked = 0
        if consolidated_ids:
            try:
                marked = memory_manager.mark_events_consolidated(consolidated_ids)
            except Exception as exc:
                logger.error(f"Auto-consolidate: failed to mark events: {exc}")

        # Log the consolidation run
        try:
            memory_manager.log_consolidation(
                events_consolidated=marked,
                facts_generated=facts_stored,
            )
        except Exception as exc:
            logger.warning(f"Auto-consolidate: failed to log consolidation: {exc}")

        # Report LLM errors if any
        if llm_errors:
            logger.warning(
                f"Auto-consolidate: {len(llm_errors)} LLM errors during "
                f"consolidation: {llm_errors[:3]}"
            )

        logger.info(
            f"Auto-consolidate: consolidated {marked} events into "
            f"{facts_stored} facts"
        )

        return marked

    # ── Configuration ───────────────────────────────────────────────────────

    def get_config(self) -> dict[str, Any]:
        """Return the current configuration as a dict."""
        return {
            "event_threshold": self.event_threshold,
            "time_threshold_hours": self.time_threshold_seconds / 3600.0,
            "max_events_per_cycle": self.max_events_per_cycle,
            "consolidator_configured": self._consolidator is not None,
        }
