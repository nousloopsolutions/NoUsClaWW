"""Tests for the VisageHUD — community VisageInterface implementation."""

from __future__ import annotations

import time

import pytest

from sovereign_sockets.visage_interface import (
    AFFECT_LABELS,
    VisageInterface,
    AffectSignal,
    FaceFrame,
    VisageState,
)
from nousclaww.visage.visage_hud import VisageHUD, _scrub_pii


def test_visage_hud_implements_interface():
    """VisageHUD must implement VisageInterface."""
    hud = VisageHUD()
    assert isinstance(hud, VisageInterface)


def test_visage_hud_get_affect_default():
    """Default affect should be 'neutral' or 'attentive'."""
    hud = VisageHUD()
    affect = hud.get_affect()
    assert affect.label in AFFECT_LABELS
    assert affect.source == "heuristic"


def test_visage_hud_get_affect_after_update():
    """update_signals should reclassify the affect."""
    hud = VisageHUD()
    hud.update_signals(elapsed_s=0.5, token_count=0, tool_calls=0)
    affect = hud.get_affect()
    assert affect.label == "attentive"  # elapsed < 2s

    hud.update_signals(elapsed_s=20, token_count=3000, tool_calls=0)
    affect = hud.get_affect()
    assert affect.label == "formulating"  # tokens > 2000

    hud.update_signals(elapsed_s=5, token_count=0, tool_calls=5, task_complete=True)
    affect = hud.get_affect()
    assert affect.label == "done"  # task_complete


def test_visage_hud_get_frames():
    """get_frames should return a non-empty list of FaceFrame."""
    hud = VisageHUD()
    frames = hud.get_frames()
    assert len(frames) > 0
    assert all(isinstance(f, FaceFrame) for f in frames)
    assert all(f.glyph for f in frames)


def test_visage_hud_get_state():
    """get_state should return a VisageState with all channels."""
    hud = VisageHUD()
    state = hud.get_state()
    assert isinstance(state, VisageState)
    assert state.spinner
    assert state.face
    assert state.object
    assert state.status
    assert state.cost
    assert state.render
    assert "  " in state.render  # channels are separated by spaces


def test_visage_hud_pii_scrub():
    """_scrub_pii should redact emails, phones, and SSNs."""
    text = "Contact john@example.com or 555-123-4567 or 123-45-6789"
    scrubbed = _scrub_pii(text)
    assert "john@example.com" not in scrubbed
    assert "555-123-4567" not in scrubbed
    assert "123-45-6789" not in scrubbed
    assert "[email]" in scrubbed
    assert "[phone]" in scrubbed
    assert "[ssn]" in scrubbed


def test_visage_hud_pii_in_context_hint():
    """PII in context_hint should be scrubbed."""
    hud = VisageHUD()
    hud.update_signals(context_hint="user email is alice@test.com")
    state = hud.get_state()
    assert "alice@test.com" not in state.render


def test_visage_hud_refusal_affect():
    """When refusal=True, the affect should be a refusal label."""
    hud = VisageHUD()
    hud.update_signals(refusal=True)
    affect = hud.get_affect()
    assert affect.refusal
    assert affect.label == "uncertain"


def test_visage_hud_retries_affect():
    """Multiple retries should show 'stuck' or 'frustrated'."""
    hud = VisageHUD()
    hud.update_signals(retries=3)
    affect = hud.get_affect()
    assert affect.label == "frustrated"

    hud.update_signals(retries=1)
    affect = hud.get_affect()
    assert affect.label == "stuck"


def test_visage_hud_object_emoji():
    """The object emoji should reflect the context hint."""
    hud = VisageHUD()
    hud.update_signals(context_hint="reading code files")
    state = hud.get_state()
    assert state.object == "🧩"  # 'code' keyword

    hud.update_signals(context_hint="searching the web")
    state = hud.get_state()
    assert state.object == "🔍"  # 'search' keyword


def test_visage_hud_cost_tier():
    """set_cost_tier should update the cost badge."""
    hud = VisageHUD()
    hud.set_cost_tier("premium")
    state = hud.get_state()
    assert "premium" in state.cost


def test_visage_hud_all_affect_labels_have_frames():
    """Every affect label in AFFECT_LABELS should have a frame set."""
    from nousclaww.visage.visage_hud import FRAME_SETS
    for label in AFFECT_LABELS:
        assert label in FRAME_SETS, f"Missing frame set for affect: {label}"
        assert len(FRAME_SETS[label]) >= 2, f"Frame set for {label} too short"
