"""SQLite session database — durable conversation and trajectory history.

SYNTH:
    purpose: SQLite session database with FTS5 full-text search for durable conversation and trajectory history.
    axioms: [local_first, open_process, evidence_over_intuition, reversibility_awareness]
    objective: Every session and its full trajectory are durably stored, searchable, and recoverable from a single portable SQLite file.
    anti_patterns:
        - Never depend on external database services or cloud APIs.
        - Never silently drop trajectory entries — every action is recorded.
        - Never mutate the database schema without migration logic.
        - Never store un-scrubbed PII in session summaries or trajectory data.

Stores every session (conversation, task, improvement cycle) with full
trajectory logging. Supports full-text search over past sessions so the
agent can recall what it did, what worked, and what failed.

Inspired by Hermes Agent's SQLite session DB + session_search tool.

Contract:
    - All sessions are stored in a single SQLite file (portable, backupable).
    - Full-text search via SQLite FTS5 (no external services).
    - Sessions can be tagged, filtered, and retrieved by content.
    - Trajectory entries record every tool call, response, and outcome.
    - The database is the source of truth — rebuildable indexes sit beside it.
"""

# ┌─ synth ──────────────────────────────────────────────────────────────────┐
# @NCL{v=1.0;agent=builder;mod=session_db;ts=2026-08-18Z;tier=L3}
# #C Adapted from NoUs-fordge Nous-hub mvp_local_core
# #S{purpose="SQLite session database with FTS5 full-text search — durable conversation and trajectory history"}
# #I{1="single SQLite file — portable, backupable";2="FTS5 full-text search — no external services";3="trajectory logging — every tool call and outcome recorded";4="sessions can be tagged and filtered";5="source of truth — indexes rebuildable beside it"}
# #D{1="SQLite + FTS5"→="zero-dependency full-text search, built into stdlib";2="trajectory entries"→="structured record of every action within a session";3="tag-based filtering"→="sessions grouped by task type, outcome, etc."]
# #M{status=IMPLEMENTED;version=1.0.0;deps="sqlite3 (stdlib)"]
# #T{pass=0;fail=0;xfail=0}
# #W{1="FTS5 may not be available in all SQLite builds — falls back to LIKE search";2="large histories may need periodic archival"]
# #L{lexicon→docs/NOUS_LEXICON.md}
# └──────────────────────────────────────────────────────────────────────────┘

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """A single session record."""
    session_id: str
    title: str = ""
    tags: str = ""  # comma-separated
    model: str = ""
    provider: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    summary: str = ""
    outcome: str = ""  # "success", "failure", "partial", "interrupted"
    trajectory: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "tags": self.tags,
            "model": self.model,
            "provider": self.provider,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "summary": self.summary,
            "outcome": self.outcome,
            "trajectory_count": len(self.trajectory),
        }


