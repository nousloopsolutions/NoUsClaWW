"""Memory manager — unified interface for events, memories, lessons, and feedback.

Provides the storage and retrieval layer that the health engine modules
(consolidation, rehearsal, self-test, etc.) depend on. Built on plain SQLite
for zero-install local use, consistent with the session DB and knowledge graph.

Contract:
    - All data in a single SQLite file (portable, backupable).
    - Events are raw observations; memories are durable facts/lessons/rules.
    - Every memory carries an importance score (0.0-1.0) and access count.
    - Feedback and lesson outcomes are tracked for adaptive importance.
    - Conflict detection compares fact values for the same subject.

SYNTH:
    purpose: Unified memory manager for events, durable memories, feedback, and lesson outcomes on SQLite
    axioms: [local_first, open_process, evidence_over_intuition, reversibility_awareness, epistemic_boundary]
    objective: Provide a single interface for the health engine to store, retrieve, score, and track
        every memory artifact, so consolidation/rehearsal/self-test can operate without knowing storage details.
        Also provides Hebbian vector crystallization (access-frequency sharpening) and content-free
        tombstone deletion with cascading lineage cleanup and residual verification.
    anti_patterns:
        - Depending on external database services or cloud APIs
        - Storing memories without an importance score
        - Silently dropping events instead of marking them consolidated
        - Allowing importance to grow without bounds
        - Storing any content in a tombstone (tombstones are content-free audit markers only)
        - Allowing crystallization alpha to exceed 0.3 (prevents large vector jumps)
        - Deleting a memory without verifying no residuals remain
#C Inspired by PMB (Project Memory Bank) sleep engine
#C Inspired by nous_memory_mcp crystallization engine (Hebbian vector sharpening)
#C Inspired by Nous-hub deletion_engine (content-free tombstone, cascading delete with lineage)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Tombstone:
    """Content-free tombstone marking a deleted memory.

    Stores ONLY the memory_id, timestamp, and reason — no content, no metadata,
    no hashes. This preserves an audit trail of deletions without retaining
    any of the deleted material, consistent with the reversibility_awareness
    axiom: the fact of deletion is recorded, but the deleted content is not.

    Attributes:
        memory_id: ID of the deleted memory.
        timestamp: When the deletion occurred (Unix epoch seconds).
        reason: Why the memory was deleted (free-text, no content from the memory).
    """

    memory_id: str
    timestamp: float
    reason: str = ""


class MemoryManager:
    """Unified memory manager backed by SQLite.

    Manages four artifact types:
      1. Events — raw observations awaiting consolidation.
      2. Memories — durable facts, lessons, and rules with importance scores.
      3. Feedback — user verdicts on memory usefulness.
      4. Lesson outcomes — success/failure tracking for earned memory metrics.

    Usage:
        mm = MemoryManager("nous_memory.db")
        eid = mm.add_event({"type": "observation", "content": "User prefers dark mode"})
        facts = mm.get_events(unconsolidated_only=True)
        mid = mm.store_memory({"type": "fact", "content": "User prefers dark mode", "importance": 0.8})
        results = mm.search_memories("dark mode")
        mm.update_importance(mid, +0.1)
        mm.record_feedback(mid, "useful")
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        metadata TEXT NOT NULL DEFAULT '{}',
        timestamp REAL NOT NULL,
        consolidated INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
    CREATE INDEX IF NOT EXISTS idx_events_consol ON events(consolidated);
    CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

    CREATE TABLE IF NOT EXISTS memories (
        memory_id TEXT PRIMARY KEY,
        memory_type TEXT NOT NULL DEFAULT 'fact',
        content TEXT NOT NULL DEFAULT '',
        subject TEXT NOT NULL DEFAULT '',
        importance REAL NOT NULL DEFAULT 0.5,
        access_count INTEGER NOT NULL DEFAULT 0,
        last_accessed REAL,
        created_at REAL NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(memory_type);
    CREATE INDEX IF NOT EXISTS idx_mem_importance ON memories(importance);
    CREATE INDEX IF NOT EXISTS idx_mem_subject ON memories(subject);
    CREATE INDEX IF NOT EXISTS idx_mem_content ON memories(content);

    CREATE TABLE IF NOT EXISTS feedback (
        feedback_id TEXT PRIMARY KEY,
        memory_id TEXT NOT NULL,
        verdict TEXT NOT NULL,
        timestamp REAL NOT NULL,
        applied INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_fb_memory ON feedback(memory_id);
    CREATE INDEX IF NOT EXISTS idx_fb_applied ON feedback(applied);

    CREATE TABLE IF NOT EXISTS lesson_outcomes (
        outcome_id TEXT PRIMARY KEY,
        lesson_id TEXT NOT NULL,
        success INTEGER NOT NULL,
        timestamp REAL NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_lo_lesson ON lesson_outcomes(lesson_id);

    CREATE TABLE IF NOT EXISTS consolidation_log (
        consolidation_id TEXT PRIMARY KEY,
        timestamp REAL NOT NULL,
        events_consolidated INTEGER NOT NULL,
        facts_generated INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS crystallized_vectors (
        memory_id TEXT PRIMARY KEY,
        current_vector TEXT NOT NULL,
        original_vector TEXT NOT NULL,
        access_count INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        last_accessed REAL
    );

    CREATE INDEX IF NOT EXISTS idx_cv_access ON crystallized_vectors(access_count);

    CREATE TABLE IF NOT EXISTS tombstones (
        memory_id TEXT PRIMARY KEY,
        timestamp REAL NOT NULL,
        reason TEXT NOT NULL DEFAULT ''
    );

    CREATE INDEX IF NOT EXISTS idx_tomb_ts ON tombstones(timestamp);
    """

    def __init__(self, db_path: str = "nous_memory.db"):
        self.db_path = str(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(self.SCHEMA)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _generate_id(self, prefix: str = "mem") -> str:
        return f"{prefix}-{int(time.time())}-{hash(time.time()) % 100000}"

    # ── Events ────────────────────────────────────────────────────────────

    def add_event(self, event: dict[str, Any]) -> str:
        """Store a raw event and return its ID.

        Args:
            event: Dict with optional keys 'type', 'content', 'metadata'.
                   Timestamp is set automatically if not provided.

        Returns:
            The generated event_id.
        """
        event_id = event.get("event_id") or self._generate_id("evt")
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO events (event_id, event_type, content, metadata, timestamp, consolidated) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (
                    event_id,
                    event.get("type", event.get("event_type", "")),
                    event.get("content", ""),
                    json.dumps(event.get("metadata", {})),
                    event.get("timestamp", now),
                ),
            )
        return event_id

    def get_events(
        self,
        since: float | None = None,
        unconsolidated_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve events, optionally filtered by time and consolidation status.

        Args:
            since: Only events after this Unix timestamp.
            unconsolidated_only: If True, only return events not yet consolidated.
            limit: Maximum number of events to return.

        Returns:
            List of event dicts with keys: event_id, event_type, content,
            metadata (parsed dict), timestamp, consolidated.
        """
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if since is not None:
            sql += " AND timestamp >= ?"
            params.append(since)
        if unconsolidated_only:
            sql += " AND consolidated = 0"
        sql += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["metadata"] = json.loads(d.get("metadata", "{}"))
                results.append(d)
            return results

    def get_event_count(self, unconsolidated_only: bool = False) -> int:
        """Count events, optionally filtered by consolidation status."""
        sql = "SELECT COUNT(*) as c FROM events"
        if unconsolidated_only:
            sql += " WHERE consolidated = 0"
        with self._get_conn() as conn:
            row = conn.execute(sql).fetchone()
            return row["c"] if row else 0

    def mark_events_consolidated(self, event_ids: list[str]) -> int:
        """Mark a list of events as consolidated. Returns count updated."""
        if not event_ids:
            return 0
        placeholders = ",".join("?" * len(event_ids))
        with self._get_conn() as conn:
            cursor = conn.execute(
                f"UPDATE events SET consolidated = 1 WHERE event_id IN ({placeholders})",
                event_ids,
            )
            return cursor.rowcount

    def get_last_consolidation_time(self) -> float | None:
        """Return the timestamp of the most recent consolidation, or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT timestamp FROM consolidation_log ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            return row["timestamp"] if row else None

    def log_consolidation(self, events_consolidated: int, facts_generated: int) -> str:
        """Record a consolidation event in the log. Returns the log ID."""
        log_id = self._generate_id("con")
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO consolidation_log (consolidation_id, timestamp, events_consolidated, facts_generated) "
                "VALUES (?, ?, ?, ?)",
                (log_id, time.time(), events_consolidated, facts_generated),
            )
        return log_id

    # ── Memories ──────────────────────────────────────────────────────────

    def store_memory(self, memory: dict[str, Any]) -> str:
        """Store a durable memory (fact, lesson, rule) and return its ID.

        Args:
            memory: Dict with keys 'type' (or 'memory_type'), 'content',
                    'importance' (0.0-1.0, default 0.5), 'subject' (optional),
                    'metadata' (optional dict).

        Returns:
            The generated or provided memory_id.
        """
        memory_id = memory.get("memory_id") or self._generate_id("mem")
        now = time.time()
        importance = max(0.0, min(1.0, memory.get("importance", 0.5)))
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memories "
                "(memory_id, memory_type, content, subject, importance, access_count, last_accessed, created_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    memory.get("type", memory.get("memory_type", "fact")),
                    memory.get("content", ""),
                    memory.get("subject", ""),
                    importance,
                    memory.get("access_count", 0),
                    memory.get("last_accessed"),
                    memory.get("created_at", now),
                    json.dumps(memory.get("metadata", {})),
                ),
            )
        return memory_id

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        """Retrieve a single memory by ID. Returns None if not found."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["metadata"] = json.loads(d.get("metadata", "{}"))
            return d

    def get_all_memories(self, memory_type: str | None = None) -> list[dict[str, Any]]:
        """Retrieve all memories, optionally filtered by type."""
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if memory_type:
            sql += " AND memory_type = ?"
            params.append(memory_type)
        sql += " ORDER BY importance DESC, created_at DESC"
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_memory(r) for r in rows]

    def search_memories(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search memories by content substring match.

        Args:
            query: Search string matched against content and subject.
            limit: Maximum results.

        Returns:
            List of matching memory dicts, ordered by importance.
        """
        pattern = f"%{query}%"
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE content LIKE ? OR subject LIKE ? "
                "ORDER BY importance DESC, access_count DESC LIMIT ?",
                (pattern, pattern, limit),
            ).fetchall()
            return [self._row_to_memory(r) for r in rows]

    def update_importance(self, memory_id: str, delta: float) -> bool:
        """Adjust a memory's importance by delta, clamped to [0.0, 1.0].

        Returns:
            True if the memory was found and updated.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT importance FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            if not row:
                return False
            new_importance = max(0.0, min(1.0, row["importance"] + delta))
            conn.execute(
                "UPDATE memories SET importance = ? WHERE memory_id = ?",
                (new_importance, memory_id),
            )
            return True

    def set_importance(self, memory_id: str, importance: float) -> bool:
        """Set a memory's importance to an absolute value, clamped to [0.0, 1.0].

        Returns:
            True if the memory was found and updated.
        """
        importance = max(0.0, min(1.0, importance))
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE memories SET importance = ? WHERE memory_id = ?",
                (importance, memory_id),
            )
            return cursor.rowcount > 0

    def bump_access(self, memory_id: str) -> bool:
        """Increment a memory's access count and update last_accessed.

        Returns:
            True if the memory was found and updated.
        """
        now = time.time()
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE memories SET access_count = access_count + 1, last_accessed = ? "
                "WHERE memory_id = ?",
                (now, memory_id),
            )
            return cursor.rowcount > 0

    def get_idle_important(
        self, days_idle: int = 7, min_importance: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Find memories that are important but haven't been accessed recently.

        Args:
            days_idle: Minimum days since last access to consider idle.
            min_importance: Minimum importance threshold.

        Returns:
            List of memory dicts meeting both criteria.
        """
        cutoff = time.time() - (days_idle * 86400)
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE importance >= ? "
                "AND (last_accessed IS NULL OR last_accessed < ?) "
                "ORDER BY importance DESC",
                (min_importance, cutoff),
            ).fetchall()
            return [self._row_to_memory(r) for r in rows]

    def _row_to_memory(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert a database row to a memory dict with parsed metadata."""
        d = dict(row)
        d["metadata"] = json.loads(d.get("metadata", "{}"))
        return d

    # ── Feedback ──────────────────────────────────────────────────────────

    def record_feedback(self, memory_id: str, verdict: str) -> bool:
        """Store a user feedback verdict for a memory.

        Args:
            memory_id: The memory the feedback applies to.
            verdict: One of 'useful', 'wrong', 'irrelevant'.

        Returns:
            True if the memory exists and feedback was recorded.
        """
        valid_verdicts = {"useful", "wrong", "irrelevant"}
        if verdict not in valid_verdicts:
            logger.warning(f"Invalid feedback verdict: {verdict}")
            return False
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            if not row:
                return False
            fb_id = self._generate_id("fb")
            conn.execute(
                "INSERT INTO feedback (feedback_id, memory_id, verdict, timestamp, applied) "
                "VALUES (?, ?, ?, ?, 0)",
                (fb_id, memory_id, verdict, time.time()),
            )
            return True

    def get_pending_feedback(self) -> list[dict[str, Any]]:
        """Return all feedback entries that haven't been applied yet."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM feedback WHERE applied = 0 ORDER BY timestamp ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_feedback_applied(self, feedback_ids: list[str]) -> int:
        """Mark feedback entries as applied. Returns count updated."""
        if not feedback_ids:
            return 0
        placeholders = ",".join("?" * len(feedback_ids))
        with self._get_conn() as conn:
            cursor = conn.execute(
                f"UPDATE feedback SET applied = 1 WHERE feedback_id IN ({placeholders})",
                feedback_ids,
            )
            return cursor.rowcount

    # ── Lesson outcomes ───────────────────────────────────────────────────

    def record_lesson_outcome(self, lesson_id: str, success: bool) -> bool:
        """Record a success or failure outcome for a lesson.

        Args:
            lesson_id: The memory_id of the lesson.
            success: True for success, False for failure.

        Returns:
            True if the lesson exists and outcome was recorded.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM memories WHERE memory_id = ?", (lesson_id,)
            ).fetchone()
            if not row:
                return False
            outcome_id = self._generate_id("out")
            conn.execute(
                "INSERT INTO lesson_outcomes (outcome_id, lesson_id, success, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (outcome_id, lesson_id, 1 if success else 0, time.time()),
            )
            return True

    def get_lesson_outcomes(self, lesson_id: str) -> dict[str, Any]:
        """Get aggregated outcomes for a lesson.

        Returns:
            Dict with keys: lesson_id, total, successes, failures, success_rate.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT success FROM lesson_outcomes WHERE lesson_id = ?",
                (lesson_id,),
            ).fetchall()
            total = len(rows)
            successes = sum(1 for r in rows if r["success"])
            failures = total - successes
            return {
                "lesson_id": lesson_id,
                "total": total,
                "successes": successes,
                "failures": failures,
                "success_rate": successes / total if total > 0 else 0.0,
            }

    def get_all_lessons(self) -> list[dict[str, Any]]:
        """Retrieve all memories of type 'lesson'."""
        return self.get_all_memories(memory_type="lesson")

    # ── Conflict detection support ────────────────────────────────────────

    def get_facts_by_subject(self) -> dict[str, list[dict[str, Any]]]:
        """Group all fact-type memories by their subject field.

        Returns:
            Dict mapping subject -> list of fact memories with that subject.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE memory_type = 'fact' AND subject != '' "
                "ORDER BY subject, importance DESC"
            ).fetchall()
            groups: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                mem = self._row_to_memory(r)
                groups.setdefault(mem["subject"], []).append(mem)
            return groups

    # ── Stats ─────────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return summary statistics about the memory store."""
        with self._get_conn() as conn:
            total_events = conn.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]
            unconsol = conn.execute(
                "SELECT COUNT(*) as c FROM events WHERE consolidated = 0"
            ).fetchone()["c"]
            total_memories = conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
            total_feedback = conn.execute("SELECT COUNT(*) as c FROM feedback").fetchone()["c"]
            pending_fb = conn.execute(
                "SELECT COUNT(*) as c FROM feedback WHERE applied = 0"
            ).fetchone()["c"]
            by_type = conn.execute(
                "SELECT memory_type, COUNT(*) as c FROM memories GROUP BY memory_type"
            ).fetchall()
            return {
                "total_events": total_events,
                "unconsolidated_events": unconsol,
                "total_memories": total_memories,
                "total_feedback": total_feedback,
                "pending_feedback": pending_fb,
                "by_memory_type": {r["memory_type"]: r["c"] for r in by_type},
                "db_path": self.db_path,
            }

    # ── Tombstone-based deletion ──────────────────────────────────────────

    def delete_with_tombstone(self, memory_id: str, reason: str = "") -> bool:
        """Delete a memory's content but keep a content-free tombstone.

        Removes the memory row and all derived artifacts (feedback, lesson
        outcomes, crystallized vectors) in a cascading delete. A content-free
        tombstone (memory_id + timestamp + reason only, NO content) is left
        behind as an audit trail.

        This implements the content-free tombstone pattern: the fact of
        deletion is recorded for governance history, but no raw content
        or hashes are retained.

        Args:
            memory_id: The memory to delete.
            reason: Why the memory is being deleted (free-text, no content
                    from the memory itself).

        Returns:
            True if the memory was found and deleted, False if not found.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            if not row:
                return False

            now = time.time()

            # Cascading delete: remove memory and all derived artifacts
            conn.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
            conn.execute("DELETE FROM feedback WHERE memory_id = ?", (memory_id,))
            conn.execute(
                "DELETE FROM lesson_outcomes WHERE lesson_id = ?", (memory_id,)
            )
            conn.execute(
                "DELETE FROM crystallized_vectors WHERE memory_id = ?", (memory_id,)
            )

            # Create content-free tombstone (no content stored)
            conn.execute(
                "INSERT OR REPLACE INTO tombstones (memory_id, timestamp, reason) "
                "VALUES (?, ?, ?)",
                (memory_id, now, reason),
            )
            return True

    def verify_no_residuals(self, memory_id: str) -> bool:
        """Verify that no content remains for a tombstoned memory.

        Checks that the memory row, associated feedback, lesson outcomes,
        and crystallized vector have all been removed. Only the tombstone
        should remain.

        Args:
            memory_id: The memory to verify.

        Returns:
            True if no residual content remains (clean), False if any
            content artifacts are still present.
        """
        with self._get_conn() as conn:
            mem = conn.execute(
                "SELECT 1 FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            if mem:
                logger.warning(f"Residual content found in memories for {memory_id}")
                return False

            fb = conn.execute(
                "SELECT 1 FROM feedback WHERE memory_id = ? LIMIT 1", (memory_id,)
            ).fetchone()
            if fb:
                logger.warning(f"Residual content found in feedback for {memory_id}")
                return False

            lo = conn.execute(
                "SELECT 1 FROM lesson_outcomes WHERE lesson_id = ? LIMIT 1",
                (memory_id,),
            ).fetchone()
            if lo:
                logger.warning(
                    f"Residual content found in lesson_outcomes for {memory_id}"
                )
                return False

            cv = conn.execute(
                "SELECT 1 FROM crystallized_vectors WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if cv:
                logger.warning(
                    f"Residual content found in crystallized_vectors for {memory_id}"
                )
                return False

            return True

    def list_tombstones(self) -> list[Tombstone]:
        """Return all tombstones as an audit trail of deletions.

        Tombstones are content-free — they contain only memory_id, timestamp,
        and reason. No content from the deleted memories is retained.

        Returns:
            List of Tombstone objects, ordered by timestamp descending
            (most recent deletions first).
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT memory_id, timestamp, reason FROM tombstones "
                "ORDER BY timestamp DESC"
            ).fetchall()
            return [
                Tombstone(
                    memory_id=r["memory_id"],
                    timestamp=r["timestamp"],
                    reason=r["reason"],
                )
                for r in rows
            ]
