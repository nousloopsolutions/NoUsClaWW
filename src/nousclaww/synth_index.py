"""Synth-Indexed Retrieval — parse SYNTH blocks from source files and index them in SQLite.

NoUsClaWW source files declare a SYNTH block in their module docstring:

.. code-block:: text

    SYNTH:
        purpose: <one-line description>
        axioms: [axiom1, axiom2, ...]
        objective: <what success looks like>
        anti_patterns:
            - <things this file should never do>

This module parses those blocks (regex-based, no AST) and builds a SQLite
index that enables:

  - Filter by status (parsed from a ``# status:`` comment or ``Status:``
    line if present).
  - Filter by dependencies (parsed from a ``deps`` field if present).
  - Full-text search (FTS5) on the ``purpose`` field.
  - Incremental updates — only changed files are re-parsed (based on
    file modification time).

The synth index is a lightweight structured metadata layer that
complements semantic retrieval — it provides exact-match filtering on
structured fields while vector search provides fuzzy semantic ranking.

Contract:
  - The synth index is rebuildable from source files (derived state).
  - Parsing is regex-based — no AST needed.
  - Index updates are incremental (only changed files re-parsed).
  - Works alongside vector retrieval, not replacing it.
  - Uses only the Python standard library (sqlite3, re, pathlib, etc.).

SYNTH:
    purpose: Parse SYNTH blocks from source files and build a SQLite index with FTS5 for structured filtering and purpose-field search.
    axioms: [open_process, evidence_over_intuition, local_first, scientific_method]
    objective: Any source file with a SYNTH block in its docstring is parsed, indexed, and queryable by status, dependencies, and purpose text — with incremental updates that only re-parse changed files.
    anti_patterns:
        - Using an AST parser instead of regex (keep it lightweight)
        - Deleting the index on every call (incremental updates only)
        - Importing internal nousclaww modules (this is a leaf module)
        - Silently swallowing parse errors without logging them
        - Storing absolute file paths that break across machines (store relative when possible)
"""
#C Adapted from nous_memory_mcp/novel/synth_index.py

from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "SynthEntry",
    "SynthIndex",
    "parse_synth_block",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for parsing SYNTH blocks
# ---------------------------------------------------------------------------

#: Matches the entire SYNTH block inside a docstring. The block starts
#: with ``SYNTH:`` and ends at the closing triple-quote or at a line
#: that is dedented past the SYNTH indentation level. We use a pragmatic
#: approach: capture everything from ``SYNTH:`` to the next ``"""""``
#: (closing docstring) or end of a line that starts at column 0 with
#: non-whitespace.
_SYNTH_BLOCK_RE = re.compile(
    r'SYNTH:\s*\n(.*?)(?:"""|\Z)',
    re.DOTALL,
)

#: Matches a ``key: value`` line within the SYNTH block. Captures the
#: key name and the rest of the line (the value or the start of a
#: multi-line value / list).
_KV_LINE_RE = re.compile(r"^(\s+)(\w+):\s*(.*)$", re.MULTILINE)

#: Matches a list item line: ``- item text``.
_LIST_ITEM_RE = re.compile(r"^\s+-\s+(.*)$", re.MULTILINE)

#: Matches bracketed list: ``[item1, item2, ...]``.
_BRACKET_LIST_RE = re.compile(r"^\[(.*)\]$")

#: Matches a ``# status: VALUE`` or ``Status: VALUE`` comment line that
#: may appear outside the SYNTH block to declare module status.
_STATUS_LINE_RE = re.compile(
    r"(?:#\s*status:\s*|Status:\s*)(\S+)", re.IGNORECASE
)

