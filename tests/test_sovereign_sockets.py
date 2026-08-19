"""Tests for the Sovereign Sockets abstract interfaces.

These tests verify the immutability of the open-core boundary:
- Every interface is abstract and cannot be instantiated directly.
- Every interface exposes the required methods.
- No proprietary_core module is importable or committed.
- No file in src/community/ or src/nousclaww/ imports proprietary_core.
- Simple stub implementations conform to the contracts.
"""
from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure the src directory is on the path so we can import sovereign_sockets.
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sovereign_sockets import (  # noqa: E402
    BrainInterface,
    BrainResponse,
    ConfidenceProvider,
    ElevatorInterface,
    RedTeamInterface,
    RedTeamVerdict,
    VoidArchiveInterface,
    VoidSocket,
)


# ---------------------------------------------------------------------------
# Abstractness tests
# ---------------------------------------------------------------------------


def test_brain_interface_is_abstract() -> None:
    """BrainInterface cannot be instantiated directly (it is abstract)."""
    with pytest.raises(TypeError):
        BrainInterface()  # type: ignore[abstract]


def test_confidence_provider_is_abstract() -> None:
    """ConfidenceProvider cannot be instantiated directly."""
    with pytest.raises(TypeError):
        ConfidenceProvider()  # type: ignore[abstract]


def test_elevator_interface_is_abstract() -> None:
    """ElevatorInterface cannot be instantiated directly."""
    with pytest.raises(TypeError):
        ElevatorInterface()  # type: ignore[abstract]


def test_redteam_interface_is_abstract() -> None:
    """RedTeamInterface cannot be instantiated directly."""
    with pytest.raises(TypeError):
        RedTeamInterface()  # type: ignore[abstract]


def test_void_archive_is_abstract() -> None:
    """VoidArchiveInterface cannot be instantiated directly."""
    with pytest.raises(TypeError):
        VoidArchiveInterface()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Method-presence tests
# ---------------------------------------------------------------------------


def test_brain_interface_has_reason_method() -> None:
    """BrainInterface has a reason() method."""
    assert hasattr(BrainInterface, "reason")
    assert callable(getattr(BrainInterface, "reason"))


def test_brain_interface_has_get_confidence_method() -> None:
    """BrainInterface has a get_confidence() method."""
    assert hasattr(BrainInterface, "get_confidence")
    assert callable(getattr(BrainInterface, "get_confidence"))


def test_brain_interface_has_create_void_socket_method() -> None:
    """BrainInterface has a create_void_socket() method."""
    assert hasattr(BrainInterface, "create_void_socket")
    assert callable(getattr(BrainInterface, "create_void_socket"))


def test_confidence_provider_has_required_methods() -> None:
    """ConfidenceProvider exposes all five required methods."""
    for name in (
        "compute_confidence",
        "get_dynamic_threshold",
        "check_sycophancy",
        "check_emotional_bias",
        "check_confirmation_bias",
    ):
        assert hasattr(ConfidenceProvider, name), f"missing {name}"


def test_elevator_interface_has_required_methods() -> None:
    """ElevatorInterface exposes all five required methods."""
    for name in (
        "cross_floor_to_surface",
        "cross_surface_to_floor",
        "annihilate_session",
        "check_session_state",
        "detect_token_fluctuation",
    ):
        assert hasattr(ElevatorInterface, name), f"missing {name}"


def test_redteam_interface_has_required_methods() -> None:
    """RedTeamInterface exposes all five required methods."""
    for name in (
        "evaluate_output",
        "check_lethal_trifecta",
        "check_false_dilemma",
        "check_slippery_slope",
        "check_sycophancy",
    ):
        assert hasattr(RedTeamInterface, name), f"missing {name}"


