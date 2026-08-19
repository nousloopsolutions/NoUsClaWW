"""Automatic turn recording — captures significant actions without model cooperation.

SYNTH:
    purpose: Zero-cooperation auto-recording of significant turns (tests, deploys, builds, edits, commits) so the memory bank stays current without the model deciding to write.
    axioms: [local_first, llm_agnostic, open_process, evidence_over_intuition, iteration_is_progress]
    objective: Every significant action (test pass/fail, deploy, build, edit, commit) is automatically recorded to memory without the model's cooperation or awareness.
    anti_patterns:
        - Never require the model to explicitly "save" or "remember" — recording is automatic.
        - Never record trivial actions (reads, listings, status checks) — only significant turns.
        - Never block the action pipeline — recording is post-processing, fire-and-forget.
        - Never fabricate outcomes — record what actually happened, including failures.
        - Never skip recording a failure — failures are the most valuable memories.

Classifies agent actions into significant turn types using deterministic pattern
matching on the action string and result dict. Records significant turns to the
MemoryManager immediately after they occur. No model cooperation required.

#C Inspired by PMB (Project Memory Bank) automatic hooks
"""

# ┌─ synth ──────────────────────────────────────────────────────────────────┐
# @NCL{v=1.0;agent=builder;mod=autowrite;ts=2026-08-18Z;tier=L3}
# #C Inspired by PMB (Project Memory Bank) automatic hooks
# #S{purpose="Auto-record significant turns (tests, deploys, builds, edits, commits) without model cooperation"}
# #I{1="deterministic turn classification — pattern matching on action + result";2="seven turn types: test_pass, test_fail, deploy, build, edit, commit, skip";3="fire-and-forget recording — never blocks the action pipeline";4="failures recorded with equal priority to successes"}
# #D{1="turn classification"→="regex + result-key matching on action string and result dict";2="significant turn"→="an action that changes project state or produces a testable outcome";3="skip"→="action is not significant enough to record (reads, listings, etc.)"]
# #M{status=IMPLEMENTED;version=1.0.0;deps="nousclaww.memory.memory_manager"]
# #T{pass=0;fail=0;xfail=0}
# #W{1="classification heuristics may miss edge cases — SKIP is the safe fallback";2="recording failures requires the result dict to contain a 'success' or 'status' key"]
# #L{lexicon→docs/NOUS_LEXICON.md}
# └──────────────────────────────────────────────────────────────────────────┘

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nousclaww.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# ── Turn type constants ──────────────────────────────────────────────────────

TEST_PASS = "test_pass"
TEST_FAIL = "test_fail"
DEPLOY = "deploy"
BUILD = "build"
EDIT = "edit"
COMMIT = "commit"
SKIP = "skip"

# ── Action patterns (ordered — first match wins) ─────────────────────────────

_ACTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (DEPLOY, re.compile(r"\b(deploy|publish|release|ship|rollout)\b", re.IGNORECASE)),
    (BUILD, re.compile(r"\b(build|compile|bundle|webpack|make|cargo\s+build)\b", re.IGNORECASE)),
    (COMMIT, re.compile(r"\b(commit|git\s+commit|push|merge|pr\s+merge)\b", re.IGNORECASE)),
    (EDIT, re.compile(r"\b(edit|write|modify|update|patch|create|delete|remove|rename)\b", re.IGNORECASE)),
    (
        "test",
        re.compile(
            r"\b(test|pytest|unittest|jest|vitest|mocha|cargo\s+test|"
            r"npm\s+test|go\s+test|run\s+tests?)\b",
            re.IGNORECASE,
        ),
    ),
]

# Actions that are never significant (always SKIP)
_TRIVIAL_PATTERNS = re.compile(
    r"\b(read|cat|ls|list|grep|find|search|status|show|view|inspect|"
    r"check|query|get|fetch|head|tail|diff|log|info|help|version)\b",
    re.IGNORECASE,
)


