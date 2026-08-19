"""Correction capture — detects user frustration and records lessons on first occurrence.

SYNTH:
    purpose: Zero-cooperation detection of user frustration markers that records a lesson immediately on the FIRST occurrence, before the same mistake is repeated.
    axioms: [local_first, llm_agnostic, open_process, evidence_over_intuition, iteration_is_progress, honest_failure_over_fake_success]
    objective: Every user correction is captured as a lesson on first occurrence, so the agent never repeats the same mistake twice in a session.
    anti_patterns:
        - Never wait for the model to decide a correction is worth recording — capture immediately.
        - Never record the same correction twice — deduplicate by frustration signature.
        - Never ignore frustration markers — every signal is evidence.
        - Never record a lesson without the agent action that triggered it — context is required.
        - Never classify a genuine question as frustration — false positives erode trust.

Detects frustration markers in user messages using regex patterns, then records
a lesson to memory immediately on the first occurrence. The lesson includes the
user's correction, the agent action that triggered it, and a prescriptive
"don't do X, do Y instead" formulation. No model cooperation required.

#C Inspired by PMB (Project Memory Bank) automatic hooks
"""

# ┌─ synth ──────────────────────────────────────────────────────────────────┐
# @NCL{v=1.0;agent=builder;mod=correction_capture;ts=2026-08-18Z;tier=L3}
# #C Inspired by PMB (Project Memory Bank) automatic hooks
# #S{purpose="Detect user frustration markers and record lessons on FIRST occurrence — zero model cooperation"}
# #I{1="regex-based frustration detection — no LLM needed";2="first-occurrence recording — deduplicate by frustration signature";3="prescriptive lesson formulation — 'don't do X, do Y instead'";4="agent action context included — lessons are actionable"}
# #D{1="frustration marker"→="regex pattern indicating user correction or dissatisfaction";2="frustration signature"→="hash of normalized user message for deduplication";3="first occurrence"→="the first time a given frustration signature appears in a session"]
# #M{status=IMPLEMENTED;version=1.0.0;deps="nousclaww.memory.memory_manager"]
# #T{pass=0;fail=0;xfail=0}
# #W{1="regex patterns may produce false positives on rhetorical questions";2="deduplication is per-session — same correction in a new session will be recorded again (by design)"]
# #L{lexicon→docs/NOUS_LEXICON.md}
# └──────────────────────────────────────────────────────────────────────────┘

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nousclaww.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# ── Frustration marker patterns ──────────────────────────────────────────────
# Each pattern captures a distinct type of user correction or dissatisfaction.
# Patterns are ordered by specificity — all are checked (not first-match-wins)
# because a message may contain multiple frustration signals.

_FRUSTRATION_PATTERNS: list[re.Pattern[str]] = [
    # Direct negation: "no that's wrong", "no, that's not right"
    re.compile(
        r"\bno[,!]\s*(that'?s\s+(?:wrong|not\s+right|incorrect|not\s+what))",
        re.IGNORECASE,
    ),
    # "that's not what I asked (for)"
    re.compile(
        r"\bthat'?s\s+not\s+what\s+I\s+(asked|wanted|requested|meant|said)",
        re.IGNORECASE,
    ),
    # "stop doing X"
    re.compile(
        r"\bstop\s+(?:doing|using|adding|removing|repeating|generating)\b",
        re.IGNORECASE,
    ),
    # "I said Y not Z"
    re.compile(
        r"\bI\s+said\s+\S+\s+not\s+\S+",
        re.IGNORECASE,
    ),
    # "I told you (not to / to)"
    re.compile(
        r"\bI\s+told\s+you\s+(?:not\s+to|to)\b",
        re.IGNORECASE,
    ),
    # "you keep (doing / making)"
    re.compile(
        r"\byou\s+keep\s+(?:doing|making|repeating|adding|using)\b",
        re.IGNORECASE,
    ),
    # "we've been over this"
    re.compile(
        r"\bwe'?ve\s+(?:been\s+over|already\s+(?:discussed|covered|talked\s+about))\s+this\b",
        re.IGNORECASE,
    ),
    # "I already told you"
    re.compile(
        r"\bI\s+already\s+told\s+you\b",
        re.IGNORECASE,
    ),
    # "not again" / "here we go again"
    re.compile(
        r"\b(?:not\s+again|here\s+we\s+go\s+again|this\s+again)\b",
        re.IGNORECASE,
    ),
    # "why (did|do) you keep" — repeated behavior frustration
    re.compile(
        r"\bwhy\s+(?:did|do)\s+you\s+(?:keep|always|still)\b",
        re.IGNORECASE,
    ),
    # "I don't want" / "I asked for X not Y" — mismatch
    re.compile(
        r"\bI\s+don'?t\s+want\b",
        re.IGNORECASE,
    ),
    # "that's the opposite of what I said"
    re.compile(
        r"\bthat'?s\s+(?:the\s+opposite|backwards|wrong\s+direction)\b",
        re.IGNORECASE,
    ),
]


