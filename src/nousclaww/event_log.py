"""
Observability event log — append-only SQLite audit trail for AI operations.

Captures every AI operation as a structured event. This is the foundation
of the observability system — every module that does AI work should emit
events through this logger. Events are persisted to SQLite for querying
and displayed in the diagnostics panel.

Contract:
    - Every AI operation emits at least one event
    - Events are never deleted (append-only audit trail)
    - Events record inputs, outputs, duration, and status
    - Events are queryable by type, module, status, and time range
    - Event log is 100% local — no data leaves the machine

SYNTH:
    purpose: Append-only SQLite event log capturing every AI operation for local observability and audit
    axioms: [local_first, open_process, evidence_over_intuition, honest_failure_over_fake_success, epistemic_boundary]
    objective: Every AI operation is recorded with inputs, outputs, duration, and status; events are queryable and never deleted; all data stays local
    anti_patterns:
        - Deleting or modifying logged events
        - Sending event data to any remote service
        - Logging without recording status (started/completed/failed/unknown)
        - Skipping event logging for any AI operation
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core

import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EventStatus(Enum):
    """Status of an AI operation."""
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"  # abstention


class EventType(Enum):
    """Type of AI operation."""
    IMPORT = "import"
    PARSE = "parse"
    CHUNK = "chunk"
    EMBED = "embed"
    RETRIEVE = "retrieve"
    CITE = "cite"
    GENERATE = "generate"
    DELETE = "delete"
    REBUILD = "rebuild"
    QUERY = "query"
    REFLECT = "reflect"  # self-reflection
    IMPROVE = "improve"  # self-improvement


@dataclass
class AIEvent:
    """A single AI operation event."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_type: str = ""
    module: str = ""
    operation: str = ""
    status: str = EventStatus.STARTED.value
    duration_ms: float = 0.0
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class EventLog:
    """Append-only event log for AI operations.

    All events are stored in SQLite. The log is queryable by type,
    module, status, and time range. Events are never deleted.

    For in-memory databases (:memory:), a single persistent connection
    is used so the database persists across operations.
    """

    SCHEMA_VERSION = "1.0"

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        # For :memory: databases, we must keep a single connection alive
        # because each new connection to :memory: creates a fresh database.
        self._conn = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a database connection."""
        if self.db_path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(self.db_path)
                self._conn.row_factory = sqlite3.Row
            return self._conn
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_events (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                module TEXT NOT NULL,
                operation TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms REAL DEFAULT 0,
                inputs TEXT,        -- JSON
                outputs TEXT,       -- JSON
                error TEXT,
                metadata TEXT,      -- JSON
                schema_version TEXT DEFAULT '1.0'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_type ON ai_events(event_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_module ON ai_events(module)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_status ON ai_events(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON ai_events(timestamp)
        """)
        conn.commit()
        if self.db_path != ":memory:":
            conn.close()

    def log(self, event: AIEvent) -> str:
        """Log an event. Returns the event_id."""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO ai_events
                (event_id, timestamp, event_type, module, operation,
                 status, duration_ms, inputs, outputs, error, metadata, schema_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.event_id,
            event.timestamp,
            event.event_type,
            event.module,
            event.operation,
            event.status,
            event.duration_ms,
            json.dumps(event.inputs, default=str),
            json.dumps(event.outputs, default=str),
            event.error,
            json.dumps(event.metadata, default=str),
            self.SCHEMA_VERSION,
        ))
        conn.commit()
        if self.db_path != ":memory:":
            conn.close()
        return event.event_id

    def log_operation(
        self,
        event_type: str,
        module: str,
        operation: str,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        status: str = EventStatus.COMPLETED.value,
        duration_ms: float = 0.0,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Convenience method to log a completed operation."""
        event = AIEvent(
            event_type=event_type,
            module=module,
            operation=operation,
            status=status,
            duration_ms=duration_ms,
            inputs=inputs or {},
            outputs=outputs or {},
            error=error,
            metadata=metadata or {},
        )
        return self.log(event)

    def query(
        self,
        event_type: str | None = None,
        module: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query events with optional filters."""
        sql = "SELECT * FROM ai_events WHERE 1=1"
        params: list[Any] = []
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if module:
            sql += " AND module = ?"
            params.append(module)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        if until:
            sql += " AND timestamp <= ?"
            params.append(until)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        conn = self._get_conn()
        rows = conn.execute(sql, params).fetchall()
        if self.db_path != ":memory:":
            conn.close()

        results = []
        for row in rows:
            d = dict(row)
            d["inputs"] = json.loads(d["inputs"]) if d["inputs"] else {}
            d["outputs"] = json.loads(d["outputs"]) if d["outputs"] else {}
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
            results.append(d)
        return results

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        """Get a single event by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM ai_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if self.db_path != ":memory:":
            conn.close()
        if not row:
            return None
        d = dict(row)
        d["inputs"] = json.loads(d["inputs"]) if d["inputs"] else {}
        d["outputs"] = json.loads(d["outputs"]) if d["outputs"] else {}
        d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
        return d

    def get_stats(self) -> dict[str, Any]:
        """Get summary statistics."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM ai_events").fetchone()[0]
        by_type = dict(conn.execute(
            "SELECT event_type, COUNT(*) FROM ai_events GROUP BY event_type"
        ).fetchall())
        by_status = dict(conn.execute(
            "SELECT status, COUNT(*) FROM ai_events GROUP BY status"
        ).fetchall())
        by_module = dict(conn.execute(
            "SELECT module, COUNT(*) FROM ai_events GROUP BY module"
        ).fetchall())
        avg_duration = conn.execute(
            "SELECT AVG(duration_ms) FROM ai_events WHERE duration_ms > 0"
        ).fetchone()[0]
        if self.db_path != ":memory:":
            conn.close()

        return {
            "total_events": total,
            "by_type": by_type,
            "by_status": by_status,
            "by_module": by_module,
            "avg_duration_ms": avg_duration or 0.0,
        }

    def get_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get most recent events."""
        return self.query(limit=limit)


class OperationTracer:
    """Context manager that traces an AI operation and logs it.

    Usage:
        with OperationTracer(event_log, "retrieve", "retriever", "search") as trace:
            results = retriever.search(query)
            trace.set_outputs({"count": len(results), "top_score": results[0].score if results else 0})
    """

    def __init__(
        self,
        event_log: EventLog,
        event_type: str,
        module: str,
        operation: str,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.event_log = event_log
        self.event_type = event_type
        self.module = module
        self.operation = operation
        self.inputs = inputs or {}
        self.metadata = metadata or {}
        self.outputs: dict[str, Any] = {}
        self.start_time: float = 0
        self.error: str | None = None

    def __enter__(self) -> "OperationTracer":
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (time.time() - self.start_time) * 1000
        status = EventStatus.COMPLETED.value
        error = None
        if exc_type:
            status = EventStatus.FAILED.value
            error = str(exc_val)
        elif self.outputs.get("abstained"):
            status = EventStatus.UNKNOWN.value

        self.event_log.log_operation(
            event_type=self.event_type,
            module=self.module,
            operation=self.operation,
            inputs=self.inputs,
            outputs=self.outputs,
            status=status,
            duration_ms=duration_ms,
            error=error,
            metadata=self.metadata,
        )
        return False  # don't suppress exceptions

    def set_outputs(self, outputs: dict[str, Any]):
        self.outputs = outputs

    def add_output(self, key: str, value: Any):
        self.outputs[key] = value

    def set_metadata(self, key: str, value: Any):
        self.metadata[key] = value
