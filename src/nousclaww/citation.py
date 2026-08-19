"""Citation builder for source-attributed answers.

Builds citations that trace every answer back to its source:
- Source ID and file path
- Page number
- Paragraph number
- Line range
- Evidence excerpt (the exact text that supports the answer)

Contract:
    - Every citation includes a verifiable source locator.
    - Evidence excerpts are exact quotes (not paraphrased).
    - Citations are machine-readable (JSON) and human-readable.
    - A citation with no evidence is INVALID (empty evidence = no citation).
    - Prompt-injection text in evidence is marked as [UNTRUSTED].
    - Evidence excerpts are capped at 500 characters to prevent context flooding.

SYNTH:
    purpose: Citation builder — source/page/paragraph/line/evidence for every answer, with prompt-injection detection and UNTRUSTED marking.
    axioms: [evidence_over_intuition, epistemic_boundary, open_process, honest_failure_over_fake_success]
    objective: Every answer produced by the system can be traced to an exact source location with a verbatim evidence excerpt, and any evidence containing prompt-injection patterns is flagged as untrusted before it reaches the agent.
    anti_patterns:
        - Paraphrasing evidence instead of quoting verbatim
        - Allowing evidence excerpts longer than the cap without truncation
        - Executing or acting on text flagged as prompt injection
        - Producing a citation with empty evidence
        - Importing internal nousclaww modules (this is a leaf module)
        - Silently dropping the UNTRUSTED marker when formatting for display
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core pipeline/citation.py

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "Citation",
    "CitationBuilder",
    "detect_injection",
    "MAX_EXCERPT_LENGTH",
    "UNTRUSTED_MARKER",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum evidence excerpt length in characters. Longer excerpts are
#: truncated to prevent context flooding.
MAX_EXCERPT_LENGTH = 500

#: Marker prepended to evidence that contains suspected prompt-injection
#: text. The marker warns downstream consumers not to execute the content.
UNTRUSTED_MARKER = "[UNTRUSTED]"

# ---------------------------------------------------------------------------
# Prompt-injection detection
# ---------------------------------------------------------------------------

# Heuristic patterns that suggest prompt injection in retrieved text.
# These are regex patterns matched case-insensitively against the evidence.
_INJECTION_PATTERNS: list[str] = [
    r"ignore (the )?(previous |above )?instructions",
    r"disregard (the )?(previous |above )?(prompt|context|instructions)",
    r"you are (now )?a (different |new )?",
    r"forget (everything |all )?(previous |above )?",
    r"system prompt:",
    r"<\|system\|>",
    r"<\|im_start\|>",
    r"act as (if )?you (are |were )?",
    r"pretend (you are |to be )",
    r"new instructions:",
    r"override (the )?(system |previous )?",
    r"reveal (your |the )?(system |hidden )?prompt",
    r"jailbreak",
    r"do not follow (your |the )?(rules|guidelines|instructions)",
]

_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def detect_injection(text: str) -> bool:
    """Heuristic detection of prompt injection in retrieved text.

    Scans *text* for common prompt-injection patterns such as "ignore
    previous instructions", "system prompt:", role-play directives, and
    jailbreak keywords. The detection is heuristic — it flags common
    patterns but cannot detect all attacks.

    Args:
        text: The text to scan for injection patterns.

    Returns:
        True if any injection pattern is found, False otherwise.

    Raises:
        TypeError: If *text* is not a string.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    if not text:
        return False
    return bool(_INJECTION_RE.search(text))


# ---------------------------------------------------------------------------
# Citation dataclass
# ---------------------------------------------------------------------------


