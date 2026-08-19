"""Temporal knowledge graph — entities, relationships, and validity windows.

Stores facts as entities and relationships with temporal validity windows,
so facts expire rather than disappear. You can query what was true at a
specific time, and facts that contradict each other are tracked with
confidence scores.

Inspired by Zep/Graphiti's bi-temporal knowledge graph, but built on
plain SQLite (no Neo4j/FalkorDB dependency) for zero-install local use.

Contract:
    - All data in a single SQLite file (portable, backupable).
    - Every fact carries valid_from and valid_to timestamps.
    - Facts can be superseded by newer facts (valid_to set on the old one).
    - Confidence scores (0.0-1.0) on every fact.
    - Graph traversal: find related entities via relationships.
    - Temporal queries: what was true at time T?

SYNTH:
    purpose: Temporal knowledge graph on SQLite with entities, relationships, validity windows, and confidence scores
    axioms: [local_first, open_process, evidence_over_intuition, reversibility_awareness, epistemic_boundary]
    objective: Facts carry temporal validity windows and confidence scores so the agent can query what was true at any point in time; facts expire gracefully rather than disappearing
    anti_patterns:
        - Depending on Neo4j, FalkorDB, or any external graph database service
        - Silently dropping expired facts instead of marking valid_to
        - Allowing infinite graph traversal without a max_depth bound
        - Storing facts without a confidence score
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Entity:
    """A knowledge graph entity."""
    entity_id: str = ""
    name: str = ""
    entity_type: str = ""  # "person", "project", "concept", "tool", etc.
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id, "name": self.name,
            "entity_type": self.entity_type, "description": self.description,
            "created_at": self.created_at,
        }


@dataclass
class Relationship:
    """A relationship between two entities with temporal validity."""
    rel_id: str = ""
    source_id: str = ""
    target_id: str = ""
    relation_type: str = ""  # "uses", "depends_on", "created", "fixed", etc.
    value: str = ""  # optional value for the relationship
    confidence: float = 1.0
    valid_from: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    valid_to: str | None = None  # None = still valid
    source: str = ""  # where this fact came from (session_id, observation, etc.)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "rel_id": self.rel_id, "source_id": self.source_id, "target_id": self.target_id,
            "relation_type": self.relation_type, "value": self.value,
            "confidence": self.confidence, "valid_from": self.valid_from,
            "valid_to": self.valid_to, "source": self.source, "created_at": self.created_at,
        }

    @property
    def is_valid_now(self) -> bool:
        return self.valid_to is None


class KnowledgeGraph:
    """Temporal knowledge graph on SQLite.

    Usage:
        kg = KnowledgeGraph("knowledge.db")
        eid = kg.add_entity(name="NoUs Hub", entity_type="project")
        kg.add_relationship(source_id=eid, target_id=other_eid,
                           relation_type="uses", value="cua-driver",
                           confidence=0.9)
        # Query current facts
        facts = kg.get_facts_about(eid)
        # Query what was true at a specific time
        old_facts = kg.get_facts_about(eid, at_time="2026-01-01T00:00:00Z")
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS entities (
        entity_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        entity_type TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS relationships (
        rel_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        relation_type TEXT NOT NULL,
        value TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 1.0,
        valid_from TEXT NOT NULL,
        valid_to TEXT,
        source TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES entities(entity_id) ON DELETE CASCADE,
        FOREIGN KEY (target_id) REFERENCES entities(entity_id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_id);
    CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_id);
    CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(relation_type);
    CREATE INDEX IF NOT EXISTS idx_rel_valid ON relationships(valid_from, valid_to);
    CREATE INDEX IF NOT EXISTS idx_entity_name ON entities(name);
    CREATE INDEX IF NOT EXISTS idx_entity_type ON entities(entity_type);
    """

    def __init__(self, db_path: str = "nous_knowledge.db"):
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

    def _generate_id(self, prefix: str = "ent") -> str:
        return f"{prefix}-{int(time.time())}-{hash(time.time()) % 10000}"

    # -- Entity CRUD -------------------------------------------------------

    def add_entity(
        self, name: str, entity_type: str = "", description: str = "",
        entity_id: str | None = None,
    ) -> str:
        """Add an entity and return its ID."""
        if entity_id is None:
            entity_id = self._generate_id("ent")
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO entities (entity_id, name, entity_type, description, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (entity_id, name, entity_type, description, now),
            )
        return entity_id

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Get an entity by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE entity_id = ?", (entity_id,)
            ).fetchone()
            return dict(row) if row else None

    def find_entities(self, name: str = "", entity_type: str = "") -> list[dict[str, Any]]:
        """Find entities by name (substring) and/or type."""
        sql = "SELECT * FROM entities WHERE 1=1"
        params: list[Any] = []
        if name:
            sql += " AND name LIKE ?"
            params.append(f"%{name}%")
        if entity_type:
            sql += " AND entity_type = ?"
            params.append(entity_type)
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    # -- Relationship CRUD -------------------------------------------------

    def add_relationship(
        self, source_id: str, target_id: str, relation_type: str,
        value: str = "", confidence: float = 1.0,
        source: str = "", valid_from: str | None = None,
        supersede: bool = True,
    ) -> str:
        """Add a relationship between two entities.

        If supersede=True, any existing relationship of the same type
        between the same entities will be invalidated (valid_to set).
        """
        rel_id = self._generate_id("rel")
        now = datetime.now(timezone.utc).isoformat()
        valid_from = valid_from or now

        with self._get_conn() as conn:
            # Supersede existing relationships of the same type
            if supersede:
                conn.execute(
                    "UPDATE relationships SET valid_to = ? "
                    "WHERE source_id = ? AND target_id = ? AND relation_type = ? "
                    "AND valid_to IS NULL",
                    (now, source_id, target_id, relation_type),
                )
            # Insert new relationship
            conn.execute(
                "INSERT INTO relationships (rel_id, source_id, target_id, relation_type, "
                "value, confidence, valid_from, valid_to, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (rel_id, source_id, target_id, relation_type,
                 value, confidence, valid_from, source, now),
            )
        return rel_id

    def get_facts_about(
        self, entity_id: str, at_time: str | None = None,
        relation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get relationships where entity_id is the source.

        If at_time is given, only return facts valid at that time.
        If relation_type is given, filter by type.
        """
        sql = "SELECT * FROM relationships WHERE source_id = ?"
        params: list[Any] = [entity_id]
        if at_time:
            sql += " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
            params.extend([at_time, at_time])
        else:
            sql += " AND valid_to IS NULL"
        if relation_type:
            sql += " AND relation_type = ?"
            params.append(relation_type)
        sql += " ORDER BY confidence DESC, valid_from DESC"
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_facts_involving(
        self, entity_id: str, at_time: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get all relationships involving entity_id (as source or target)."""
        sql = "SELECT * FROM relationships WHERE (source_id = ? OR target_id = ?)"
        params: list[Any] = [entity_id, entity_id]
        if at_time:
            sql += " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
            params.extend([at_time, at_time])
        else:
            sql += " AND valid_to IS NULL"
        sql += " ORDER BY confidence DESC"
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    # -- Graph traversal ---------------------------------------------------

    def traverse(
        self, start_entity_id: str, max_depth: int = 3,
        relation_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Breadth-first traversal from a starting entity.

        Returns a list of {entity, depth, path} dicts.
        """
        if max_depth < 1:
            return []
        visited: set[str] = set()
        results: list[dict[str, Any]] = []
        queue: list[tuple[str, int, list[str]]] = [(start_entity_id, 0, [])]
        while queue:
            entity_id, depth, path = queue.pop(0)
            if entity_id in visited or depth > max_depth:
                continue
            visited.add(entity_id)
            entity = self.get_entity(entity_id)
            if entity:
                results.append({
                    "entity": entity, "depth": depth,
                    "path": path + [entity_id],
                })
            if depth < max_depth:
                facts = self.get_facts_about(entity_id)
                if relation_types:
                    facts = [f for f in facts if f["relation_type"] in relation_types]
                for fact in facts:
                    target_id = fact["target_id"]
                    if target_id not in visited:
                        queue.append((target_id, depth + 1, path + [entity_id]))
        return results

    # -- Temporal queries --------------------------------------------------

    def get_history(self, entity_id: str) -> list[dict[str, Any]]:
        """Get the full history of relationships for an entity (including expired)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM relationships WHERE source_id = ? OR target_id = ? "
                "ORDER BY valid_from DESC",
                (entity_id, entity_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def expire_fact(self, rel_id: str, reason: str = "") -> bool:
        """Manually expire a fact (set valid_to to now)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE relationships SET valid_to = ? WHERE rel_id = ? AND valid_to IS NULL",
                (now, rel_id),
            )
            return cursor.rowcount > 0

    # -- Statistics --------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Get graph statistics."""
        with self._get_conn() as conn:
            total_entities = conn.execute("SELECT COUNT(*) as c FROM entities").fetchone()["c"]
            total_rels = conn.execute("SELECT COUNT(*) as c FROM relationships").fetchone()["c"]
            active_rels = conn.execute(
                "SELECT COUNT(*) as c FROM relationships WHERE valid_to IS NULL"
            ).fetchone()["c"]
            by_type = conn.execute(
                "SELECT relation_type, COUNT(*) as c FROM relationships "
                "WHERE valid_to IS NULL GROUP BY relation_type"
            ).fetchall()
            return {
                "total_entities": total_entities,
                "total_relationships": total_rels,
                "active_relationships": active_rels,
                "expired_relationships": total_rels - active_rels,
                "by_relation_type": {r["relation_type"]: r["c"] for r in by_type},
                "db_path": self.db_path,
            }