#: Matches a ``deps:`` field if present in the SYNTH block (optional).
_DEPS_LINE_RE = re.compile(r"^\s+deps:\s*(.*)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# SynthEntry dataclass
# ---------------------------------------------------------------------------


@dataclass
class SynthEntry:
    """A parsed SYNTH block entry from a source file.

    Attributes:
        file_path: Path to the source file (as provided to the indexer).
        purpose: The one-line purpose description from the SYNTH block.
        axioms: List of declared axioms.
        objective: The success criteria description.
        anti_patterns: List of anti-pattern descriptions.
        status: Module status (e.g. "IMPLEMENTED", "INITIAL"). Parsed
            from a ``# status:`` comment or ``Status:`` line if present,
            otherwise empty string.
        deps: List of declared dependencies. Parsed from a ``deps:``
            field if present in the SYNTH block, otherwise empty.
        raw_text: The raw SYNTH block text as found in the file.
        parsed_at: UTC ISO-8601 timestamp of when this entry was parsed.
    """

    file_path: str = ""
    purpose: str = ""
    axioms: list[str] = field(default_factory=list)
    objective: str = ""
    anti_patterns: list[str] = field(default_factory=list)
    status: str = ""
    deps: list[str] = field(default_factory=list)
    raw_text: str = ""
    parsed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "file_path": self.file_path,
            "purpose": self.purpose,
            "axioms": self.axioms,
            "objective": self.objective,
            "anti_patterns": self.anti_patterns,
            "status": self.status,
            "deps": self.deps,
            "parsed_at": self.parsed_at,
        }


# ---------------------------------------------------------------------------
# Parsing functions
# ---------------------------------------------------------------------------


def parse_synth_block(file_path: str, content: str) -> Optional[SynthEntry]:
    """Parse a SYNTH block from file content.

    The SYNTH block is expected to be inside the module docstring and
    follow the YAML-like format::

        SYNTH:
            purpose: <one-line description>
            axioms: [axiom1, axiom2, ...]
            objective: <what success looks like>
            anti_patterns:
                - <things this file should never do>

    The ``objective`` field may span multiple indented continuation lines.
    The ``axioms`` field may use bracket notation ``[a, b, c]`` or a
    YAML-style list. The ``anti_patterns`` field is a YAML-style list.

    A ``status`` may be declared via a ``# status: VALUE`` comment or
    a ``Status: VALUE`` line anywhere in the file. A ``deps`` field may
    appear in the SYNTH block as ``deps: [dep1, dep2]`` or ``deps: dep1, dep2``.

    Args:
        file_path: Path to the source file (for metadata).
        content: The full text content of the source file.

    Returns:
        A SynthEntry if a SYNTH block is found, None otherwise.
    """
    synth_match = _SYNTH_BLOCK_RE.search(content)
    if not synth_match:
        return None

    raw_block = synth_match.group(0)
    block_body = synth_match.group(1)

    entry = SynthEntry(file_path=file_path, raw_text=raw_block)

    # Parse key-value lines.
    _parse_synth_fields(block_body, entry)

    # Parse status from the full file content (may be outside SYNTH block).
    status_match = _STATUS_LINE_RE.search(content)
    if status_match:
        entry.status = status_match.group(1).strip().rstrip(".")

    # Parse deps from the SYNTH block body if present.
    deps_match = _DEPS_LINE_RE.search(block_body)
    if deps_match:
        deps_raw = deps_match.group(1).strip()
        entry.deps = _parse_list_value(deps_raw)

    return entry


def _parse_synth_fields(block_body: str, entry: SynthEntry) -> None:
    """Parse the key-value fields from a SYNTH block body.

    Args:
        block_body: The text between ``SYNTH:`` and the closing quote.
        entry: The SynthEntry to populate.
    """
    # Find all key-value lines.
    kv_matches = list(_KV_LINE_RE.finditer(block_body))
    # Also check for deps line.
    deps_matches = list(_DEPS_LINE_RE.finditer(block_body))

    # Merge and sort by position to process in order.
    all_matches: list[tuple[int, str, str, str]] = []
    for m in kv_matches:
        indent = m.group(1)
        key = m.group(2)
        value = m.group(3)
        all_matches.append((m.start(), indent, key, value))
    for m in deps_matches:
        indent = m.group(0).split("deps:")[0]
        key = "deps"
        value = m.group(1)
        all_matches.append((m.start(), indent, key, value))

    all_matches.sort(key=lambda x: x[0])

    for i, (pos, indent, key, value) in enumerate(all_matches):
        # Determine the end position: next field start or end of block.
        if i + 1 < len(all_matches):
            end_pos = all_matches[i + 1][0]
        else:
            end_pos = len(block_body)

        section_text = block_body[pos:end_pos]

        if key == "purpose":
            entry.purpose = _parse_scalar_value(value, section_text)
        elif key == "axioms":
            entry.axioms = _parse_list_value(value) or _parse_yaml_list(
                section_text
            )
        elif key == "objective":
            entry.objective = _parse_multiline_value(value, section_text)
        elif key == "anti_patterns":
            entry.anti_patterns = _parse_yaml_list(section_text)
        elif key == "deps":
            # Already handled separately, but handle here too for safety.
            if not entry.deps:
                entry.deps = _parse_list_value(value) or _parse_yaml_list(
                    section_text
                )


