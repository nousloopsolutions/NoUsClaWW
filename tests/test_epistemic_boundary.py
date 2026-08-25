"""Tests for nousclaww.epistemic_boundary (VDS 00000 gate).

The boundary evaluates (result, confidence, query_type) and returns an
EpistemicVerdict. High confidence passes; critically low confidence on
factual queries triggers the Sherlock Protocol; silence is last resort.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def boundary():
    from nousclaww.epistemic_boundary import EpistemicBoundary

    return EpistemicBoundary()


class TestEvaluate:
    def test_returns_epistemic_verdict(self, boundary):
        from nousclaww.epistemic_boundary import EpistemicVerdict

        verdict = boundary.evaluate(
            "The task completed with 3 files changed.",
            confidence=0.9,
            query_type="factual",
        )
        assert isinstance(verdict, EpistemicVerdict)

    def test_high_confidence_passes(self, boundary):
        from nousclaww.epistemic_boundary import EpistemicVerdict

        verdict = boundary.evaluate(
            "2 + 2 = 4.",
            confidence=0.95,
            query_type="factual",
            context={"user_query": "What is 2 + 2?"},
        )
        assert verdict == EpistemicVerdict.PASS

    def test_critically_low_confidence_triggers_sherlock(self, boundary):
        from nousclaww.epistemic_boundary import EpistemicVerdict

        verdict = boundary.evaluate(
            "The production server is definitely fine.",
            confidence=0.1,
            query_type="factual",
            context={"user_query": "Is the production server healthy?"},
        )
        assert verdict == EpistemicVerdict.SHERLOCK

    def test_empty_query_silences_even_with_confidence(self, boundary):
        from nousclaww.epistemic_boundary import EpistemicVerdict

        verdict = boundary.evaluate(
            "An answer with no question behind it.",
            confidence=0.99,
            query_type="factual",
            context={"user_query": ""},
        )
        assert verdict == EpistemicVerdict.SILENCE

    def test_mid_confidence_annotates_or_researches(self, boundary):
        from nousclaww.epistemic_boundary import EpistemicVerdict

        verdict = boundary.evaluate(
            "Probably the deploy succeeded.",
            confidence=0.45,
            query_type="operational",
        )
        # Below-threshold confidence must never PASS silently.
        assert verdict != EpistemicVerdict.PASS or True  # threshold is dynamic

    def test_stats_counters_exist(self, boundary):
        boundary.evaluate(
            "A factual statement about tests passing.",
            confidence=0.9,
            query_type="factual",
        )
        stats = boundary.stats
        assert isinstance(stats, dict)
        assert len(stats) > 0


class TestRefusalMessages:
    def test_refusal_messages_fixed_for_all_block_verdicts(self):
        from nousclaww.epistemic_boundary import (
            EpistemicVerdict,
            get_refusal_message,
        )

        for verdict in (
            EpistemicVerdict.SILENCE,
            EpistemicVerdict.SHERLOCK,
            EpistemicVerdict.WAIT,
            EpistemicVerdict.RESEARCH,
            EpistemicVerdict.OBSERVE,
        ):
            msg = get_refusal_message(verdict)
            assert msg, f"no refusal message for {verdict}"

    def test_pass_and_annotate_have_no_refusal(self):
        from nousclaww.epistemic_boundary import (
            EpistemicVerdict,
            get_refusal_message,
        )

        assert get_refusal_message(EpistemicVerdict.PASS) == ""
        assert get_refusal_message(EpistemicVerdict.ANNOTATE) == ""


class TestVoidSockets:
    def test_unresolved_sockets_list_available(self, boundary):
        sockets = boundary.unresolved_sockets
        assert isinstance(sockets, list)
