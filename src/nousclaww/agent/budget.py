"""Token budget tracker — approximate counting with compaction trigger.

SYNTH:
    purpose: Approximate token counting and budget management that triggers compaction at 75% of the context window.
    axioms: [local_first, evidence_over_intuition, honest_failure_over_fake_success, reversibility_awareness]
    objective: The agent always knows how much context budget remains and triggers compaction before overflow, using a fast local approximation that never depends on an external tokenizer.
    anti_patterns:
        - Never call an external API or cloud tokenizer to count tokens
        - Never silently exceed the context window without triggering compaction
        - Never report a budget as "fits" when it does not
        - Never mutate max_tokens or context_window after construction without explicit intent

A LEAF module: no internal NoUsClaWW dependencies. Only the standard library.

Inspired by PMB (Project Memory Bank) agent loop — the budget tracker is the
foundation that the compression policy and the agent loop build upon. Without
an honest budget, compression is blind.

Token approximation: chars / 4. This is a well-known heuristic that stays
within ~15% of BPE tokenizers for English text. It is deliberately approximate
because (a) we are local-first and cannot assume a specific tokenizer is
installed, and (b) the compaction threshold has a 25% safety margin that
absorbs the approximation error.

Contract:
    - estimate_tokens(text) returns chars // 4 (minimum 1 for non-empty text).
    - add(text) increments current_tokens by the estimate and returns the delta.
    - can_fit(text) returns True if current + estimate <= context_window.
    - needs_compaction() returns True when current > 75% of context_window.
    - remaining() returns context_window - current_tokens (clamped at 0).
"""

#C Inspired by PMB (Project Memory Bank) agent loop

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TokenBudget:
    """Approximate token budget with compaction threshold.

    Tracks how many tokens (approximately) have been consumed within a
    context window and signals when compaction should fire.

    Args:
        max_tokens: Hard cap on tokens the LLM can receive in one call.
            This is the model's true output+input limit (e.g. 8192).
        context_window: The portion of max_tokens reserved for context
            (prompt + injected memories). Must be <= max_tokens.
            Defaults to max_tokens if not specified.
        compaction_threshold: Fraction of context_window at which
            compaction triggers. Default 0.75 (75%).

    Usage:
        budget = TokenBudget(max_tokens=8192, context_window=6144)
        budget.add("Hello world")  # ~3 tokens
        assert budget.remaining() == 6141
        assert not budget.needs_compaction()
    """

    def __init__(
        self,
        max_tokens: int,
        context_window: int | None = None,
        compaction_threshold: float = 0.75,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        cw = context_window if context_window is not None else max_tokens
        if cw <= 0:
            raise ValueError("context_window must be positive")
        if cw > max_tokens:
            raise ValueError(
                f"context_window ({cw}) cannot exceed max_tokens ({max_tokens})"
            )
        if not 0.0 < compaction_threshold <= 1.0:
            raise ValueError("compaction_threshold must be in (0.0, 1.0]")

        self.max_tokens: int = max_tokens
        self.context_window: int = cw
        self.compaction_threshold: float = compaction_threshold
        self.current_tokens: int = 0

        logger.debug(
            "TokenBudget initialized: max=%d context_window=%d threshold=%.2f",
            self.max_tokens,
            self.context_window,
            self.compaction_threshold,
        )

    # ── Core estimation ───────────────────────────────────────────────────

    def estimate_tokens(self, text: str) -> int:
        """Approximate the token count of a string.

        Uses the chars/4 heuristic. Returns at least 1 for any non-empty
        string so that a single character is never counted as zero
        (which would let unbounded single-char messages slip through).

        Args:
            text: The string to estimate.

        Returns:
            Approximate token count (>= 0). Empty string returns 0.
        """
        if not text:
            return 0
        return max(1, len(text) // 4)

    # ── Mutation ──────────────────────────────────────────────────────────

    def add(self, text: str) -> int:
        """Add text to the budget, incrementing current_tokens.

        Args:
            text: The text to account for.

        Returns:
            The number of tokens added (the estimate for this text).
        """
        delta = self.estimate_tokens(text)
        self.current_tokens += delta
        logger.debug(
            "Budget add: +%d tokens (now %d / %d)",
            delta,
            self.current_tokens,
            self.context_window,
        )
        return delta

    def subtract(self, text: str) -> int:
        """Subtract text from the budget (e.g. after compression).

        Clamps at zero so a bad estimate can never produce a negative
        budget.

        Args:
            text: The text to remove from the count.

        Returns:
            The number of tokens subtracted.
        """
        delta = self.estimate_tokens(text)
        self.current_tokens = max(0, self.current_tokens - delta)
        return delta

    def reset(self) -> None:
        """Reset current_tokens to zero."""
        self.current_tokens = 0
        logger.debug("Budget reset to 0")

    # ── Queries ───────────────────────────────────────────────────────────

    def can_fit(self, text: str) -> bool:
        """Check whether text fits within the remaining context window.

        Args:
            text: The text to test.

        Returns:
            True if current_tokens + estimate <= context_window.
        """
        return self.current_tokens + self.estimate_tokens(text) <= self.context_window

    def needs_compaction(self) -> bool:
        """Check whether the budget has crossed the compaction threshold.

        Returns:
            True if current_tokens > compaction_threshold * context_window.
        """
        return self.current_tokens > int(self.context_window * self.compaction_threshold)

    def remaining(self) -> int:
        """Return the number of tokens remaining in the context window.

        Clamped at 0 — never returns a negative number.
        """
        return max(0, self.context_window - self.current_tokens)

    # ── Diagnostics ───────────────────────────────────────────────────────

    def utilization(self) -> float:
        """Return the fraction of the context window currently used.

        Returns:
            current_tokens / context_window, in [0.0, 1.0+] range.
            May exceed 1.0 if tokens were added beyond the window.
        """
        if self.context_window == 0:
            return 0.0
        return self.current_tokens / self.context_window

    def summary(self) -> dict[str, int | float]:
        """Return a diagnostic summary of the budget state.

        Returns:
            Dict with current, max, context_window, remaining,
            threshold, and utilization fields.
        """
        return {
            "current_tokens": self.current_tokens,
            "max_tokens": self.max_tokens,
            "context_window": self.context_window,
            "remaining": self.remaining(),
            "compaction_threshold": self.compaction_threshold,
            "utilization": round(self.utilization(), 4),
            "needs_compaction": self.needs_compaction(),
        }

    def __repr__(self) -> str:
        return (
            f"TokenBudget(current={self.current_tokens}, "
            f"window={self.context_window}, "
            f"remaining={self.remaining()}, "
            f"needs_compaction={self.needs_compaction()})"
        )
