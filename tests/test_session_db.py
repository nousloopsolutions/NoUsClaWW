"""Tests for SessionDB — SQLite session database with FTS5 full-text search.

SYNTH:
    purpose: Verify SessionDB stores/retrieves events, FTS5 search, and time-range queries.
    axioms: [evidence_over_intuition, scientific_method, honest_failure_over_fake_success]
    objective: Every SessionDB operation is verified to store, retrieve, and search correctly.
    anti_patterns:
        - Using a persistent database that leaks state between tests.
        - Not cleaning up test databases.
"""
import time

import pytest

from nousclaww.memory.session_db import SessionDB, Session


@pytest.fixture
def db(tmp_path):
    """Provide a SessionDB backed by a temporary SQLite file."""
    return SessionDB(str(tmp_path / "test_sessions.db"))


class TestSessionCRUD:
    """Tests for session create/read/update/delete operations."""

    def test_create_session_returns_id(self, db):
        """create_session should return a non-empty session ID."""
        sid = db.create_session(title="Fix auth bug", tags="bugfix,auth")
        assert sid is not None
        assert isinstance(sid, str)
        assert len(sid) > 0

    def test_get_session_retrieves_created_session(self, db):
        """get_session should return the session with correct fields."""
        sid = db.create_session(title="My Task", tags="work", model="gpt-4", provider="openai")
        session = db.get_session(sid)
        assert session is not None
        assert session["session_id"] == sid
        assert session["title"] == "My Task"
        assert session["tags"] == "work"
        assert session["model"] == "gpt-4"

    def test_update_session_changes_fields(self, db):
        """update_session should update summary and outcome."""
        sid = db.create_session(title="Task")
        updated = db.update_session(sid, summary="Fixed the bug", outcome="success")
        assert updated is True
        session = db.get_session(sid)
        assert session["summary"] == "Fixed the bug"
        assert session["outcome"] == "success"

    def test_delete_session_removes_it(self, db):
        """delete_session should remove the session."""
        sid = db.create_session(title="To delete")
        assert db.delete_session(sid) is True
        assert db.get_session(sid) is None

    def test_get_nonexistent_session_returns_none(self, db):
        """get_session on a nonexistent ID should return None."""
        assert db.get_session("nonexistent-id") is None


class TestTrajectoryLogging:
    """Tests for trajectory entry storage and retrieval."""

    def test_add_trajectory_entry_stores_data(self, db):
        """add_trajectory_entry should store the entry and return a row ID."""
        sid = db.create_session(title="Task with trajectory")
        entry_id = db.add_trajectory_entry(sid, {"tool": "read_file", "path": "auth.py"})
        assert entry_id is not None
        assert isinstance(entry_id, int)

    def test_get_trajectory_returns_entries_in_order(self, db):
        """get_trajectory should return entries ordered by seq."""
        sid = db.create_session(title="Multi-step task")
        db.add_trajectory_entry(sid, {"step": 1}, entry_type="action")
        db.add_trajectory_entry(sid, {"step": 2}, entry_type="action")
        db.add_trajectory_entry(sid, {"step": 3}, entry_type="action")
        traj = db.get_trajectory(sid)
        assert len(traj) == 3
        assert traj[0]["seq"] == 1
        assert traj[1]["seq"] == 2
        assert traj[2]["seq"] == 3
        assert traj[0]["data"]["step"] == 1

    def test_get_session_with_trajectory_includes_it(self, db):
        """get_session with include_trajectory=True should include trajectory."""
        sid = db.create_session(title="Task")
        db.add_trajectory_entry(sid, {"tool": "edit"}, entry_type="action")
        session = db.get_session(sid, include_trajectory=True)
        assert session is not None
        assert "trajectory" in session
        assert len(session["trajectory"]) == 1
        assert session["trajectory"][0]["data"]["tool"] == "edit"

    def test_trajectory_entry_data_is_json_parsed(self, db):
        """Trajectory entry data should be parsed from JSON into a dict."""
        sid = db.create_session(title="Task")
        db.add_trajectory_entry(sid, {"key": "value", "nested": {"a": 1}})
        traj = db.get_trajectory(sid)
        assert traj[0]["data"]["key"] == "value"
        assert traj[0]["data"]["nested"]["a"] == 1