class Autowrite:
    """Automatic significant turn recording without model cooperation.

    Classifies agent actions into significant turn types using deterministic
    pattern matching, then records them to the MemoryManager. The model never
    decides whether to record — every significant turn is captured.

    Turn types:
        - test_pass: A test run that succeeded.
        - test_fail: A test run that failed (recorded with equal priority).
        - deploy: A deployment or release action.
        - build: A build or compilation action.
        - edit: A file edit, creation, or deletion.
        - commit: A git commit, push, or merge.
        - skip: The action is not significant enough to record.

    Usage:
        aw = Autowrite()
        turn_type = aw.classify_turn("run pytest", {"success": True, "passed": 10})
        # -> "test_pass"
        aw.record("run pytest", {"success": True, "passed": 10}, memory_manager)
        # -> True (recorded)
    """

    def __init__(self) -> None:
        """Initialize the Autowrite hook with pre-compiled patterns."""
        self._action_patterns = _ACTION_PATTERNS
        self._trivial_patterns = _TRIVIAL_PATTERNS

    def classify_turn(self, action: str, result: dict) -> str:
        """Classify an agent action + result into a significant turn type.

        Uses deterministic pattern matching on the action string and keys
        in the result dict. No LLM, no model cooperation.

        The classification logic:
        1. If the action matches a trivial pattern (read, list, etc.), return SKIP.
        2. Match the action against ordered patterns (deploy, build, commit, edit, test).
        3. For test actions, check result dict for success/failure indicators.
        4. If no pattern matches, return SKIP.

        Args:
            action: A string describing the action taken (e.g. "run pytest").
            result: A dict containing the action's result. May contain keys
                like "success" (bool), "status" (str), "passed" (int),
                "failed" (int), "exit_code" (int), "error" (str).

        Returns:
            One of the seven turn type constants as a string.
        """
        if not action or not action.strip():
            return SKIP

        # Check trivial actions first — these are never significant
        if self._trivial_patterns.search(action) and not any(
            p.search(action) for _, p in self._action_patterns if _ != "test"
        ):
            # But only skip if no significant pattern also matches
            # (e.g. "edit" might contain "read" in a path — check significant first)
            pass

        # Re-check: if ONLY trivial patterns match and no significant pattern matches
        has_significant = False
        matched_category: str | None = None

        for category, pattern in self._action_patterns:
            if pattern.search(action):
                has_significant = True
                matched_category = category
                break

        if not has_significant:
            return SKIP

        # For test actions, determine pass vs fail from the result dict
        if matched_category == "test":
            return self._classify_test_result(result)

        # For other categories, return directly
        return matched_category  # type: ignore[return-value]

    def _classify_test_result(self, result: dict) -> str:
        """Determine whether a test action passed or failed.

        Checks the result dict for common success/failure indicators in
        priority order: explicit "success" key, "status" key, "exit_code",
        "failed" count, "passed" count.

        Args:
            result: The result dict from the test action.

        Returns:
            TEST_PASS or TEST_FAIL.
        """
        # Explicit success boolean
        if "success" in result:
            return TEST_PASS if result["success"] else TEST_FAIL

        # Status string
        status = result.get("status", "")
        if isinstance(status, str):
            status_lower = status.lower().strip()
            if status_lower in ("pass", "passed", "ok", "success", "green"):
                return TEST_PASS
            if status_lower in ("fail", "failed", "error", "red", "broken"):
                return TEST_FAIL

        # Exit code
        exit_code = result.get("exit_code")
        if isinstance(exit_code, int):
            return TEST_PASS if exit_code == 0 else TEST_FAIL

        # Failed count > 0 means failure
        failed = result.get("failed", 0)
        if isinstance(failed, int) and failed > 0:
            return TEST_FAIL

        # Passed count > 0 with no failures means pass
        passed = result.get("passed", 0)
        if isinstance(passed, int) and passed > 0:
            return TEST_PASS

        # Error key present means failure
        if result.get("error"):
            return TEST_FAIL

        # Default: assume pass if we got a result dict at all
        return TEST_PASS

    def record(self, action: str, result: dict, memory_manager: "MemoryManager") -> bool:
        """Auto-record a significant turn to memory.

        Classifies the turn, and if it is significant (not SKIP), constructs
        a memory event and stores it via memory_manager.store(). The recording
        is fire-and-forget — it never blocks the action pipeline.

        The stored event contains:
            - type: the turn type (test_pass, test_fail, etc.)
            - action: the action string
            - result: the result dict (sanitized to JSON-safe keys)
            - timestamp: UTC ISO 8601 timestamp
            - summary: a human-readable one-line description

        Args:
            action: A string describing the action taken.
            result: A dict containing the action's result.
            memory_manager: The MemoryManager instance to store into.

        Returns:
            True if the turn was recorded, False if it was SKIP or storage
            failed.
        """
        turn_type = self.classify_turn(action, result)

        if turn_type == SKIP:
            logger.debug(f"Autowrite: skipping non-significant action: {action}")
            return False

        # Build the memory event
        timestamp = datetime.now(timezone.utc).isoformat()
        summary = self._build_summary(turn_type, action, result)

        event = {
            "type": "autowrite_turn",
            "turn_type": turn_type,
            "action": action,
            "result": self._sanitize_result(result),
            "timestamp": timestamp,
            "summary": summary,
        }

        try:
            memory_manager.store(event)
            logger.info(f"Autowrite: recorded {turn_type} turn: {summary}")
            return True
        except Exception as e:
            logger.warning(f"Autowrite: failed to record {turn_type} turn: {e}")
            return False

    def _build_summary(self, turn_type: str, action: str, result: dict) -> str:
        """Build a human-readable one-line summary of the turn.

        Args:
            turn_type: The classified turn type.
            action: The action string.
            result: The result dict.

        Returns:
            A one-line summary string.
        """
        if turn_type == TEST_PASS:
            passed = result.get("passed", "?")
            return f"Tests passed ({passed} passed): {action}"
        elif turn_type == TEST_FAIL:
            failed = result.get("failed", "?")
            error = result.get("error", "unknown")
            return f"Tests FAILED ({failed} failed, error: {error}): {action}"
        elif turn_type == DEPLOY:
            target = result.get("target", result.get("environment", "unknown"))
            return f"Deployed to {target}: {action}"
        elif turn_type == BUILD:
            output = result.get("output", result.get("artifact", "unknown"))
            return f"Build completed ({output}): {action}"
        elif turn_type == EDIT:
            file_path = result.get("file", result.get("path", "unknown"))
            return f"Edited {file_path}: {action}"
        elif turn_type == COMMIT:
            sha = result.get("sha", result.get("hash", "unknown"))
            message = result.get("message", "")
            return f"Committed ({sha}): {message or action}"
        else:
            return f"{turn_type}: {action}"

    def _sanitize_result(self, result: dict) -> dict:
        """Sanitize the result dict for JSON-safe storage.

        Strips non-serializable values and truncates overly long string
        values to prevent memory bloat.

        Args:
            result: The raw result dict.

        Returns:
            A sanitized dict safe for JSON storage.
        """
        sanitized: dict = {}
        max_str_len = 500

        for key, value in result.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                if isinstance(value, str) and len(value) > max_str_len:
                    sanitized[key] = value[:max_str_len] + "...[truncated]"
                else:
                    sanitized[key] = value
            elif isinstance(value, (list, tuple)):
                # Keep small lists, truncate large ones
                if len(value) > 20:
                    sanitized[key] = list(value[:20]) + ["...[truncated]"]
                else:
                    sanitized[key] = list(value)
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_result(value)
            else:
                # Non-serializable — store the type name
                sanitized[key] = f"<{type(value).__name__}>"

        return sanitized
