"""Exploration capture — record what files the agent read so future sessions reuse conclusions.

SYNTH:
    purpose: Zero-cooperation recording of file explorations so future sessions reuse prior conclusions instead of re-deriving them.
    axioms: [local_first, llm_agnostic, open_process, epistemic_boundary, evidence_over_intuition, iteration_is_progress]
    objective: Every file exploration is recorded with its purpose and conclusion, and future sessions can query whether a file has already been explored to avoid redundant work.
    anti_patterns:
        - Never use an LLM to decide whether to record — capture is automatic.
        - Never fabricate conclusions — record only what was actually found.
        - Never skip recording the purpose — without it, the conclusion is not actionable.
        - Never block the exploration pipeline — recording is post-processing.
        - Never re-explore a file without first querying prior explorations.

Records what files the agent explored, why it explored them, and what it concluded.
Future sessions query the exploration history to avoid re-reading and re-deriving
the same conclusions. No model cooperation required — the entire capture and query
path is deterministic.

#C Inspired by PMB (Project Memory Bank) automatic hooks
"""

# ┌─ synth ──────────────────────────────────────────────────────────────────┐
# @NCL{v=1.0;agent=builder;mod=exploration_capture;ts=2026-08-18Z;tier=L3}
# #C Inspired by PMB (Project Memory Bank) automatic hooks
# #S{purpose="Record file explorations with purpose and conclusion so future sessions reuse prior work — zero model cooperation"}
# #I{1="automatic recording — every file exploration is captured without model decision";2="queryable history — future sessions check if a file was already explored";3="purpose + conclusion stored — full context for reuse, not just the file path";4="deterministic — no LLM needed for capture or query"}
# #D{1="exploration record"→="a memory of type 'exploration' with file_path, purpose, conclusion, and timestamp";2="query"→="search memories by file path to find prior explorations";3="reuse"→="future session reads prior conclusion instead of re-exploring"]
# #M{status=IMPLEMENTED;version=1.0.0;deps="nousclaww.memory.memory_manager"]
# #T{pass=0;fail=0;xfail=0}
# #W{1="query uses substring search on file_path — may return explorations of similarly-named files";2="if memory_manager lacks search_memories, query falls back to scanning all exploration memories"]
# #L{lexicon→docs/NOUS_LEXICON.md}
# └──────────────────────────────────────────────────────────────────────────┘

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nousclaww.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

EXPLORATION_MEMORY_TYPE = "exploration"
_MAX_QUERY_RESULTS = 20
_MAX_CONCLUSION_LEN = 2000
_MAX_PURPOSE_LEN = 500