def _parse_scalar_value(value: str, section_text: str) -> str:
    """Parse a single-line scalar value.

    Args:
        value: The text after ``key:`` on the first line.
        section_text: The full section text (unused for scalars, but
            kept for interface consistency).

    Returns:
        The trimmed scalar string.
    """
    return value.strip()


def _parse_multiline_value(value: str, section_text: str) -> str:
    """Parse a value that may span multiple indented continuation lines.

    The first line's value is taken as the start. Subsequent lines that
    are indented more than the key (and are not list items or new keys)
    are appended as continuation lines.

    Args:
        value: The text after ``key:`` on the first line.
        section_text: The full section text from the key line to the
            next key or end of block.

    Returns:
        The combined multi-line value, joined with spaces.
    """
    lines: list[str] = []
    first = value.strip()
    if first:
        lines.append(first)

    # Process continuation lines from the section text.
    for line in section_text.split("\n")[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip lines that look like new keys (``word: value``).
        if _KV_LINE_RE.match(line):
            continue
        # Skip list items.
        if _LIST_ITEM_RE.match(line):
            continue
        lines.append(stripped)

    return " ".join(lines)


def _parse_list_value(value: str) -> list[str]:
    """Parse a bracketed list value ``[item1, item2, ...]``.

    If the value is not bracketed, treat it as a comma-separated list.
    If the value is empty, return an empty list.

    Args:
        value: The raw value string after ``key:``.

    Returns:
        A list of trimmed items.
    """
    value = value.strip()
    if not value:
        return []

    bracket_match = _BRACKET_LIST_RE.match(value)
    if bracket_match:
        inner = bracket_match.group(1).strip()
        if not inner:
            return []
        return [item.strip() for item in inner.split(",") if item.strip()]

    # Comma-separated fallback.
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_yaml_list(section_text: str) -> list[str]:
    """Parse a YAML-style list from a section of the SYNTH block.

    Looks for lines matching ``- item text`` within the section.

    Args:
        section_text: The text of the section to parse.

    Returns:
        A list of trimmed item strings.
    """
    items: list[str] = []
    for m in _LIST_ITEM_RE.finditer(section_text):
        item = m.group(1).strip()
        if item:
            items.append(item)
    return items


# ---------------------------------------------------------------------------
# SynthIndex — SQLite index
# ---------------------------------------------------------------------------


class SynthIndex:
    """SQLite index of SYNTH block metadata for structured retrieval.

    Usage::

        idx = SynthIndex("synth_index.db")
        idx.index_dir("src/nousclaww/")
        # Query by metadata
        results = idx.query(status="IMPLEMENTED")
        # Full-text search on purpose
        results = idx.search_purpose("hallucination detection")
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS synth_entries (
        file_path TEXT PRIMARY KEY,
        purpose TEXT NOT NULL DEFAULT '',
        axioms TEXT NOT NULL DEFAULT '',
        objective TEXT NOT NULL DEFAULT '',
        anti_patterns TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '',
        deps TEXT NOT NULL DEFAULT '',
        raw_text TEXT NOT NULL DEFAULT '',
        file_mtime REAL NOT NULL DEFAULT 0,
        parsed_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_synth_status ON synth_entries(status);

    CREATE VIRTUAL TABLE IF NOT EXISTS synth_fts USING fts5(
        file_path, purpose, objective,
        content='synth_entries',
        content_rowid='rowid'
    );
    """

    TRIGGERS = """
    CREATE TRIGGER IF NOT EXISTS synth_ai AFTER INSERT ON synth_entries BEGIN
        INSERT INTO synth_fts(rowid, file_path, purpose, objective)
        VALUES (new.rowid, new.file_path, new.purpose, new.objective);
    END;
    CREATE TRIGGER IF NOT EXISTS synth_ad AFTER DELETE ON synth_entries BEGIN
        INSERT INTO synth_fts(synth_fts, rowid, file_path, purpose, objective)
        VALUES ('delete', old.rowid, old.file_path, old.purpose, old.objective);
    END;
    CREATE TRIGGER IF NOT EXISTS synth_au AFTER UPDATE ON synth_entries BEGIN
        INSERT INTO synth_fts(synth_fts, rowid, file_path, purpose, objective)
        VALUES ('delete', old.rowid, old.file_path, old.purpose, old.objective);
        INSERT INTO synth_fts(rowid, file_path, purpose, objective)
        VALUES (new.rowid, new.file_path, new.purpose, new.objective);
    END;
    """

    def __init__(self, db_path: str | Path = "nousclaww_synth_index.db") -> None:
        """Initialize the index, creating the database and schema if needed.

        Args:
            db_path: Path to the SQLite database file. If the file does
                not exist, it will be created.
        """
        self.db_path = str(db_path)
        self._fts_available: bool = False
        self._init_db()

    def _init_db(self) -> None:
        """Create the database schema and FTS5 triggers."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(self.SCHEMA)
            try:
                conn.executescript(self.TRIGGERS)
                self._fts_available = True
            except sqlite3.OperationalError:
                # FTS5 not available in this SQLite build.
                logger.warning(
                    "FTS5 not available — falling back to LIKE queries "
                    "for search_purpose()"
                )
                self._fts_available = False

    def _get_conn(self) -> sqlite3.Connection:
        """Create a new SQLite connection with Row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_file_mtime(self, file_path: str) -> float:
        """Get the modification time of a file.

        Args:
            file_path: Path to the file.

        Returns:
            The file's modification time as a float, or 0.0 if the file
            cannot be accessed.
        """
        try:
            return Path(file_path).stat().st_mtime
        except (OSError, ValueError):
            return 0.0

    def _needs_reindex(self, file_path: str, mtime: float) -> bool:
        """Check if a file needs to be re-indexed.

        A file needs re-indexing if it is not in the database or if its
        modification time is newer than the stored value.

        Args:
            file_path: Path to the file.
            mtime: The file's current modification time.

        Returns:
            True if the file should be re-indexed, False if it is up
            to date.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT file_mtime FROM synth_entries WHERE file_path = ?",
                (file_path,),
            ).fetchone()
            if row is None:
                return True
            return mtime > row["file_mtime"]

    def index_file(self, file_path: str) -> bool:
        """Index a single source file.

        If the file has not changed since the last index (based on
        modification time), it is skipped (incremental update).

        Args:
            file_path: Path to the source file to index.

        Returns:
            True if the file was indexed or updated, False if it was
            skipped (unchanged) or had no SYNTH block.
        """
        mtime = self._get_file_mtime(file_path)
        if mtime > 0 and not self._needs_reindex(file_path, mtime):
            return False  # Up to date — skip.

        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("Failed to read %s: %s", file_path, exc)
            return False

        entry = parse_synth_block(file_path, content)
        if entry is None:
            return False  # No SYNTH block found.

        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO synth_entries "
                "(file_path, purpose, axioms, objective, anti_patterns, "
                "status, deps, raw_text, file_mtime, parsed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.file_path,
                    entry.purpose,
                    json_list(entry.axioms),
                    entry.objective,
                    json_list(entry.anti_patterns),
                    entry.status,
                    json_list(entry.deps),
                    entry.raw_text,
                    mtime,
                    now,
                ),
            )
        return True

    def index_dir(
        self,
        dir_path: str | Path,
        extensions: tuple[str, ...] = (".py",),
    ) -> int:
        """Index all source files in a directory tree.

        Only files with matching extensions are indexed. Files that have
        not changed since the last index are skipped (incremental).

        Args:
            dir_path: Root directory to scan recursively.
            extensions: File extensions to index (e.g. ``(".py",)``).

        Returns:
            The number of files that were indexed or updated.
        """
        count = 0
        root = str(dir_path)
        for dirpath, _dirs, files in os.walk(root):
            for fname in files:
                if fname.endswith(extensions):
                    fpath = str(Path(dirpath) / fname)
                    if self.index_file(fpath):
                        count += 1
        logger.info("Indexed %d SYNTH blocks from %s", count, root)
        return count

    def query(
        self,
        status: Optional[str] = None,
        has_deps: Optional[list[str]] = None,
        purpose_search: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Query synth entries by structured metadata.

        Args:
            status: Filter by exact status string (e.g. "IMPLEMENTED").
                If None, no status filter is applied.
            has_deps: Filter to entries that declare all of the given
                dependencies. Each dependency is matched as a substring
                of the deps field.
            purpose_search: Substring search on the purpose field. If
                None, no purpose filter is applied. For FTS5-enabled
                databases, consider using :meth:`search_purpose` instead.

        Returns:
            A list of dictionaries, one per matching entry, with all
            columns from the ``synth_entries`` table.
        """
        sql = "SELECT * FROM synth_entries WHERE 1=1"
        params: list[Any] = []

        if status:
            sql += " AND status = ?"
            params.append(status)
        if has_deps:
            for dep in has_deps:
                sql += " AND deps LIKE ?"
                params.append(f"%{dep}%")
        if purpose_search:
            sql += " AND purpose LIKE ?"
            params.append(f"%{purpose_search}%")

        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def search_purpose(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Full-text search on synth purpose (and objective) fields.

        Uses FTS5 if available, otherwise falls back to a LIKE query.

        Args:
            query: The search query string. For FTS5, this is passed
                directly to the MATCH operator.
            limit: Maximum number of results to return.

        Returns:
            A list of dictionaries, one per matching entry, sorted by
            FTS5 rank (relevance) or by purpose substring match.
        """
        if self._fts_available:
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT s.* FROM synth_entries s "
                    "JOIN synth_fts f ON s.rowid = f.rowid "
                    "WHERE synth_fts MATCH ? ORDER BY rank LIMIT ?",
                    (query, limit),
                ).fetchall()
                return [dict(r) for r in rows]
        # Fallback to LIKE.
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM synth_entries "
                "WHERE purpose LIKE ? OR objective LIKE ? "
                "LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_entry(self, file_path: str) -> Optional[dict[str, Any]]:
        """Get a single synth entry by file path.

        Args:
            file_path: The file path to look up.

        Returns:
            A dictionary with the entry's columns, or None if not found.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM synth_entries WHERE file_path = ?",
                (file_path,),
            ).fetchone()
            return dict(row) if row is not None else None

    def get_stats(self) -> dict[str, Any]:
        """Get summary statistics about the index.

        Returns:
            A dictionary with:
              - ``total_entries``: Total number of indexed entries.
              - ``by_status``: Dict mapping status to count.
              - ``fts_available``: Whether FTS5 is available.
              - ``db_path``: The database file path.
        """
        with self._get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM synth_entries"
            ).fetchone()["c"]
            by_status = conn.execute(
                "SELECT status, COUNT(*) as c FROM synth_entries "
                "GROUP BY status"
            ).fetchall()
            return {
                "total_entries": total,
                "by_status": {
                    r["status"] or "unknown": r["c"] for r in by_status
                },
                "fts_available": self._fts_available,
                "db_path": self.db_path,
            }

    def clear(self) -> None:
        """Remove all entries from the index.

        This does not delete the database file — it clears the tables.
        Useful for forcing a full re-index.
        """
        with self._get_conn() as conn:
            conn.execute("DELETE FROM synth_entries")
            if self._fts_available:
                conn.execute("DELETE FROM synth_fts")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def json_list(items: list[str]) -> str:
    """Serialize a list of strings to a JSON array string for storage.

    Args:
        items: List of strings.

    Returns:
        A JSON-encoded array string, e.g. ``["a", "b"]``.
    """
    import json

    return json.dumps(items, ensure_ascii=False)
