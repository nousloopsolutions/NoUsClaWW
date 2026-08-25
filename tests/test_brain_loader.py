"""Tests for the brain layer — LocalOllamaBrain and SocketLoader."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sovereign_sockets.brain_interface import BrainInterface, BrainResponse


# ── LocalOllamaBrain ──────────────────────────────────────────────────────


def test_local_ollama_brain_with_mock_llm():
    """LocalOllamaBrain should reason via the injected LLM and return a BrainResponse."""
    from nousclaww.brain.local_ollama_brain import LocalOllamaBrain

    mock_llm = MagicMock()
    mock_llm.ask.return_value = "The answer is 42."
    brain = LocalOllamaBrain(llm=mock_llm)

    resp = brain.reason(context="some context", query="What is the answer?", constraints={})
    assert isinstance(resp, BrainResponse)
    assert resp.output == "The answer is 42."
    assert resp.confidence == 0.7  # context was provided
    assert resp.void_sockets == []


def test_local_ollama_brain_no_context_lower_confidence():
    """Without context, confidence should be 0.5."""
    from nousclaww.brain.local_ollama_brain import LocalOllamaBrain

    mock_llm = MagicMock()
    mock_llm.ask.return_value = "I think yes."
    brain = LocalOllamaBrain(llm=mock_llm)

    resp = brain.reason(context="", query="Is the sky blue?")
    assert resp.confidence == 0.5


def test_local_ollama_brain_abstention():
    """When the LLM returns UNKNOWN:, confidence should be 0.0 and a void socket created."""
    from nousclaww.brain.local_ollama_brain import LocalOllamaBrain

    mock_llm = MagicMock()
    mock_llm.ask.return_value = "UNKNOWN: insufficient evidence"
    brain = LocalOllamaBrain(llm=mock_llm)

    resp = brain.reason(context="ctx", query="What happened?")
    assert resp.output == ""
    assert resp.confidence == 0.0
    assert len(resp.void_sockets) == 1
    assert resp.void_sockets[0].startswith("void-")


def test_local_ollama_brain_llm_error():
    """When the LLM raises, the brain should return silence + void socket."""
    from nousclaww.brain.local_ollama_brain import LocalOllamaBrain

    mock_llm = MagicMock()
    mock_llm.ask.side_effect = RuntimeError("connection refused")
    brain = LocalOllamaBrain(llm=mock_llm)

    resp = brain.reason(context="", query="test")
    assert resp.output == ""
    assert resp.confidence == 0.0
    assert len(resp.void_sockets) == 1
    assert "error" in resp.metadata


def test_local_ollama_brain_implements_interface():
    """LocalOllamaBrain must implement BrainInterface."""
    from nousclaww.brain.local_ollama_brain import LocalOllamaBrain

    mock_llm = MagicMock()
    brain = LocalOllamaBrain(llm=mock_llm)
    assert isinstance(brain, BrainInterface)


def test_local_ollama_brain_get_confidence():
    """get_confidence should return 0.0 for empty/UNKNOWN, 0.7 with context, 0.5 without."""
    from nousclaww.brain.local_ollama_brain import LocalOllamaBrain

    mock_llm = MagicMock()
    brain = LocalOllamaBrain(llm=mock_llm)

    assert brain.get_confidence("", "context") == 0.0
    assert brain.get_confidence("UNKNOWN: test", "context") == 0.0
    assert brain.get_confidence("real answer", "context") == 0.7
    assert brain.get_confidence("real answer", "") == 0.5


def test_local_ollama_brain_void_sockets_tracking():
    """Void sockets should be tracked and accessible."""
    from nousclaww.brain.local_ollama_brain import LocalOllamaBrain

    mock_llm = MagicMock()
    mock_llm.ask.return_value = "UNKNOWN: no data"
    brain = LocalOllamaBrain(llm=mock_llm)

    brain.reason(context="", query="q1")
    brain.reason(context="", query="q2")
    assert len(brain.void_sockets) == 2


# ── SocketLoader ──────────────────────────────────────────────────────────


def test_socket_loader_dotted_path():
    """SocketLoader should load a brain from a dotted path."""
    from nousclaww.brain.socket_loader import SocketLoader
    from nousclaww.brain.local_ollama_brain import LocalOllamaBrain

    loader = SocketLoader()
    # Use a mock llm to avoid needing ollama installed
    mock_llm = MagicMock()
    brain = loader.load(
        "nousclaww.brain.local_ollama_brain:LocalOllamaBrain",
        llm=mock_llm,
    )
    assert isinstance(brain, LocalOllamaBrain)
    assert isinstance(brain, BrainInterface)


def test_socket_loader_empty_path_raises():
    """Empty path should raise ValueError."""
    from nousclaww.brain.socket_loader import SocketLoader

    loader = SocketLoader()
    with pytest.raises(ValueError, match="empty"):
        loader.load("")


def test_socket_loader_invalid_dotted_no_colon():
    """Dotted path without a colon should raise ValueError."""
    from nousclaww.brain.socket_loader import SocketLoader

    loader = SocketLoader()
    with pytest.raises(ValueError, match="module:ClassName"):
        loader.load("nousclaww.brain.local_ollama_brain")


def test_socket_loader_nonexistent_module():
    """Nonexistent module should raise ValueError."""
    from nousclaww.brain.socket_loader import SocketLoader

    loader = SocketLoader()
    with pytest.raises(ValueError, match="cannot import"):
        loader.load("nonexistent.module:SomeClass")


def test_socket_loader_non_brain_class():
    """A class that doesn't implement BrainInterface should be rejected."""
    from nousclaww.brain.socket_loader import SocketLoader

    loader = SocketLoader()
    with pytest.raises(ValueError, match="BrainInterface"):
        loader.load("collections:OrderedDict")


def test_socket_loader_manifest_file(tmp_path):
    """SocketLoader should load from a JSON manifest file."""
    import json
    from nousclaww.brain.socket_loader import SocketLoader
    from nousclaww.brain.local_ollama_brain import LocalOllamaBrain

    manifest = tmp_path / "brain.json"
    manifest.write_text(json.dumps({
        "class": "nousclaww.brain.local_ollama_brain:LocalOllamaBrain",
        "kwargs": {},
    }), encoding="utf-8")

    loader = SocketLoader()
    mock_llm = MagicMock()
    brain = loader.load(str(manifest), llm=mock_llm)
    assert isinstance(brain, LocalOllamaBrain)


def test_socket_loader_manifest_missing_class(tmp_path):
    """Manifest without a 'class' key should raise ValueError."""
    import json
    from nousclaww.brain.socket_loader import SocketLoader

    manifest = tmp_path / "bad.json"
    manifest.write_text(json.dumps({"kwargs": {}}), encoding="utf-8")

    loader = SocketLoader()
    with pytest.raises(ValueError, match="missing 'class'"):
        loader.load(str(manifest))


def test_socket_loader_nonexistent_manifest():
    """Nonexistent path should raise ValueError (treated as dotted path, not file)."""
    from nousclaww.brain.socket_loader import SocketLoader

    loader = SocketLoader()
    # A path that doesn't exist as a file is treated as a dotted path.
    # Since it has no colon, it should raise ValueError.
    with pytest.raises(ValueError, match="module:ClassName"):
        loader.load("/nonexistent/path/brain.json")
