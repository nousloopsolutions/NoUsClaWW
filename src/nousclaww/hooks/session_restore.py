"""Session restore — rebuilds "where you left off" context after compaction.

SYNTH:
    purpose: Zero-cooperation context reconstruction that rebuilds a "where you left off" summary from recent memories after context compaction wipes the working window.
    axioms: [local_first, llm_agnostic, open_process, epistemic_boundary, evidence_over_intuition]
    objective: After context compaction, the agent receives a coherent summary of what it was doing, what it completed, and what it was about to do next — reconstructed entirely from memory, not from the model.
    anti_patterns:
        - Never use an LLM to generate the summary — deterministic reconstruction from memory only.
        - Never fabricate context that isn't in memory — if memory is empty, say so.
        - Never include stale or irrelevant memories — only recent, actionable context.
        - Never omit failures or lessons — they are critical for not repeating mistakes.
        - Never block the session — restoration is pre-processing before the model resumes.

Reconstructs a working context summary from recent memories stored by the
MemoryManager. After context compaction (when the context window is truncated
to make room for new content), this hook recalls the most recent memories and
assembles them into a human-readable "where you left off" summary.

The summary structure:
    "Last session: you were working on X, had completed Y, were about to do Z.
     Lessons to remember: L1, L2."

No model cooperation required — the entire reconstruction is deterministic.

#C Inspired by PMB (Project Memory Bank) automatic hooks
"""

# ┌─ synth ──────────────────────────────────────────────────────────────────┐
# @NCL{v=1.0;agent=builder;mod=session_restore;ts=2026-08-18Z;tier=L3}
# #C Inspired by PMB (Project Memory Bank) automatic hooks
# #S{purpose="Rebuild 'where you left off' context from recent memories after context compaction — zero model cooperation"}
# #I{1="deterministic reconstruction — no LLM, assembles from memory records";2="structured summary — working on / completed / about to do / lessons";3="epistemic honesty — empty memory yields 'no prior context' not fabrication";4="recent-only — stale memories excluded from restoration"}
# #D{1="context compaction"→="context window truncation that wipes working memory";2="reconstruction"→="assembling recent memories into a coherent summary";3="recent"→="the last N memories by timestamp, configurable"]
# #M{status=IMPLEMENTED;version=1.0.0;deps="nousclaww.memory.memory_manager"]
# #T{pass=0;fail=0;xfail=0}
# #W{1="reconstruction quality depends on what Autowrite and CorrectionCapture recorded";2="if no memories exist, the summary is intentionally minimal"]
# #L{lexicon→docs/NOUS_LEXICON.md}
# └──────────────────────────────────────────────────────────────────────────┘

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nousclaww.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

# Number of recent memories to recall for reconstruction
_DEFAULT_RECALL_LIMIT = 20

# Turn types that indicate completion
_COMPLETION_TYPES = {"test_pass", "build", "deploy", "commit"}

# Turn types that indicate in-progress work
_IN_PROGRESS_TYPES = {"edit", "test_fail"}

# Memory types that indicate lessons
_LESSON_TYPES = {"correction_lesson"}


