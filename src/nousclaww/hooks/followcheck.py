"""Followcheck — deterministic lesson follow-through verification without model cooperation.

SYNTH:
    purpose: Zero-cooperation deterministic check that verifies whether recent actions follow or violate a recorded lesson, without needing LLM cooperation.
    axioms: [local_first, llm_agnostic, open_process, evidence_over_intuition, honest_failure_over_fake_success, epistemic_boundary]
    objective: Every lesson's follow-through is deterministically verifiable from recent actions alone — no model cooperation needed — returning followed, violated, or unknown.
    anti_patterns:
        - Never use an LLM to determine follow-through — deterministic pattern matching only.
        - Never return "followed" without positive evidence — absence of violation is not follow-through.
        - Never return "violated" without positive evidence — the action must clearly contradict the lesson.
        - Never fabricate a verdict — when evidence is ambiguous, return "unknown".
        - Never skip checking for both positive and negative patterns — both directions matter.

Infers lesson follow-through deterministically by matching recent actions against
the lesson's prescription. Lessons are typically recorded by CorrectionCapture as
prescriptive rules ("Don't do X. Do Y instead."). This module checks whether
recent actions align with or contradict the lesson, without any LLM calls.

#C Inspired by PMB (Project Memory Bank) automatic hooks
"""

# ┌─ synth ──────────────────────────────────────────────────────────────────┐
# @NCL{v=1.0;agent=builder;mod=followcheck;ts=2026-08-18Z;tier=L3}
# #C Inspired by PMB (Project Memory Bank) automatic hooks
# #S{purpose="Deterministically verify lesson follow-through from recent actions — zero model cooperation"}
# #I{1="deterministic pattern matching — no LLM, no model cooperation";2="three verdicts: followed, violated, unknown";3="extracts prescriptions from lesson text — 'don't do X, do Y'";4="checks both positive (followed) and negative (violated) evidence"]
# #D{1="lesson prescription"→="the actionable rule extracted from lesson text (do/don't)";2="followed"→="at least one recent action matches the positive prescription";3="violated"→="at least one recent action matches the negative prescription";4="unknown"→="neither followed nor violated can be determined from recent actions"]
# #M{status=IMPLEMENTED;version=1.0.0;deps=""]
# #T{pass=0;fail=0;xfail=0}
# #W{1="prescription extraction is heuristic — complex lessons may not yield a clear do/don't pattern";2="unknown is the safe fallback when evidence is ambiguous — not a failure"]
# #L{lexicon→docs/NOUS_LEXICON.md}
# └──────────────────────────────────────────────────────────────────────────┘

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Verdict constants ────────────────────────────────────────────────────────

FOLLOWED = "followed"
VIOLATED = "violated"
UNKNOWN = "unknown"

# ── Prescription extraction patterns ─────────────────────────────────────────

# Matches "Don't do X" / "Do not do X" / "Never do X" / "Stop doing X"
# Captures the action X that should be avoided.
_NEGATIVE_PRESCRIPTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:don'?t\s+do|do\s+not\s+do|never\s+do|stop\s+doing|avoid\s+doing|"
        r"don'?t\s+use|do\s+not\s+use|never\s+use|avoid\s+using|"
        r"don'?t\s+(?:add|remove|create|delete|write|call|run|execute)|"
        r"never\s+(?:add|remove|create|delete|write|call|run|execute)|"
        r"stop\s+(?:adding|removing|creating|deleting|writing|calling|running))"
        r"\s*[::]?\s*(.+?)(?:\.|$)",
        re.IGNORECASE,
    ),
    # "User correction: X" — the correction itself implies what not to do
    re.compile(
        r"user\s+correction\s*[:]\s*(.+?)(?:\.|$)",
        re.IGNORECASE,
    ),
]

