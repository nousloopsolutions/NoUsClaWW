"""Agent loop — the chat cycle that orchestrates all NoUsClaWW systems.

SYNTH:
    purpose: The agent chat loop that ties auto-recall, budget management, LLM routing, epistemic boundary, and autowrite into a single coherent turn cycle.
    axioms: [local_first, llm_agnostic, open_process, epistemic_boundary, completion_assumption, honest_failure_over_fake_success, reversibility_awareness]
    objective: Each chat turn classifies intent, injects relevant memories, builds context within budget, calls the LLM via the router, passes the result through the epistemic boundary, records the turn via autowrite, and returns the output — or honest silence with a cost-log when the boundary blocks or the LLM fails.
    anti_patterns:
        - Never fabricate output when the LLM fails or the boundary blocks — return silence
        - Never bypass the epistemic boundary — it is the last gate before the user
        - Never skip autowrite — every significant turn is recorded
        - Never let the context budget overflow without triggering compaction
        - Never call a cloud LLM directly — always go through the router
        - Never present a blocked/low-confidence response as confident

Depends on:
    - nousclaww.llm_router.LLMRouter
    - nousclaww.memory.memory_manager.MemoryManager
    - nousclaww.agent.policy.CompressionPolicy
    - nousclaww.epistemic_boundary.EpistemicBoundary
    - nousclaww.hooks.auto_recall.AutoRecall
    - nousclaww.hooks.autowrite.Autowrite

Inspired by PMB (Project Memory Bank) agent loop — the loop is the conductor.
It does not contain intelligence itself; it orchestrates the systems that do.
The budget says how much fits. The policy says what to keep. Auto-recall says
what to remember. The router says where to ask. The epistemic boundary says
what is safe to say. Autowrite says what to record. The loop's job is to call
these in the right order and to fail honestly when any step breaks.

Turn lifecycle (chat method):
    1. Auto-recall: classify the user's intent, retrieve relevant memories,
       inject them into the context.
    2. Budget management: add the user message and recalled memories to the
       budget. If the budget needs compaction, trigger compact() before
       building the LLM context.
    3. LLM call: route the context + question through the LLM router.
    4. Epistemic boundary: pass the LLM output through the boundary. If the
       boundary blocks it (low confidence, out-of-scope, unsafe), return
       silence with a cost-log entry.
    5. Autowrite: record the significant turn (user message, output,
       routing metadata, boundary verdict) to memory.
    6. Return: the output string, or an empty string (silence) with a
       cost-log dict if the boundary blocked or the LLM failed.

Contract:
    - chat(user_message) returns a string (possibly empty for silence).
    - compact() compresses the message history and resets the budget.
    - The loop never raises on LLM failure or boundary block — it returns
      silence and logs the reason. Exceptions from infrastructure (e.g.
      memory DB corruption) still propagate.
"""

#C Inspired by PMB (Project Memory Bank) agent loop

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from nousclaww.llm_router import LLMRouter
from nousclaww.memory.memory_manager import MemoryManager
from nousclaww.agent.policy import CompressionPolicy
from nousclaww.epistemic_boundary import EpistemicBoundary
from nousclaww.hooks.auto_recall import AutoRecall
from nousclaww.hooks.autowrite import Autowrite

logger = logging.getLogger(__name__)


