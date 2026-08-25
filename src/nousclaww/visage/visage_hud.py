"""VisageHUD — community VisageInterface implementation.

Implements ``sovereign_sockets.visage_interface.VisageInterface`` with
the 19 affect labels defined in the sovereign socket. This is a
heuristic-only implementation (no model self-report) that maps
observable process signals to affect labels and renders a one-line HUD.

The HUD line format is::

    {spinner}  {face}  {object}  {status}  {cost}

All output passes through a simple PII scrub (email/phone redaction)
before rendering, per the Visage safety rule.

SYNTH:
    purpose: Community VisageInterface implementation with heuristic affect classification and PII scrubbing.
    axioms: [local_first, epistemic_boundary, honest_failure_over_fake_success]
    objective: Render a truthful, one-line affective HUD that mirrors the
        agent's actual process state — no fabricated emotions, no PII leaks.
    anti_patterns:
        - Never show an affect that does not reflect the real process state
        - Never render PII in any channel
        - Never fabricate a refusal affect to soften user anger
        - Never influence agent decisions — Visage is display only

#C Adapted from sovereign_sockets.visage_interface
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from sovereign_sockets.visage_interface import (
    AFFECT_LABELS,
    SETTLING_CURVE,
    AffectSignal,
    FaceFrame,
    VisageInterface,
    VisageState,
)

# ── Kaomoji frame sets ─────────────────────────────────────────────────────
# Each affect label maps to 2-6 kaomoji frames that cycle to create motion.
# All kaomoji are plain ASCII-safe or properly quoted Unicode strings.

FRAME_SETS: dict[str, list[str]] = {
    "neutral":      ["(._.)", "(._.) "],
    "attentive":    ["(o_o)", "(O_O)"],
    "formulating":  ["(o_o)", "(O_O) "],
    "searching":    ["(o.o)", "(O.O)"],
    "puzzled":      ["(o_o)?", "(O_O)?"],
    "stuck":        ["(x_x)", "(x_x) "],
    "frustrated":   ["(>_<)", "(>_<) "],
    "determined":   ["(>_o)", "(o_<) "],
    "confident":    ["(^_^)", "(^_^) "],
    "smug":         ["(^_-)", "(^-_) "],
    "pleased":      ["(^-^)", "(^-^) "],
    "talking":      ["(o_o)", "(o_o) ", "(o_o)  "],
    "done":         ["(-_-)", "(-_-) "],
    "satisfied":    ["(^o^)", "(^o^) "],
    "sleepy":       ["(-_-)z", "(-_-)z "],
    "uncertain":    ["(o_o;)", "(O_O;) "],
    "cautious":     ["(o_o)", "(O_O) "],
    "firm":         ["(>_o)", "(o_<) "],
    "resolute":     ["(>_o)", "(o_<) "],
    "apologetic":   ["(T_T)", "(T_T) "],
}

# Semantic object emoji — maps context keywords to a noun emoji.
OBJECT_MAP: dict[str, str] = {
    "code": "\U0001f9e9",
    "file": "\U0001f4c4",
    "docs": "\U0001f4da",
    "auth": "\U0001f511",
    "search": "\U0001f50d",
    "web": "\U0001f310",
    "data": "\U0001f4ca",
    "test": "\U0001f9ea",
    "build": "\U0001f528",
    "deploy": "\U0001f680",
    "error": "\u26a0\ufe0f",
    "memory": "\U0001f9e0",
    "task": "\U0001f4cb",
    "shell": "\U0001f4bb",
    "browser": "\U0001f5a5\ufe0f",
}

# Spinner glyphs (moon phases)
SPINNER_FRAMES = [
    "\U0001f311", "\U0001f312", "\U0001f313", "\U0001f314",
    "\U0001f315", "\U0001f316", "\U0001f317", "\U0001f318",
]

# PII scrub patterns
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def _scrub_pii(text: str) -> str:
    """Redact PII patterns from text."""
    text = _EMAIL_RE.sub("[email]", text)
    text = _PHONE_RE.sub("[phone]", text)
    text = _SSN_RE.sub("[ssn]", text)
    return text


def _frame_rate_for_elapsed(elapsed_s: float) -> float:
    """Return the frame rate (fps) for the given elapsed time."""
    for threshold, rate in SETTLING_CURVE:
        if elapsed_s <= threshold:
            return rate
    return 0.1


def _duration_ms_for_rate(rate: float) -> int:
    """Convert fps to duration in milliseconds."""
    if rate <= 0:
        return 10000
    return int(1000 / rate)


@dataclass
class _VisageState:
    """Internal mutable state for the VisageHUD."""

    start_time: float = field(default_factory=time.time)
    affect_label: str = "neutral"
    affect_confidence: float = 0.5
    affect_source: str = "heuristic"
    refusal: bool = False
    signals: dict[str, Any] = field(default_factory=dict)
    context_hint: str = ""
    cost_tier: str = "free"
    frame_index: int = 0
    spinner_index: int = 0


class VisageHUD(VisageInterface):
    """Community VisageInterface implementation — heuristic affect HUD.

    Args:
        cost_tier: The cost badge to display (default ``"free"``).
    """

    def __init__(self, cost_tier: str = "free") -> None:
        self._state = _VisageState(cost_tier=cost_tier)

    # ── Public API (for the sidecar to feed signals) ──────────────────────

    def update_signals(
        self,
        elapsed_s: float | None = None,
        token_count: int = 0,
        tool_calls: int = 0,
        retries: int = 0,
        stream_rate: float = 0.0,
        task_complete: bool = False,
        refusal: bool = False,
        context_hint: str = "",
    ) -> None:
        """Update the internal process signals and reclassify affect."""
        if elapsed_s is not None:
            self._state.start_time = time.time() - elapsed_s
        self._state.signals = {
            "elapsed_s": self._elapsed(),
            "token_count": token_count,
            "tool_calls": tool_calls,
            "retries": retries,
            "stream_rate": stream_rate,
            "task_complete": task_complete,
        }
        self._state.refusal = refusal
        self._state.context_hint = _scrub_pii(context_hint)
        self._classify()

    def set_cost_tier(self, tier: str) -> None:
        """Update the cost badge."""
        self._state.cost_tier = tier

    # ── VisageInterface ───────────────────────────────────────────────────

    def get_affect(self) -> AffectSignal:
        """Classify the agent's current process state into an affect signal."""
        return AffectSignal(
            label=self._state.affect_label,
            confidence=self._state.affect_confidence,
            source=self._state.affect_source,
            refusal=self._state.refusal,
            signals=dict(self._state.signals),
        )

    def get_frames(self) -> list[FaceFrame]:
        """Return the current frame set for the active affect."""
        glyphs = FRAME_SETS.get(self._state.affect_label, FRAME_SETS["neutral"])
        rate = _frame_rate_for_elapsed(self._elapsed())
        duration = _duration_ms_for_rate(rate)
        frames: list[FaceFrame] = []
        for i, glyph in enumerate(glyphs):
            frames.append(FaceFrame(glyph=glyph, duration_ms=duration, blink=(i == len(glyphs) - 1 and rate < 1.0)))
        return frames

    def get_state(self) -> VisageState:
        """Return the full render state — all five channels composed."""
        elapsed = self._elapsed()
        rate = _frame_rate_for_elapsed(elapsed)

        # Spinner
        self._state.spinner_index = (self._state.spinner_index + 1) % len(SPINNER_FRAMES)
        spinner = SPINNER_FRAMES[self._state.spinner_index]

        # Face
        glyphs = FRAME_SETS.get(self._state.affect_label, FRAME_SETS["neutral"])
        self._state.frame_index = (self._state.frame_index + 1) % len(glyphs)
        face = glyphs[self._state.frame_index]

        # Object
        obj = self._object_for_context()

        # Status
        status = f"{self._state.affect_label}... ({elapsed:.1f}s)"

        # Cost
        cost = f"\u2695 {self._state.cost_tier}"

        # Compose render
        render = _scrub_pii(f"{spinner}  {face}  {obj}  {status}  {cost}")

        return VisageState(
            spinner=spinner,
            face=face,
            object=obj,
            status=status,
            cost=cost,
            affect=self.get_affect(),
            frames=self.get_frames(),
            elapsed_s=elapsed,
            frame_rate=rate,
            render=render,
        )

    # ── Internal ──────────────────────────────────────────────────────────

    def _elapsed(self) -> float:
        return time.time() - self._state.start_time

    def _classify(self) -> None:
        """Map process signals to an affect label using heuristics."""
        s = self._state.signals
        elapsed = s.get("elapsed_s", 0.0)
        retries = s.get("retries", 0)
        tool_calls = s.get("tool_calls", 0)
        tokens = s.get("token_count", 0)
        stream_rate = s.get("stream_rate", 0.0)
        complete = s.get("task_complete", False)

        if self._state.refusal:
            self._state.affect_label = "uncertain"
            self._state.affect_confidence = 0.7
        elif complete:
            self._state.affect_label = "done"
            self._state.affect_confidence = 0.9
        elif retries >= 3:
            self._state.affect_label = "frustrated"
            self._state.affect_confidence = 0.8
        elif retries >= 1:
            self._state.affect_label = "stuck"
            self._state.affect_confidence = 0.7
        elif tool_calls > 3 and elapsed > 10:
            self._state.affect_label = "searching"
            self._state.affect_confidence = 0.6
        elif tokens > 2000:
            self._state.affect_label = "formulating"
            self._state.affect_confidence = 0.6
        elif stream_rate > 10:
            self._state.affect_label = "talking"
            self._state.affect_confidence = 0.7
        elif elapsed < 2:
            self._state.affect_label = "attentive"
            self._state.affect_confidence = 0.5
        else:
            self._state.affect_label = "neutral"
            self._state.affect_confidence = 0.5

        self._state.affect_source = "heuristic"

    def _object_for_context(self) -> str:
        """Pick a semantic object emoji from the context hint."""
        hint = self._state.context_hint.lower()
        for keyword, emoji in OBJECT_MAP.items():
            if keyword in hint:
                return emoji
        return "\u25c7"  # default neutral object