def test_void_archive_has_required_methods() -> None:
    """VoidArchiveInterface exposes all five required methods."""
    for name in (
        "store_void_socket",
        "retrieve_void_socket",
        "list_unresolved_void_sockets",
        "resolve_void_socket",
        "get_void_socket_stats",
    ):
        assert hasattr(VoidArchiveInterface, name), f"missing {name}"


# ---------------------------------------------------------------------------
# Open-core boundary tests
# ---------------------------------------------------------------------------


def test_open_core_boundary() -> None:
    """import proprietary_core raises ModuleNotFoundError (it is gitignored)."""
    with pytest.raises(ModuleNotFoundError):
        import proprietary_core  # noqa: F401, PLC0415


def test_no_reflection_to_brain() -> None:
    """importlib.import_module('proprietary_core.*') fails for any submodule."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("proprietary_core.brain")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("proprietary_core.elevator")


def test_no_proprietary_core_files_committed() -> None:
    """git ls-files proprietary_core/ returns empty (skip if not in a git repo)."""
    if not (ROOT / ".git").exists():
        pytest.skip("not a git repo yet")
    result = subprocess.run(
        ["git", "ls-files", "proprietary_core/"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "", (
        f"proprietary_core files are committed: {result.stdout!r}"
    )


def _python_files_in(directory: Path) -> list[Path]:
    """Return all .py files under directory (recursive)."""
    if not directory.exists():
        return []
    return list(directory.rglob("*.py"))


def _imports_proprietary_core(path: Path) -> list[str]:
    """Return any import statements in path that reference proprietary_core."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("proprietary_core"):
                    offenders.append(f"{path}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("proprietary_core"):
                offenders.append(
                    f"{path}:{node.lineno}: from {node.module} import ..."
                )
    return offenders


def test_no_brain_imports_in_community() -> None:
    """AST analysis of src/community/ and src/nousclaww/ finds no imports of proprietary_core."""
    offenders: list[str] = []
    for sub in ("community", "nousclaww"):
        for py in _python_files_in(SRC / sub):
            offenders.extend(_imports_proprietary_core(py))
    assert not offenders, (
        "files in src/community/ or src/nousclaww/ import proprietary_core:\n"
        + "\n".join(offenders)
    )


def test_sovereign_sockets_have_no_proprietary_imports() -> None:
    """The sovereign_sockets package itself must not import proprietary_core."""
    for py in _python_files_in(SRC / "sovereign_sockets"):
        offenders = _imports_proprietary_core(py)
        assert not offenders, f"sovereign_sockets imports proprietary_core: {offenders}"


# ---------------------------------------------------------------------------
# Stub-implementation conformance tests
# ---------------------------------------------------------------------------


class _StubBrain(BrainInterface):
    """Minimal stub implementation of BrainInterface for conformance testing."""

    def reason(self, context: str, query: str, constraints: dict) -> BrainResponse:
        return BrainResponse(
            output="stub",
            confidence=0.5,
            void_sockets=[],
            metadata={"stub": True},
        )

    def get_confidence(self, response: str, context: str) -> float:
        return 0.5

    def create_void_socket(self, query: str, gap_description: str) -> str:
        return "void-stub-id"


class _StubConfidenceProvider(ConfidenceProvider):
    def compute_confidence(self, response, context, model_constraints) -> float:
        return 0.9

    def get_dynamic_threshold(self, session_state: dict) -> float:
        return 0.5

    def check_sycophancy(self, response: str, user_input: str) -> bool:
        return False

    def check_emotional_bias(self, response: str) -> bool:
        return False

    def check_confirmation_bias(self, response: str, prior_context: str) -> bool:
        return False


class _StubElevator(ElevatorInterface):
    def cross_floor_to_surface(self, data: dict, session_id: str) -> dict:
        return data

    def cross_surface_to_floor(self, data: dict, session_id: str) -> dict:
        return data

    def annihilate_session(self, session_id: str) -> None:
        return None

    def check_session_state(self, session_id: str) -> str:
        return "active"

    def detect_token_fluctuation(self, session_id: str, token_state: dict) -> bool:
        return False


class _StubRedTeam(RedTeamInterface):
    def evaluate_output(self, response: str, context: str) -> RedTeamVerdict:
        return RedTeamVerdict(passed=True, violations=[], severity="none")

    def check_lethal_trifecta(
        self, response: str, has_private_access: bool, has_external_action: bool
    ) -> bool:
        return False

    def check_false_dilemma(self, response: str) -> bool:
        return False

    def check_slippery_slope(self, response: str) -> bool:
        return False

    def check_sycophancy(self, response: str, user_input: str) -> bool:
        return False


class _StubVoidArchive(VoidArchiveInterface):
    def store_void_socket(self, query: str, gap_description: str, session_id: str) -> str:
        return "void-stub-id"

    def retrieve_void_socket(self, void_socket_id: str):
        return None

    def list_unresolved_void_sockets(self):
        return []

    def resolve_void_socket(self, void_socket_id: str, resolution: str) -> None:
        return None

    def get_void_socket_stats(self) -> dict:
        return {"total": 0, "resolved": 0, "unresolved": 0, "by_category": {}}


def test_stub_implementations_conform() -> None:
    """Create simple stub implementations of each interface and verify they work."""
    brain = _StubBrain()
    resp = brain.reason("ctx", "q", {})
    assert resp.output == "stub"
    assert 0.0 <= resp.confidence <= 1.0
    assert brain.get_confidence("x", "y") == 0.5
    assert brain.create_void_socket("q", "gap") == "void-stub-id"

    cp = _StubConfidenceProvider()
    assert 0.0 <= cp.compute_confidence("r", "c", {}) <= 1.0
    assert 0.0 <= cp.get_dynamic_threshold({}) <= 1.0
    assert cp.check_sycophancy("r", "u") is False
    assert cp.check_emotional_bias("r") is False
    assert cp.check_confirmation_bias("r", "p") is False

    elev = _StubElevator()
    assert elev.cross_floor_to_surface({"a": 1}, "s") == {"a": 1}
    assert elev.cross_surface_to_floor({"a": 1}, "s") == {"a": 1}
    elev.annihilate_session("s")
    assert elev.check_session_state("s") == "active"
    assert elev.detect_token_fluctuation("s", {}) is False

    rt = _StubRedTeam()
    verdict = rt.evaluate_output("r", "c")
    assert isinstance(verdict, RedTeamVerdict)
    assert verdict.passed is True
    assert rt.check_lethal_trifecta("r", True, True) is False
    assert rt.check_false_dilemma("r") is False
    assert rt.check_slippery_slope("r") is False
    assert rt.check_sycophancy("r", "u") is False

    va = _StubVoidArchive()
    assert va.store_void_socket("q", "gap", "s") == "void-stub-id"
    assert va.retrieve_void_socket("nope") is None
    assert va.list_unresolved_void_sockets() == []
    va.resolve_void_socket("id", "resolved")
    stats = va.get_void_socket_stats()
    assert {"total", "resolved", "unresolved", "by_category"} <= set(stats)


def test_dataclasses_construct_correctly() -> None:
    """BrainResponse, RedTeamVerdict, and VoidSocket dataclasses construct cleanly."""
    br = BrainResponse(output="x", confidence=0.7, void_sockets=["a"], metadata={})
    assert br.output == "x"
    assert br.confidence == 0.7
    assert br.void_sockets == ["a"]

    v = RedTeamVerdict(passed=False, violations=["lethal"], severity="lethal")
    assert v.passed is False
    assert v.violations == ["lethal"]
    assert v.severity == "lethal"

    vs = VoidSocket(
        id="id",
        query="q",
        gap_description="gap",
        session_id="s",
        created_at="2026-01-01T00:00:00Z",
        resolved=False,
        resolution=None,
    )
    assert vs.id == "id"
    assert vs.resolved is False
    assert vs.resolution is None
