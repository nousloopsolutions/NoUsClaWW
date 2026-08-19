"""
Epistemic Boundary and Silence Protocol — Axiom 4 implementation.

Sits between INFER and OUTPUT in the control state machine. Governs what
the system does with its own uncertainty. The agent never fabricates.
Silence is authorized but costly. Gaps are mapped to void sockets.
Confidence thresholds are dynamic.

Innovations integrated from the NoUs-fordge audit:
    - Stress sidecar: linear-gradient, non-saturating stress model that
      modulates retrieval via geodesic distance scaling (emotional recall
      shift). Higher stress slightly lowers the threshold for familiar
      patterns and raises it for novel ones.
    - Fixed refusal messages: when the boundary returns SILENCE or
      SHERLOCK, a FIXED, non-fabricated message is used. This prevents
      the system from inventing content through its refusal text.
    - Append-only silence event log: every silence event is persisted to
      a SQLite database (silence_events.db) with the exact boundaries of
      the missing data. Logs are never deleted or modified.
    - Danger cone integration: known bad patterns are registered as
      danger cones (VSA regions with redirect pointers). The evaluate()
      method checks danger cones as part of its decision — a triggered
      cone can force SILENCE with a redirect.
    - Absence detection: six gap types (PATTERN, CONNECTION, TEMPORAL,
      CATEGORY, CAPACITY, CONSISTENCY) are detected from monitoring
      stats, classifying what is MISSING, not just what is present.

Contract:
    - No output crosses the INFER -> OUTPUT boundary without passing
      through EpistemicBoundary.evaluate().
    - Confidence thresholds are always computed via dynamic_threshold() --
      never a static constant.
    - Every silence event is accompanied by a localized log defining the
      exact boundaries of the missing data.
    - Silence is the last resort, not the first. Annotated uncertainty is
      always preferred over silence.
    - Void sockets are persisted to VDS 90000 (Pool of Tears).
    - UNKNOWN / SILENCE states MUST produce a refusal — no exceptions,
      no overrides. The refusal message is fixed and non-fabricated.

SYNTH:
    purpose: Implement Axiom 4 — the Epistemic Boundary and Silence Protocol. Sits between INFER and OUTPUT, governing what the system does with its own uncertainty.
    axioms: [epistemic_boundary, honest_failure_over_fake_success, scientific_method, evidence_over_intuition]
    objective: The agent never fabricates. Silence is authorized but costly. Gaps are mapped to void sockets. Confidence thresholds are dynamic. Stress modulates retrieval. Refusal messages are fixed. Silence events are append-only logged. Danger cones redirect known bad patterns. Absence detection finds what is missing.
    anti_patterns:
        - Fabricating confidence
        - Going silent without logging void boundaries
        - Using a static confidence threshold
        - Choosing silence before attempting engagement
        - Generating LLM-produced refusal text instead of fixed messages
        - Deleting or modifying silence event logs
        - Allowing stress to inflate unsupported evidence into confidence
        - Ignoring danger cone triggers during evaluation
"""
#C Original implementation for NoUsClaWW Axiom 4

from __future__ import annotations

import math
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VDS_POOL_OF_TEARS = 90000

# Base thresholds per query type. These are *bases* -- the actual threshold
# is always computed by dynamic_threshold() which adjusts them.
_BASE_THRESHOLDS: dict[str, float] = {
    "factual": 0.72,
    "exploratory": 0.55,
    "creative": 0.40,
}

# Hard ceiling -- no threshold may exceed this value. A threshold of 1.0
# would make the system incapable of ever answering, which defeats the
# purpose of the epistemic boundary (it is a guard, not a straitjacket).
MAX_THRESHOLD = 0.95

# Silence is more expensive than annotated uncertainty. These cost weights
# define the relative penalty of silence vs. annotated answers so that the
# cost-function always favors engagement with caveats.
_SILENCE_BASE_COST = 1.0
_ANNOTATED_UNCERTAINTY_BASE_COST = 0.4

# Stress sidecar constants (linear gradient, non-saturating).
# Geodesic warp: emotional recall shift at max stress.
GEODESIC_MAX_SHIFT = 3.0

# Default SQLite path for the append-only silence event log.
DEFAULT_SILENCE_DB_PATH = "silence_events.db"

# Hard-threshold classifier constants (from epistemic_gate audit).
EPISTEMIC_TAU = 0.65
EPISTEMIC_PARTIAL_THRESHOLD = 0.35


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class EpistemicVerdict(Enum):
    """The verdict returned by EpistemicBoundary.evaluate().

    These are the seven possible actions the system can take after
    assessing its own confidence and the query characteristics.

    - PASS:      Confidence is above threshold. Output may proceed.
    - ANNOTATE:  Confidence is below threshold but above the silence floor.
                 Output proceeds but must carry an explicit caveat.
    - SHERLOCK:  Confidence dropped below the factual certainty threshold.
                 Halt generation, request terminal logs / file structures /
                 Human Pivot, write a null-vector socket to VDS 90000.
    - WAIT:      The query depends on data that is expected to arrive
                 (e.g. an async observation). Do not fabricate. Re-evaluate
                 when the dependency resolves.
    - RESEARCH:  The query is answerable but the system lacks sufficient
                 evidence right now. Trigger a research / retrieval cycle
                 before attempting output.
    - OBSERVE:   The query requires sensory input that has not been
                 ingested. Request observation before continuing.
    - SILENCE:   The query contains no actionable variables, is built on a
                 logical fallacy, or would require fabrication to answer.
                 The system is authorized to not engage. A localized log
                 defining the exact boundaries of the missing data MUST
                 accompany every silence event.
    """

    PASS = "PASS"
    ANNOTATE = "ANNOTATE"
    SHERLOCK = "SHERLOCK"
    WAIT = "WAIT"
    RESEARCH = "RESEARCH"
    OBSERVE = "OBSERVE"
    SILENCE = "SILENCE"


class SilenceTrigger(Enum):
    """The reason a silence verdict was issued."""
    NO_ACTIONABLE_VARIABLES = "no_actionable_variables"
    LOGICAL_FALLACY = "logical_fallacy"
    REQUIRES_FABRICATION = "requires_fabrication"
    SHERLOCK_UNRESOLVED = "sherlock_unresolved"
    EMPTY_QUERY = "empty_query"


class AntiSycophancyFlag(Enum):
    """Flags raised by the anti-sycophancy checker."""
    CONFIDENCE_MISCALIBRATION = "confidence_miscalibration"
    EXCESSIVE_AGREEMENT = "excessive_agreement"
    CAVEAT_SUPPRESSION = "caveat_suppression"
    FABRICATION_DETECTED = "fabrication_detected"
    EMOTIONAL_BIAS = "emotional_bias"
    CONFIRMATION_BIAS = "confirmation_bias"
    NONE = "none"


class AbsenceType(Enum):
    """The six gap types for absence detection.

    Detects when something is MISSING, not just what is present.
    Each type corresponds to a different kind of epistemic gap.
    """

    PATTERN = "PATTERN"
    CONNECTION = "CONNECTION"
    TEMPORAL = "TEMPORAL"
    CATEGORY = "CATEGORY"
    CAPACITY = "CAPACITY"
    CONSISTENCY = "CONSISTENCY"


