"""Automatic memory recall — regex intent classification and pre-injection.

SYNTH:
    purpose: Zero-cooperation memory injection that classifies user intent via regex and pre-injects relevant memories before the LLM thinks.
    axioms: [local_first, llm_agnostic, open_process, epistemic_boundary, evidence_over_intuition]
    objective: Relevant memories are injected into context before the model generates a response, with zero model cooperation and sub-100ms latency.
    anti_patterns:
        - Never use an LLM for intent classification — regex only, deterministic.
        - Never block the model's response pipeline — injection is pre-processing.
        - Never inject stale or irrelevant memories — intent drives the query.
        - Never exceed 100ms on the classify+recall path — this is a hot path.
        - Never fabricate memories — if recall returns nothing, inject nothing.

Classifies user queries into one of eight intents using compiled regex patterns,
then recalls relevant memories from the MemoryManager and returns them for
injection into the model's context window. No LLM calls, no model cooperation.

#C Inspired by PMB (Project Memory Bank) automatic hooks
"""

# ┌─ synth ──────────────────────────────────────────────────────────────────┐
# @NCL{v=1.0;agent=builder;mod=auto_recall;ts=2026-08-18Z;tier=L3}
# #C Inspired by PMB (Project Memory Bank) automatic hooks
# #S{purpose="Zero-cooperation memory injection — regex intent classification + pre-inject before LLM thinks"}
# #I{1="regex-based intent classification — no LLM, sub-100ms";2="eight intent categories covering project, query, goals, lessons, and generic";3="pre-injection — memories available before model generates";4="zero model cooperation — fully automatic"}
# #D{1="intent classification"→="compiled regex patterns matched against user query";2="pre-injection"→="memories recalled and returned before model response";3="SKIP intent"→="no recall needed, returns empty list"]
# #M{status=IMPLEMENTED;version=1.0.0;deps="nousclaww.memory.memory_manager"]
# #T{pass=0;fail=0;xfail=0}
# #W{1="regex patterns may miss edge cases — GENERIC_FACTUAL is the fallback";2="recall quality depends on MemoryManager's search implementation"]
# #L{lexicon→docs/NOUS_LEXICON.md}
# └──────────────────────────────────────────────────────────────────────────┘

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nousclaww.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# ── Intent constants ────────────────────────────────────────────────────────

PROJECT_PREP = "PROJECT_PREP"
PROJECT_OVERVIEW = "PROJECT_OVERVIEW"
PAST_QUERY = "PAST_QUERY"
RECENT_QUERY = "RECENT_QUERY"
GOALS_QUERY = "GOALS_QUERY"
LESSONS_QUERY = "LESSONS_QUERY"
GENERIC_FACTUAL = "GENERIC_FACTUAL"
SKIP = "SKIP"

# ── Compiled regex patterns (ordered by specificity — first match wins) ─────

_INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # PROJECT_PREP — user wants to start/prepare a project or task
    (
        PROJECT_PREP,
        re.compile(
            r"\b(prep(are)?|set\s*up|get\s*ready|before\s+we\s+start|"
            r"let'?s\s+start|kick\s*off|begin\s+(?:work|task|project)|"
            r"what\s+do\s+you\s+know\s+about|onboard)"
            r"\b",
            re.IGNORECASE,
        ),
    ),
    # PROJECT_OVERVIEW — user wants a summary/status of the project
    (
        PROJECT_OVERVIEW,
        re.compile(
            r"\b(overview|summary|status|where\s+are\s+we|"
            r"what'?s\s+(?:the\s+)?(?:current\s+)?state|big\s+picture|"
            r"project\s+status|progress\s+(?:report|update)?|"
            r"what\s+have\s+we\s+done)"
            r"\b",
            re.IGNORECASE,
        ),
    ),
    # PAST_QUERY — user references something done previously
    (
        PAST_QUERY,
        re.compile(
            r"\b(last\s+time|previously|before|earlier|"
            r"you\s+(?:said|did|mentioned|told\s+me)|"
            r"we\s+(?:tried|did|discussed|talked\s+about)|"
            r"remember\s+when|that\s+time\s+we|"
            r"what\s+did\s+we\s+(?:do|decide|try))"
            r"\b",
            re.IGNORECASE,
        ),
    ),
    # RECENT_QUERY — user asks about very recent activity (current session)
    (
        RECENT_QUERY,
        re.compile(
            r"\b(just\s+now|recently|a\s+moment\s+ago|"
            r"what\s+(?:just|did\s+just)\s+happened|"
            r"current(?:ly)?|right\s+now|"
            r"latest|most\s+recent)"
            r"\b",
            re.IGNORECASE,
        ),
    ),
    # GOALS_QUERY — user asks about goals, objectives, plans
    (
        GOALS_QUERY,
        re.compile(
            r"\b(goal|objective|target|aim|plan|roadmap|"
            r"what\s+(?:are|is)\s+(?:our|the)\s+(?:goal|plan|objective)|"
            r"where\s+are\s+we\s+going|what'?s\s+next|"
            r"milestone|deliverable|deadline)"
            r"\b",
            re.IGNORECASE,
        ),
    ),
    # LESSONS_QUERY — user asks about lessons learned, mistakes, corrections
    (
        LESSONS_QUERY,
        re.compile(
            r"\b(lesson|mistake|error\s+we\s+made|don'?t\s+do|"
            r"what\s+went\s+wrong|what\s+(?:not\s+to|to\s+avoid)|"
            r"correction|feedback|gotcha|pitfall|"
            r"things\s+to\s+(?:avoid|watch\s+out|remember))"
            r"\b",
            re.IGNORECASE,
        ),
    ),
]

