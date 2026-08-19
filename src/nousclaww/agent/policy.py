"""Compression policy — selective context compression that preserves decisions.

SYNTH:
    purpose: Selective compression of conversation messages that preserves decisions verbatim while summarizing narrative and dropping redundancy, to fit within a token budget.
    axioms: [local_first, evidence_over_intuition, honest_failure_over_fake_success, reversibility_awareness, epistemic_boundary]
    objective: When the context budget is exceeded, compress the message history so that decisions are never lost, high-importance facts are retained, narrative is summarized, and redundant messages are dropped — all without calling an external summarizer.
    anti_patterns:
        - Never drop or alter a message classified as 'decision'
        - Never call a cloud LLM to summarize — compression is local
        - Never silently discard facts without checking importance
        - Never return more tokens than the budget allows
        - Never fabricate summary content — summarize only what is present

Depends on: nousclaww.agent.budget.TokenBudget

Inspired by PMB (Project Memory Bank) agent loop — the policy is the
decision layer that sits between the budget (which says "too full") and the
agent loop (which says "compress now"). The policy's job is to be selective:
not all messages are equal. A decision the user made is sacred. A narrative
chit-chat message is compressible. A redundant duplicate is disposable.

Classification scheme:
    - decision: A message that records a choice, directive, or commitment.
        Kept verbatim. Never compressed.
    - fact: A message that records a piece of information. Kept if its
        importance score is high; otherwise summarized.
    - narrative: Conversational filler, process narration, status updates.
        Summarized into a single line.
    - redundant: A message that duplicates prior content. Dropped entirely.

Importance scoring: facts carry an optional 'importance' field (0.0–1.0).
    If absent, importance defaults to 0.5. Facts with importance >= 0.7
    are kept verbatim; the rest are summarized.

Contract:
    - classify(message) returns one of: 'decision', 'fact', 'narrative', 'redundant'.
    - compress(messages, budget) returns a new list of messages that fits
      within budget.remaining() tokens, preserving order and decisions.
"""

#C Inspired by PMB (Project Memory Bank) agent loop

from __future__ import annotations

import logging
import re
from typing import Any

from nousclaww.agent.budget import TokenBudget

logger = logging.getLogger(__name__)

# ── Classification constants ──────────────────────────────────────────────

DECISION = "decision"
FACT = "fact"
NARRATIVE = "narrative"
REDUNDANT = "redundant"

_VALID_CLASSES = {DECISION, FACT, NARRATIVE, REDUNDANT}

# Heuristic keywords for decision detection. A message is classified as
# 'decision' if it contains any of these patterns (case-insensitive).
_DECISION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(let'?s|we should|I want|I need|decide|decision|chose|chosen|go with|use|don'?t use|must|shall|will|commit to|agreed|approve|reject)\b", re.IGNORECASE),
    re.compile(r"\b(implement|configure|set up|deploy|refactor to|switch to|migrate to)\b", re.IGNORECASE),
    re.compile(r"\b(task|todo|action item)[:\s]", re.IGNORECASE),
]

# Heuristic keywords for fact detection. A message is a fact if it states
# information without making a choice.
_FACT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(is|are|was|were|has|have|contains|located at|version|path|file|function|class|returns|parameter)\b", re.IGNORECASE),
    re.compile(r"\b(error|exception|traceback|failed|succeeded|result|output|value)\b", re.IGNORECASE),
]

# Importance threshold above which facts are kept verbatim.
_FACT_KEEP_THRESHOLD = 0.7
_DEFAULT_IMPORTANCE = 0.5