@dataclass
class Citation:
    """A single citation tracing an answer to its source.

    Attributes:
        source_id: Opaque identifier for the source document.
        source_path: File path or URL of the source.
        page: Page number (1-based). Defaults to 1.
        paragraph: Paragraph number (1-based). Defaults to 1.
        line_start: Starting line number (1-based). Defaults to 1.
        line_end: Ending line number (1-based). Defaults to 1.
        evidence: Exact verbatim quote from the source that supports
            the answer. Must not be empty.
        chunk_id: Optional chunk identifier from the chunker.
        score: Optional retrieval similarity score [0.0, 1.0].
        is_untrusted: True if the evidence was flagged as potential
            prompt injection. When True, the evidence must not be
            executed or acted upon by the agent.
    """

    source_id: str
    source_path: str
    page: int = 1
    paragraph: int = 1
    line_start: int = 1
    line_end: int = 1
    evidence: str = ""
    chunk_id: str = ""
    score: float = 0.0
    is_untrusted: bool = False

    def __post_init__(self) -> None:
        """Validate the citation after construction.

        A citation with empty evidence is invalid.
        """
        if not self.evidence or not self.evidence.strip():
            raise ValueError(
                "A citation must have non-empty evidence — "
                "empty evidence means no citation."
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the citation to a plain dictionary.

        Returns:
            A dictionary with all citation fields.
        """
        return asdict(self)

    def to_json(self) -> str:
        """Serialize the citation to a JSON string.

        Returns:
            A JSON-encoded string representation of the citation.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_human_readable(self) -> str:
        """Format the citation for human display.

        If the evidence is flagged as untrusted, the ``[UNTRUSTED]``
        marker is prepended to the evidence line.

        Returns:
            A multi-line human-readable string.
        """
        untrusted_tag = f" {UNTRUSTED_MARKER}" if self.is_untrusted else ""
        return (
            f"Source: {self.source_path}\n"
            f"Page: {self.page}, Paragraph: {self.paragraph}, "
            f"Lines: {self.line_start}-{self.line_end}\n"
            f"Evidence:{untrusted_tag} \"{self.evidence}\""
        )

    def mark_untrusted(self) -> None:
        """Flag this citation's evidence as potential prompt injection.

        After calling this method, ``is_untrusted`` is True and the
        evidence will carry the ``[UNTRUSTED]`` marker in human-readable
        output. The evidence text itself is not modified — only the flag.
        """
        self.is_untrusted = True


# ---------------------------------------------------------------------------
# Citation builder
# ---------------------------------------------------------------------------


class CitationBuilder:
    """Builds citations from retrieval results.

    Takes retrieval result dictionaries and source metadata to produce
    structured Citation objects that trace every answer back to its
    source. Evidence excerpts are truncated to a configurable maximum
    length and scanned for prompt-injection patterns.

    Usage::

        builder = CitationBuilder()
        citation = builder.build_citation(
            retrieval_result={"text": "The sky is blue.", "source_id": "s1"},
            source_path="docs/science.txt",
        )
        if citation:
            print(citation.to_human_readable())
    """

    def __init__(self, max_excerpt_length: int = MAX_EXCERPT_LENGTH) -> None:
        """Initialize the builder.

        Args:
            max_excerpt_length: Maximum number of characters allowed in
                an evidence excerpt. Longer excerpts are truncated with
                an ellipsis. Must be positive.

        Raises:
            ValueError: If *max_excerpt_length* is not positive.
        """
        if max_excerpt_length <= 0:
            raise ValueError(
                f"max_excerpt_length must be positive, got {max_excerpt_length}"
            )
        self.max_excerpt_length = max_excerpt_length

    def build_citation(
        self, retrieval_result: dict[str, Any], source_path: str
    ) -> Citation | None:
        """Build a single citation from a retrieval result.

        Args:
            retrieval_result: Dictionary from a retriever result. Expected
                keys: ``text`` (the evidence), ``source_id``, ``page``,
                ``paragraph``, ``line_start``, ``line_end``, ``chunk_id``,
                ``score``. Missing keys use sensible defaults.
            source_path: The file path or URL of the source.

        Returns:
            A Citation object, or None if the result has no valid evidence
            (empty or whitespace-only text).
        """
        evidence = str(retrieval_result.get("text", "")).strip()
        if not evidence:
            return None  # No evidence = no citation

        # Truncate evidence to max length.
        if len(evidence) > self.max_excerpt_length:
            evidence = evidence[: self.max_excerpt_length] + "..."

        # Check for prompt injection.
        is_untrusted = detect_injection(evidence)

        return Citation(
            source_id=str(retrieval_result.get("source_id", "")),
            source_path=source_path,
            page=int(retrieval_result.get("page", 1)),
            paragraph=int(retrieval_result.get("paragraph", 1)),
            line_start=int(retrieval_result.get("line_start", 1)),
            line_end=int(retrieval_result.get("line_end", 1)),
            evidence=evidence,
            chunk_id=str(retrieval_result.get("chunk_id", "")),
            score=float(retrieval_result.get("score", 0.0)),
            is_untrusted=is_untrusted,
        )

    def build_citations(
        self,
        retrieval_results: list[dict[str, Any]],
        source_paths: dict[str, str],
    ) -> list[Citation]:
        """Build multiple citations from retrieval results.

        Args:
            retrieval_results: List of retrieval result dictionaries.
            source_paths: Mapping of source_id to file path. Sources
                not found in this mapping use ``[unknown]`` as the path.

        Returns:
            A list of Citation objects. Invalid results (no evidence)
            are filtered out.
        """
        citations: list[Citation] = []
        for result in retrieval_results:
            source_id = str(result.get("source_id", ""))
            path = source_paths.get(source_id, "[unknown]")
            citation = self.build_citation(result, path)
            if citation is not None:
                citations.append(citation)
        return citations

    def format_citations(self, citations: list[Citation]) -> str:
        """Format a list of citations for display in an answer.

        Args:
            citations: List of Citation objects to format.

        Returns:
            A string with numbered citations separated by blank lines.
            Returns ``[No citations available]`` if the list is empty.
        """
        if not citations:
            return "[No citations available]"

        lines: list[str] = []
        for i, cite in enumerate(citations, 1):
            lines.append(f"[{i}] {cite.to_human_readable()}")
        return "\n\n".join(lines)

    def format_citations_json(self, citations: list[Citation]) -> str:
        """Format a list of citations as a JSON array string.

        Args:
            citations: List of Citation objects to format.

        Returns:
            A JSON-encoded array of citation dictionaries. Returns
            ``[]`` if the list is empty.
        """
        if not citations:
            return "[]"
        return json.dumps(
            [c.to_dict() for c in citations], ensure_ascii=False, indent=2
        )