# Patterns that indicate the query is not memory-relevant (SKIP)
_SKIP_PATTERNS = re.compile(
    r"^(hi|hey|hello|thanks|thank\s+you|ok|okay|sure|yes|no|"
    r"cool|nice|great|got\s+it|understood|makes\s+sense|"
    r"lol|haha|👍|np|nvm|never\s*mind)"
    r"[!.\s]*$",
    re.IGNORECASE,
)

# ── Recall limits per intent ─────────────────────────────────────────────────

_RECALL_LIMITS: dict[str, int] = {
    PROJECT_PREP: 10,
    PROJECT_OVERVIEW: 8,
    PAST_QUERY: 5,
    RECENT_QUERY: 5,
    GOALS_QUERY: 5,
    LESSONS_QUERY: 5,
    GENERIC_FACTUAL: 3,
    SKIP: 0,
}

# ── Recall query templates per intent ────────────────────────────────────────

_RECALL_QUERIES: dict[str, str] = {
    PROJECT_PREP: "project setup preparation overview goals",
    PROJECT_OVERVIEW: "project overview status summary progress",
    PAST_QUERY: "past session work done decisions",
    RECENT_QUERY: "recent activity current session actions",
    GOALS_QUERY: "goals objectives plans milestones roadmap",
    LESSONS_QUERY: "lessons learned mistakes corrections feedback",
    GENERIC_FACTUAL: "",
    SKIP: "",
}


class AutoRecall:
    """Automatic memory recall with regex-based intent classification.

    Classifies user queries into one of eight intents using compiled regex
    patterns (no LLM), then recalls relevant memories from the MemoryManager
    and returns them for injection into the model's context window.

    Zero-cooperation: the model never participates in the classification or
    recall decision. Everything is deterministic and sub-100ms.

    Usage:
        auto = AutoRecall()
        intent = auto.classify_intent("what did we do last time?")
        # -> "PAST_QUERY"
        memories = auto.inject("what did we do last time?", memory_manager)
        # -> [{"content": "...", "metadata": {...}, ...}]
    """

    def __init__(self) -> None:
        """Initialize the AutoRecall hook with pre-compiled patterns."""
        self._patterns = _INTENT_PATTERNS
        self._skip_pattern = _SKIP_PATTERNS
        self._recall_limits = _RECALL_LIMITS
        self._recall_queries = _RECALL_QUERIES

    def classify_intent(self, query: str) -> str:
        """Classify a user query into one of eight intent categories.

        Uses compiled regex patterns — no LLM, no model cooperation.
        Classification is deterministic and runs in microseconds.

        Intent categories:
            - PROJECT_PREP: user wants to prepare/start a project or task
            - PROJECT_OVERVIEW: user wants a project summary or status
            - PAST_QUERY: user references something done previously
            - RECENT_QUERY: user asks about very recent activity
            - GOALS_QUERY: user asks about goals, objectives, plans
            - LESSONS_QUERY: user asks about lessons, mistakes, corrections
            - GENERIC_FACTUAL: general factual question (fallback)
            - SKIP: greeting, acknowledgment, or non-memory-relevant query

        Args:
            query: The user's input string.

        Returns:
            One of the eight intent constants as a string.
        """
        if not query or not query.strip():
            return SKIP

        # Check for skip patterns first (greetings, acknowledgments)
        if self._skip_pattern.match(query.strip()):
            return SKIP

        # Check intent patterns in order — first match wins
        for intent, pattern in self._patterns:
            if pattern.search(query):
                return intent

        # Fallback: generic factual question
        return GENERIC_FACTUAL

    def inject(self, query: str, memory_manager: "MemoryManager") -> list[dict]:
        """Classify intent, recall relevant memories, and return for injection.

        This is the main entry point for the auto-recall hook. It:
        1. Classifies the user query into an intent (regex, sub-ms).
        2. Constructs a recall query based on the intent.
        3. Calls memory_manager.recall() with the appropriate limit.
        4. Returns the memories as a list of dicts for context injection.

        If the intent is SKIP, returns an empty list — no recall needed.
        If recall returns nothing, returns an empty list — no fabrication.

        Args:
            query: The user's input string.
            memory_manager: The MemoryManager instance to recall from.

        Returns:
            A list of memory dicts (as returned by MemoryManager.recall()).
            Each dict typically contains content, metadata, and a relevance
            score. Returns an empty list if no memories are found or the
            intent is SKIP.
        """
        start = time.monotonic()

        intent = self.classify_intent(query)
        logger.debug(f"AutoRecall classified intent: {intent}")

        if intent == SKIP:
            return []

        limit = self._recall_limits.get(intent, 3)
        recall_query = self._recall_queries.get(intent, "")

        # For GENERIC_FACTUAL, use the user's own query as the recall query
        if intent == GENERIC_FACTUAL:
            recall_query = query

        try:
            memories = memory_manager.recall(recall_query, limit=limit)
        except Exception as e:
            logger.warning(f"AutoRecall recall failed for intent {intent}: {e}")
            return []

        if not memories:
            logger.debug(f"AutoRecall: no memories found for intent {intent}")
            return []

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug(
            f"AutoRecall: injected {len(memories)} memories for intent "
            f"{intent} in {elapsed_ms:.1f}ms"
        )

        return memories