class CorrectionCapture:
    """Automatic frustration detection and lesson recording on first occurrence.

    Detects user frustration markers in messages using regex patterns, then
    records a lesson to memory immediately on the first occurrence. The same
    correction (by signature) is never recorded twice in the same session.

    The recorded lesson includes:
        - The user's correction (their exact words).
        - The agent action that triggered the correction.
        - A prescriptive formulation: "Don't do X. Do Y instead."
        - A frustration signature for deduplication.

    Usage:
        cc = CorrectionCapture()
        if cc.detect_frustration("no that's wrong, I said use POST not GET"):
            cc.capture(
                user_message="no that's wrong, I said use POST not GET",
                agent_action="Used GET method for the API request",
                memory_manager=memory_manager,
            )
    """

    def __init__(self) -> None:
        """Initialize the CorrectionCapture hook with pre-compiled patterns."""
        self._patterns = _FRUSTRATION_PATTERNS
        # Track frustration signatures seen in this session for deduplication
        self._seen_signatures: set[str] = set()

    def detect_frustration(self, user_message: str) -> bool:
        """Detect whether a user message contains frustration markers.

        Scans the message against all compiled frustration patterns. If any
        pattern matches, the message is classified as frustrated. This is a
        boolean check — it does not quantify the level of frustration.

        Args:
            user_message: The user's input string.

        Returns:
            True if at least one frustration marker is detected, False
            otherwise.
        """
        if not user_message or not user_message.strip():
            return False

        for pattern in self._patterns:
            if pattern.search(user_message):
                logger.debug(
                    f"CorrectionCapture: frustration detected "
                    f"(pattern: {pattern.pattern[:40]}...)"
                )
                return True

        return False

    def capture(
        self,
        user_message: str,
        agent_action: str,
        memory_manager: "MemoryManager",
    ) -> bool:
        """Record a lesson immediately on the first occurrence of a frustration.

        If the user message contains frustration markers AND the frustration
        signature has not been seen before in this session, a lesson is
        recorded to memory. If the signature has been seen, the correction is
        ignored (already recorded).

        The lesson is formulated as a prescriptive rule:
            "Don't do {agent_action}. User correction: {user_message}"

        Args:
            user_message: The user's frustrated message.
            agent_action: A description of what the agent did that triggered
                the correction.
            memory_manager: The MemoryManager instance to store into.

        Returns:
            True if a new lesson was recorded, False if no frustration was
            detected, the signature was already seen, or storage failed.
        """
        if not self.detect_frustration(user_message):
            return False

        # Compute frustration signature for deduplication
        signature = self._compute_signature(user_message)

        if signature in self._seen_signatures:
            logger.debug(
                "CorrectionCapture: duplicate frustration signature, "
                "skipping (already recorded)"
            )
            return False

        # Mark as seen
        self._seen_signatures.add(signature)

        # Build the lesson event
        timestamp = datetime.now(timezone.utc).isoformat()
        lesson_text = self._formulate_lesson(user_message, agent_action)

        event = {
            "type": "correction_lesson",
            "lesson": lesson_text,
            "user_message": user_message,
            "agent_action": agent_action,
            "frustration_signature": signature,
            "timestamp": timestamp,
            "summary": f"Lesson captured: {lesson_text[:80]}",
        }

        try:
            memory_manager.store(event)
            logger.info(
                f"CorrectionCapture: recorded lesson on first occurrence: "
                f"{lesson_text[:80]}"
            )
            return True
        except Exception as e:
            logger.warning(f"CorrectionCapture: failed to record lesson: {e}")
            # Remove from seen so it can be retried
            self._seen_signatures.discard(signature)
            return False

    def _compute_signature(self, user_message: str) -> str:
        """Compute a deduplication signature for a frustrated user message.

        Normalizes the message (lowercase, strip whitespace, collapse spaces)
        and produces a SHA-256 hash. Two messages with the same normalized
        text produce the same signature.

        Args:
            user_message: The user's frustrated message.

        Returns:
            A hex string signature for deduplication.
        """
        normalized = re.sub(r"\s+", " ", user_message.lower().strip())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _formulate_lesson(self, user_message: str, agent_action: str) -> str:
        """Formulate a prescriptive lesson from the user's correction.

        Creates a "don't do X, do Y instead" style lesson that captures
        both what the agent did wrong and the user's correction.

        Args:
            user_message: The user's frustrated message (the correction).
            agent_action: What the agent did that triggered the correction.

        Returns:
            A prescriptive lesson string.
        """
        return (
            f"Don't do: {agent_action}. "
            f"User correction: {user_message.strip()}. "
            f"Apply this correction in future similar situations."
        )

    def reset_session(self) -> None:
        """Reset the seen-signatures set for a new session.

        Call this when a new session begins so that corrections from a
        previous session don't suppress recording in the new one. By design,
        the same correction in a new session IS recorded again — the agent
        may have a fresh context and needs the reminder.
        """
        self._seen_signatures.clear()
        logger.debug("CorrectionCapture: session reset, signatures cleared")