class SessionDB:
    """SQLite-backed session database with full-text search.

    Usage:
        db = SessionDB("sessions.db")
        sid = db.create_session(title="Fix auth bug", tags="bugfix,auth")
        db.add_trajectory_entry(sid, {"tool": "read_file", "path": "auth.py"})
        db.update_session(sid, outcome="success", summary="Fixed JWT validation")
        results = db.search("JWT validation")
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        outcome TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS trajectory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        timestamp TEXT NOT NULL,
        entry_type TEXT NOT NULL,
        data TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_trajectory_session ON trajectory(session_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_tags ON sessions(tags);
    CREATE INDEX IF NOT EXISTS idx_sessions_outcome ON sessions(outcome);
    CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at);
    """

    FTS_SCHEMA = """
    CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
        session_id,
        title,
        tags,
        summary,
        content='sessions',
        content_rowid='rowid'
    );

    CREATE TRIGGER IF NOT EXISTS sessions_ai AFTER INSERT ON sessions BEGIN
        INSERT INTO sessions_fts(rowid, session_id, title, tags, summary)
        VALUES (new.rowid, new.session_id, new.title, new.tags, new.summary);
    END;

    CREATE TRIGGER IF NOT EXISTS sessions_ad AFTER DELETE ON sessions BEGIN
        INSERT INTO sessions_fts(sessions_fts, rowid, session_id, title, tags, summary)
        VALUES ('delete', old.rowid, old.session_id, old.title, old.tags, old.summary);
    END;

    CREATE TRIGGER IF NOT EXISTS sessions_au AFTER UPDATE ON sessions BEGIN
        INSERT INTO sessions_fts(sessions_fts, rowid, session_id, title, tags, summary)
        VALUES ('delete', old.rowid, old.session_id, old.title, old.tags, old.summary);
        INSERT INTO sessions_fts(rowid, session_id, title, tags, summary)
        VALUES (new.rowid, new.session_id, new.title, new.tags, new.summary);
    END;
    """

    def __init__(self, db_path: str = "nous_sessions.db"):
        self.db_path = str(db_path)
        self._fts_available = False
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(self.SCHEMA)
            # Try FTS5, fall back gracefully
            try:
                conn.executescript(self.FTS_SCHEMA)
                self._fts_available = True
                logger.info("SessionDB FTS5 full-text search enabled")
            except sqlite3.OperationalError as e:
                logger.warning(f"FTS5 not available, falling back to LIKE search: {e}")
                self._fts_available = False

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ── Session CRUD ──────────────────────────────────────────────────────

    def create_session(
        self,
        session_id: str | None = None,
        title: str = "",
        tags: str = "",
        model: str = "",
        provider: str = "",
    ) -> str:
        """Create a new session and return its ID."""
        if session_id is None:
            session_id = f"sess-{int(time.time())}-{hash(time.time()) % 10000}"
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, title, tags, model, provider, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, title, tags, model, provider, now, now),
            )
        logger.debug(f"Session created: {session_id}")
        return session_id

    def update_session(
        self,
        session_id: str,
        title: str | None = None,
        tags: str | None = None,
        summary: str | None = None,
        outcome: str | None = None,
    ) -> bool:
        """Update session fields."""
        updates = []
        values = []
        for field_name, value in [("title", title), ("tags", tags), ("summary", summary), ("outcome", outcome)]:
            if value is not None:
                updates.append(f"{field_name} = ?")
                values.append(value)
        if not updates:
            return False
        updates.append("updated_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(session_id)
        with self._get_conn() as conn:
            cursor = conn.execute(
                f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?",
                values,
            )
            return cursor.rowcount > 0

    def get_session(self, session_id: str, include_trajectory: bool = False) -> dict[str, Any] | None:
        """Get a session by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            session = dict(row)
            if include_trajectory:
                traj_rows = conn.execute(
                    "SELECT * FROM trajectory WHERE session_id = ? ORDER BY seq",
                    (session_id,),
                ).fetchall()
                session["trajectory"] = [
                    {"seq": r["seq"], "type": r["entry_type"], "timestamp": r["timestamp"],
                     "data": json.loads(r["data"])}
                    for r in traj_rows
                ]
            return session

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its trajectory entries."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            return cursor.rowcount > 0

    # ── Trajectory logging ────────────────────────────────────────────────

    def add_trajectory_entry(
        self,
        session_id: str,
        entry: dict[str, Any],
        entry_type: str = "action",
    ) -> int:
        """Add a trajectory entry to a session."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            # Get next seq
            row = conn.execute(
                "SELECT MAX(seq) as max_seq FROM trajectory WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = (row["max_seq"] or 0) + 1
            cursor = conn.execute(
                "INSERT INTO trajectory (session_id, seq, timestamp, entry_type, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, seq, now, entry_type, json.dumps(entry)),
            )
            # Update session timestamp
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            return cursor.lastrowid

    def get_trajectory(self, session_id: str) -> list[dict[str, Any]]:
        """Get the full trajectory for a session."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trajectory WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
            return [
                {"seq": r["seq"], "type": r["entry_type"], "timestamp": r["timestamp"],
                 "data": json.loads(r["data"])}
                for r in rows
            ]

    # ── Search ────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 10,
        tags: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search sessions by content.

        Uses FTS5 if available, otherwise falls back to LIKE search.
        """
        if self._fts_available:
            return self._search_fts(query, limit, tags, outcome)
        return self._search_like(query, limit, tags, outcome)

    def _search_fts(self, query: str, limit: int, tags: str | None, outcome: str | None) -> list[dict[str, Any]]:
        """Full-text search using FTS5."""
        sql = """
            SELECT s.* FROM sessions s
            JOIN sessions_fts f ON s.rowid = f.rowid
            WHERE sessions_fts MATCH ?
        """
        params: list[Any] = [query]
        if tags:
            sql += " AND s.tags LIKE ?"
            params.append(f"%{tags}%")
        if outcome:
            sql += " AND s.outcome = ?"
            params.append(outcome)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def _search_like(self, query: str, limit: int, tags: str | None, outcome: str | None) -> list[dict[str, Any]]:
        """Fallback search using LIKE."""
        sql = "SELECT * FROM sessions WHERE (title LIKE ? OR summary LIKE ? OR tags LIKE ?)"
        pattern = f"%{query}%"
        params: list[Any] = [pattern, pattern, pattern]
        if tags:
            sql += " AND tags LIKE ?"
            params.append(f"%{tags}%")
        if outcome:
            sql += " AND outcome = ?"
            params.append(outcome)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    # ── Statistics ────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Get database statistics."""
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
            by_outcome = conn.execute(
                "SELECT outcome, COUNT(*) as c FROM sessions GROUP BY outcome"
            ).fetchall()
            by_tag_rows = conn.execute(
                "SELECT tags, COUNT(*) as c FROM sessions GROUP BY tags"
            ).fetchall()
            total_traj = conn.execute("SELECT COUNT(*) as c FROM trajectory").fetchone()["c"]
            return {
                "total_sessions": total,
                "total_trajectory_entries": total_traj,
                "by_outcome": {r["outcome"] or "unknown": r["c"] for r in by_outcome},
                "fts_available": self._fts_available,
                "db_path": self.db_path,
            }

    def get_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get most recently updated sessions."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