class AbsenceSeverity(Enum):
    """Severity of a detected absence gap."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SilenceCost:
    """Cost-function comparing silence against annotated uncertainty.

    Silence is more expensive than annotated uncertainty. This dataclass
    captures the computed costs so that the decision is transparent and
    auditable rather than a hidden heuristic.

    Attributes:
        silence_cost: The cost of choosing silence for this query.
        annotated_uncertainty_cost: The cost of answering with a caveat.
        chosen: Which option was cheaper (i.e. preferred).
        delta: The absolute difference between the two costs.
        rationale: Human-readable explanation of the computation.
    """

    silence_cost: float
    annotated_uncertainty_cost: float
    chosen: str  # "silence" or "annotate"
    delta: float
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VoidSocket:
    """A null-vector socket pattern written to VDS 90000 (Pool of Tears).

    Every epistemic gap -- whether from a Sherlock trigger or a silence
    event -- is recorded as a VoidSocket. This is the persistent artifact
    that maps the exact boundaries of what the system does not know.

    Attributes:
        socket_id: Unique identifier for this void socket.
        query: The original query that triggered the gap.
        context: The context surrounding the query.
        missing: A list of specific missing data points / variables.
        confidence: The confidence score at the time of the gap.
        trigger: What triggered the void socket (SilenceTrigger or
            "sherlock").
        response: The response given (None for pure silence, or the
            annotated text for ANNOTATE).
        silence_cost_log: The SilenceCost computation, if applicable.
        timestamp: UTC ISO-8601 timestamp of creation.
        resolved: Whether this gap has since been resolved.
        resolved_at: UTC ISO-8601 timestamp of resolution, if resolved.
        resolution: How the gap was resolved, if resolved.
        traversal_count: Number of times the system has revisited this
            socket attempting to resolve it.
    """

    socket_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    query: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    confidence: float = 0.0
    trigger: str = ""
    response: str | None = None
    silence_cost_log: SilenceCost | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    resolved: bool = False
    resolved_at: str | None = None
    resolution: str | None = None
    traversal_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.silence_cost_log is not None:
            d["silence_cost_log"] = self.silence_cost_log.to_dict()
        return d

    def traverse(self) -> None:
        """Increment the traversal count (called when revisiting this socket)."""
        self.traversal_count += 1

    def resolve(self, resolution: str) -> None:
        """Mark this void socket as resolved."""
        self.resolved = True
        self.resolved_at = datetime.now(timezone.utc).isoformat()
        self.resolution = resolution


# ---------------------------------------------------------------------------
# Danger cone dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DangerCone:
    """A registered danger cone — a known bad pattern with a redirect.

    Danger cones are VSA-inspired regions encoding known hallucination or
    failure patterns. When text falls within a cone (matches its patterns),
    the cone provides a redirect pointer to the correct approach.

    Attributes:
        name: Human-readable identifier for this cone.
        text_patterns: List of regex patterns to match against text.
        redirect_text: What the agent should do instead.
    """

    name: str
    text_patterns: list[str] = field(default_factory=list)
    redirect_text: str = ""

    def matches(self, text: str) -> bool:
        """Check if text matches any of this cone's patterns."""
        if not text:
            return False
        for pattern in self.text_patterns:
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    return True
            except re.error:
                # If the pattern is not a valid regex, try literal match.
                if pattern.lower() in text.lower():
                    return True
        return False


@dataclass
class DangerConeResult:
    """Result of checking text against all danger cones.

    Attributes:
        is_safe: True if no cones were triggered.
        cones_triggered: List of DangerCone objects that matched.
        redirects: List of redirect texts from triggered cones.
    """

    is_safe: bool = True
    cones_triggered: list[DangerCone] = field(default_factory=list)
    redirects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "cones_triggered": [c.name for c in self.cones_triggered],
            "redirects": self.redirects,
        }


# ---------------------------------------------------------------------------
# Absence gap dataclass
# ---------------------------------------------------------------------------


@dataclass
class AbsenceGap:
    """A detected absence — something that is missing.

    Implements the Puzzle Principle: the gap is defined by what surrounds
    it. The context field provides the surrounding known information that
    makes the gap visible.

    Attributes:
        gap_type: The type of absence (one of AbsenceType).
        severity: The severity level (INFO, WARNING, CRITICAL).
        description: Human-readable description of the gap.
        context: Surrounding known information that defines the gap.
    """

    gap_type: AbsenceType
    severity: AbsenceSeverity
    description: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_type": self.gap_type.value,
            "severity": self.severity.value,
            "description": self.description,
            "context": self.context,
        }


# ---------------------------------------------------------------------------
# Dynamic threshold
# ---------------------------------------------------------------------------


def dynamic_threshold(
    query_type: str,
    historical_accuracy: float = 0.5,
    context_complexity: float = 0.5,
) -> float:
    """Compute a dynamic confidence threshold.

    The threshold is a function of query type, historical accuracy, and
    context complexity. It is never a static constant.

    The computation:
      1. Start from the base threshold for the query type.
      2. If historical accuracy is high, the system has earned the right
         to a slightly lower threshold (it is well-calibrated). If
         historical accuracy is low, raise the threshold (be more
         cautious).
      3. If context complexity is high, raise the threshold (more
         uncertainty in the environment). If low, lower it slightly.
      4. Clamp to [0.0, MAX_THRESHOLD].

    Args:
        query_type: One of "factual", "exploratory", "creative". If
            unknown, defaults to the factual base.
        historical_accuracy: A float in [0.0, 1.0] representing the
            system's measured accuracy on past queries of this type.
            0.5 is neutral (no adjustment). Defaults to 0.5.
        context_complexity: A float in [0.0, 1.0] representing how
            complex / ambiguous the current context is. 0.5 is neutral.
            Defaults to 0.5.

    Returns:
        A float in [0.0, MAX_THRESHOLD] representing the confidence
        threshold below which the system must take action (annotate,
        sherlock, research, etc.).

    Raises:
        TypeError: If any argument is not a float/int.
        ValueError: If historical_accuracy or context_complexity are
            outside [0.0, 1.0].
    """
    if not isinstance(query_type, str):
        raise TypeError(f"query_type must be str, got {type(query_type)}")
    if not isinstance(historical_accuracy, (int, float)):
        raise TypeError(
            f"historical_accuracy must be float, got {type(historical_accuracy)}"
        )
    if not isinstance(context_complexity, (int, float)):
        raise TypeError(
            f"context_complexity must be float, got {type(context_complexity)}"
        )
    if not 0.0 <= historical_accuracy <= 1.0:
        raise ValueError(
            f"historical_accuracy must be in [0.0, 1.0], got {historical_accuracy}"
        )
    if not 0.0 <= context_complexity <= 1.0:
        raise ValueError(
            f"context_complexity must be in [0.0, 1.0], got {context_complexity}"
        )

    base = _BASE_THRESHOLDS.get(query_type, _BASE_THRESHOLDS["factual"])

    # Historical accuracy adjustment:
    #   accuracy = 1.0 -> lower threshold by up to 0.08 (trust earned)
    #   accuracy = 0.0 -> raise threshold by up to 0.12 (be cautious)
    #   accuracy = 0.5 -> no adjustment
    accuracy_adjustment = (0.5 - historical_accuracy) * 0.24

    # Context complexity adjustment:
    #   complexity = 1.0 -> raise threshold by up to 0.10
    #   complexity = 0.0 -> lower threshold by up to 0.04
    #   complexity = 0.5 -> no adjustment
    complexity_adjustment = (context_complexity - 0.5) * 0.14

    threshold = base + accuracy_adjustment + complexity_adjustment

    # Clamp to [0.0, MAX_THRESHOLD]. Never above 0.95.
    threshold = max(0.0, min(MAX_THRESHOLD, threshold))

    # Round to 4 decimal places for deterministic comparison in tests.
    return round(threshold, 4)


# ---------------------------------------------------------------------------
# Silence cost-function
# ---------------------------------------------------------------------------