# Matches "Do Y instead" / "Always do Y" / "Use Y" / "Prefer Y"
# Captures the action Y that should be done.
# Note: "use Y" is also matched here, but the extraction method filters
# out matches preceded by negation words (don't, do not, never, stop, avoid).
_POSITIVE_PRESCRIPTION_PATTERNS: list[re.Pattern[str]] = [
    # "do Y instead" — the positive alternative after a negation
    re.compile(
        r"do\s+(.+?)\s+instead(?:\.|$)",
        re.IGNORECASE,
    ),
    # "always do Y" / "should do Y" / "should use Y" / "prefer Y" / "apply Y"
    re.compile(
        r"(?:always\s+do\s+(.+?)|should\s+(?:use|do|always)\s+(.+?)"
        r"|prefer\s+(.+?)|apply\s+(?:this\s+)?(.+?))"
        r"(?:\.|$)",
        re.IGNORECASE,
    ),
    # "use Y" — extraction method filters out negated occurrences
    re.compile(
        r"use\s+(.+?)(?:\.|$)",
        re.IGNORECASE,
    ),
]

# Negation words that, when preceding "use", indicate a negative prescription
_NEGATION_PREFIXES = re.compile(
    r"(?:don'?t|do\s+not|never|stop|avoid)\s+$",
    re.IGNORECASE,
)

# Patterns that indicate a lesson is prescriptive (contains do/don't guidance)
_PRESCRIPTIVE_INDICATORS = re.compile(
    r"(?:don'?t|do\s+not|never|stop|avoid|always|should|prefer|use|"
    r"instead|correction|apply)",
    re.IGNORECASE,
)