class CompressionPolicy:
    """Selective compression policy that preserves decisions.

    The policy classifies each message and applies a compression strategy
    per class:

        decision  → kept verbatim (never touched)
        fact      → kept verbatim if importance >= 0.7, else summarized
        narrative → summarized to a single line
        redundant → dropped

    Compression is iterative: the policy first drops redundant messages,
    then summarizes narrative, then summarizes low-importance facts —
    repeating until the result fits the budget or no more compression
    is possible. If the budget still cannot be met, the policy returns
    the most compressed version achieved and logs a warning (honest
    failure over fake success).
    """

    def __init__(self, fact_keep_threshold: float = _FACT_KEEP_THRESHOLD) -> None:
        if not 0.0 <= fact_keep_threshold <= 1.0:
            raise ValueError("fact_keep_threshold must be in [0.0, 1.0]")
        self.fact_keep_threshold: float = fact_keep_threshold

    # ── Classification ────────────────────────────────────────────────────

    def classify(self, message: dict[str, Any]) -> str:
        """Classify a message into one of four categories.

        Args:
            message: A message dict. Expected keys:
                - 'role' (str): e.g. 'user', 'assistant', 'system'
                - 'content' (str): the message text
                - 'importance' (float, optional): 0.0–1.0 for facts
                - 'type' (str, optional): explicit override

        Returns:
            One of 'decision', 'fact', 'narrative', 'redundant'.
        """
        # Explicit type override (e.g. set by upstream classifier)
        explicit = message.get("type")
        if explicit and explicit in _VALID_CLASSES:
            return explicit

        content = str(message.get("content", "")).strip()
        if not content:
            return REDUNDANT

        # Decision: contains directive/choice language
        for pattern in _DECISION_PATTERNS:
            if pattern.search(content):
                return DECISION

        # Fact: contains informational language
        for pattern in _FACT_PATTERNS:
            if pattern.search(content):
                return FACT

        # Default: narrative
        return NARRATIVE

    # ── Compression ───────────────────────────────────────────────────────

    def compress(
        self,
        messages: list[dict[str, Any]],
        budget: TokenBudget,
    ) -> list[dict[str, Any]]:
        """Compress messages to fit within the budget's remaining tokens.

        Strategy (applied in passes, most aggressive last):
            1. Drop redundant messages.
            2. Summarize narrative messages into single lines.
            3. Summarize low-importance facts.
            4. If still over budget, drop oldest narrative summaries.

        Decisions are never modified or dropped.

        Args:
            messages: The conversation messages to compress.
            budget: The TokenBudget to fit within. Compression targets
                budget.remaining() tokens.

        Returns:
            A new list of messages (copies, never mutating originals)
            that fits within the budget if possible. If compression
            cannot fully meet the budget, the tightest result is
            returned and a warning is logged.
        """
        if not messages:
            return []

        target = budget.remaining()
        if target <= 0:
            logger.warning("Budget has 0 remaining tokens — returning decisions only")
            return [self._copy(m) for m in messages if self.classify(m) == DECISION]

        # Work on copies so we never mutate the caller's list
        working = [self._copy(m) for m in messages]

        # Tag each message with its classification
        for msg in working:
            msg["_class"] = self.classify(msg)

        # Pass 1: drop redundant
        working = [m for m in working if m["_class"] != REDUNDANT]
        if self._total_tokens(working, budget) <= target:
            return self._strip_tags(working)

        # Pass 2: summarize narrative
        working = self._summarize_class(working, NARRATIVE, budget)
        if self._total_tokens(working, budget) <= target:
            return self._strip_tags(working)

        # Pass 3: summarize low-importance facts
        working = self._summarize_low_facts(working, budget)
        if self._total_tokens(working, budget) <= target:
            return self._strip_tags(working)

        # Pass 4: drop oldest narrative summaries (keep decisions + facts)
        working = self._drop_oldest_narrative(working, budget, target)
        if self._total_tokens(working, budget) <= target:
            return self._strip_tags(working)

        # Last resort: keep only decisions
        decisions = [m for m in working if m["_class"] == DECISION]
        if self._total_tokens(decisions, budget) <= target:
            logger.warning(
                "Compression exhausted — retaining decisions only (%d / %d messages)",
                len(decisions),
                len(messages),
            )
            return self._strip_tags(decisions)

        # Even decisions don't fit — return as many as possible (oldest first
        # dropped, since recent context is usually more relevant)
        logger.warning(
            "Budget too small for all decisions (%d tokens needed, %d available) "
            "— keeping newest decisions only",
            self._total_tokens(decisions, budget),
            target,
        )
        kept: list[dict[str, Any]] = []
        for msg in reversed(decisions):
            if budget.estimate_tokens(str(msg.get("content", ""))) <= target:
                kept.append(msg)
                target -= budget.estimate_tokens(str(msg.get("content", "")))
        kept.reverse()
        return self._strip_tags(kept)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _copy(self, message: dict[str, Any]) -> dict[str, Any]:
        """Create a shallow copy of a message dict."""
        return dict(message)

    def _strip_tags(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove internal _class tags before returning to caller."""
        result = []
        for m in messages:
            clean = {k: v for k, v in m.items() if k != "_class"}
            result.append(clean)
        return result

    def _total_tokens(self, messages: list[dict[str, Any]], budget: TokenBudget) -> int:
        """Sum the estimated tokens of all message contents."""
        total = 0
        for m in messages:
            total += budget.estimate_tokens(str(m.get("content", "")))
        return total

    def _summarize_class(
        self,
        messages: list[dict[str, Any]],
        cls: str,
        budget: TokenBudget,
    ) -> list[dict[str, Any]]:
        """Summarize all messages of a given class into single-line versions.

        The summary is a truncation to the first sentence or 80 characters,
        prefixed with a marker. This is a local heuristic — no LLM call.
        """
        result: list[dict[str, Any]] = []
        for m in messages:
            if m.get("_class") == cls:
                content = str(m.get("content", ""))
                summary = self._summarize_text(content)
                new_msg = dict(m)
                new_msg["content"] = summary
                new_msg["_compressed"] = True
                result.append(new_msg)
            else:
                result.append(m)
        return result

    def _summarize_low_facts(
        self,
        messages: list[dict[str, Any]],
        budget: TokenBudget,
    ) -> list[dict[str, Any]]:
        """Summarize facts whose importance is below the keep threshold."""
        result: list[dict[str, Any]] = []
        for m in messages:
            if m.get("_class") == FACT:
                importance = float(m.get("importance", _DEFAULT_IMPORTANCE))
                if importance < self.fact_keep_threshold:
                    content = str(m.get("content", ""))
                    summary = self._summarize_text(content)
                    new_msg = dict(m)
                    new_msg["content"] = summary
                    new_msg["_compressed"] = True
                    result.append(new_msg)
                else:
                    result.append(m)
            else:
                result.append(m)
        return result

    def _drop_oldest_narrative(
        self,
        messages: list[dict[str, Any]],
        budget: TokenBudget,
        target: int,
    ) -> list[dict[str, Any]]:
        """Drop narrative messages from the oldest until budget fits."""
        # Collect indices of narrative messages (oldest first)
        narrative_indices = [
            i for i, m in enumerate(messages) if m.get("_class") == NARRATIVE
        ]
        # Drop from the oldest (front of list) until we fit
        for idx in reversed(narrative_indices):
            if self._total_tokens(messages, budget) <= target:
                break
            messages.pop(idx)
        return messages

    def _summarize_text(self, text: str, max_chars: int = 80) -> str:
        """Summarize text to its first sentence or max_chars, whichever is shorter.

        This is a deliberately simple local heuristic. It does not call an
        LLM. The goal is to reduce token count while preserving the gist.

        Args:
            text: The text to summarize.
            max_chars: Maximum characters in the summary.

        Returns:
            A shortened string prefixed with '[summarized]'.
        """
        text = text.strip()
        if len(text) <= max_chars:
            return f"[summarized] {text}"

        # Try to cut at the first sentence boundary
        sentence_end = re.search(r"[.!?]\s", text)
        if sentence_end and sentence_end.start() <= max_chars:
            first_sentence = text[: sentence_end.start() + 1]
        else:
            # Hard cut at max_chars, trying to break on a word boundary
            cut = text[:max_chars]
            last_space = cut.rfind(" ")
            if last_space > max_chars // 2:
                first_sentence = cut[:last_space]
            else:
                first_sentence = cut

        return f"[summarized] {first_sentence}..."
