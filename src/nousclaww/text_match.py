"""Token matching utilities for retrieval ranking.

SYNTH:
    purpose: Token matching utilities for retrieval ranking — tokenize, find
             distinctive tokens, and judge whether a candidate is a strong
             match for a query using set-overlap heuristics.
    axioms: [evidence_over_intuition, scientific_method, honest_failure_over_fake_success]
    objective: Provide fast, dependency-free token matching primitives that
               retrieval and ranking layers can rely on for relevance scoring
               without importing anything internal.
    anti_patterns:
        - Importing internal nousclaww modules (this is a LEAF module)
        - Using fuzzy or embedding-based similarity (that belongs in a richer layer)
        - Mutating input strings or returning mutable shared state
        - Silently swallowing malformed input instead of handling it explicitly
        - Hardcoding stop words that match domain-specific meaningful tokens

#C Inspired by PMB (Project Memory Bank) patterns
"""

from __future__ import annotations

import re
import unicodedata
from typing import FrozenSet, Set

__all__ = [
    "tokenize",
    "distinctive_tokens",
    "is_strong_match",
]

# ---------------------------------------------------------------------------
# Stop words — a compact, broadly-applicable English set.
#
# These are tokens that carry almost no discriminative signal for retrieval.
# We keep the set small and frozen so callers can rely on it being immutable.
# Domain-specific callers may build their own superset and pass tokens through
# distinctive_tokens with a custom stop set via the internal helper.
# ---------------------------------------------------------------------------

_STOP_WORDS: FrozenSet[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "else", "when",
        "at", "by", "for", "with", "about", "against", "between", "into",
        "through", "during", "before", "after", "above", "below", "to", "from",
        "up", "down", "in", "out", "on", "off", "over", "under", "again",
        "further", "once", "here", "there", "all", "any", "both", "each",
        "few", "more", "most", "other", "some", "such", "no", "nor", "not",
        "only", "own", "same", "so", "than", "too", "very", "can", "will",
        "just", "should", "now", "is", "am", "are", "was", "were", "be",
        "been", "being", "have", "has", "had", "having", "do", "does", "did",
        "doing", "of", "as", "it", "its", "this", "that", "these", "those",
        "i", "you", "he", "she", "we", "they", "them", "his", "her", "their",
        "our", "your", "my", "me", "him", "us",
    }
)

# A token is a run of alphanumeric characters (Unicode-aware).
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _normalize(text: str) -> str:
    """Normalize text to lowercase with Unicode folding.

    NFKC normalization collapses compatibility characters (e.g. fullwidth
    Latin letters to their ASCII equivalents) so that visually identical
    tokens compare equal. Lowercasing follows so matching is case-insensitive.

    Args:
        text: Raw input string.

    Returns:
        Normalized string suitable for tokenization.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.lower()


def tokenize(text: str) -> list[str]:
    """Split text into a list of lowercase alphanumeric tokens.

    Tokenization is Unicode-aware: any run of word characters (letters,
    digits, underscore) becomes a token. The order of tokens in the input
    is preserved in the output, which makes this suitable for n-gram
    construction or positional heuristics by callers.

    Args:
        text: The input string to tokenize.

    Returns:
        A list of tokens in their original order. Returns an empty list
        for empty or whitespace-only input.

    Raises:
        TypeError: If *text* is not a string.
    """
    normalized = _normalize(text)
    return _TOKEN_RE.findall(normalized)


def distinctive_tokens(text: str, min_len: int = 3) -> set[str]:
    """Return the set of distinctive tokens in *text*.

    Distinctive tokens are those that survive two filters:
      1. Length >= *min_len* — short tokens (``is``, ``a``, ``to``) rarely
         carry discriminative signal.
      2. Not in the stop-word set — common function words are removed.

    The result is a set, so order and duplicates are collapsed. This is the
    primary building block for overlap-based relevance scoring.

    Args:
        text: The input string.
        min_len: Minimum token length (in characters) to be considered
            distinctive. Defaults to 3. Must be >= 0.

    Returns:
        A set of distinctive, lowercased tokens.

    Raises:
        TypeError: If *text* is not a string.
        ValueError: If *min_len* is negative.
    """
    if min_len < 0:
        raise ValueError(f"min_len must be non-negative, got {min_len}")
    tokens = tokenize(text)
    return {
        tok
        for tok in tokens
        if len(tok) >= min_len and tok not in _STOP_WORDS
    }


def is_strong_match(
    query: str,
    candidate: str,
    min_overlap: float = 0.15,
) -> bool:
    """Judge whether *candidate* is a strong match for *query*.

    Strength is measured by the Jaccard-like overlap ratio between the
    distinctive token sets of the query and the candidate::

        overlap = |query_tokens ∩ candidate_tokens| / |query_tokens ∪ candidate_tokens|

    A candidate is a **strong match** when the overlap ratio is >= *min_overlap*.

    Edge cases:
      - If both query and candidate produce zero distinctive tokens, the
        overlap is undefined. We return ``False`` (no signal = not a match),
        honoring the epistemic boundary: silence over fabrication.
      - If only one side has zero tokens, the intersection is empty, so the
        ratio is 0.0 and the result is ``False``.

    Args:
        query: The search query string.
        candidate: The candidate text to evaluate.
        min_overlap: Minimum Jaccard overlap ratio [0.0, 1.0] for a strong
            match. Defaults to 0.15. Values outside [0.0, 1.0] are clamped.

    Returns:
        ``True`` if the overlap ratio >= *min_overlap*, ``False`` otherwise.

    Raises:
        TypeError: If *query* or *candidate* is not a string.
    """
    # Clamp min_overlap into a valid range.
    threshold = max(0.0, min(1.0, float(min_overlap)))

    query_tokens: Set[str] = distinctive_tokens(query)
    candidate_tokens: Set[str] = distinctive_tokens(candidate)

    union = query_tokens | candidate_tokens
    if not union:
        # No signal from either side — cannot claim a match.
        return False

    intersection = query_tokens & candidate_tokens
    overlap_ratio = len(intersection) / len(union)
    return overlap_ratio >= threshold