class Followcheck:
    """Deterministic lesson follow-through verification.

    Infers whether recent actions follow or violate a recorded lesson
    using deterministic pattern matching. No LLM cooperation required.

    The check works by:
    1. Extracting the lesson's prescription (what to do and what not to do).
    2. Scanning recent actions for patterns that match the prescription.
    3. Returning a verdict: followed, violated, or unknown.

    Verdicts:
        - followed: At least one recent action matches the positive
          prescription (the "do Y" part).
        - violated: At least one recent action matches the negative
          prescription (the "don't do X" part).
        - unknown: Neither followed nor violated can be determined —
          the lesson has no clear prescription, or recent actions
          don't relate to the lesson.

    Usage:
        fc = Followcheck()
        lesson = "Don't use GET for mutations. Use POST instead."
        actions = [
            {"action": "create user via POST /api/users", "result": {"success": True}},
            {"action": "update profile via PUT /api/profile", "result": {"success": True}},
        ]
        verdict = fc.check(lesson, actions)
        # -> "followed" (actions use POST/PUT, not GET for mutations)
    """

    def __init__(self) -> None:
        """Initialize the Followcheck hook with pre-compiled patterns."""
        self._negative_patterns = _NEGATIVE_PRESCRIPTION_PATTERNS
        self._positive_patterns = _POSITIVE_PRESCRIPTION_PATTERNS
        self._prescriptive_indicators = _PRESCRIPTIVE_INDICATORS
        self._negation_prefixes = _NEGATION_PREFIXES

    def check(self, lesson: str, recent_actions: list[dict[str, Any]]) -> str:
        """Check whether recent actions follow or violate a lesson.

        Extracts the lesson's prescription (what to do and what not to
        do), then scans recent actions for matching patterns. Returns a
        deterministic verdict without any LLM calls.

        The verdict logic:
        1. If the lesson has no prescriptive content, return UNKNOWN.
        2. Extract negative prescriptions ("don't do X").
        3. Extract positive prescriptions ("do Y instead").
        4. If any recent action matches a negative prescription, return
           VIOLATED.
        5. If any recent action matches a positive prescription, return
           FOLLOWED.
        6. If neither matches, return UNKNOWN.

        Violation takes priority over follow-through — a single
        violation is more important than multiple follow-throughs.

        Args:
            lesson: The lesson text to check against. Typically a
                prescriptive rule like "Don't do X. Do Y instead."
            recent_actions: A list of action dicts. Each dict should
                have an "action" key containing a string description of
                the action. May also contain "result", "type",
                "summary", etc.

        Returns:
            One of ``FOLLOWED``, ``VIOLATED``, or ``UNKNOWN``.
        """
        if not lesson or not lesson.strip():
            logger.debug("Followcheck: empty lesson, returning unknown")
            return UNKNOWN

        if not recent_actions:
            logger.debug("Followcheck: no recent actions, returning unknown")
            return UNKNOWN

        # Check if the lesson is prescriptive at all
        if not self._prescriptive_indicators.search(lesson):
            logger.debug("Followcheck: lesson is not prescriptive, returning unknown")
            return UNKNOWN

        # Extract prescriptions from the lesson
        negative_terms = self._extract_negative_prescriptions(lesson)
        positive_terms = self._extract_positive_prescriptions(lesson)

        if not negative_terms and not positive_terms:
            logger.debug(
                "Followcheck: could not extract prescriptions from lesson, "
                "returning unknown"
            )
            return UNKNOWN

        # Build action text strings from recent actions
        action_texts = self._extract_action_texts(recent_actions)

        if not action_texts:
            logger.debug("Followcheck: no action text found in recent_actions")
            return UNKNOWN

        # Check for violations first (higher priority)
        for action_text in action_texts:
            if self._matches_any(action_text, negative_terms):
                logger.info(
                    f"Followcheck: VIOLATED — action '{action_text[:60]}' "
                    f"matches negative prescription"
                )
                return VIOLATED

        # Check for follow-through
        for action_text in action_texts:
            if self._matches_any(action_text, positive_terms):
                logger.info(
                    f"Followcheck: FOLLOWED — action '{action_text[:60]}' "
                    f"matches positive prescription"
                )
                return FOLLOWED

        # Neither followed nor violated — not enough evidence
        logger.debug("Followcheck: no matching actions, returning unknown")
        return UNKNOWN

    def _extract_negative_prescriptions(self, lesson: str) -> list[str]:
        """Extract the 'don't do X' terms from a lesson.

        Scans the lesson text for negative prescription patterns and
        extracts the terms that should be avoided. Each extracted term
        is normalized to lowercase and stripped of trailing punctuation.

        Args:
            lesson: The lesson text.

        Returns:
            A list of normalized terms that the lesson says to avoid.
            Empty list if no negative prescriptions are found.
        """
        terms: list[str] = []
        seen: set[str] = set()

        for pattern in self._negative_patterns:
            for match in pattern.finditer(lesson):
                # The captured group may be any of the alternation groups
                captured = match.group(1)
                if not captured:
                    # Try other groups for the positive pattern with
                    # multiple capture groups
                    for i in range(2, match.lastindex + 1 if match.lastindex else 1):
                        g = match.group(i)
                        if g:
                            captured = g
                            break
                if not captured:
                    continue

                normalized = self._normalize_term(captured)
                if normalized and normalized not in seen:
                    terms.append(normalized)
                    seen.add(normalized)

        return terms

    def _extract_positive_prescriptions(self, lesson: str) -> list[str]:
        """Extract the 'do Y instead' terms from a lesson.

        Scans the lesson text for positive prescription patterns and
        extracts the terms that should be done. Each extracted term is
        normalized to lowercase and stripped of trailing punctuation.

        For the bare "use Y" pattern, matches preceded by a negation
        word (don't, do not, never, stop, avoid) are filtered out —
        those are negative prescriptions, not positive ones.

        Args:
            lesson: The lesson text.

        Returns:
            A list of normalized terms that the lesson says to do.
            Empty list if no positive prescriptions are found.
        """
        terms: list[str] = []
        seen: set[str] = set()

        for pattern in self._positive_patterns:
            for match in pattern.finditer(lesson):
                # Check if this match is preceded by a negation word
                # (only relevant for the bare "use Y" pattern)
                preceding_text = lesson[:match.start()]
                if self._negation_prefixes.search(preceding_text):
                    continue

                # The positive pattern has multiple capture groups
                captured = None
                if match.lastindex:
                    for i in range(1, match.lastindex + 1):
                        g = match.group(i)
                        if g:
                            captured = g
                            break
                if not captured:
                    continue

                normalized = self._normalize_term(captured)
                if normalized and normalized not in seen:
                    terms.append(normalized)
                    seen.add(normalized)

        return terms

    def _normalize_term(self, term: str) -> str:
        """Normalize a prescription term for matching.

        Converts to lowercase, strips whitespace and trailing
        punctuation, and collapses internal whitespace.

        Args:
            term: The raw extracted term.

        Returns:
            A normalized term string.
        """
        # Strip trailing punctuation and whitespace
        cleaned = term.strip().rstrip(".,;:!?")
        # Collapse internal whitespace
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.lower()

    def _extract_action_texts(self, recent_actions: list[dict[str, Any]]) -> list[str]:
        """Extract action description strings from recent action dicts.

        Looks for common keys in the action dicts: "action", "summary",
        "description", "command", "message". Combines all found text
        for each action into a single string for matching.

        Args:
            recent_actions: List of action dicts.

        Returns:
            A list of action text strings.
        """
        texts: list[str] = []
        text_keys = ("action", "summary", "description", "command", "message")

        for action_dict in recent_actions:
            if not isinstance(action_dict, dict):
                continue
            parts: list[str] = []
            for key in text_keys:
                value = action_dict.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
            if parts:
                texts.append(" ".join(parts))

        return texts

    # Common words that should not be used as matching terms when
    # splitting multi-word prescriptions into individual words.
    _STOP_WORDS = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "must", "shall",
        "can", "of", "to", "in", "on", "at", "by", "for", "with",
        "about", "as", "into", "through", "during", "before", "after",
        "above", "below", "from", "up", "down", "out", "off", "over",
        "under", "again", "further", "then", "once", "here", "there",
        "when", "where", "why", "how", "all", "each", "few", "more",
        "most", "other", "some", "such", "no", "nor", "not", "only",
        "own", "same", "so", "than", "too", "very", "what", "which",
        "who", "whom", "this", "that", "these", "those", "i", "you",
        "he", "she", "it", "we", "they", "me", "him", "her", "us",
        "them", "my", "your", "his", "its", "our", "their", "and",
        "or", "but", "if", "because", "while", "until", "please",
        "tell", "give", "show", "explain", "describe", "instead",
        "always", "never", "also", "just", "like", "want", "need",
    })

    def _matches_any(self, text: str, terms: list[str]) -> bool:
        """Check if any term appears in the text.

        Uses word-boundary matching for terms that are single words,
        and substring matching for multi-word terms. For multi-word
        terms, also extracts significant individual words (excluding
        stop words) and matches those with word boundaries. This
        ensures that a prescription like "get for mutations" matches
        an action containing the word "get" even if the full phrase
        doesn't appear.

        The match is case-insensitive.

        Args:
            text: The text to search in.
            terms: The terms to search for.

        Returns:
            True if any term is found in the text, False otherwise.
        """
        if not terms:
            return False

        text_lower = text.lower()

        for term in terms:
            if not term:
                continue
            if " " in term:
                # Multi-word term: try full substring match first
                if term in text_lower:
                    return True
                # Then try matching individual significant words
                words = term.split()
                for word in words:
                    if word in self._STOP_WORDS or len(word) < 2:
                        continue
                    if re.search(r"\b" + re.escape(word) + r"\b", text_lower):
                        return True
            else:
                # Single word: word-boundary match
                if re.search(r"\b" + re.escape(term) + r"\b", text_lower):
                    return True

        return False