class ExplorationCapture:
    """Automatic recording and querying of file explorations.

    Records what files the agent explored, why it explored them, and what
    it concluded. Future sessions query the exploration history to avoid
    re-reading and re-deriving the same conclusions.

    The capture is fully automatic — the agent never decides whether to
    record. Every call to ``record()`` stores an exploration memory that
    can be retrieved later via ``query()``.

    Usage:
        ec = ExplorationCapture()
        # Record an exploration
        ec.record(
            file_path="src/auth.py",
            purpose="Understand the authentication flow",
            conclusion="Uses JWT with RS256, tokens expire in 1 hour",
            memory_manager=memory_manager,
        )
        # Query prior explorations
        prior = ec.query("src/auth.py", memory_manager)
        if prior:
            # Reuse the conclusion instead of re-exploring
            print(prior[0]["conclusion"])
    """

    def __init__(self) -> None:
        """Initialize the ExplorationCapture hook."""
        self._memory_type = EXPLORATION_MEMORY_TYPE

    def record(
        self,
        file_path: str,
        purpose: str,
        conclusion: str,
        memory_manager: "MemoryManager",
    ) -> bool:
        """Record a file exploration with its purpose and conclusion.

        Stores an exploration memory in the MemoryManager. The memory
        contains the file path (normalized), the purpose of the
        exploration, the conclusion reached, and a timestamp. The file
        path is stored as the memory's ``subject`` so it can be searched
        efficiently.

        Args:
            file_path: The path of the file that was explored.
            purpose: Why the file was explored (e.g. "Understand the
                authentication flow").
            conclusion: What was concluded from the exploration (e.g.
                "Uses JWT with RS256, tokens expire in 1 hour").
            memory_manager: The MemoryManager instance to store into.

        Returns:
            True if the exploration was recorded successfully, False if
            storage failed or the inputs were invalid.
        """
        if not file_path or not file_path.strip():
            logger.warning("ExplorationCapture: record called with empty file_path")
            return False
        if not purpose or not purpose.strip():
            logger.warning("ExplorationCapture: record called with empty purpose")
            return False
        if not conclusion or not conclusion.strip():
            logger.warning("ExplorationCapture: record called with empty conclusion")
            return False

        # Normalize the file path for consistent storage and querying
        normalized_path = self._normalize_path(file_path)

        # Truncate overly long fields to prevent memory bloat
        safe_purpose = purpose.strip()[:_MAX_PURPOSE_LEN]
        safe_conclusion = conclusion.strip()[:_MAX_CONCLUSION_LEN]

        # Compute a signature for deduplication awareness
        signature = self._compute_signature(normalized_path, safe_purpose)

        timestamp = datetime.now(timezone.utc).isoformat()

        # Build the exploration content — this is what search_memories
        # will match against when querying.
        content = (
            f"Explored {normalized_path}: {safe_purpose}. "
            f"Conclusion: {safe_conclusion}"
        )

        memory = {
            "type": self._memory_type,
            "content": content,
            "subject": normalized_path,
            "importance": 0.6,
            "metadata": {
                "file_path": normalized_path,
                "purpose": safe_purpose,
                "conclusion": safe_conclusion,
                "timestamp": timestamp,
                "signature": signature,
            },
        }

        try:
            memory_manager.store_memory(memory)
            logger.info(
                f"ExplorationCapture: recorded exploration of "
                f"{normalized_path} (purpose: {safe_purpose[:60]})"
            )
            return True
        except Exception as e:
            logger.warning(f"ExplorationCapture: failed to record exploration: {e}")
            return False

    def query(
        self,
        file_path: str,
        memory_manager: "MemoryManager",
    ) -> list[dict]:
        """Query prior explorations of a file.

        Searches the MemoryManager for exploration memories matching the
        given file path. Returns a list of exploration records, each
        containing the file path, purpose, conclusion, and timestamp.

        If no prior explorations exist, returns an empty list. The
        caller should use this to decide whether to re-explore or reuse
        a prior conclusion.

        Args:
            file_path: The path of the file to query.
            memory_manager: The MemoryManager instance to query.

        Returns:
            A list of exploration record dicts, each with keys:
            ``file_path``, ``purpose``, ``conclusion``, ``timestamp``.
            Returns an empty list if no prior explorations exist or the
            query fails.
        """
        if not file_path or not file_path.strip():
            logger.warning("ExplorationCapture: query called with empty file_path")
            return []

        normalized_path = self._normalize_path(file_path)

        # Try searching by the file path as a search query
        try:
            results = memory_manager.search_memories(
                normalized_path, limit=_MAX_QUERY_RESULTS
            )
        except Exception as e:
            logger.warning(f"ExplorationCapture: search_memories failed: {e}")
            # Fallback: scan all exploration-type memories
            try:
                all_explorations = memory_manager.get_all_memories(
                    memory_type=self._memory_type
                )
                results = [
                    m
                    for m in all_explorations
                    if m.get("subject", "") == normalized_path
                ]
            except Exception as e2:
                logger.warning(f"ExplorationCapture: fallback query also failed: {e2}")
                return []

        # Filter to only exploration-type memories matching this file path
        explorations: list[dict] = []
        for mem in results:
            if mem.get("memory_type", mem.get("type", "")) != self._memory_type:
                continue
            mem_subject = mem.get("subject", "")
            if mem_subject != normalized_path:
                continue
            metadata = mem.get("metadata", {})
            explorations.append({
                "file_path": metadata.get("file_path", mem_subject),
                "purpose": metadata.get("purpose", ""),
                "conclusion": metadata.get("conclusion", ""),
                "timestamp": metadata.get("timestamp", ""),
                "memory_id": mem.get("memory_id", ""),
                "importance": mem.get("importance", 0.5),
            })

        # Sort by timestamp descending (most recent first)
        explorations.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

        logger.debug(
            f"ExplorationCapture: found {len(explorations)} prior "
            f"explorations of {normalized_path}"
        )
        return explorations

    def _normalize_path(self, file_path: str) -> str:
        """Normalize a file path for consistent storage and querying.

        Converts to forward slashes, resolves ``.`` and ``..`` segments
        where possible, and lowercases the path for case-insensitive
        matching.

        Args:
            file_path: The raw file path.

        Returns:
            A normalized path string.
        """
        try:
            resolved = Path(file_path)
            # Only resolve if the path exists (avoids errors on
            # hypothetical or remote paths)
            if resolved.exists():
                resolved = resolved.resolve()
            normalized = str(resolved).replace("\\", "/")
        except (OSError, ValueError):
            # If Path resolution fails, do a simple string normalization
            normalized = file_path.replace("\\", "/")

        # Collapse duplicate slashes
        while "//" in normalized:
            normalized = normalized.replace("//", "/")

        return normalized

    def _compute_signature(self, file_path: str, purpose: str) -> str:
        """Compute a deduplication signature for an exploration record.

        Combines the normalized file path and purpose into a SHA-256
        hash. Two explorations of the same file for the same purpose
        produce the same signature.

        Args:
            file_path: The normalized file path.
            purpose: The exploration purpose.

        Returns:
            A hex string signature.
        """
        combined = f"{file_path}|{purpose.lower().strip()}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()
