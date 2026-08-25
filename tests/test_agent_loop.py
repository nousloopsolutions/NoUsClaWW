"""Integration tests for the AgentLoop chat turn lifecycle.

These tests would have caught the regression where the loop called
``epistemic_boundary.evaluate()`` with a stale signature, causing every
turn to fail into silence.
"""
from __future__ import annotations

from typing import Any

import pytest


class FakeRouter:
    """Router stub returning canned RoutingResult-like objects."""

    def __init__(self, text: str = "Here is the answer.", error: str | None = None):
        from nousclaww.llm_router import RoutingResult

        self._result = RoutingResult(
            text=text,
            provider="local",
            decision="test",
            model="fake",
            duration_ms=1.0,
            error=error,
            cloud_used=False,
        )
        self.calls: list[dict[str, Any]] = []

    def ask(self, question: str, context: str = ""):
        self.calls.append({"question": question, "context": context})
        return self._result


class RecordingBoundary:
    """Epistemic boundary stub that records calls and passes everything."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, result, confidence, query_type, context=None):
        # Mirror the REAL signature so signature drift fails loudly here.
        self.calls.append(
            {"result": result, "confidence": confidence,
             "query_type": query_type, "context": context}
        )

        from nousclaww.epistemic_boundary import EpistemicVerdict

        return EpistemicVerdict.PASS


@pytest.fixture
def memory(tmp_path):
    from nousclaww.memory.memory_manager import MemoryManager

    return MemoryManager(str(tmp_path / "loop_test.db"))


def _make_loop(memory, boundary=None, router=None):
    from nousclaww.agent.loop import AgentLoop
    from nousclaww.agent.policy import CompressionPolicy

    return AgentLoop(
        llm_router=router or FakeRouter(),
        memory_manager=memory,
        policy=CompressionPolicy(),
        epistemic_boundary=boundary or RecordingBoundary(),
    )


class TestTurnLifecycle:
    def test_successful_turn_returns_output(self, memory):
        loop = _make_loop(memory)
        out = loop.chat("What did I work on yesterday?")
        assert out == "Here is the answer."
        result = loop.last_turn_result()
        assert result is not None
        assert result.silenced is False
        assert result.cost_log.get("boundary_verdict") == "pass"

    def test_boundary_receives_required_arguments(self, memory):
        """The loop must call evaluate(result, confidence, query_type)."""
        boundary = RecordingBoundary()
        loop = _make_loop(memory, boundary=boundary)
        loop.chat("Remember my favorite color is teal.")
        assert len(boundary.calls) == 1
        call = boundary.calls[0]
        assert isinstance(call["confidence"], float)
        assert 0.0 <= call["confidence"] <= 1.0
        assert isinstance(call["query_type"], str)

    def test_real_boundary_signature_compatible(self, memory):
        """The REAL boundary must accept how the loop calls it.

        This is the regression test: the loop previously passed only
        (result, context) and every turn silently failed.
        """
        from nousclaww.epistemic_boundary import EpistemicBoundary

        real = EpistemicBoundary()
        loop = _make_loop(memory, boundary=real)
        loop.chat("What is 2 + 2?")
        result = loop.last_turn_result()
        # Whatever the verdict, the boundary must NOT have errored.
        assert result is not None
        assert not str(result.cost_log.get("boundary_verdict", "")).startswith(
            "boundary_error"
        ), f"boundary integration broken: {result.cost_log}"
        assert result.error is None

    def test_llm_failure_produces_honest_silence(self, memory):
        router = FakeRouter(text="", error="model unavailable")
        loop = _make_loop(memory, router=router)
        out = loop.chat("hello")
        assert out == ""
        result = loop.last_turn_result()
        assert result.silenced is True
        assert result.cost_log.get("boundary_verdict") == "llm_failure"

    def test_autowrite_records_turn(self, memory):
        loop = _make_loop(memory)
        loop.chat("Note that project alpha ships in August.")
        stats = memory.get_stats()
        assert stats["total_events"] >= 1  # chat turn recorded as event

    def test_history_grows_and_compacts(self, memory):
        loop = _make_loop(memory)
        loop.set_budget(max_tokens=60, context_window=40,
                        compaction_threshold=0.5)
        for i in range(6):
            loop.chat(f"Message number {i} with some filler content to grow tokens.")
        assert len(loop.get_messages()) > 0