class TestFTS5Search:
    """Tests for full-text search functionality."""

    def test_search_finds_matching_summary(self, db):
        """search should find sessions by summary content."""
        sid1 = db.create_session(title="Task A")
        db.update_session(sid1, summary="Fixed JWT validation in auth module")
        sid2 = db.create_session(title="Task B")
        db.update_session(sid2, summary="Refactored database layer")

        results = db.search("JWT")
        assert len(results) >= 1
        assert any(r["session_id"] == sid1 for r in results)

    def test_search_filters_by_tags(self, db):
        """search with tags filter should only return matching sessions."""
        sid1 = db.create_session(title="Task", tags="bugfix")
        db.update_session(sid1, summary="Fixed authentication bug")
        sid2 = db.create_session(title="Task", tags="feature")
        db.update_session(sid2, summary="Fixed authentication feature")

        results = db.search("authentication", tags="bugfix")
        assert all("bugfix" in r["tags"] for r in results)
        assert any(r["session_id"] == sid1 for r in results)

    def test_search_filters_by_outcome(self, db):
        """search with outcome filter should only return matching sessions."""
        sid1 = db.create_session(title="Task")
        db.update_session(sid1, summary="Fixed bug", outcome="success")
        sid2 = db.create_session(title="Task")
        db.update_session(sid2, summary="Fixed bug", outcome="failure")

        results = db.search("bug", outcome="success")
        assert all(r["outcome"] == "success" for r in results)

    def test_search_returns_empty_for_no_match(self, db):
        """search with a non-matching query should return an empty list."""
        db.create_session(title="Task")
        results = db.search("nonexistent_query_xyz123")
        assert len(results) == 0

    def test_search_respects_limit(self, db):
        """search should respect the limit parameter."""
        for i in range(10):
            sid = db.create_session(title=f"Task {i}")
            db.update_session(sid, summary="common keyword search")
        results = db.search("common keyword", limit=3)
        assert len(results) <= 3


class TestStatsAndRecent:
    """Tests for statistics and recent session retrieval."""

    def test_get_stats_returns_counts(self, db):
        """get_stats should return total session and trajectory counts."""
        sid1 = db.create_session(title="Task 1")
        sid2 = db.create_session(title="Task 2")
        db.add_trajectory_entry(sid1, {"step": 1})
        stats = db.get_stats()
        assert stats["total_sessions"] == 2
        assert stats["total_trajectory_entries"] == 1
        assert "fts_available" in stats

    def test_get_recent_returns_latest_first(self, db):
        """get_recent should return sessions ordered by updated_at descending."""
        sid1 = db.create_session(title="Old")
        time.sleep(0.01)
        sid2 = db.create_session(title="New")
        db.update_session(sid2, summary="updated")
        recent = db.get_recent(limit=10)
        assert len(recent) >= 2
        # The most recently updated should be first
        assert recent[0]["session_id"] == sid2

    def test_stats_by_outcome_groups_correctly(self, db):
        """get_stats should group sessions by outcome."""
        sid1 = db.create_session(title="A")
        db.update_session(sid1, outcome="success")
        sid2 = db.create_session(title="B")
        db.update_session(sid2, outcome="failure")
        stats = db.get_stats()
        assert stats["by_outcome"]["success"] == 1
        assert stats["by_outcome"]["failure"] == 1


class TestSessionDataclass:
    """Tests for the Session dataclass."""

    def test_session_to_dict_includes_trajectory_count(self):
        """Session.to_dict should include trajectory_count."""
        s = Session(session_id="test-1", title="Test", trajectory=[{"a": 1}, {"b": 2}])
        d = s.to_dict()
        assert d["session_id"] == "test-1"
        assert d["trajectory_count"] == 2

    def test_session_defaults_are_empty(self):
        """Session should have sensible defaults."""
        s = Session(session_id="test-2")
        assert s.title == ""
        assert s.tags == ""
        assert s.summary == ""
        assert s.trajectory == []
