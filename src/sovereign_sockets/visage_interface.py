"""Sovereign Visage — Affective HUD interface for NoUs agents.

Visage is the signature progress affordance for NoUs products. While an agent is
working, it renders a single compact line that tells the user three things at once:

1. That something is happening (replaces the generic spinner)
2. What kind of work is happening (affective mood + semantic object)
3. How the work is going (status, elapsed time, cost)

The face is a *real affect mirror* — it reflects the agent's actual process state,
not a scripted animation. This is what separates Visage from decoration: the user
is watching a truthful signal, which builds trust during long tasks and gives the
product a recognizable personality across surfaces (desktop, terminal, chat,
mobile).

A Visage render is one line with five independent channels:

    🌗  (◔_◔)  📚  formulating... (23.8s)  ⚕ free
    └─┘  └────┘  └┘  └────────────────────┘  └────┘
    spinner  face  object       status              cost

Each channel is driven by a different signal source and updates at its own rate.
Layers are independent — the face can shift mood without the object changing,
and vice versa.

This is a BLANK abstract interface (sovereign socket pattern). The proprietary
Brain provides the concrete implementation. No implementation lives here — only
the contract.

#C Adapted from SOVEREIGN_VISAGE_SPEC.md
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Affect classifier mapping
# ---------------------------------------------------------------------------

# Maps observable agent signals to an emotion label. Hybrid approach: cheap
# heuristics first, model self-report for nuance (Phase 2+, opt-in only).
#
#   Signal                                    | Heuristic                  | Affect
#   ------------------------------------------|----------------------------|----------------
#   elapsed < 2s, no tool calls               | just started               | neutral / attentive
#   many tokens in context                    | heavy load                 | focused / formulating
#   repeated searches, low-confidence results | research difficulty        | searching / puzzled
#   same prompt retried N times               | stuck                      | frustrated / determined
#   quick confident answer, few tool calls    | confident                  | smug / pleased
#   stream tokens flowing                     | actively emitting          | talking (mouth frames)
#   task complete                             | done                       | satisfied / sleepy
#
# Affect safety rule: affect reflects *process*, not output quality. The face
# may show "this is hard" (formulating, stuck) but must never show "I don't know
# what I'm doing" in a way that undermines trust in a correct answer. Affects
# like `confused` / `lost` are reserved for genuinely unresolved states.
#
# Refusal affect: when the agent declines to answer or comply — low confidence,
# safety gate, scope violation, axiom refusal — the face shows the agent's
# *actual state at the moment of refusal*, not a calculated apology.
#
#   Refusal reason              | Affect       | Face (example)  | What it tells the user
#   ----------------------------|--------------|-----------------|-------------------------------------------
#   low confidence              | uncertain    | (´・_・`)        | "I'm not refusing to be difficult — I genuinely don't know"
#   safety gate                 | cautious     | (•̀_•́ )         | "I see a risk here and I'm being careful"
#   scope violation             | firm         | ( ͡° ͜ʖ ͡°)       | "This is outside what I'm built for"
#   axiom refusal               | resolute     | ( •̀_•́)         | "This crosses a line I won't cross"
#   genuinely tried, came empty | apologetic   | (；ω；)          | "I really did try, and I'm sorry"
#
# Hard rule — reflective, not performative: the refusal face must mirror the
# agent's real internal state. It must never be a sad face deployed strategically
# to soften user anger or avoid accountability.
#
# No affect on deceptive refusals: if the agent is refusing for a reason it
# cannot disclose (e.g. a hidden safety policy), it shows `neutral` rather than
# performing a false emotion. A blank face is more honest than a fabricated one.

AFFECT_LABELS: tuple[str, ...] = (
    "neutral",
    "attentive",
    "formulating",
    "searching",
    "puzzled",
    "stuck",
    "frustrated",
    "determined",
    "confident",
    "smug",
    "pleased",
    "talking",
    "done",
    "satisfied",
    "sleepy",
    # refusal affects
    "uncertain",
    "cautious",
    "firm",
    "resolute",
    "apologetic",
)


# ---------------------------------------------------------------------------
# Settling curve
# ---------------------------------------------------------------------------

# Frame rate decays as elapsed time grows. The animation *settles* the longer a
# task runs, the same way real spinners get quieter.
#
#   Elapsed    | Frame rate             | Behavior
#   -----------|------------------------|----------------------------------
#   0–5s       | ~5 fps                 | lively cycling
#   5–30s      | ~2 fps                 | slower, steadier
#   30s–2min   | ~1 fps                 | mostly static, occasional blink
#   > 2min     | static + blink ~10s    | calm, not panicking
#
# Affect changes still trigger an immediate frame shift regardless of settled
# rate — mood changes are meaningful and should read instantly.

SETTLING_CURVE: tuple[tuple[float, float], ...] = (
    (5.0, 5.0),    # 0–5s:   ~5 fps
    (30.0, 2.0),   # 5–30s:  ~2 fps
    (120.0, 1.0),  # 30s–2m: ~1 fps
    (float("inf"), 0.1),  # >2m:   static + blink every ~10s
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AffectSignal:
    """A single classified affect reading from the agent's process state.

    The affect classifier maps observable agent signals (elapsed time, token
    count, tool call count, retry count, stream token rate, task completion)
    to an emotion label. This is a *real* mirror of process state, not a
    scripted animation.

    Attributes:
        label: The affect label (must be one of AFFECT_LABELS).
        confidence: How certain the classifier is about this label (0.0–1.0).
        source: What triggered this affect — "heuristic" or "self_report".
        refusal: True if this affect was captured at a refusal moment. When
            True, the face shows the agent's actual state at the moment of
            refusal, not a calculated apology.
        signals: The raw signal values that produced this classification
            (elapsed_s, token_count, tool_calls, retries, stream_rate, etc.).
    """

    label: str
    confidence: float
    source: str  # "heuristic" | "self_report"
    refusal: bool = False
    signals: dict[str, Any] = field(default_factory=dict)


@dataclass
class FaceFrame:
    """A single frame in an affect's kaomoji frame set.

    Each affect label maps to a frame set of 2–6 kaomoji that cycle to create
    motion. Frame sets are hand-authored in v0; procedurally generated in a
    later phase.

    Attributes:
        glyph: The kaomoji string for this frame (e.g. "(◔_◔)").
        duration_ms: How long to hold this frame before advancing (ms).
            Governed by the settling curve — shorter early, longer as the
            task runs.
        blink: True if this is a blink frame (sparse, used in settled state).
    """

    glyph: str
    duration_ms: int
    blink: bool = False


@dataclass
class VisageState:
    """The full render state of a Visage line — all five channels.

    Composed by the Visage renderer from telemetry signals. The render string
    is assembled as:

        {spinner}  {face}  {object}  {status}  {cost}

    Attributes:
        spinner: Cyclic progress indicator glyph (e.g. moon phase, dots).
            Always-on while working, fixed ~4 fps.
        face: Current kaomoji glyph from the active affect's frame set.
            Variable rate, settles over time per SETTLING_CURVE.
        object: A single noun emoji hinting at what the agent is processing
            (e.g. 📚 for docs, 🔑 for auth, 🧩 for code synthesis).
            Shifts on context change.
        status: State label + elapsed timer (e.g. "formulating... (23.8s)").
            ~10 fps for timer.
        cost: Tier/credits badge (e.g. "⚕ free"). Updates on change.
        affect: The current AffectSignal driving the face channel.
        frames: The current frame set for the active affect.
        elapsed_s: Elapsed seconds since task start (used for settling curve).
        frame_rate: Current frame rate in fps, derived from SETTLING_CURVE.
        render: The fully composed render string (all channels joined).
    """

    spinner: str
    face: str
    object: str
    status: str
    cost: str
    affect: AffectSignal
    frames: list[FaceFrame]
    elapsed_s: float
    frame_rate: float
    render: str


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class VisageInterface(ABC):
    """Sovereign Visage — the affective HUD contract.

    Visage is a *render layer*, not a new subsystem. It subscribes to existing
    telemetry and emits a render string. It does not influence agent decisions,
    does not store state, and does not cross VDS boundaries. It reads telemetry
    and renders.

    The proprietary implementation lives in /proprietary_core/ (gitignored).
    The public Tool may only read Visage state through this interface.

    Sovereign Axiom check: Visage is a display layer only.

    Cheshire scrubber: Visage output must pass through the scrubber — no PII
    in object/face selection.
    """

    @abstractmethod
    def get_affect(self) -> AffectSignal:
        """Classify the agent's current process state into an affect signal.

        Uses the hybrid affect classifier: cheap heuristics first (elapsed
        time, token count, tool calls, retries, stream rate, task completion),
        with optional model self-report for nuance (Phase 2+, opt-in only).

        Returns:
            AffectSignal with the current label, confidence, source, and
            raw signals. If the agent is at a refusal moment, `refusal=True`
            and the label reflects the agent's actual state — not a calculated
            apology.

        Safety rule: affect reflects *process*, not output quality. Affects
        like `confused` / `lost` are reserved for genuinely unresolved states.
        Deceptive refusals show `neutral` — a blank face is more honest than
        a fabricated one.
        """
        ...

    @abstractmethod
    def get_frames(self) -> list[FaceFrame]:
        """Return the current frame set for the active affect.

        Each affect label maps to 2–6 kaomoji frames that cycle to create
        motion. Frame durations are governed by the settling curve: lively
        (~5 fps) early, slowing to static + blink as elapsed time grows.

        Affect changes trigger an immediate frame shift regardless of the
        settled rate — mood changes are meaningful and should read instantly.

        Returns:
            A list of FaceFrame for the currently active affect label.
        """
        ...

    @abstractmethod
    def get_state(self) -> VisageState:
        """Return the full render state — all five channels composed.

        Assembles the complete Visage line:

            {spinner}  {face}  {object}  {status}  {cost}

        The spinner is a fixed cyclic indicator (~4 fps). The face is the
        current kaomoji from the active affect's frame set (variable rate,
        settling). The object is a noun emoji from the semantic extractor
        (shifts on context change). The status is the state label + elapsed
        timer (~10 fps). The cost is the tier/credits badge (on change).

        Returns:
            VisageState with all channels, the current AffectSignal, the
            frame set, elapsed time, current frame rate, and the composed
            render string.

        All output must pass through the Cheshire scrubber — no PII in
        object or face selection.
        """
        ...