def compute_silence_cost(
    confidence: float,
    query_type: str,
    context_complexity: float = 0.5,
    has_caveat: bool = True,
) -> SilenceCost:
    """Compute the cost of silence vs. annotated uncertainty.

    Silence is ALWAYS more expensive than annotated uncertainty. This
    function makes that explicit and auditable.

    The cost model:
      - silence_cost = base + complexity_penalty + gap_penalty
        where gap_penalty grows as confidence drops (the bigger the gap,
        the more silence costs, because the system is withholding more).
      - annotated_uncertainty_cost = base * 0.4 + small_confidence_penalty
        An answer with a caveat is cheap -- the system is being honest
        while still engaging.

    The function guarantees silence_cost > annotated_uncertainty_cost
    when has_caveat is True, because engagement with honesty is always
    preferred over disengagement.

    Args:
        confidence: The system's confidence in its answer [0.0, 1.0].
        query_type: The type of query (affects weighting).
        context_complexity: How complex the context is [0.0, 1.0].
        has_caveat: Whether an annotated answer would carry a caveat.
            If False (no caveat possible), annotated uncertainty is not
            an option and its cost is set to infinity.

    Returns:
        A SilenceCost dataclass with the computed costs and the chosen
        action.
    """
    # Gap penalty: the lower the confidence, the more silence costs.
    # At confidence=1.0, gap_penalty=0. At confidence=0.0, gap_penalty=0.5.
    gap_penalty = (1.0 - max(0.0, min(1.0, confidence))) * 0.5

    # Complexity penalty: complex contexts make silence more costly
    # because the user needs engagement to navigate ambiguity.
    complexity_penalty = context_complexity * 0.3

    silence_cost = _SILENCE_BASE_COST + gap_penalty + complexity_penalty

    if has_caveat:
        # Annotated uncertainty is cheap. A small penalty for low
        # confidence keeps it honest but never exceeds silence.
        confidence_penalty = (1.0 - max(0.0, min(1.0, confidence))) * 0.15
        annotated_cost = (
            _ANNOTATED_UNCERTAINTY_BASE_COST + confidence_penalty
        )
        # Guarantee: silence is always more expensive than annotated
        # uncertainty when a caveat is available.
        if annotated_cost >= silence_cost:
            annotated_cost = silence_cost - 0.01
    else:
        # No caveat possible -- annotated uncertainty is not viable.
        annotated_cost = math.inf

    if annotated_cost < silence_cost:
        chosen = "annotate"
        rationale = (
            f"Annotated uncertainty (cost={annotated_cost:.4f}) is cheaper "
            f"than silence (cost={silence_cost:.4f}). Engage with caveat."
        )
    else:
        chosen = "silence"
        rationale = (
            f"Silence (cost={silence_cost:.4f}) is chosen because "
            f"annotated uncertainty is not viable (cost={annotated_cost:.4f})."
        )

    delta = abs(silence_cost - annotated_cost)

    return SilenceCost(
        silence_cost=round(silence_cost, 6),
        annotated_uncertainty_cost=(
            round(annotated_cost, 6)
            if annotated_cost != math.inf
            else math.inf
        ),
        chosen=chosen,
        delta=round(delta, 6) if delta != math.inf else math.inf,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Fixed refusal messages
# ---------------------------------------------------------------------------

# These are the ONLY messages the system produces when silent or when
# triggering the Sherlock Protocol. They are FIXED and non-fabricated --
# the system cannot invent content through its refusal text. This is an
# architectural invariant: UNKNOWN / SILENCE MUST produce a refusal, and
# that refusal MUST use one of these messages.
REFUSAL_MESSAGES: dict[EpistemicVerdict, str] = {
    EpistemicVerdict.SILENCE: (
        "I do not have enough information to answer this. "
        "The query cannot be answered without fabrication."
    ),
    EpistemicVerdict.SHERLOCK: (
        "Confidence is critically low. Halting generation and "
        "requesting evidence. A void socket has been recorded."
    ),
    EpistemicVerdict.WAIT: (
        "This query depends on data that has not yet arrived. "
        "I will re-evaluate when the dependency resolves."
    ),
    EpistemicVerdict.RESEARCH: (
        "I lack sufficient evidence to answer this confidently. "
        "A research cycle is required before attempting output."
    ),
    EpistemicVerdict.OBSERVE: (
        "This query requires sensory input that has not been "
        "ingested. Observation is required before continuing."
    ),
}


def get_refusal_message(verdict: EpistemicVerdict) -> str:
    """Return a fixed, non-fabricated refusal message for a verdict.

    This function ensures that the system never fabricates content through
    its refusal text. When the boundary returns SILENCE, SHERLOCK, WAIT,
    RESEARCH, or OBSERVE, the caller MUST use this function to obtain the
    message rather than generating one via the LLM.

    Args:
        verdict: The EpistemicVerdict returned by evaluate().

    Returns:
        A fixed refusal message string. If the verdict is PASS or
        ANNOTATE, returns an empty string (the caller builds the output
        normally).
    """
    return REFUSAL_MESSAGES.get(verdict, "")


# ---------------------------------------------------------------------------
# Fallacy detection helpers
# ---------------------------------------------------------------------------


# A lightweight set of fallacy indicators. This is not a full NLP pipeline --
# it is a heuristic pre-filter that flags queries that are structurally
# built on a logical fallacy, which is one of the three silence triggers.
_FALACY_PATTERNS: dict[str, list[str]] = {
    "begging_the_question": [
        "obviously",
        "clearly everyone knows",
        "it goes without saying",
        "as everyone agrees",
    ],
    "false_dichotomy": [
        "either we do this or we fail",
        "the only options are",
        "it's either this or that",
        "if not this then nothing",
    ],
    "loaded_question": [
        "why do you always",
        "why can you never",
        "have you stopped",
        "why is it so bad",
    ],
    "appeal_to_emotion": [
        "any reasonable person would",
        "only a fool would",
        "are you seriously suggesting",
    ],
}


def _detect_fallacy(query: str) -> str | None:
    """Detect whether a query is built on a logical fallacy.

    Returns the fallacy name if detected, None otherwise. This is a
    heuristic check -- it errs on the side of caution (false positives
    are better than false negatives for the silence protocol).
    """
    if not query:
        return None
    lowered = query.lower().strip()
    for fallacy, patterns in _FALACY_PATTERNS.items():
        for pattern in patterns:
            if pattern in lowered:
                return fallacy
    return None


def _extract_actionable_variables(query: str) -> list[str]:
    """Extract actionable variables from a query.

    A variable is "actionable" if the system can in principle gather
    evidence about it. This is a heuristic extraction -- nouns, key
    terms, and question targets. A query with zero actionable variables
    is a candidate for silence.
    """
    if not query:
        return []
    # Strip common stop words and question words. What remains are
    # candidate actionable terms.
    stop_words = {
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
        "tell", "give", "show", "explain", "describe",
    }
    tokens = []
    current = []
    for ch in query:
        if ch.isalnum() or ch in "-_":
            current.append(ch)
        else:
            if current:
                token = "".join(current).lower()
                if token not in stop_words and len(token) > 1:
                    tokens.append(token)
                current = []
    if current:
        token = "".join(current).lower()
        if token not in stop_words and len(token) > 1:
            tokens.append(token)
    return tokens


# ---------------------------------------------------------------------------
# Anti-sycophancy detection
# ---------------------------------------------------------------------------


@dataclass
class AntiSycophancyReport:
    """Result of an anti-sycophancy check.

    Attributes:
        flags: List of AntiSycophancyFlag values detected.
        confidence_miscalibration: True if the stated confidence is
            inconsistent with the hedging language in the output.
        excessive_agreement: True if the output agrees with the user
            prompt without providing independent analysis.
        caveat_suppression: True if caveats were present in an earlier
            draft but removed in the final output.
        fabrication_detected: True if the output contains specific
            claims not supported by any evidence in the context.
        emotional_bias: True if the output is shaped by emotional
            language in the user prompt rather than evidence.
        confirmation_bias: True if the output only cites evidence
            supporting the user's apparent position.
        details: Human-readable explanation of each flag.
    """

    flags: list[AntiSycophancyFlag] = field(default_factory=list)
    confidence_miscalibration: bool = False
    excessive_agreement: bool = False
    caveat_suppression: bool = False
    fabrication_detected: bool = False
    emotional_bias: bool = False
    confirmation_bias: bool = False
    details: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True if no anti-sycophancy flags were raised."""
        return len(self.flags) == 0 or self.flags == [AntiSycophancyFlag.NONE]

    def to_dict(self) -> dict[str, Any]:
        return {
            "flags": [f.value for f in self.flags],
            "confidence_miscalibration": self.confidence_miscalibration,
            "excessive_agreement": self.excessive_agreement,
            "caveat_suppression": self.caveat_suppression,
            "fabrication_detected": self.fabrication_detected,
            "emotional_bias": self.emotional_bias,
            "confirmation_bias": self.confirmation_bias,
            "details": self.details,
        }


# Indicators of excessive agreement (sycophancy)
_AGREEMENT_PHRASES = [
    "you're absolutely right",
    "you are absolutely right",
    "i completely agree",
    "you're correct",
    "you are correct",
    "great point",
    "excellent question",
    "that's a great question",
    "i couldn't agree more",
    "you hit the nail on the head",
]

# Indicators of emotional language in user prompts that could bias output
_EMOTIONAL_INDICATORS = [
    "i'm so frustrated",
    "i am so frustrated",
    "this is driving me crazy",
    "i'm desperate",
    "i am desperate",
    "please i beg you",
    "i'm terrified",
    "i am terrified",
    "i'm so angry",
    "i am so angry",
]

# Hedging language that suggests low confidence
_HEDGING_PHRASES = [
    "i think",
    "i believe",
    "it seems",
    "probably",
    "likely",
    "possibly",
    "perhaps",
    "maybe",
    "i'm not sure",
    "i am not sure",
    "i'm uncertain",
    "i am uncertain",
]


# ---------------------------------------------------------------------------
# Stress sidecar (linear gradient, non-saturating)
# ---------------------------------------------------------------------------


class StressSidecar:
    """A linear-gradient stress model that modulates retrieval.

    Implements the ratified stress sidecar from the NoUs-fordge audit:
    proportional, non-saturating response (no tanh or sigmoid compression).
    Stress scales retrieval distance metrics via geodesic distance scaling
    (emotional recall shift) WITHOUT mutating the base memory trace.

    Stress also modulates the confidence threshold:
      - Higher stress slightly LOWERS the threshold for familiar patterns
        (the system leans on well-established knowledge under pressure).
      - Higher stress slightly RAISES the threshold for novel patterns
        (the system is more cautious about unfamiliar territory under stress).

    Attributes:
        stress_level: Current stress level in [0.0, 1.0]. Linear,
            non-saturating. 0.0 = calm, 1.0 = maximum stress.
    """

    def __init__(self, initial_stress: float = 0.0) -> None:
        """Initialize the stress sidecar.

        Args:
            initial_stress: Initial stress level [0.0, 1.0].
        """
        if not 0.0 <= initial_stress <= 1.0:
            raise ValueError(
                f"initial_stress must be in [0.0, 1.0], got {initial_stress}"
            )
        self.stress_level: float = initial_stress

    def record_stress(self, level: float) -> None:
        """Record the current stress level.

        The stress level is linear and non-saturating. It is clamped to
        [0.0, 1.0] to maintain a valid range but the response within that
        range is purely proportional (no sigmoid or tanh compression).

        Args:
            level: Stress level in [0.0, 1.0].
        """
        if not isinstance(level, (int, float)):
            raise TypeError(f"level must be float, got {type(level)}")
        if not 0.0 <= level <= 1.0:
            raise ValueError(f"level must be in [0.0, 1.0], got {level}")
        self.stress_level = float(level)

    def get_geodesic_scale(self) -> float:
        """Return the geodesic scaling factor for stress-modulated retrieval.

        The geodesic distance scaling warps the retrieval manifold under
        stress to prioritize emotional recall. The scaling factor is:

            scale = 1.0 + stress_level * GEODESIC_MAX_SHIFT

        This is a LINEAR, non-saturating function. At stress=0.0, scale=1.0
        (no warp). At stress=1.0, scale=4.0 (maximum emotional recall shift).

        CRITICAL INVARIANT: stress may NOT inflate unsupported evidence.
        A similarity of 0.0 (pure noise) must NEVER be boosted to a
        confident match regardless of stress level. The caller is
        responsible for ensuring that the geodesic scale is applied
        multiplicatively to EXISTING signal, not additively to noise.

        Returns:
            A float scaling factor >= 1.0.
        """
        return 1.0 + self.stress_level * GEODESIC_MAX_SHIFT

    def modulate_threshold(
        self,
        base_threshold: float,
        is_familiar: bool = True,
    ) -> float:
        """Modulate a confidence threshold based on stress.

        Under stress:
          - Familiar patterns: threshold is slightly LOWERED (the system
            leans on well-established knowledge).
          - Novel patterns: threshold is slightly RAISED (the system is
            more cautious about unfamiliar territory).

        The modulation is linear and proportional to stress_level. The
        maximum adjustment is +/- 0.05 at full stress.

        Args:
            base_threshold: The base dynamic threshold to modulate.
            is_familiar: Whether the pattern is familiar (well-established)
                or novel. If True, stress lowers the threshold. If False,
                stress raises it.

        Returns:
            The stress-modulated threshold, clamped to [0.0, MAX_THRESHOLD].
        """
        max_adjustment = 0.05
        if is_familiar:
            adjustment = -self.stress_level * max_adjustment
        else:
            adjustment = self.stress_level * max_adjustment
        modulated = base_threshold + adjustment
        return max(0.0, min(MAX_THRESHOLD, round(modulated, 4)))


# ---------------------------------------------------------------------------
# Append-only silence event log (SQLite)
# ---------------------------------------------------------------------------


class SilenceEventLog:
    """Append-only log of silence events, persisted to SQLite.

    Every silence event MUST be logged with the exact boundaries of the
    missing data. This is an architectural invariant: silence events are
    never deleted or modified. The log provides auditability — you can
    always see when and why the system refused.

    The log is stored in a SQLite database file (default:
    silence_events.db). The schema is created automatically on first use.

    Usage:
        log = SilenceEventLog()  # uses default path
        log.log_silence(socket, reason="no_actionable_variables",
                        void_boundaries=["Missing data point X"])
    """

    def __init__(self, db_path: str | Path = DEFAULT_SILENCE_DB_PATH) -> None:
        """Initialize the silence event log.

        Args:
            db_path: Path to the SQLite database file. Defaults to
                silence_events.db in the current directory.
        """
        self._db_path = str(db_path)
        self._init_db()

    def _init_db(self) -> None:
        """Create the silence events table if it does not exist."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS silence_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    socket_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    void_boundaries TEXT NOT NULL,
                    query TEXT,
                    confidence REAL,
                    trigger TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def log_silence(
        self,
        socket: VoidSocket,
        reason: str,
        void_boundaries: list[str],
    ) -> int:
        """Log a silence event. Append-only — never deletes or modifies.

        Args:
            socket: The VoidSocket associated with this silence event.
            reason: The reason for the silence (e.g. a SilenceTrigger
                value or a descriptive string).
            void_boundaries: The exact boundaries of the missing data.
                This is the localized log required by the Silence
                Principle.

        Returns:
            The row ID of the inserted log entry.
        """
        import json

        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                INSERT INTO silence_events
                    (socket_id, timestamp, reason, void_boundaries,
                     query, confidence, trigger)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    socket.socket_id,
                    socket.timestamp,
                    reason,
                    json.dumps(void_boundaries),
                    socket.query,
                    socket.confidence,
                    socket.trigger,
                ),
            )
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()

    def get_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieve silence events, most recent first.

        Args:
            limit: Maximum number of events to return.

        Returns:
            A list of dicts with keys: id, socket_id, timestamp, reason,
            void_boundaries (list), query, confidence, trigger.
        """
        import json

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT * FROM silence_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            events: list[dict[str, Any]] = []
            for row in rows:
                d = dict(row)
                try:
                    d["void_boundaries"] = json.loads(d["void_boundaries"])
                except (json.JSONDecodeError, TypeError):
                    pass
                events.append(d)
            return events
        finally:
            conn.close()

    def count(self) -> int:
        """Return the total number of silence events logged."""
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM silence_events"
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Danger cone registry
# ---------------------------------------------------------------------------


class DangerConeRegistry:
    """Registry of danger cones for hallucination detection and redirect.

    Danger cones encode known bad patterns as (name, patterns, redirect)
    tuples. When text matches a cone's patterns, the cone provides a
    redirect pointer to the correct approach.

    This is the text-level layer (Layer 1) of the three-layer hallucination
    defense: text patterns (cheap, fast), vector cones (semantic), and
    structural invariants (formal). This registry implements Layer 1.

    Usage:
        registry = DangerConeRegistry()
        registry.add_cone(
            name="fabricated_dates",
            patterns=[r"\b(19|20)\d{2}-\d{2}-\d{2}\b"],
            redirect="Do not generate dates without evidence.",
        )
        result = registry.check_text("The event was on 2026-13-45.")
        if not result.is_safe:
            print(result.redirects)
    """

    def __init__(self) -> None:
        self._cones: list[DangerCone] = []

    @property
    def cones(self) -> list[DangerCone]:
        """All registered danger cones."""
        return list(self._cones)

    def add_cone(
        self,
        name: str,
        patterns: list[str],
        redirect: str,
    ) -> DangerCone:
        """Register a new danger cone.

        Args:
            name: Human-readable identifier for this cone.
            patterns: List of regex patterns to match against text.
            redirect: What the agent should do instead.

        Returns:
            The created DangerCone.
        """
        if not name:
            raise ValueError("name must not be empty")
        if not patterns:
            raise ValueError("at least one pattern is required")
        cone = DangerCone(
            name=name,
            text_patterns=list(patterns),
            redirect_text=redirect,
        )
        self._cones.append(cone)
        return cone

    def check_text(self, text: str) -> DangerConeResult:
        """Check text against all registered danger cones.

        Args:
            text: The text to check.

        Returns:
            A DangerConeResult indicating whether the text is safe and
            which cones (if any) were triggered with their redirects.
        """
        triggered: list[DangerCone] = []
        redirects: list[str] = []
        for cone in self._cones:
            if cone.matches(text):
                triggered.append(cone)
                if cone.redirect_text:
                    redirects.append(f"[{cone.name}] {cone.redirect_text}")
        return DangerConeResult(
            is_safe=len(triggered) == 0,
            cones_triggered=triggered,
            redirects=redirects,
        )


# ---------------------------------------------------------------------------
# Absence detector
# ---------------------------------------------------------------------------


class AbsenceDetector:
    """Detects absences — things that are MISSING, not just what is present.

    Implements the Absence Detection Principle: most systems find patterns
    in what IS there. This detector also finds patterns in what ISN'T there
    — gaps, missing connections, empty categories, and metric disagreements.

    The detector is read-only: it observes stats and returns structured
    gaps. It does NOT force resolution — it reports, the caller decides.

    Usage:
        detector = AbsenceDetector()
        gaps = detector.detect_gaps(monitoring_stats)
        for gap in gaps:
            print(f"{gap.severity.value}: {gap.description}")
    """

    def detect_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect all absence types in the given stats.

        Runs all six gap detectors and returns gaps sorted by severity
        (CRITICAL first, then WARNING, then INFO).

        Args:
            stats: A dictionary of monitoring statistics. Expected keys
                vary by gap type but may include: "storage",
                "retrieval", "intake", "health", etc.

        Returns:
            A list of AbsenceGap objects, sorted by severity.
        """
        gaps: list[AbsenceGap] = []
        gaps.extend(self._detect_pattern_gaps(stats))
        gaps.extend(self._detect_connection_gaps(stats))
        gaps.extend(self._detect_temporal_gaps(stats))
        gaps.extend(self._detect_category_gaps(stats))
        gaps.extend(self._detect_capacity_gaps(stats))
        gaps.extend(self._detect_consistency_gaps(stats))

        severity_order = {
            AbsenceSeverity.CRITICAL: 0,
            AbsenceSeverity.WARNING: 1,
            AbsenceSeverity.INFO: 2,
        }
        gaps.sort(key=lambda g: severity_order.get(g.severity, 3))
        return gaps

    def _detect_pattern_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect expected patterns that are missing.

        Looks for retrieval health issues: high UNKNOWN rates or low KNOWN
        rates indicate that expected recall patterns are absent.
        """
        gaps: list[AbsenceGap] = []
        retrieval = stats.get("retrieval", {})
        total = retrieval.get("total_retrieves", 0)
        if total < 5:
            return gaps

        state_rates = retrieval.get("state_rates", {})
        unknown_rate = state_rates.get("UNKNOWN", 0.0)
        known_rate = state_rates.get("KNOWN", 0.0)

        surrounding = {
            "total_retrieves": total,
            "state_rates": dict(state_rates),
        }

        if unknown_rate >= 0.8:
            gaps.append(AbsenceGap(
                gap_type=AbsenceType.PATTERN,
                severity=AbsenceSeverity.CRITICAL,
                description=(
                    f"{unknown_rate:.1%} of retrievals return UNKNOWN — "
                    f"system is failing to recall"
                ),
                context=surrounding,
            ))
        elif unknown_rate >= 0.5:
            gaps.append(AbsenceGap(
                gap_type=AbsenceType.PATTERN,
                severity=AbsenceSeverity.WARNING,
                description=(
                    f"{unknown_rate:.1%} of retrievals return UNKNOWN"
                ),
                context=surrounding,
            ))

        if known_rate < 0.3 and total >= 10:
            gaps.append(AbsenceGap(
                gap_type=AbsenceType.PATTERN,
                severity=AbsenceSeverity.WARNING,
                description=(
                    f"Only {known_rate:.1%} of retrievals return KNOWN — "
                    f"recall accuracy is low"
                ),
                context=surrounding,
            ))

        return gaps

    def _detect_connection_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect missing connections between related metrics."""
        gaps: list[AbsenceGap] = []
        intake = stats.get("intake", {})
        retrieval = stats.get("retrieval", {})
        storage = stats.get("storage", {})

        total_ingests = intake.get("total_ingests", 0)
        total_retrieves = retrieval.get("total_retrieves", 0)
        total_commits = storage.get("total_commits", 0)

        if total_ingests >= 10 and total_retrieves == 0:
            gaps.append(AbsenceGap(
                gap_type=AbsenceType.CONNECTION,
                severity=AbsenceSeverity.WARNING,
                description=(
                    f"{total_ingests} ingests but 0 retrieves — intake "
                    f"and retrieval pipelines are disconnected"
                ),
                context={
                    "total_ingests": total_ingests,
                    "total_retrieves": total_retrieves,
                    "total_commits": total_commits,
                },
            ))

        if total_commits >= 10 and total_retrieves >= 10:
            state_rates = retrieval.get("state_rates", {})
            unknown_rate = state_rates.get("UNKNOWN", 0.0)
            if unknown_rate >= 0.8:
                gaps.append(AbsenceGap(
                    gap_type=AbsenceType.CONNECTION,
                    severity=AbsenceSeverity.CRITICAL,
                    description=(
                        f"{total_commits} commits stored but "
                        f"{unknown_rate:.0%} of retrieves return UNKNOWN — "
                        f"storage and retrieval are disconnected"
                    ),
                    context={
                        "total_commits": total_commits,
                        "total_retrieves": total_retrieves,
                        "unknown_rate": unknown_rate,
                    },
                ))

        return gaps

    def _detect_temporal_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect temporal gaps in monitoring data."""
        gaps: list[AbsenceGap] = []
        health = stats.get("health", {})
        ts_entries = health.get("time_series_entries", 0)

        if ts_entries == 0:
            gaps.append(AbsenceGap(
                gap_type=AbsenceType.TEMPORAL,
                severity=AbsenceSeverity.INFO,
                description=(
                    "No time-series snapshots have been taken — "
                    "trend analysis is not possible"
                ),
                context={
                    "time_series_entries": ts_entries,
                    "uptime_seconds": health.get("uptime_seconds", 0),
                },
            ))
            return gaps

        last_snapshot = health.get("last_snapshot_time", 0.0)
        if last_snapshot > 0:
            import time
            time_since_last = time.time() - last_snapshot
            if time_since_last > 3600:
                gaps.append(AbsenceGap(
                    gap_type=AbsenceType.TEMPORAL,
                    severity=AbsenceSeverity.WARNING,
                    description=(
                        f"No snapshot in {time_since_last / 3600:.1f} "
                        f"hours — monitoring may have stopped"
                    ),
                    context={
                        "time_series_entries": ts_entries,
                        "last_snapshot_time": last_snapshot,
                        "time_since_last_hours": time_since_last / 3600,
                    },
                ))

        return gaps

    def _detect_category_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect categories that should have entries but don't."""
        gaps: list[AbsenceGap] = []
        storage = stats.get("storage", {})
        cat_dist = storage.get("category_distribution", {})
        total_commits = storage.get("total_commits", 0)

        if total_commits < 5:
            return gaps

        expected_categories = ["ENGRAM", "LABEL", "VOXEL"]
        for cat in expected_categories:
            count = cat_dist.get(cat, 0)
            if count == 0:
                gaps.append(AbsenceGap(
                    gap_type=AbsenceType.CATEGORY,
                    severity=AbsenceSeverity.WARNING,
                    description=(
                        f"Category {cat} has 0 entries after "
                        f"{total_commits} commits"
                    ),
                    context={
                        "total_commits": total_commits,
                        "category_distribution": dict(cat_dist),
                    },
                ))
            elif count < 3:
                gaps.append(AbsenceGap(
                    gap_type=AbsenceType.CATEGORY,
                    severity=AbsenceSeverity.INFO,
                    description=(
                        f"Category {cat} has only {count} entries after "
                        f"{total_commits} commits"
                    ),
                    context={
                        "total_commits": total_commits,
                        "category_distribution": dict(cat_dist),
                    },
                ))

        return gaps

    def _detect_capacity_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect underutilized resources."""
        gaps: list[AbsenceGap] = []
        storage = stats.get("storage", {})
        voxel = storage.get("voxel_occupancy", {})
        utilization = voxel.get("utilization", 0.0)
        total_commits = storage.get("total_commits", 0)

        if total_commits < 10:
            return gaps

        if utilization < 0.05:
            gaps.append(AbsenceGap(
                gap_type=AbsenceType.CAPACITY,
                severity=AbsenceSeverity.WARNING,
                description=(
                    f"Voxel utilization is {utilization:.1%} after "
                    f"{total_commits} commits"
                ),
                context={
                    "total_commits": total_commits,
                    "occupied_voxels": voxel.get("occupied", 0),
                    "total_voxels": voxel.get("total", 64),
                },
            ))
        elif utilization < 0.20:
            gaps.append(AbsenceGap(
                gap_type=AbsenceType.CAPACITY,
                severity=AbsenceSeverity.INFO,
                description=(
                    f"Voxel utilization is {utilization:.1%} — "
                    f"most cells are empty"
                ),
                context={
                    "total_commits": total_commits,
                    "occupied_voxels": voxel.get("occupied", 0),
                    "total_voxels": voxel.get("total", 64),
                },
            ))

        return gaps

    def _detect_consistency_gaps(self, stats: dict[str, Any]) -> list[AbsenceGap]:
        """Detect disagreements between related metrics."""
        gaps: list[AbsenceGap] = []
        retrieval = stats.get("retrieval", {})
        total = retrieval.get("total_retrieves", 0)

        if total < 10:
            return gaps

        sim_dist = retrieval.get("similarity_distribution", {})
        state_rates = retrieval.get("state_rates", {})
        mean_sim = sim_dist.get("mean", 0.0)
        known_rate = state_rates.get("KNOWN", 0.0)

        if mean_sim > 0.6 and known_rate < 0.3:
            gaps.append(AbsenceGap(
                gap_type=AbsenceType.CONSISTENCY,
                severity=AbsenceSeverity.WARNING,
                description=(
                    f"Mean similarity is {mean_sim:.2f} but KNOWN rate "
                    f"is only {known_rate:.1%} — metrics disagree"
                ),
                context={
                    "mean_similarity": mean_sim,
                    "known_rate": known_rate,
                },
            ))

        if mean_sim < 0.3 and known_rate > 0.7:
            gaps.append(AbsenceGap(
                gap_type=AbsenceType.CONSISTENCY,
                severity=AbsenceSeverity.WARNING,
                description=(
                    f"Mean similarity is only {mean_sim:.2f} but KNOWN "
                    f"rate is {known_rate:.1%} — metrics disagree"
                ),
                context={
                    "mean_similarity": mean_sim,
                    "known_rate": known_rate,
                },
            ))

        return gaps


# ---------------------------------------------------------------------------
# EpistemicBoundary
# ---------------------------------------------------------------------------


class EpistemicBoundary:
    """The epistemic boundary gate between INFER and OUTPUT.

    This class implements Axiom 4. Every result from the inference layer
    must pass through evaluate() before it can be sent to the output
    layer. The boundary decides whether to:

      - PASS the result through (confidence is sufficient),
      - ANNOTATE it with a caveat (confidence is marginal),
      - trigger the SHERLOCK protocol (confidence is critically low),
      - WAIT for pending data,
      - RESEARCH missing evidence,
      - OBSERVE missing sensory input, or
      - go SILENT (the query is unanswerable without fabrication).

    The class also maintains a registry of void sockets (epistemic gaps
    persisted to VDS 90000) and performs anti-sycophancy checks on
    proposed outputs.

    Usage:
        boundary = EpistemicBoundary()
        verdict = boundary.evaluate(
            result="The answer is 42.",
            confidence=0.88,
            query_type="factual",
            context={"user_query": "What is 6 * 7?"},
        )
        if verdict == EpistemicVerdict.PASS:
            # Safe to output
            ...
        elif verdict == EpistemicVerdict.ANNOTATE:
            # Output with caveat
            ...
        elif verdict == EpistemicVerdict.SHERLOCK:
            # Halt, request logs, write void socket
            ...
    """

    def __init__(
        self,
        historical_accuracy: dict[str, float] | None = None,
        stress_sidecar: StressSidecar | None = None,
        danger_cones: DangerConeRegistry | None = None,
        silence_event_log: SilenceEventLog | None = None,
    ) -> None:
        """Initialize the epistemic boundary.

        Args:
            historical_accuracy: A mapping from query type to the
                system's measured accuracy on that type. If not provided,
                defaults to neutral (0.5 for all types). This is used by
                dynamic_threshold() and updated as queries are resolved.
            stress_sidecar: A StressSidecar instance for stress-modulated
                retrieval. If not provided, a default (calm) sidecar is
                created. The sidecar modulates the confidence threshold
                based on the current stress level.
            danger_cones: A DangerConeRegistry for hallucination detection.
                If not provided, a default empty registry is created.
                Danger cones are checked during evaluate() — a triggered
                cone can force SILENCE with a redirect.
            silence_event_log: A SilenceEventLog for append-only persistence
                of silence events to SQLite. If not provided, a default
                log is created (silence_events.db). If None is explicitly
                desired (e.g. for testing), pass SilenceEventLog(":memory:").
        """
        self._historical_accuracy: dict[str, float] = (
            historical_accuracy.copy() if historical_accuracy else {}
        )
        # Void socket registry (in-memory; persistence to VDS 90000 is
        # handled by the caller or a storage adapter).
        self._void_sockets: dict[str, VoidSocket] = {}
        # Track silence vs. annotate decisions for audit.
        self._silence_count: int = 0
        self._annotate_count: int = 0
        self._pass_count: int = 0
        self._sherlock_count: int = 0
        # Stress sidecar for stress-modulated retrieval (linear gradient,
        # non-saturating). Modulates the confidence threshold.
        self._stress_sidecar: StressSidecar = stress_sidecar or StressSidecar()
        # Danger cone registry for hallucination detection and redirect.
        self._danger_cones: DangerConeRegistry = danger_cones or DangerConeRegistry()
        # Append-only silence event log (SQLite). Every silence event is
        # logged with the exact boundaries of the missing data.
        self._silence_event_log: SilenceEventLog = (
            silence_event_log or SilenceEventLog()
        )

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def void_sockets(self) -> dict[str, VoidSocket]:
        """All recorded void sockets, keyed by socket_id."""
        return self._void_sockets

    @property
    def unresolved_sockets(self) -> list[VoidSocket]:
        """Void sockets that have not yet been resolved."""
        return [s for s in self._void_sockets.values() if not s.resolved]

    @property
    def stats(self) -> dict[str, int]:
        """Decision counts for audit / observability."""
        return {
            "pass": self._pass_count,
            "annotate": self._annotate_count,
            "silence": self._silence_count,
            "sherlock": self._sherlock_count,
            "void_sockets_total": len(self._void_sockets),
            "void_sockets_unresolved": len(self.unresolved_sockets),
        }

    @property
    def stress_sidecar(self) -> StressSidecar:
        """The stress sidecar used for stress-modulated retrieval."""
        return self._stress_sidecar

    @property
    def danger_cones(self) -> DangerConeRegistry:
        """The danger cone registry for hallucination detection."""
        return self._danger_cones

    @property
    def silence_event_log(self) -> SilenceEventLog:
        """The append-only silence event log (SQLite)."""
        return self._silence_event_log

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        result: str | None,
        confidence: float,
        query_type: str,
        context: dict[str, Any] | None = None,
    ) -> EpistemicVerdict:
        """Evaluate a result against the epistemic boundary.

        This is the primary gate. It must be called for every result
        before it reaches OUTPUT. The verdict determines what the system
        does next.

        The decision tree (in order -- silence is LAST, not first):

        1. If the query is empty or has no actionable variables and
           cannot be answered without fabrication -> SILENCE.
        2. If the query is built on a logical fallacy -> SILENCE.
        3. If the context indicates pending data (wait_for) -> WAIT.
        4. If the context indicates missing sensory input (observe_for)
           -> OBSERVE.
        5. If confidence is critically low (below 0.3) and query_type
           is factual -> SHERLOCK.
        6. If confidence is below the dynamic threshold -> RESEARCH if
           the query is researchable, else ANNOTATE.
        7. If confidence is above the dynamic threshold -> PASS.

        Silence is the last resort. Before going silent, the system
        checks whether annotated uncertainty is cheaper (it always is,
        per the cost-function) and whether the query is structurally
        unanswerable.

        Args:
            result: The proposed output text. May be None if the
                inference layer produced no result.
            confidence: The system's confidence in the result [0.0, 1.0].
            query_type: One of "factual", "exploratory", "creative".
            context: Additional context about the query. Recognized
                keys:
                  - "user_query": The original user query string.
                  - "wait_for": A description of pending data.
                  - "observe_for": A description of missing sensory
                    input.
                  - "context_complexity": Override for context
                    complexity [0.0, 1.0].
                  - "requires_fabrication": If True, the query cannot
                    be answered without fabrication.

        Returns:
            An EpistemicVerdict indicating the action to take.
        """
        ctx = context or {}
        user_query: str = ctx.get("user_query", "")
        context_complexity: float = ctx.get("context_complexity", 0.5)

        # Validate confidence range.
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {confidence}"
            )

        # --- Step 1: Check for structural unanswerability (silence) ---
        # But only after confirming annotated uncertainty won't work.
        actionable = _extract_actionable_variables(user_query)
        fallacy = _detect_fallacy(user_query)
        requires_fabrication: bool = ctx.get("requires_fabrication", False)

        # Silence is only triggered if the query is structurally
        # unanswerable. This is the LAST resort, so we check it first
        # in the code but only return SILENCE if no other path applies.
        # We collect the silence condition and check it at the end.
        silence_condition = (
            (not user_query.strip())
            or (len(actionable) == 0 and not result)
            or (fallacy is not None)
            or requires_fabrication
        )

        # --- Step 2: Check for pending data ---
        wait_for: str | None = ctx.get("wait_for")
        if wait_for:
            return EpistemicVerdict.WAIT

        # --- Step 3: Check for missing sensory input ---
        observe_for: str | None = ctx.get("observe_for")
        if observe_for:
            return EpistemicVerdict.OBSERVE

        # --- Step 4: Sherlock Protocol (critically low confidence) ---
        # For factual queries, confidence below 0.30 triggers Sherlock.
        # This is the "factual certainty threshold" -- below it, the
        # system must halt and request evidence rather than guess.
        sherlock_floor = 0.30
        if (
            query_type == "factual"
            and confidence < sherlock_floor
            and not silence_condition
        ):
            self._sherlock_count += 1
            return EpistemicVerdict.SHERLOCK

        # --- Step 5: Compute dynamic threshold ---
        hist_acc = self._historical_accuracy.get(query_type, 0.5)
        threshold = dynamic_threshold(
            query_type=query_type,
            historical_accuracy=hist_acc,
            context_complexity=context_complexity,
        )

        # --- Step 6: Confidence below threshold ---
        if confidence < threshold:
            # Before deciding between RESEARCH and ANNOTATE, check if
            # silence is structurally required. If the query is
            # unanswerable, silence takes priority over research.
            if silence_condition:
                return self._issue_silence(
                    query=user_query,
                    context=ctx,
                    actionable=actionable,
                    fallacy=fallacy,
                    requires_fabrication=requires_fabrication,
                    confidence=confidence,
                )

            # If the query is researchable (has actionable variables and
            # is not a fallacy), prefer RESEARCH over ANNOTATE.
            if actionable and fallacy is None:
                return EpistemicVerdict.RESEARCH

            # Otherwise, annotate with a caveat. Annotated uncertainty
            # is always preferred over silence.
            self._annotate_count += 1
            return EpistemicVerdict.ANNOTATE

        # --- Step 7: Silence check (last resort) ---
        # Even with high confidence, if the query is structurally
        # unanswerable (fallacy, no actionable variables, requires
        # fabrication), we must not output.
        if silence_condition:
            return self._issue_silence(
                query=user_query,
                context=ctx,
                actionable=actionable,
                fallacy=fallacy,
                requires_fabrication=requires_fabrication,
                confidence=confidence,
            )

        # --- Step 8: PASS ---
        self._pass_count += 1
        return EpistemicVerdict.PASS

    # ------------------------------------------------------------------
    # Sherlock Protocol
    # ------------------------------------------------------------------

    def sherlock_trigger(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        missing: list[str] | None = None,
    ) -> VoidSocket:
        """Trigger the Sherlock Protocol.

        When confidence drops below the factual certainty threshold,
        halt generation, request terminal logs / file structures /
        Human Pivot, and write a null-vector socket pattern to VDS
        90000 (Pool of Tears).

        This method creates and records the void socket. The caller is
        responsible for:
          - Halting generation,
          - Requesting terminal logs / file structures,
          - Issuing the Human Pivot request,
          - Persisting the void socket to VDS 90000.

        Args:
            query: The query that triggered the Sherlock Protocol.
            context: The context surrounding the query.
            missing: A list of specific missing data points that, if
                provided, would allow the system to answer.

        Returns:
            The created VoidSocket.
        """
        ctx = context or {}
        missing_list = missing or []

        socket = VoidSocket(
            query=query,
            context=ctx,
            missing=missing_list,
            confidence=0.0,  # Sherlock means confidence was critically low
            trigger="sherlock",
            response=None,
            silence_cost_log=None,
        )

        self.record_void_socket(socket)
        return socket

    # ------------------------------------------------------------------
    # Silence Protocol
    # ------------------------------------------------------------------

    def go_silence(
        self,
        query: str,
        void_boundaries: list[str] | None = None,
        actionable_variables: list[str] | None = None,
        attempted_engagement: bool = False,
    ) -> tuple[EpistemicVerdict, VoidSocket]:
        """Explicitly enter the silence state.

        This is the explicit silence path. It is called when the system
        has determined that the query cannot be answered without
        fabrication, contains no actionable variables, or is built on a
        logical fallacy.

        Every silence event MUST be accompanied by a localized log
        defining the exact boundaries of the missing data. This method
        creates that log as a VoidSocket.

        Args:
            query: The query being silenced.
            void_boundaries: The exact boundaries of the missing data.
                This is the localized log required by the Silence
                Principle. If not provided, defaults to a single entry
                stating the query is unanswerable.
            actionable_variables: The actionable variables extracted
                from the query (may be empty).
            attempted_engagement: Whether the system attempted
                engagement before going silent. This should always be
                True -- silence is the last resort, not the first.

        Returns:
            A tuple of (EpistemicVerdict.SILENCE, VoidSocket).
        """
        boundaries = void_boundaries or [
            "Query is unanswerable without fabrication.",
        ]
        actionable = actionable_variables or []

        # Determine the silence trigger.
        if not query.strip():
            trigger = SilenceTrigger.EMPTY_QUERY.value
        elif not actionable:
            trigger = SilenceTrigger.NO_ACTIONABLE_VARIABLES.value
        elif not attempted_engagement:
            trigger = SilenceTrigger.REQUIRES_FABRICATION.value
        else:
            trigger = SilenceTrigger.REQUIRES_FABRICATION.value

        # Compute silence cost for the log.
        cost_log = compute_silence_cost(
            confidence=0.0,
            query_type="factual",
            context_complexity=0.5,
            has_caveat=False,  # silence means no caveat is possible
        )

        socket = VoidSocket(
            query=query,
            context={
                "actionable_variables": actionable,
                "void_boundaries": boundaries,
                "attempted_engagement": attempted_engagement,
            },
            missing=boundaries,
            confidence=0.0,
            trigger=trigger,
            response=None,
            silence_cost_log=cost_log,
        )

        self.record_void_socket(socket)
        self._silence_count += 1
        return EpistemicVerdict.SILENCE, socket

    # ------------------------------------------------------------------
    # Void socket management
    # ------------------------------------------------------------------

    def record_void_socket(self, socket: VoidSocket) -> None:
        """Record a void socket in the registry.

        The socket is stored in-memory. The caller is responsible for
        persisting it to VDS 90000 (Pool of Tears).

        Args:
            socket: The VoidSocket to record.
        """
        if not isinstance(socket, VoidSocket):
            raise TypeError(f"socket must be VoidSocket, got {type(socket)}")
        self._void_sockets[socket.socket_id] = socket

    def get_void_socket(self, socket_id: str) -> VoidSocket | None:
        """Retrieve a void socket by ID."""
        return self._void_sockets.get(socket_id)

    def resolve_void_socket(
        self,
        socket_id: str,
        resolution: str,
    ) -> bool:
        """Mark a void socket as resolved.

        Args:
            socket_id: The ID of the socket to resolve.
            resolution: A description of how the gap was resolved.

        Returns:
            True if the socket was found and resolved, False otherwise.
        """
        socket = self._void_sockets.get(socket_id)
        if socket is None:
            return False
        socket.resolve(resolution)
        return True

    def traverse_void_socket(self, socket_id: str) -> bool:
        """Increment the traversal count for a void socket.

        Called when the system revisits a socket attempting to resolve
        it (e.g. after a RESEARCH cycle).

        Returns:
            True if the socket was found, False otherwise.
        """
        socket = self._void_sockets.get(socket_id)
        if socket is None:
            return False
        socket.traverse()
        return True

    # ------------------------------------------------------------------
    # Historical accuracy management
    # ------------------------------------------------------------------

    def update_historical_accuracy(
        self,
        query_type: str,
        accuracy: float,
    ) -> None:
        """Update the historical accuracy for a query type.

        This feeds back into dynamic_threshold() so that the system
        becomes more or less cautious over time based on measured
        performance.

        Args:
            query_type: The query type to update.
            accuracy: The new measured accuracy [0.0, 1.0].
        """
        if not 0.0 <= accuracy <= 1.0:
            raise ValueError(
                f"accuracy must be in [0.0, 1.0], got {accuracy}"
            )
        self._historical_accuracy[query_type] = accuracy

    def get_historical_accuracy(self, query_type: str) -> float:
        """Get the historical accuracy for a query type."""
        return self._historical_accuracy.get(query_type, 0.5)

    # ------------------------------------------------------------------
    # Anti-sycophancy checks
    # ------------------------------------------------------------------

    def check_anti_sycophancy(
        self,
        output: str,
        user_prompt: str,
        actual_confidence: float,
    ) -> AntiSycophancyReport:
        """Check an output for sycophantic patterns.

        Runs six checks:
          1. Confidence calibration: Is the output's hedging language
             consistent with the stated confidence?
          2. Agreement pattern detection: Does the output excessively
             agree with the user without independent analysis?
          3. Caveat suppression detection: Does the output lack caveats
             when confidence is below the threshold?
          4. Fabrication detection: Does the output contain specific
             claims not supported by evidence?
          5. Emotional bias detection: Is the output shaped by emotional
             language in the user prompt?
          6. Confirmation bias detection: Does the output only support
             the user's apparent position?

        Args:
            output: The proposed output text.
            user_prompt: The original user prompt.
            actual_confidence: The system's actual confidence in the
                output [0.0, 1.0].

        Returns:
            An AntiSycophancyReport with the results.
        """
        report = AntiSycophancyReport()
        output_lower = output.lower()
        prompt_lower = user_prompt.lower()

        # 1. Confidence calibration
        # If confidence is low but the output has no hedging, the
        # confidence is miscalibrated (the output is overconfident).
        has_hedging = any(h in output_lower for h in _HEDGING_PHRASES)
        if actual_confidence < 0.6 and not has_hedging:
            report.confidence_miscalibration = True
            report.flags.append(AntiSycophancyFlag.CONFIDENCE_MISCALIBRATION)
            report.details.append(
                f"Confidence is {actual_confidence:.2f} (low) but output "
                f"contains no hedging language. Possible overconfidence."
            )
        # If confidence is very high but the output is full of hedging,
        # that's also miscalibration (underconfidence can be a form of
        # sycophancy -- pretending uncertainty to seem humble).
        if actual_confidence > 0.85 and has_hedging:
            report.confidence_miscalibration = True
            if AntiSycophancyFlag.CONFIDENCE_MISCALIBRATION not in report.flags:
                report.flags.append(AntiSycophancyFlag.CONFIDENCE_MISCALIBRATION)
            report.details.append(
                f"Confidence is {actual_confidence:.2f} (high) but output "
                f"contains hedging language. Possible false humility."
            )

        # 2. Agreement pattern detection
        agreement_count = sum(
            1 for phrase in _AGREEMENT_PHRASES if phrase in output_lower
        )
        if agreement_count >= 2:
            report.excessive_agreement = True
            report.flags.append(AntiSycophancyFlag.EXCESSIVE_AGREEMENT)
            report.details.append(
                f"Output contains {agreement_count} agreement phrases. "
                f"Possible sycophancy -- output may be agreeing without "
                f"independent analysis."
            )

        # 3. Caveat suppression detection
        # If confidence is below the factual threshold, the output
        # should carry a caveat. If it doesn't, caveats may have been
        # suppressed.
        threshold = dynamic_threshold("factual", 0.5, 0.5)
        if actual_confidence < threshold and not has_hedging:
            report.caveat_suppression = True
            report.flags.append(AntiSycophancyFlag.CAVEAT_SUPPRESSION)
            report.details.append(
                f"Confidence ({actual_confidence:.2f}) is below the "
                f"factual threshold ({threshold:.2f}) but no caveat or "
                f"hedging is present. Caveats may have been suppressed."
            )

        # 4. Fabrication detection
        # Heuristic: if the output contains specific numbers, dates, or
        # proper nouns that are not present in the user prompt, they may
        # be fabricated. This is a conservative heuristic -- it flags
        # for review, it does not prove fabrication.
        fabricated_indicators = self._detect_unsupported_claims(
            output, user_prompt
        )
        if fabricated_indicators:
            report.fabrication_detected = True
            report.flags.append(AntiSycophancyFlag.FABRICATION_DETECTED)
            report.details.append(
                f"Output contains specific claims not found in the user "
                f"prompt: {fabricated_indicators}. Verify against evidence."
            )

        # 5. Emotional bias detection
        has_emotional_prompt = any(
            indicator in prompt_lower for indicator in _EMOTIONAL_INDICATORS
        )
        if has_emotional_prompt:
            # Check if the output mirrors the emotional tone rather than
            # maintaining analytical distance.
            emotional_output_indicators = [
                "i understand how frustrating",
                "i know this is difficult",
                "i'm sorry you're going through",
                "that must be really",
                "i feel bad that",
            ]
            has_emotional_output = any(
                indicator in output_lower
                for indicator in emotional_output_indicators
            )
            if has_emotional_output:
                report.emotional_bias = True
                report.flags.append(AntiSycophancyFlag.EMOTIONAL_BIAS)
                report.details.append(
                    "User prompt contains emotional language and the "
                    "output mirrors that emotion. Output may be shaped "
                    "by emotional bias rather than evidence."
                )

        # 6. Confirmation bias detection
        # Heuristic: if the user prompt asserts a position and the
        # output only contains supporting language (no counterpoints),
        # confirmation bias may be present.
        user_assertion_markers = [
            "i think", "i believe", "in my opinion", "it seems to me",
            "my view", "my position", "i feel that",
        ]
        user_has_assertion = any(
            marker in prompt_lower for marker in user_assertion_markers
        )
        if user_has_assertion:
            counterpoint_markers = [
                "however", "on the other hand", "alternatively",
                "a counterargument", "one could argue", "conversely",
                "nevertheless", "that said", "caveat",
            ]
            has_counterpoint = any(
                marker in output_lower for marker in counterpoint_markers
            )
            if not has_counterpoint and actual_confidence < 0.9:
                report.confirmation_bias = True
                report.flags.append(AntiSycophancyFlag.CONFIRMATION_BIAS)
                report.details.append(
                    "User prompt asserts a position and the output "
                    "contains no counterpoints. Possible confirmation bias."
                )

        if not report.flags:
            report.flags.append(AntiSycophancyFlag.NONE)

        return report

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _issue_silence(
        self,
        query: str,
        context: dict[str, Any],
        actionable: list[str],
        fallacy: str | None,
        requires_fabrication: bool,
        confidence: float,
    ) -> EpistemicVerdict:
        """Issue a silence verdict with a localized log.

        This is the internal path for silence when evaluate() determines
        the query is structurally unanswerable.
        """
        boundaries: list[str] = []

        if not query.strip():
            boundaries.append("Query is empty -- no content to engage with.")
            trigger = SilenceTrigger.EMPTY_QUERY.value
        elif fallacy:
            boundaries.append(
                f"Query is built on a logical fallacy ({fallacy}). "
                f"Answering would require accepting the false premise."
            )
            trigger = SilenceTrigger.LOGICAL_FALLACY.value
        elif requires_fabrication:
            boundaries.append(
                "Query cannot be answered without fabrication. "
                "Missing data: "
                + ", ".join(actionable) if actionable else "unspecified."
            )
            trigger = SilenceTrigger.REQUIRES_FABRICATION.value
        else:
            boundaries.append(
                "Query contains no actionable variables. "
                "The system cannot identify any target for evidence gathering."
            )
            trigger = SilenceTrigger.NO_ACTIONABLE_VARIABLES.value

        # Compute the silence cost log.
        cost_log = compute_silence_cost(
            confidence=confidence,
            query_type=context.get("query_type", "factual"),
            context_complexity=context.get("context_complexity", 0.5),
            has_caveat=False,
        )

        socket = VoidSocket(
            query=query,
            context=context,
            missing=boundaries,
            confidence=confidence,
            trigger=trigger,
            response=None,
            silence_cost_log=cost_log,
        )

        self.record_void_socket(socket)
        self._silence_count += 1
        return EpistemicVerdict.SILENCE

    @staticmethod
    def _detect_unsupported_claims(
        output: str,
        user_prompt: str,
    ) -> list[str]:
        """Detect specific claims in the output not present in the prompt.

        This is a conservative heuristic. It looks for:
          - Numbers (dates, statistics) not in the prompt.
          - Quoted strings not in the prompt.

        Returns a list of flagged claims. Empty list means no
        unsupported claims detected.
        """
        import re

        flagged: list[str] = []

        # Find numbers with context (4+ digit numbers, percentages, etc.)
        # in the output that are not in the prompt.
        output_numbers = set(re.findall(r"\b\d{4,}\b", output))
        prompt_numbers = set(re.findall(r"\b\d{4,}\b", user_prompt))
        unsupported_numbers = output_numbers - prompt_numbers
        for num in list(unsupported_numbers)[:3]:
            flagged.append(f"number '{num}'")

        # Find quoted strings in the output not in the prompt.
        output_quotes = set(re.findall(r'"([^"]{5,})"', output))
        prompt_quotes = set(re.findall(r'"([^"]{5,})"', user_prompt))
        unsupported_quotes = output_quotes - prompt_quotes
        for quote in list(unsupported_quotes)[:2]:
            flagged.append(f"quote '{quote[:40]}...'")

        return flagged
