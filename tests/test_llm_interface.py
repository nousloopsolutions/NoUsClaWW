"""Tests for nousclaww.llm_interface.

The module must import WITHOUT the optional ``ollama`` package installed
(AGENTS.md Section 4.4: optional dependencies must be guarded and the test
suite must pass without them). The epistemic abstention helpers and the
deterministic fallback are tested for honest behavior.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch


class TestOptionalOllamaImport:
    def test_module_imports_without_ollama(self):
        # Simulate ollama being absent.
        with patch.dict(sys.modules, {"ollama": None}):
            sys.modules.pop("nousclaww.llm_interface", None)
            try:
                import nousclaww.llm_interface  # noqa: F401
            finally:
                sys.modules.pop("nousclaww.llm_interface", None)

    def test_interface_raises_informative_error_without_ollama(self):
        with patch.dict(sys.modules, {"ollama": None}):
            sys.modules.pop("nousclaww.llm_interface", None)
            try:
                from nousclaww.llm_interface import LLMInterface

                try:
                    LLMInterface()
                except ImportError as e:
                    assert "pip install" in str(e), (
                        "error should tell the user how to install ollama"
                    )
                else:
                    raise AssertionError(
                        "LLMInterface() must raise ImportError without ollama"
                    )
            finally:
                sys.modules.pop("nousclaww.llm_interface", None)

    def test_interface_works_with_mocked_client(self):
        sys.modules.pop("nousclaww.llm_interface", None)
        try:
            fake_ollama = MagicMock()
            fake_ollama.Client.return_value.list.return_value = {
                "models": [{"name": "test-model"}]
            }
            fake_ollama.Client.return_value.chat.return_value = {
                "message": {"content": "answer"}
            }
            with patch.dict(sys.modules, {"ollama": fake_ollama}):
                from nousclaww.llm_interface import LLMInterface

                iface = LLMInterface(model="test-model")
                out = iface.ask("What is 2+2?")
                assert out == "answer"
        finally:
            sys.modules.pop("nousclaww.llm_interface", None)


class TestAbstention:
    """UNKNOWN abstention: silence over fabrication."""

    def test_reject_untrusted_evidence(self):
        from nousclaww.llm_interface import reject_untrusted_evidence

        evidence = [
            {"text": "good", "score": 0.9},
            {"text": "bad", "is_untrusted": True},
            {"text": "bad2", "trusted": False},
            {"text": "bad3", "trust": "UNTRUSTED"},
            {"text": "bad4", "status": "untrusted"},
        ]
        trusted = reject_untrusted_evidence(evidence)
        assert len(trusted) == 1
        assert trusted[0]["text"] == "good"

    def test_abstain_on_no_evidence(self):
        from nousclaww.llm_interface import abstain_if_ungrounded

        assert abstain_if_ungrounded("q?", []) == "UNKNOWN: insufficient evidence"

    def test_abstain_on_all_untrusted(self):
        from nousclaww.llm_interface import abstain_if_ungrounded

        result = abstain_if_ungrounded("q?", [{"t": 1, "is_untrusted": True}])
        assert result == "UNKNOWN: insufficient evidence"

    def test_no_abstain_with_sufficient_trusted(self):
        from nousclaww.llm_interface import abstain_if_ungrounded

        result = abstain_if_ungrounded("q?", [{"text": "e", "score": 0.9}])
        assert result is None

    def test_abstain_below_threshold(self):
        from nousclaww.llm_interface import abstain_if_ungrounded

        result = abstain_if_ungrounded("q?", [{"text": "e", "score": 0.1}])
        assert result == "UNKNOWN: insufficient evidence"


class TestDeterministicFallback:
    def test_fallback_grounded_response(self):
        from nousclaww.llm_interface import deterministic_fallback

        out = deterministic_fallback(
            "q?", [{"summary": "fact one", "score": 0.8, "source": "/a"}]
        )
        assert "fact one" in out
        assert "without LLM inference" in out

    def test_fallback_abstains_when_nothing_trusted(self):
        from nousclaww.llm_interface import deterministic_fallback

        out = deterministic_fallback("q?", [{"s": 1, "is_untrusted": True}])
        assert out == "UNKNOWN: insufficient evidence"

    def test_epistemic_directive_present(self):
        from nousclaww.llm_interface import EPISTEMIC_BOUNDARY_DIRECTIVE

        assert "don't know" in EPISTEMIC_BOUNDARY_DIRECTIVE.lower()