@dataclass
class TurnResult:
    """The outcome of a single chat turn.

    Attributes:
        output: The text returned to the user. Empty string means silence
            (boundary block or LLM failure).
        silenced: True if the epistemic boundary blocked the output.
        cost_log: A dict recording what happened during the turn —
            routing decision, token usage, boundary verdict, recall
            count, autowrite status. Always populated, even on silence.
        error: Error message if the LLM call failed, else None.
    """
    output: str = ""
    silenced: bool = False
    cost_log: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class AgentLoop:
    """The agent chat loop — orchestrates recall, budget, routing, boundary, and write.

    Args:
        llm_router: The hybrid LLM router (local-first, cloud fallback).
        memory_manager: The memory manager for storage and retrieval.
        policy: The compression policy for context compaction.
        epistemic_boundary: The boundary that gates output for confidence/safety.
        auto_recall: Optional AutoRecall hook. If None, a default one is
            constructed from the memory_manager.
        autowrite: Optional Autowrite hook. If None, a default one is
            constructed from the memory_manager.

    Usage:
        router = LLMRouter()
        memory = MemoryManager(...)
        policy = CompressionPolicy()
        boundary = EpistemicBoundary()
        loop = AgentLoop(router, memory, policy, boundary)
        reply = loop.chat("What files did I work on yesterday?")
    """

    def __init__(
        self,
        llm_router: LLMRouter,
        memory_manager: MemoryManager,
        policy: CompressionPolicy,
        epistemic_boundary: EpistemicBoundary,
        auto_recall: AutoRecall | None = None,
        autowrite: Autowrite | None = None,
    ) -> None:
        self.llm_router: LLMRouter = llm_router
        self.memory_manager: MemoryManager = memory_manager
        self.policy: CompressionPolicy = policy
        self.epistemic_boundary: EpistemicBoundary = epistemic_boundary

        # Hooks — construct defaults if not provided
        self.auto_recall: AutoRecall = auto_recall or AutoRecall(memory_manager)
        self.autowrite: Autowrite = autowrite or Autowrite(memory_manager)

        # Conversation history (list of message dicts)
        self._messages: list[dict[str, Any]] = []

        # Token budget — default to a generous window; can be overridden
        # by the caller via set_budget()
        self._budget = self._init_default_budget()

        logger.debug("AgentLoop initialized with %d messages in history", len(self._messages))

    # ── Configuration ─────────────────────────────────────────────────────

    def _init_default_budget(self):
        """Initialize a default token budget from the router's local model."""
        from nousclaww.agent.budget import TokenBudget
        return TokenBudget(max_tokens=8192, context_window=6144, compaction_threshold=0.75)

    def set_budget(self, max_tokens: int, context_window: int | None = None,
                   compaction_threshold: float = 0.75) -> None:
        """Replace the token budget with a new configuration.

        Args:
            max_tokens: Hard cap on tokens for the LLM.
            context_window: Portion reserved for context. Defaults to max_tokens.
            compaction_threshold: Fraction at which compaction triggers.
        """
        from nousclaww.agent.budget import TokenBudget
        self._budget = TokenBudget(
            max_tokens=max_tokens,
            context_window=context_window,
            compaction_threshold=compaction_threshold,
        )
        logger.debug("Budget replaced: max=%d", max_tokens)

    def get_budget(self):
        """Return the current token budget instance."""
        return self._budget

    def get_messages(self) -> list[dict[str, Any]]:
        """Return a copy of the current conversation history."""
        return list(self._messages)

    def clear_history(self) -> None:
        """Clear the conversation history and reset the budget."""
        self._messages.clear()
        self._budget.reset()
        logger.debug("History cleared and budget reset")

    # ── Main chat loop ────────────────────────────────────────────────────

    def chat(self, user_message: str) -> str:
        """Process a single user turn and return the agent's response.

        This is the main entry point. It runs the full turn lifecycle:
        auto-recall, budget management, LLM call, epistemic boundary,
        autowrite, and return.

        Args:
            user_message: The user's input text.

        Returns:
            The agent's response string. Empty string ("") means silence
            — the epistemic boundary blocked the output or the LLM failed.
            The reason is available in the last turn's cost_log via
            last_turn_result().
        """
        result = self._run_turn(user_message)
        return result.output

    def last_turn_result(self) -> TurnResult | None:
        """Return the result of the most recent turn, or None if no turns yet."""
        return self._last_result if hasattr(self, "_last_result") else None

    def _run_turn(self, user_message: str) -> TurnResult:
        """Execute the full turn lifecycle and return a TurnResult."""
        turn_start = time.time()
        cost_log: dict[str, Any] = {
            "user_message_len": len(user_message),
            "timestamp": turn_start,
        }

        # ── Step 1: Auto-recall ───────────────────────────────────────────
        recalled: list[dict[str, Any]] = []
        intent = "unknown"
        try:
            intent = self.auto_recall.classify_intent(user_message)
            recalled = self.auto_recall.recall(user_message, intent=intent)
            cost_log["intent"] = intent
            cost_log["recall_count"] = len(recalled)
            logger.debug("Auto-recall: intent=%s, %d memories", intent, len(recalled))
        except Exception as e:
            cost_log["recall_error"] = str(e)
            logger.warning("Auto-recall failed: %s", e)

        # ── Step 2: Build context with budget management ──────────────────
        # Add the user message to history
        user_msg: dict[str, Any] = {"role": "user", "content": user_message}
        self._messages.append(user_msg)

        # Add recalled memories as system context
        recall_context = self._format_recall(recalled)

        # Check if we need compaction before building the LLM context
        self._budget.add(user_message)
        if recall_context:
            self._budget.add(recall_context)

        if self._budget.needs_compaction():
            logger.info("Budget at %.0f%% — triggering compaction",
                        self._budget.utilization() * 100)
            self.compact()
            cost_log["compaction_triggered"] = True

        # Build the context string for the LLM
        context = self._build_context(recall_context)
        cost_log["context_tokens"] = self._budget.current_tokens
        cost_log["budget_utilization"] = round(self._budget.utilization(), 4)

        # ── Step 3: Call LLM via router ───────────────────────────────────
        llm_output = ""
        llm_error: str | None = None
        routing_meta: dict[str, Any] = {}
        try:
            result = self.llm_router.ask(
                question=user_message,
                context=context,
            )
            llm_output = result.text
            routing_meta = {
                "provider": result.provider,
                "decision": result.decision,
                "model": result.model,
                "duration_ms": result.duration_ms,
                "cloud_used": result.cloud_used,
            }
            cost_log["routing"] = routing_meta
            if result.error:
                llm_error = result.error
                cost_log["llm_error"] = result.error
                logger.warning("LLM call failed: %s", result.error)
        except Exception as e:
            llm_error = str(e)
            cost_log["llm_error"] = str(e)
            logger.warning("LLM router exception: %s", e)

        # ── Step 4: Epistemic boundary ────────────────────────────────────
        silenced = False
        boundary_verdict = "pass"
        if llm_error or not llm_output:
            # LLM failed — silence with honest failure
            silenced = True
            boundary_verdict = "llm_failure"
            cost_log["boundary_verdict"] = boundary_verdict
            output = ""
        else:
            try:
                boundary_result = self.epistemic_boundary.evaluate(
                    llm_output,
                    context={
                        "intent": intent,
                        "user_message": user_message,
                        "routing": routing_meta,
                    },
                )
                if boundary_result.allowed:
                    output = boundary_result.text
                    boundary_verdict = "pass"
                else:
                    output = ""
                    silenced = True
                    boundary_verdict = boundary_result.reason or "blocked"
                    logger.info("Epistemic boundary blocked output: %s", boundary_verdict)
                cost_log["boundary_verdict"] = boundary_verdict
                cost_log["boundary_confidence"] = getattr(
                    boundary_result, "confidence", None
                )
            except Exception as e:
                # If the boundary itself errors, fail safe: silence
                output = ""
                silenced = True
                boundary_verdict = f"boundary_error: {e}"
                cost_log["boundary_verdict"] = boundary_verdict
                logger.warning("Epistemic boundary error: %s", e)

        # ── Step 5: Autowrite ─────────────────────────────────────────────
        # Record the turn (both user and assistant messages)
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": output,
            "silenced": silenced,
            "intent": intent,
            "boundary_verdict": boundary_verdict,
        }
        self._messages.append(assistant_msg)
        self._budget.add(output)

        try:
            self.autowrite.record_turn(
                user_message=user_message,
                output=output,
                metadata={
                    "intent": intent,
                    "routing": routing_meta,
                    "boundary_verdict": boundary_verdict,
                    "silenced": silenced,
                    "recall_count": len(recalled),
                    "context_tokens": self._budget.current_tokens,
                },
            )
            cost_log["autowrite"] = "recorded"
        except Exception as e:
            cost_log["autowrite_error"] = str(e)
            logger.warning("Autowrite failed: %s", e)

        # ── Step 6: Assemble result ───────────────────────────────────────
        duration_ms = (time.time() - turn_start) * 1000
        cost_log["total_duration_ms"] = round(duration_ms, 2)
        cost_log["silenced"] = silenced

        turn_result = TurnResult(
            output=output,
            silenced=silenced,
            cost_log=cost_log,
            error=llm_error,
        )
        self._last_result = turn_result

        logger.debug(
            "Turn complete: silenced=%s verdict=%s duration=%.0fms",
            silenced,
            boundary_verdict,
            duration_ms,
        )
        return turn_result

    # ── Compaction ────────────────────────────────────────────────────────

    def compact(self) -> int:
        """Trigger context compression when the budget is exceeded.

        Compresses the conversation history using the compression policy
        to fit within the budget, then resets the budget and re-adds the
        compressed messages.

        Returns:
            The number of tokens saved by compaction.
        """
        tokens_before = self._budget.current_tokens

        # Compress the message history to fit the remaining budget
        compressed = self.policy.compress(self._messages, self._budget)

        # Reset budget and re-add compressed messages
        self._budget.reset()
        for msg in compressed:
            self._budget.add(str(msg.get("content", "")))

        tokens_after = self._budget.current_tokens
        saved = max(0, tokens_before - tokens_after)

        # Replace history with compressed version
        self._messages = compressed

        logger.info(
            "Compaction complete: %d → %d tokens (saved %d), %d → %d messages",
            tokens_before,
            tokens_after,
            saved,
            len(self._messages) + (len(compressed) - len(self._messages)),
            len(compressed),
        )
        return saved

    # ── Context building ──────────────────────────────────────────────────

    def _format_recall(self, recalled: list[dict[str, Any]]) -> str:
        """Format recalled memories into a context string.

        Args:
            recalled: List of memory dicts from auto-recall.

        Returns:
            A formatted string of recalled context, or empty string.
        """
        if not recalled:
            return ""
        lines: list[str] = ["[Recalled context]"]
        for mem in recalled:
            content = str(mem.get("content", mem.get("summary", "")))
            source = mem.get("source", "memory")
            lines.append(f"  - ({source}) {content}")
        return "\n".join(lines)

    def _build_context(self, recall_context: str) -> str:
        """Build the full context string for the LLM call.

        Combines the recall context with a compressed view of the recent
        conversation history.

        Args:
            recall_context: Formatted recalled memories string.

        Returns:
            The context string to pass to the LLM router.
        """
        parts: list[str] = []
        if recall_context:
            parts.append(recall_context)

        # Include recent conversation history (compressed if needed)
        if self._messages:
            history_lines: list[str] = ["[Conversation history]"]
            for msg in self._messages:
                role = msg.get("role", "unknown")
                content = str(msg.get("content", ""))
                if content:
                    history_lines.append(f"  {role}: {content}")
            parts.append("\n".join(history_lines))

        return "\n\n".join(parts)