class SessionRestore:
    """Reconstructs working context from recent memories after compaction.

    After context compaction wipes the working window, this hook recalls the
    most recent memories from the MemoryManager and assembles them into a
    coherent "where you left off" summary. The reconstruction is entirely
    deterministic — no LLM calls, no model cooperation.

    The summary is structured as:
        "Last session: you were working on X, had completed Y, were about to
         do Z. Lessons to remember: L1, L2."

    If no memories are available, the summary honestly states that no prior
    context exists (epistemic boundary — silence over fabrication).

    Usage:
        sr = SessionRestore()
        summary = sr.rebuild(memory_manager)
        # -> "Last session: you were working on auth.py, had completed
        #     test suite, were about to do deploy. Lessons to remember:
        #     Don't use GET for mutations."
    """

    def __init__(self, recall_limit: int = _DEFAULT_RECALL_LIMIT) -> None:
        """Initialize the SessionRestore hook.

        Args:
            recall_limit: Maximum number of recent memories to recall for
                reconstruction. Defaults to 20.
        """
        self._recall_limit = recall_limit
        self._completion_types = _COMPLETION_TYPES
        self._in_progress_types = _IN_PROGRESS_TYPES
        self._lesson_types = _LESSON_TYPES

    def rebuild(self, memory_manager: "MemoryManager") -> str:
        """Reconstruct a "where you left off" summary from recent memories.

        Recalls the most recent memories from the MemoryManager, categorizes
        them into working-on, completed, about-to-do, and lessons, then
        assembles a coherent summary string.

        If no memories are available, returns an honest "no prior context"
        message rather than fabricating one.

        Args:
            memory_manager: The MemoryManager instance to recall from.

        Returns:
            A human-readable summary string describing where the agent left
            off. Example:
                "Last session: you were working on auth.py, had completed
                 test suite, were about to do deploy. Lessons to remember:
                 Don't use GET for mutations."
            If no memories exist:
                "No prior context available. This appears to be a fresh
                 session."
        """
        # Recall recent memories
        try:
            memories = memory_manager.recall("recent session work", limit=self._recall_limit)
        except Exception as e:
            logger.warning(f"SessionRestore: recall failed: {e}")
            return self._empty_context_message()

        if not memories:
            logger.debug("SessionRestore: no memories found, returning empty context")
            return self._empty_context_message()

        # Categorize memories
        working_on = self._extract_working_on(memories)
        completed = self._extract_completed(memories)
        about_to_do = self._extract_about_to_do(memories)
        lessons = self._extract_lessons(memories)

        # Assemble the summary
        return self._assemble_summary(working_on, completed, about_to_do, lessons)

    def _extract_working_on(self, memories: list[dict]) -> list[str]:
        """Extract what the agent was working on from recent memories.

        Looks for edit and test_fail turn types, plus any action descriptions
        that indicate in-progress work.

        Args:
            memories: List of memory dicts from recall.

        Returns:
            A list of strings describing what was being worked on.
        """
        working_on: list[str] = []
        seen: set[str] = set()

        for mem in memories:
            turn_type = mem.get("turn_type", "")
            if turn_type in self._in_progress_types:
                action = mem.get("action", "")
                summary = mem.get("summary", action)
                if summary and summary not in seen:
                    working_on.append(summary)
                    seen.add(summary)

        return working_on

    def _extract_completed(self, memories: list[dict]) -> list[str]:
        """Extract what the agent had completed from recent memories.

        Looks for test_pass, build, deploy, and commit turn types.

        Args:
            memories: List of memory dicts from recall.

        Returns:
            A list of strings describing completed work.
        """
        completed: list[str] = []
        seen: set[str] = set()

        for mem in memories:
            turn_type = mem.get("turn_type", "")
            if turn_type in self._completion_types:
                summary = mem.get("summary", mem.get("action", ""))
                if summary and summary not in seen:
                    completed.append(summary)
                    seen.add(summary)

        return completed

    def _extract_about_to_do(self, memories: list[dict]) -> list[str]:
        """Extract what the agent was about to do next.

        Infers "about to do" from the last few memories — if the most recent
        action was an edit or test, the next logical step is often testing or
        deployment. This is a heuristic, not a prediction.

        Args:
            memories: List of memory dicts from recall.

        Returns:
            A list of strings describing likely next steps.
        """
        if not memories:
            return []

        about_to_do: list[str] = []
        # Look at the last few memories for hints
        recent = memories[-3:] if len(memories) >= 3 else memories

        for mem in reversed(recent):
            turn_type = mem.get("turn_type", "")
            action = mem.get("action", "")

            # If last action was an edit, likely about to test
            if turn_type == "edit" and "run tests" not in about_to_do:
                about_to_do.append("run tests on the edited files")
            # If last action was a test pass, likely about to commit or deploy
            elif turn_type == "test_pass" and "commit changes" not in about_to_do:
                about_to_do.append("commit changes")
            # If last action was a test fail, about to fix
            elif turn_type == "test_fail" and "fix failing tests" not in about_to_do:
                about_to_do.append("fix failing tests")
            # If last action was a commit, about to deploy
            elif turn_type == "commit" and "deploy" not in about_to_do:
                about_to_do.append("deploy")

            if len(about_to_do) >= 2:
                break

        return about_to_do

    def _extract_lessons(self, memories: list[dict]) -> list[str]:
        """Extract lessons to remember from recent memories.

        Looks for correction_lesson memory types and extracts the lesson text.

        Args:
            memories: List of memory dicts from recall.

        Returns:
            A list of lesson strings to remember.
        """
        lessons: list[str] = []
        seen: set[str] = set()

        for mem in memories:
            mem_type = mem.get("type", "")
            if mem_type in self._lesson_types:
                lesson = mem.get("lesson", mem.get("summary", ""))
                if lesson and lesson not in seen:
                    lessons.append(lesson)
                    seen.add(lesson)

        return lessons

    def _assemble_summary(
        self,
        working_on: list[str],
        completed: list[str],
        about_to_do: list[str],
        lessons: list[str],
    ) -> str:
        """Assemble the final summary string from categorized components.

        Args:
            working_on: What was being worked on.
            completed: What had been completed.
            about_to_do: What was about to be done next.
            lessons: Lessons to remember.

        Returns:
            A coherent summary string.
        """
        parts: list[str] = ["Last session:"]

        if working_on:
            parts.append(f"you were working on {self._format_list(working_on)}")

        if completed:
            parts.append(f"had completed {self._format_list(completed)}")

        if about_to_do:
            parts.append(f"were about to {self._format_list(about_to_do)}")

        summary = ", ".join(parts) + "."

        if lessons:
            lesson_text = "; ".join(lessons[:5])  # Cap at 5 lessons
            summary += f" Lessons to remember: {lesson_text}"

        return summary

    def _format_list(self, items: list[str]) -> str:
        """Format a list of strings into a natural-language conjunction.

        Examples:
            ["A"] -> "A"
            ["A", "B"] -> "A and B"
            ["A", "B", "C"] -> "A, B, and C"

        Args:
            items: List of strings to format.

        Returns:
            A naturally-formatted string.
        """
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]} and {items[1]}"
        return ", ".join(items[:-1]) + f", and {items[-1]}"

    def _empty_context_message(self) -> str:
        """Return the honest empty-context message.

        When no memories are available, we do not fabricate context. We
        honestly state that no prior context exists. This respects the
        epistemic boundary axiom — silence over fabrication.

        Returns:
            A string indicating no prior context is available.
        """
        return (
            "No prior context available. This appears to be a fresh session. "
            "No memories were found to reconstruct previous work."
        )
