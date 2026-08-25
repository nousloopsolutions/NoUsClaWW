"""Tests for the extended ControlState — JS_EVAL_ENABLED and SHELL_ENABLED."""

from __future__ import annotations

import pytest

from nousclaww.control_state import (
    ControlFlag,
    ControlState,
    ControlStateError,
    DEFAULT_CONTROLS,
)


def test_js_eval_flag_exists():
    """JS_EVAL_ENABLED should be a valid ControlFlag."""
    assert ControlFlag.JS_EVAL_ENABLED in ControlFlag
    assert ControlFlag.JS_EVAL_ENABLED.value == "JS_EVAL_ENABLED"


def test_shell_flag_exists():
    """SHELL_ENABLED should be a valid ControlFlag."""
    assert ControlFlag.SHELL_ENABLED in ControlFlag
    assert ControlFlag.SHELL_ENABLED.value == "SHELL_ENABLED"


def test_new_flags_default_disabled():
    """New flags should be disabled by default."""
    state = ControlState()
    assert not state.can_js_eval()
    assert not state.can_shell()
    assert not state.is_enabled(ControlFlag.JS_EVAL_ENABLED)
    assert not state.is_enabled(ControlFlag.SHELL_ENABLED)


def test_default_controls_includes_new_flags():
    """DEFAULT_CONTROLS should include the new flags."""
    assert ControlFlag.JS_EVAL_ENABLED in DEFAULT_CONTROLS
    assert ControlFlag.SHELL_ENABLED in DEFAULT_CONTROLS
    assert DEFAULT_CONTROLS[ControlFlag.JS_EVAL_ENABLED] is False
    assert DEFAULT_CONTROLS[ControlFlag.SHELL_ENABLED] is False


def test_enable_js_eval():
    """Enabling JS_EVAL_ENABLED should allow can_js_eval."""
    state = ControlState()
    state.enable(ControlFlag.JS_EVAL_ENABLED)
    assert state.can_js_eval()
    assert state.is_enabled(ControlFlag.JS_EVAL_ENABLED)


def test_enable_shell():
    """Enabling SHELL_ENABLED should allow can_shell."""
    state = ControlState()
    state.enable(ControlFlag.SHELL_ENABLED)
    assert state.can_shell()


def test_require_js_eval_raises():
    """require_js_eval should raise when disabled."""
    state = ControlState()
    with pytest.raises(ControlStateError, match="JS_EVAL_ENABLED"):
        state.require_js_eval()


def test_require_shell_raises():
    """require_shell should raise when disabled."""
    state = ControlState()
    with pytest.raises(ControlStateError, match="SHELL_ENABLED"):
        state.require_shell()


def test_require_js_eval_passes_when_enabled():
    """require_js_eval should not raise when enabled."""
    state = ControlState()
    state.enable(ControlFlag.JS_EVAL_ENABLED)
    state.require_js_eval()  # should not raise


def test_require_shell_passes_when_enabled():
    """require_shell should not raise when enabled."""
    state = ControlState()
    state.enable(ControlFlag.SHELL_ENABLED)
    state.require_shell()  # should not raise


def test_generic_can_method():
    """can() should work for any flag."""
    state = ControlState()
    assert not state.can(ControlFlag.OBSERVE_ENABLED)
    state.enable(ControlFlag.OBSERVE_ENABLED)
    assert state.can(ControlFlag.OBSERVE_ENABLED)


def test_generic_require_method():
    """require() should raise for any disabled flag."""
    state = ControlState()
    with pytest.raises(ControlStateError, match="OBSERVE_ENABLED"):
        state.require(ControlFlag.OBSERVE_ENABLED)
    state.enable(ControlFlag.OBSERVE_ENABLED)
    state.require(ControlFlag.OBSERVE_ENABLED)  # should not raise


def test_get_state_includes_new_flags():
    """get_state should include the new flags."""
    state = ControlState()
    state_dict = state.get_state()
    assert "JS_EVAL_ENABLED" in state_dict
    assert "SHELL_ENABLED" in state_dict
    assert state_dict["JS_EVAL_ENABLED"] is False
    assert state_dict["SHELL_ENABLED"] is False


def test_all_disabled_with_new_flags():
    """all_disabled should be True by default with the new flags."""
    state = ControlState()
    assert state.all_disabled()


def test_all_enabled_with_new_flags():
    """all_enabled should require the new flags too."""
    state = ControlState()
    for flag in ControlFlag:
        state.enable(flag)
    assert state.all_enabled()

    state.disable(ControlFlag.JS_EVAL_ENABLED)
    assert not state.all_enabled()
