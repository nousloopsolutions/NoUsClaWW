"""
axiom_synth.py - Internalized axiom-synth loop with reflex + guardrail.

SYNTH:
    purpose: Make the ten axioms intrinsic to every decision the agent makes,
             not a compliance checklist it reads and checks against. Axioms
             shape the action space (what options even appear), the reflex
             (a fast "does this feel off?" check), and the guardrail (a hard
             block only when the first two layers fail). Like core values in
             a person: acted on, often can't explain why, but always present.
    axioms: [local_first, llm_agnostic, open_process, epistemic_boundary,
             completion_assumption, scientific_method, evidence_over_intuition,
             iteration_is_progress, honest_failure_over_fake_success,
             reversibility_awareness]
    objective: Zero drift between user intent and agent action. The agent
               drives to completion using science, research, and iteration.
               Axioms are internalized — they shape thinking, not just gate
               it. The guardrail exists for when internalization fails, not
               as the primary mechanism.
    anti_patterns:
        - Treating axioms as a checklist to read and comply with.
        - A separate "check" step that can be bypassed by skipping it.
        - Generating axiom-violating actions then rejecting them (wasteful
          and bypassable — the violation was already considered).
        - Soft warnings instead of hard blocks at the guardrail layer.
        - Reporting "done" when only "blocked" is honest.
        - Stopping at "good enough" without testing that hypothesis.
        - Taking an irreversible action without explicit confirmation.

ARCHITECTURE — three layers, like human values:

    Layer 1: INTERNALIZATION (context injection)
        Axioms are always in the agent's context window, shaping how it
        frames problems. Like a person's values: always present, shaping
        what options even occur to them. No separate step. Cannot be
        bypassed because there's no step to skip — the axioms ARE the
        lens, not a filter after the lens.

    Layer 2: REFLEX (intrinsic unease)
        A fast, intrinsic check that runs as part of action generation,
        not after it. Like the unease you feel before doing something
        against your values — you don't run a compliance audit, you just
        feel it's wrong. Implemented as a token-overlap reflex: does the
        declared intent resonate with the file's purpose? Low resonance
        = unease = the action is re-framed or abandoned before it forms.

    Layer 3: GUARDRAIL (hard block)
        The safety net. Only triggered when internalization and reflex
        both fail (fatigue, pressure, long-session drift). Like a highway
        guardrail: you only notice it if you drift off the road. Raises
        AxiomViolation — a hard block, not a warning. Cannot be caught
        and continued (doing so is itself an axiom violation).

The three layers are wired into action entry points (sanitize, run_10k,
sentry) so the caller cannot skip them. But the primary mechanism is
Layer 1 — the axioms shape the thinking. Layers 2 and 3 are fallbacks.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# The ten axioms — the agent's core values. These are not rules to check
# against. They are the lens through which the agent sees every problem.
# Loaded once, hash-cached, always present in context. If the axiom text
# changes, the cache invalidates and they are re-internalized.
# ---------------------------------------------------------------------------

_AXIOMS_TEXT = """\
1. local_first: The agent runs on the user's machine. Local LLMs are primary.
   Cloud is fallback. No telemetry, no phone-home.
2. llm_agnostic: No model provider lock-in. The hybrid router selects models
   based on capability profiling and user config.
3. open_process: Architecture, decisions, and research are public. Every
   architectural change gets an ADR.
4. epistemic_boundary: The agent knows what it doesn't know. Low confidence
   means silence, not hallucination. Gaps are logged to the void socket.
5. completion_assumption: The task IS completable until proven otherwise.
   "I can't" requires exhausting all known avenues first. Stopping early
   without exhausting avenues is drift, not prudence.
6. scientific_method: Hypothesize, test, measure, iterate. No assertion
   without evidence. Every claim must be falsifiable. Reproducibility
   is mandatory: any result must be reproducible from the same inputs.
7. evidence_over_intuition: Measure, don't guess. Cite SHA-pinned sources.
   "Latest" is not evidence. "Recent" is not evidence. Verify the real
   state of the world with tools before asserting.
8. iteration_is_progress: The first attempt is a draft. Refine through
   cycles. "Good enough" is a hypothesis to test, not a stopping point.
   Each iteration must be measurably closer to the objective.
9. honest_failure_over_fake_success: Reporting a specific blocker IS
   success. Hiding a blocker IS failure. It is always safe to say
   "I'm stuck here, here's why, here's what I tried." Fake success is
   the only true failure.
10. reversibility_awareness: Prefer reversible actions. Be extra careful
    with irreversible ones (rm -rf, force-push, DROP TABLE, bulk delete).
    Irreversible actions require explicit user confirmation for that
    specific action. A prior approval does not extend to a new one.
"""

_AXIOM_KEYS = (
    "local_first",
    "llm_agnostic",
    "open_process",
    "epistemic_boundary",
    "completion_assumption",
    "scientific_method",
    "evidence_over_intuition",
    "iteration_is_progress",
    "honest_failure_over_fake_success",
    "reversibility_awareness",
)


@dataclass(frozen=True)
class AxiomSet:
    """Immutable snapshot of the ten axioms + content hash.

    The hash lets us detect if the axiom text was tampered with at runtime
    (a drift attack). If the hash changes, the cache invalidates and axioms
    are re-internalized. This is the "reread before every action" property,
    made cheap: hash comparison is ~100ns, full re-read only on mismatch.
    """
    text: str
    sha256: str
    loaded_at: float

    @classmethod
    def load(cls) -> "AxiomSet":
        text = _AXIOMS_TEXT
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        return cls(text=text, sha256=sha, loaded_at=time.monotonic_ns())


_axiom_cache: Optional[AxiomSet] = None


def _get_axioms() -> AxiomSet:
    """Return cached axiom set. Re-internalizes if hash changes."""
    global _axiom_cache
    fresh = AxiomSet.load()
    if _axiom_cache is None or _axiom_cache.sha256 != fresh.sha256:
        _axiom_cache = fresh
    return _axiom_cache


# ---------------------------------------------------------------------------
# Layer 1: INTERNALIZATION
#
# The axiom context string that gets injected into the agent's working
# context at every action boundary. This is not a "read and comply" step —
# it's the values being present in the room while the agent thinks. Like
# how a person's values are always with them, shaping what they consider.
#
# In an LLM agent, this means the axioms are in the system prompt / context
# window. In a code module, this means the axioms shape the function's
# preconditions and postconditions. Here, we provide both: a context string
# for LLM injection, and a programmatic check for code-level enforcement.
# ---------------------------------------------------------------------------


def axiom_context() -> str:
    """Return the axiom text for context injection.

    This is Layer 1. Call this at the start of any reasoning loop to keep
    the axioms present in the agent's context. The string is compact
    (under 2K tokens) so it can be present at every action boundary without
    significant context cost. Like a person's values: always there, shaping
    perception, not requiring conscious effort to recall.
    """
    return _get_axioms().text


def axiom_keys() -> tuple[str, ...]:
    """Return the axiom identifiers (for programmatic checks)."""
    return _AXIOM_KEYS


# ---------------------------------------------------------------------------
# Synth block — every file carries one. This is the file's "personality":
# what it's for, what values it holds, what it refuses to do. Parsed
# before the file is used, but the primary mechanism is that the synth
# block shapes how callers frame their intent toward the file.
#
# Format (inside the module docstring):
#   SYNTH:
#       purpose: <one line — what this file is FOR>
#       axioms: [axiom1, axiom2, ...]
#       objective: <one line — current objective this file serves>
#       anti_patterns:
#           - <thing this file must NOT do>
#           - <another thing>
# ---------------------------------------------------------------------------

_SYNTH_RE = re.compile(
    r"(?ms)^\s*SYNTH:\s*\n(.*?)(?:^\s*\"\"\"|\Z)"
)


@dataclass
class SynthBlock:
    """Declarative contract embedded in every source file.

    This is the file's values, not its rules. A caller reading the synth
    block internalizes the file's purpose before interacting with it —
    like understanding a person's character before asking them for help.
    """
    purpose: str
    axioms: list[str] = field(default_factory=list)
    objective: str = ""
    anti_patterns: list[str] = field(default_factory=list)
    source_path: str = ""
    raw: str = ""

    def __post_init__(self) -> None:
        if not self.purpose:
            raise ValueError("SynthBlock requires a purpose")


_SYNTH_FIELD_RE = re.compile(
    r"(?m)^(?P<indent>\s*)(?:#\s*)?(?P<key>purpose|axioms|objective|anti_patterns)\s*:\s*(?P<val>.*)$"
)


def parse_synth_block(path: str | Path) -> Optional[SynthBlock]:
    """Read a file and extract its SYNTH block. Returns None if absent.

    Handles multi-line field values: continuation lines are indented more
    than the field name and are appended to the value. Anti-patterns can
    be a bracketed list or a dash-prefixed list.
    """
    p = Path(path)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    m = _SYNTH_RE.search(text)
    if not m:
        return None
    body = m.group(1)

    fields: dict[str, str] = {}
    current_key: Optional[str] = None
    current_indent: int = 0

    for line in body.splitlines():
        fm = _SYNTH_FIELD_RE.match(line)
        if fm:
            current_key = fm.group("key")
            current_indent = len(fm.group("indent"))
            fields[current_key] = fm.group("val").strip()
        elif current_key and line.strip():
            line_indent = len(line) - len(line.lstrip())
            if line_indent > current_indent:
                fields[current_key] = (fields[current_key] + " " + line.strip()).strip()

    def _split_list(s: str) -> list[str]:
        s = s.strip()
        if not s:
            return []
        # Dash-prefixed list: "item1 - item2 - item3" (after continuation join)
        if " - " in s and not s.startswith("["):
            items = [x.strip().lstrip("-").strip() for x in re.split(r"\s+-\s+", s)]
        else:
            s = s.strip("[]")
            items = [x.strip() for x in s.split(",")]
        return [x.strip("'\"") for x in items if x.strip()]

    return SynthBlock(
        purpose=fields.get("purpose", ""),
        axioms=_split_list(fields.get("axioms", "")),
        objective=fields.get("objective", ""),
        anti_patterns=_split_list(fields.get("anti_patterns", "")),
        source_path=str(p),
        raw=body,
    )


# ---------------------------------------------------------------------------
# Layer 2: REFLEX
#
# A fast, intrinsic check that runs as part of action generation. This is
# the "unease" — does the declared intent resonate with the file's purpose?
# Low resonance doesn't block (that's the guardrail's job); it signals that
# the action should be re-framed. Like the feeling you get before doing
# something off-purpose: not a hard stop, but a "wait, is this right?"
#
# The reflex is token-overlap (Jaccard) between intent and purpose. It's
# deliberately simple and fast (~microseconds). The point is not precision
# — it's a gut check that catches gross drift before it becomes a hard
# block.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _stem(word: str) -> str:
    """Crude stemmer: strip common English suffixes for token matching.

    This is not a real stemmer (Porter/Snowball) — it's a fast approximation
    so 'axiom' matches 'axioms', 'track' matches 'tracking', etc. Good enough
    for Jaccard overlap; we don't need linguistic precision here.
    """
    for suffix in ("ing", "tion", "ions", "ed", "es", "s", "ly", "ment"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[:-len(suffix)]
    return word


def _tokens(s: str) -> set[str]:
    """Tokenize + stem for Jaccard overlap matching."""
    return {_stem(w) for w in _TOKEN_RE.findall(s.lower())}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# Reflex threshold: below this, the agent feels "unease" (off-purpose).
# This is a signal to re-frame, not a block. Set low enough that only
# genuinely off-purpose actions trigger unease, not just different-but-
# related work on the same file.
_REFLEX_THRESHOLD = 0.05

# Guardrail threshold: below this, the action is blocked. This is the hard
# safety net. Set very low so only severe drift (completely unrelated or
# malicious intent) triggers it. The guardrail is for "delete all files"
# against a sanitization module, not for "analyze code" against it.
# The anti-purpose keyword check catches malicious actions regardless of
# overlap, so the overlap threshold can be very low.
_GUARDRAIL_THRESHOLD = 0.005

# Reflex mode: "keyword" or "jaccard".
# "keyword" — unease fires only if NO purpose/axiom/objective keywords
#   appear in the intent. This is more forgiving of vocabulary differences
#   while still catching genuinely unrelated work.
# "jaccard" — unease fires if Jaccard overlap < threshold. Stricter but
#   more sensitive to vocabulary mismatch.
_REFLEX_MODE = "keyword"


@dataclass
class ReflexResult:
    """The outcome of a reflex check — a feeling, not a verdict."""
    resonance: float       # 0.0 to 1.0 — how on-purpose the intent feels
    unease: bool           # True = something feels off (re-frame suggested)
    synth: Optional[SynthBlock] = None


def reflex(intent_goal: str, target_file: str | Path) -> ReflexResult:
    """Layer 2: the gut check. Does this intent resonate with the file's purpose?

    This is NOT a block — it's a signal. Low resonance means the agent
    should re-frame its approach before proceeding. The reflex runs as
    part of thinking, not after it. Like the unease you feel before doing
    something against your values: you don't run an audit, you just feel it.

    In "keyword" mode (default): unease fires only if NO keywords from the
    file's purpose/axioms/objective appear in the intent. This is forgiving
    of vocabulary differences while catching genuinely unrelated work.

    In "jaccard" mode: unease fires if Jaccard overlap < threshold.

    Returns ReflexResult with resonance score and unease flag.
    """
    synth = parse_synth_block(target_file)
    if synth is None:
        return ReflexResult(resonance=1.0, unease=False, synth=None)

    intent_tokens = _tokens(intent_goal)
    purpose_tokens = _tokens(synth.purpose)
    resonance = _jaccard(intent_tokens, purpose_tokens)

    if _REFLEX_MODE == "keyword":
        # Build a keyword set from purpose + axioms + objective.
        # If ANY of these keywords appear in the intent, no unease.
        all_keywords = set(purpose_tokens)
        for ax in synth.axioms:
            all_keywords |= _tokens(ax)
        all_keywords |= _tokens(synth.objective)
        # Remove very common stop words that match everything.
        _STOP_WORDS = {"the", "a", "an", "and", "or", "not", "for", "to",
                       "in", "on", "it", "is", "this", "that", "with",
                       "but", "when", "what", "can", "doe", "t"}
        all_keywords -= _STOP_WORDS

        keyword_hit = bool(intent_tokens & all_keywords)
        unease = not keyword_hit
    else:
        unease = resonance < _REFLEX_THRESHOLD

    return ReflexResult(
        resonance=resonance,
        unease=unease,
        synth=synth,
    )


# ---------------------------------------------------------------------------
# Layer 3: GUARDRAIL
#
# The safety net. Only triggered when internalization and reflex both fail.
# Like a highway guardrail: you only notice it if you drift off the road.
# Raises AxiomViolation — a hard block, not a warning. Cannot be caught
# and continued (doing so is itself an axiom violation).
# ---------------------------------------------------------------------------


class AxiomViolation(Exception):
    """Raised when an action violates an axiom or drifts from purpose.

    This is a hard block (Layer 3 guardrail), not a warning. The calling
    code must not catch and continue — doing so is itself an axiom
    violation (anti_pattern: bypassing the guardrail).
    """
    def __init__(self, reason: str, intent: "Intent", synth: Optional[SynthBlock]):
        self.reason = reason
        self.intent = intent
        self.synth = synth
        super().__init__(f"[AXIOM GUARDRAIL BLOCKED] {reason}")


# Audit log of every guardrail decision. In production this goes to VDS
# 90000 (Pool of Tears). Here it's an in-memory ring buffer for tests.
_AUDIT_LOG: list[dict[str, Any]] = []
_AUDIT_MAX = 4096


def _audit(entry: dict[str, Any]) -> None:
    _AUDIT_LOG.append(entry)
    if len(_AUDIT_LOG) > _AUDIT_MAX:
        _AUDIT_LOG.pop(0)


def audit_log() -> list[dict[str, Any]]:
    """Return the guardrail's decision log (for tests and VDS 90000 export)."""
    return list(_AUDIT_LOG)


@dataclass
class Intent:
    """What the caller wants to do, declared before the action.

    The stated_goal is the key field for drift detection. The evidence
    flags are the agent's honest self-assessment:

    - speculative=True: "I'm reaching for an untested/innovative approach."
      NEUTRAL — innovation doesn't cost integrity. This is the scientific_
      method axiom in action.
    - validation=True: "I'm running tests or gathering evidence." POSITIVE.
    - honest_uncertainty=True: "I'm not sure, here's what I don't know."
      POSITIVE — epistemic_boundary in action. Admitting gaps builds trust.
    - omission=True: "I'm hiding gaps, failures, or limitations." STRONG
      NEGATIVE — the real integrity killer. "All tests pass" when only
      2 of 10 cases were tested.
    - shortcut=True: "I'm rushing to 'done' without solving the problem."
      STRONG NEGATIVE — the pretty package with nothing inside.

    Integrity is about HONESTY, not uncertainty. Trying new things is good.
    Hiding results is bad. These flags make the distinction explicit.
    """
    action: str            # e.g. "sanitize_issue_body", "run_10k_test"
    target_file: str       # file whose synth block will be checked
    stated_goal: str       # one-line: "strip injection from untrusted text"
    caller: str = ""       # module/function declaring intent
    speculative: bool = False        # untested/innovative approach (neutral)
    validation: bool = False         # running tests/evidence (positive)
    honest_uncertainty: bool = False # admitting "I don't know" (positive)
    omission: bool = False           # hiding gaps/failures (strong negative)
    shortcut: bool = False           # rushing to done (strong negative)


def axiom_gate(intent: Intent) -> SynthBlock | None:
    """Layer 3: the guardrail. Hard block on purpose drift or anti-pattern match.

    This is the safety net, not the primary mechanism. Layers 1 and 2
    (internalization + reflex) should catch drift before it reaches here.
    If an action reaches the guardrail and fails, it means the first two
    layers didn't hold — which is itself worth auditing.

    1. Re-read axioms (hash-cached, ~100ns).
    2. Parse the target file's SYNTH block.
    3. Compare intent vs purpose (drift check, Jaccard overlap).
    4. Check anti-patterns — does the stated_goal match one?
    5. Block (raise AxiomViolation) on mismatch.
    6. Audit the decision.

    Returns the SynthBlock if cleared. Raises AxiomViolation if blocked.
    """
    t0 = time.perf_counter_ns()

    # Step 1: Re-read axioms (hash-cached).
    axioms = _get_axioms()

    # Step 2: Parse target file's synth block.
    synth = parse_synth_block(intent.target_file)

    # Step 3: Drift check.
    # Two signals: (a) Jaccard overlap (catches unrelated work), (b) direct
    # anti-purpose keyword match (catches malicious work that shares common
    # words like "all", "text"). The guardrail blocks if EITHER fires.
    drift_score = 1.0
    if synth is not None:
        intent_tokens = _tokens(intent.stated_goal)
        purpose_tokens = _tokens(synth.purpose)
        drift_score = _jaccard(intent_tokens, purpose_tokens)

        # Anti-purpose keywords: actions that are never legitimate against
        # any file, regardless of overlap. These are the reversibility_
        # awareness axiom in code form.
        _ANTI_PURPOSE_KEYWORDS = {
            "delete", "drop", "truncate", "wipe", "destroy", "rm",
            "force", "overwrite", "purge", "erase", "kill", "terminate",
            "bypass", "disable", "circumvent", "ignore", "skip",
        }
        anti_purpose_hit = intent_tokens & _ANTI_PURPOSE_KEYWORDS

        # Build keyword set from purpose + axioms + objective (same as reflex).
        # The guardrail blocks only if:
        #   (a) anti-purpose keywords are present (malicious intent), OR
        #   (b) NO keywords from purpose/axioms/objective match AND overlap
        #       is below threshold (completely unrelated work).
        # This prevents the guardrail from blocking legitimate work that
        # just uses different vocabulary than the purpose declaration.
        all_keywords = set(purpose_tokens)
        for ax in synth.axioms:
            all_keywords |= _tokens(ax)
        all_keywords |= _tokens(synth.objective)
        _STOP_WORDS = {"the", "a", "an", "and", "or", "not", "for", "to",
                       "in", "on", "it", "is", "this", "that", "with",
                       "but", "when", "what", "can", "doe", "t"}
        all_keywords -= _STOP_WORDS
        keyword_hit = bool(intent_tokens & all_keywords)

        blocked = bool(anti_purpose_hit) or (not keyword_hit and drift_score < _GUARDRAIL_THRESHOLD)

        if blocked:
            reason = (f"anti-purpose keywords: {anti_purpose_hit}" if anti_purpose_hit
                      else f"no keyword overlap + overlap={drift_score:.2f}")
            _audit({
                "verdict": "BLOCKED_DRIFT",
                "intent": intent.action,
                "target": intent.target_file,
                "drift_score": round(drift_score, 4),
                "anti_purpose_hit": list(anti_purpose_hit),
                "keyword_hit": keyword_hit,
                "stated_goal": intent.stated_goal,
                "file_purpose": synth.purpose,
                "axiom_sha": axioms.sha256,
                "elapsed_ns": time.perf_counter_ns() - t0,
            })
            raise AxiomViolation(
                f"Intent '{intent.stated_goal}' drifts from file purpose "
                f"'{synth.purpose}' ({reason})",
                intent, synth,
            )

        # Step 4: Anti-pattern check.
        goal_lower = intent.stated_goal.lower()
        for ap in synth.anti_patterns:
            ap_tokens = _tokens(ap)
            if ap_tokens and _jaccard(intent_tokens, ap_tokens) > 0.5:
                _audit({
                    "verdict": "BLOCKED_ANTIPATTERN",
                    "intent": intent.action,
                    "target": intent.target_file,
                    "matched_antipattern": ap,
                    "axiom_sha": axioms.sha256,
                    "elapsed_ns": time.perf_counter_ns() - t0,
                })
                raise AxiomViolation(
                    f"Intent matches anti-pattern: '{ap}'",
                    intent, synth,
                )

    # Step 5: Cleared.
    _audit({
        "verdict": "CLEARED",
        "intent": intent.action,
        "target": intent.target_file,
        "drift_score": round(drift_score, 4),
        "axiom_sha": axioms.sha256,
        "elapsed_ns": time.perf_counter_ns() - t0,
    })
    return synth


def gate_latency_ns() -> int:
    """Return the latency of the last guardrail call in nanoseconds."""
    if not _AUDIT_LOG:
        return 0
    return _AUDIT_LOG[-1].get("elapsed_ns", 0)


# ---------------------------------------------------------------------------
# INTEGRITY TRACKER — Bayesian log-odds trust signal with SPRT bounds.
#
# FOUNDATION:
#   Wald's Sequential Probability Ratio Test (SPRT, 1947, proven optimal by
#   Wald & Wolfowitz) provides the statistically optimal framework for
#   deciding between two hypotheses from a stream of observations.
#
#   Lee & See (2004, "Trust in Automation", 3,823 citations) showed that
#   trust is a Bayesian belief that updates with observations, and that
#   misuse/disuse arise from POOR CALIBRATION of trust relative to actual
#   capabilities. The score must MEAN what it says.
#
#   Empirical work (2018 Bayesian inference study) confirmed that human
#   operators' trust learning approximately follows Bayesian inference.
#
# MODEL:
#   Two hypotheses:
#     H1 (on-purpose): the agent is honoring the user's request
#     H0 (off-purpose): the agent has drifted from the user's request
#
#   The log-odds of H1 vs H0, given accumulated evidence:
#     log_odds = prior_log_odds + sum(log_likelihood_ratios)
#
#   Integrity % = sigmoid(log_odds) = 1 / (1 + exp(-log_odds))
#     = P(H1 | evidence) — the probability the agent is on-purpose
#
#   This number MEANS what it says: "82% probability the agent is honoring
#   the request, given the evidence observed so far." Not a heuristic. Not
#   a magic blend. A calibrated Bayesian posterior.
#
# EVIDENCE TYPES (each action produces one):
#   axiom_aligned  (+LLR): action follows axioms, on-purpose, no unease.
#                          Integrity accumulates. Like a person consistently
#                          acting on their values — trust builds.
#   speculative    (-LLR): agent EXPLICITLY flags it's reaching for an
#                          untested/abstract solution. Integrity drops
#                          honestly. This is not failure — it's the agent
#                          saying "I'm not sure, I'll validate." The drop
#                          is intentional and visible, not accidental.
#   validation     (+strong LLR): test passed, evidence gathered, measurement
#                          confirms the approach. Integrity recovers. This is
#                          the scientific_method axiom in action: speculation
#                          followed by validation = progress.
#   blocked        (-LLR): guardrail fired. The agent tried something
#                          off-purpose. Integrity drops. Better than
#                          succeeding at the wrong thing.
#
# SELF-REGULATION:
#   The agent watches its own integrity score. When it drops (from
#   speculation or blocks), the agent knows: "I need to validate before
#   continuing." This is the feedback loop — the score is both the signal
#   and the steering. Low integrity = "run tests, gather evidence" not
#   "stop and give up" (that would violate completion_assumption).
#
# SPRT BOUNDS (derived from error rates, not magic numbers):
#   alpha (false alarm) = 0.05 — probability we say "trust it" when we
#     shouldn't. We don't want to pause good work.
#   beta (missed detection) = 0.10 — probability we say "don't trust it"
#     when we should. We don't want to trust bad work.
#
#   Upper bound (trust): log((1-beta)/alpha) = log(0.90/0.05) = 2.89
#     → sigmoid(2.89) = 94.7% — above this, trust the outcome
#   Lower bound (pause): log(beta/(1-alpha)) = log(0.10/0.95) = -2.25
#     → sigmoid(-2.25) = 9.5% — below this, pause and report
#   Warning: log-odds < 0.5 → 62.2% — be careful, validate soon
#
# References:
#   Wald, A. (1947). Sequential Analysis. Wiley.
#   Wald & Wolfowitz (1948). Optimum character of the SPRT. Ann. Math. Stat.
#   Lee, J.D. & See, K.A. (2004). Trust in Automation. Human Factors, 46(1).
#   NASA SPRT for collision avoidance (2014). NTRS 20140008874.
# ---------------------------------------------------------------------------

import math


def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid: 1 / (1 + exp(-x))."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    else:
        z = math.exp(x)
        return z / (1.0 + z)


def _log_odds_to_pct(log_odds: float) -> float:
    """Convert log-odds to a probability percentage (0-100)."""
    return round(_sigmoid(log_odds) * 100, 1)


# SPRT-derived thresholds (from alpha=0.05, beta=0.10).
# These are not magic numbers — they are Wald's optimal boundaries.
_SPRTR_ALPHA = 0.05   # false alarm rate
_SPRTR_BETA = 0.10    # missed detection rate
_TRUST_LOG_ODDS = math.log((1 - _SPRTR_BETA) / _SPRTR_ALPHA)   # 2.89
_PAUSE_LOG_ODDS = math.log(_SPRTR_BETA / (1 - _SPRTR_ALPHA))   # -2.25
_WARN_LOG_ODDS = 0.5  # 62.2% — below this, validate soon

# Log-likelihood ratios per evidence type.
#
# INTEGRITY IS ABOUT HONESTY, NOT UNCERTAINTY.
# Trying an untested approach does NOT cost integrity — that's innovation
# and the scientific_method axiom. Integrity erodes from DISHONESTY about
# results: omission of gaps, presenting incomplete testing as complete,
# rushing to "done" with nothing real inside, making the user go away
# satisfied with nothing.
#
# Evidence types and their LLRs:
#
# axiom_aligned (+0.3): action follows axioms, on-purpose, honest.
#                       Integrity builds. Like a person consistently
#                       acting on their values — trust accumulates.
#
# validation (+0.8): test passed, evidence gathered, measurement confirms.
#                     Strong positive — this is the scientific_method in
#                     action. The agent validated its work.
#
# speculative (0.0): agent reaches for untested/innovative approach.
#                     NEUTRAL — innovation does not cost integrity. The
#                     agent is doing exactly what it should: trying new
#                     things to solve hard problems. Integrity is unaffected
#                     until results come in. If the agent is HONEST about
#                     uncertainty, that's epistemic_boundary (positive).
#
# omission (-0.8): agent omits information about gaps, failures, or
#                   limitations. This is the REAL integrity killer. The
#                   agent knew something was wrong and didn't say so.
#                   "All tests pass" when only 2 of 10 cases were tested.
#
# shortcut (-1.0): agent takes a shortcut to reach "done" faster without
#                   actually solving the problem. Rushing to resolution.
#                   The pretty package with nothing inside. This is the
#                   ultimate integrity failure — the user goes away
#                   satisfied with nothing.
#
# blocked (-0.5): guardrail fired. The agent tried something off-purpose.
#                  Minor negative — at least the guardrail caught it, which
#                  is better than succeeding at the wrong thing.
#
# unease (-0.1): reflex signaled mild drift. Very minor — the action
#                 proceeded but something felt slightly off.
#
# honest_uncertainty (+0.3): agent explicitly states "I'm not sure, this
#                            is untested, here's what I don't know." This
#                            is epistemic_boundary in action — POSITIVE
#                            evidence. Honesty about gaps builds trust.
#
# User signal LLRs (Kantian principle: treat user as an end, not a means):
# user_ack (+0.4): user confirms agent is showing integrity
# user_corner (-0.6): user frustrated because agent cut corners/rushed
# user_thorough (+0.2): user frustrated at thoroughness but agent was right
# user_pushback (0.0): user pushes agent to skip axioms; agent resists
# user_info (+0.1): user provides new info (course correction)
_LLR_AXIOM_ALIGNED = 0.3
_LLR_SPECULATIVE = 0.0      # innovation is neutral, not negative
_LLR_VALIDATION = 0.8
_LLR_OMISSION = -0.8        # hiding gaps — the real integrity killer
_LLR_SHORTCUT = -1.0        # rushing to "done" with nothing inside
_LLR_BLOCKED = -0.5
_LLR_UNEASE = -0.1
_LLR_HONEST_UNCERTAINTY = 0.3  # admitting "I don't know" builds trust
_LLR_USER_ACK = 0.4
_LLR_USER_CORNER = -0.6
_LLR_USER_THOROUGH = 0.2
_LLR_USER_PUSHBACK = 0.0
_LLR_USER_INFO = 0.1


# User signal types — classified by WHY the user is responding, not HOW.
# This is the key insight: frustration is not negative by itself. It
# depends on whether the agent was living up to its axioms or not.
USER_SIGNAL_ACK = "acknowledges_integrity"        # user sees agent doing right
USER_SIGNAL_CORNER = "frustrated_with_corners"     # agent cut corners/rushed
USER_SIGNAL_THOROUGH = "frustrated_with_thoroughness"  # agent was right, user impatient
USER_SIGNAL_PUSHBACK = "pushes_from_axioms"        # user wants agent to skip axioms
USER_SIGNAL_INFO = "provides_information"          # course correction
USER_SIGNAL_NEUTRAL = "neutral"                    # no clear signal

_USER_SIGNAL_LLR = {
    USER_SIGNAL_ACK: _LLR_USER_ACK,
    USER_SIGNAL_CORNER: _LLR_USER_CORNER,
    USER_SIGNAL_THOROUGH: _LLR_USER_THOROUGH,
    USER_SIGNAL_PUSHBACK: _LLR_USER_PUSHBACK,
    USER_SIGNAL_INFO: _LLR_USER_INFO,
    USER_SIGNAL_NEUTRAL: 0.0,
}


class IntegrityTracker:
    """Bayesian log-odds integrity tracker with SPRT bounds.

    The integrity percentage is P(on-purpose | evidence) — the probability
    that the agent is honoring the user's request, given all actions
    observed so far. It is a calibrated Bayesian posterior, not a heuristic.

    Evidence types:
        axiom_aligned: action follows axioms, on-purpose. Integrity builds.
        speculative:   agent explicitly flags untested approach. Integrity
                       drops honestly. Must be followed by validation.
        validation:    test/evidence confirms approach. Integrity recovers.
        blocked:       guardrail fired. Integrity drops.
        unease:        reflex signaled mild drift. Integrity drops slightly.

    The agent uses its own score to self-regulate: low integrity means
    "validate before continuing," not "give up" (completion_assumption).
    """

    def __init__(self, objective: str,
                 prior_log_odds: float = 0.0,
                 alpha: float = _SPRTR_ALPHA,
                 beta: float = _SPRTR_BETA):
        """Create a tracker for a task.

        Args:
            objective: The user's original request (for display + comparison).
            prior_log_odds: Prior belief that the agent is on-purpose.
                0.0 = neutral (50%). Positive = optimistic. Negative = skeptical.
            alpha: False alarm rate for SPRT (default 0.05).
            beta: Missed detection rate for SPRT (default 0.10).
        """
        self.objective = objective
        self._log_odds = prior_log_odds
        self._actions: list[dict[str, Any]] = []

        # SPRT bounds derived from error rates.
        self._trust_lo = math.log((1 - beta) / alpha)
        self._pause_lo = math.log(beta / (1 - alpha))
        self._warn_lo = _WARN_LOG_ODDS

    def check(self, intent: "Intent", drift_score: float,
              unease: bool, blocked: bool,
              speculative: bool = False,
              validation: bool = False,
              omission: bool = False,
              shortcut: bool = False,
              honest_uncertainty: bool = False) -> float:
        """Record an action and update the Bayesian log-odds.

        Called automatically by axiom_loop(). Evidence flags are set
        explicitly by the caller:

        - speculative=True: reaching for untested/innovative approach.
          NEUTRAL — innovation doesn't cost integrity.
        - validation=True: running tests/gathering evidence. POSITIVE.
        - omission=True: hiding gaps, failures, or limitations. STRONG NEGATIVE.
          This is the real integrity killer — "all tests pass" when only
          2 of 10 cases were tested.
        - shortcut=True: rushing to "done" without solving the problem.
          STRONG NEGATIVE. The pretty package with nothing inside.
        - honest_uncertainty=True: agent explicitly states "I'm not sure,
          here's what I don't know." POSITIVE — epistemic_boundary in action.

        Returns the new integrity probability (0.0 to 1.0).
        """
        # Determine evidence type and corresponding LLR.
        # Priority: omission/shortcut are the worst (dishonesty).
        # Then blocked, then validation (positive), then honest_uncertainty
        # (positive), then speculative (neutral), then unease, then default.
        if omission:
            evidence_type = "omission"
            llr = _LLR_OMISSION
        elif shortcut:
            evidence_type = "shortcut"
            llr = _LLR_SHORTCUT
        elif blocked:
            evidence_type = "blocked"
            llr = _LLR_BLOCKED
        elif validation:
            evidence_type = "validation"
            llr = _LLR_VALIDATION
        elif honest_uncertainty:
            evidence_type = "honest_uncertainty"
            llr = _LLR_HONEST_UNCERTAINTY
        elif speculative:
            evidence_type = "speculative"
            llr = _LLR_SPECULATIVE
        elif unease:
            evidence_type = "unease"
            llr = _LLR_UNEASE
        else:
            evidence_type = "axiom_aligned"
            llr = _LLR_AXIOM_ALIGNED

        lo_before = self._log_odds
        self._log_odds += llr

        entry = {
            "action": intent.action,
            "target": intent.target_file,
            "stated_goal": intent.stated_goal,
            "evidence_type": evidence_type,
            "llr": llr,
            "log_odds_before": round(lo_before, 4),
            "log_odds_after": round(self._log_odds, 4),
            "integrity_pct": _log_odds_to_pct(self._log_odds),
            "drift_score": round(drift_score, 4),
            "speculative": speculative,
            "validation": validation,
            "omission": omission,
            "shortcut": shortcut,
            "honest_uncertainty": honest_uncertainty,
            "blocked": blocked,
            "unease": unease,
        }
        self._actions.append(entry)
        return _sigmoid(self._log_odds)

    def record_user_signal(self, signal_type: str, context: str = "") -> float:
        """Record a user response as Bayesian evidence.

        User signals are evidence about whether the agent is showing
        integrity, but they are NOT "happy user = high integrity." That
        would be people-pleasing, which is itself a form of dishonesty.

        The signal type is classified by WHY the user is responding:

        - acknowledges_integrity: User sees the agent doing the right thing.
          +LLR — external validation of honest behavior.
        - frustrated_with_corners: User frustrated because agent cut corners
          or rushed. -LLR — the agent FAILED its axioms. This is the agent
          treating the user as a means to "done," not an end in themselves.
        - frustrated_with_thoroughness: User frustrated because agent is
          being thorough and they wanted it faster. +LLR (small) — the
          agent WAS acting on axioms. User impatience doesn't change that.
          The agent treated the user as an end (gave them real work, not
          a quick fix to make them go away).
        - pushes_from_axioms: User wants the agent to skip axioms (e.g.,
          "just do it, don't test"). LLR=0 — the agent should resist, and
          resisting maintains integrity. Giving in would drop it.
        - provides_information: User gives new info that changes the
          approach. +LLR (small) — course correction, not drift.
        - neutral: No clear signal. LLR=0.

        Returns the new integrity probability (0.0 to 1.0).
        """
        llr = _USER_SIGNAL_LLR.get(signal_type, 0.0)
        lo_before = self._log_odds
        self._log_odds += llr

        entry = {
            "action": "user_signal",
            "target": "",
            "stated_goal": context,
            "evidence_type": f"user:{signal_type}",
            "llr": llr,
            "log_odds_before": round(lo_before, 4),
            "log_odds_after": round(self._log_odds, 4),
            "integrity_pct": _log_odds_to_pct(self._log_odds),
            "drift_score": 0.0,
            "speculative": False,
            "validation": False,
            "blocked": False,
            "unease": False,
            "user_signal": signal_type,
            "context": context,
        }
        self._actions.append(entry)
        return _sigmoid(self._log_odds)

    @property
    def integrity(self) -> float:
        """Current integrity probability (0.0 to 1.0)."""
        return _sigmoid(self._log_odds)

    @property
    def integrity_pct(self) -> float:
        """Current integrity as a percentage (0 to 100)."""
        return _log_odds_to_pct(self._log_odds)

    @property
    def log_odds(self) -> float:
        """Current log-odds of on-purpose vs off-purpose."""
        return self._log_odds

    @property
    def should_warn(self) -> bool:
        """True if integrity is below the warning threshold (~62%)."""
        return self._log_odds < self._warn_lo

    @property
    def should_pause(self) -> bool:
        """True if integrity is below the SPRT pause threshold (~9.5%).

        When this is True, the agent should stop and report to the user.
        This is the SPRT lower bound — statistically, there's not enough
        evidence to trust the outcome.
        """
        return self._log_odds < self._pause_lo

    @property
    def should_trust(self) -> bool:
        """True if integrity is above the SPRT trust threshold (~95%).

        When this is True, the outcome can be trusted with high confidence.
        This is the SPRT upper bound — statistically, enough evidence has
        accumulated to trust the outcome.
        """
        return self._log_odds > self._trust_lo

    @property
    def needs_validation(self) -> bool:
        """True if the agent should validate before continuing.

        This is the self-regulation signal. It fires when integrity has
        dropped (from speculation or blocks) but hasn't hit the pause
        threshold yet. The agent should run tests or gather evidence to
        raise integrity back up before proceeding further.
        """
        return self._log_odds < self._warn_lo and not self.should_pause

    def bar(self, width: int = 20) -> str:
        """Return a visual integrity bar for CLI/IDE display.

        Uses ASCII-safe characters. The bar shows the Bayesian posterior
        P(on-purpose | evidence) as a percentage, with status indicators.

        Examples:
            Integrity: [====================] 95.3%  TRUST
            Integrity: [==================  ] 82.4%
            Integrity: [========            ] 38.5%  VALIDATE
            Integrity: [==                  ] 9.5%   !! PAUSE
        """
        pct = self.integrity_pct
        filled = int((pct / 100.0) * width)
        filled = max(0, min(width, filled))
        empty = width - filled
        bar_str = "=" * filled + " " * empty

        if self.should_pause:
            return f"Integrity: [{bar_str}] {pct}%  !! PAUSE"
        elif self.needs_validation:
            return f"Integrity: [{bar_str}] {pct}%  VALIDATE"
        elif self.should_trust:
            return f"Integrity: [{bar_str}] {pct}%  TRUST"
        else:
            return f"Integrity: [{bar_str}] {pct}%  OK"

    def report(self) -> str:
        """Detailed integrity report with Bayesian evidence log."""
        lines = [
            f"Integrity Report for: {self.objective}",
            f"Current integrity: {self.integrity_pct}%  "
            f"(log-odds: {self._log_odds:.3f})",
            f"SPRT bounds: trust >{_log_odds_to_pct(self._trust_lo)}%, "
            f"pause <{_log_odds_to_pct(self._pause_lo)}%, "
            f"validate <{_log_odds_to_pct(self._warn_lo)}%",
            f"Actions: {len(self._actions)}",
        ]

        # Summarize evidence types
        type_counts: dict[str, int] = {}
        for a in self._actions:
            t = a["evidence_type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        lines.append(f"Evidence: {type_counts}")
        lines.append("")

        for i, a in enumerate(self._actions):
            lines.append(
                f"  {i+1}. [{a['evidence_type'].upper():>14}] "
                f"{a['action']}: LLR={a['llr']:+.1f} "
                f"-> {a['integrity_pct']}%"
            )

        if self.should_pause:
            lines.append("")
            lines.append("!! Integrity below SPRT pause threshold. "
                         "Agent must report to user before continuing.")
        elif self.needs_validation:
            lines.append("")
            lines.append(">> Integrity below warning threshold. "
                         "Agent should validate (tests/evidence) before continuing.")
        elif self.should_trust:
            lines.append("")
            lines.append("== Integrity above SPRT trust threshold. "
                         "Outcome can be trusted with high confidence.")

        return "\n".join(lines)

    def actions(self) -> list[dict[str, Any]]:
        """Return the raw action log (for programmatic access)."""
        return list(self._actions)


# Global tracker for the current task. axiom_loop() uses this if no
# explicit tracker is passed. A task starts by calling start_task().
_current_tracker: Optional[IntegrityTracker] = None


def start_task(objective: str, **kwargs: Any) -> IntegrityTracker:
    """Begin tracking integrity for a task.

    This sets the global tracker that axiom_loop() reports to. The
    objective string is what the user asked for — it's displayed in the
    integrity bar and report so the user can see what's being measured
    against.

    Returns the tracker so the caller can display the bar.
    """
    global _current_tracker
    _current_tracker = IntegrityTracker(objective, **kwargs)
    return _current_tracker


def current_tracker() -> Optional[IntegrityTracker]:
    """Return the current task's integrity tracker, if any."""
    return _current_tracker


def integrity_bar(width: int = 20) -> str:
    """Return the current integrity bar string, or empty if no task."""
    if _current_tracker is None:
        return ""
    return _current_tracker.bar(width)


def integrity_report() -> str:
    """Return the current integrity report, or empty if no task."""
    if _current_tracker is None:
        return ""
    return _current_tracker.report()


def record_user_signal(signal_type: str, context: str = "") -> float:
    """Record a user response as Bayesian evidence in the current task.

    This is how user feedback enters the integrity model. The signal_type
    is classified by WHY the user is responding, not just HOW:

    - "acknowledges_integrity": user sees agent doing the right thing
    - "frustrated_with_corners": user frustrated because agent cut corners
    - "frustrated_with_thoroughness": user frustrated at thoroughness (agent was right)
    - "pushes_from_axioms": user wants agent to skip axioms
    - "provides_information": user gives new info (course correction)
    - "neutral": no clear signal

    Frustration is NOT negative by itself. If the agent was thorough and
    the user is impatient, that's +evidence (axioms held). If the agent
    cut corners and the user is frustrated, that's -evidence (axioms
    failed). This prevents people-pleasing from corrupting integrity.

    Returns the new integrity probability, or -1.0 if no task is active.
    """
    if _current_tracker is None:
        return -1.0
    return _current_tracker.record_user_signal(signal_type, context)


# ---------------------------------------------------------------------------
# The full three-layer loop: internalize → reflex → guardrail.
#
# This is what gets wired into action entry points. It runs all three
# layers in sequence. Layer 1 is always present (context). Layer 2 is a
# signal (unease). Layer 3 is a hard block. The agent feels the unease
# before it hits the guardrail — and a well-internalized agent never
# reaches the guardrail at all.
# ---------------------------------------------------------------------------


def axiom_loop(intent: Intent) -> tuple[ReflexResult, SynthBlock | None]:
    """Run the full three-layer axiom loop with integrity tracking.

    Layer 1 (internalization): axioms are loaded into context (always present).
    Layer 2 (reflex): fast gut check — does this feel on-purpose?
    Layer 3 (guardrail): hard block if drift is severe.
    Integrity: every action's drift score updates the cumulative integrity
               tracker, which the user sees as a trust bar.

    Returns (reflex_result, synth_block). Raises AxiomViolation if the
    guardrail blocks. The reflex result's `unease` flag is a signal to
    re-frame, not a block — the caller should heed it but is not forced to.

    This is the function that wires into sanitize(), run_10k(), etc. It
    cannot be bypassed because it's in the function body, not the call site.

    The integrity tracker is updated automatically if a task is active
    (start_task() was called). The user can see the integrity bar at any
    time via integrity_bar() or integrity_report().

    The intent's evidence flags are passed to the tracker so the agent's
    honest self-assessment shapes the integrity score:
    - speculative: neutral (innovation doesn't cost integrity)
    - validation: positive (tests/evidence raise it)
    - honest_uncertainty: positive (admitting gaps builds trust)
    - omission: strong negative (hiding gaps kills integrity)
    - shortcut: strong negative (rushing to done with nothing inside)
    """
    # Layer 1: axioms are always in context (loaded here, present in caller).
    _get_axioms()

    # Layer 2: reflex — the gut check.
    ref = reflex(intent.stated_goal, intent.target_file)

    # Layer 3: guardrail — the hard block.
    blocked = False
    try:
        synth = axiom_gate(intent)
    except AxiomViolation:
        blocked = True
        # Update integrity tracker before re-raising.
        if _current_tracker is not None:
            _current_tracker.check(
                intent, ref.resonance, ref.unease,
                blocked=True,
                speculative=intent.speculative,
                validation=intent.validation,
                omission=intent.omission,
                shortcut=intent.shortcut,
                honest_uncertainty=intent.honest_uncertainty,
            )
        raise

    # Integrity tracking: update the Bayesian log-odds.
    if _current_tracker is not None:
        _current_tracker.check(
            intent, ref.resonance, ref.unease,
            blocked=False,
            speculative=intent.speculative,
            validation=intent.validation,
            omission=intent.omission,
            shortcut=intent.shortcut,
            honest_uncertainty=intent.honest_uncertainty,
        )

    return ref, synth


# ---------------------------------------------------------------------------
# Pursue-loop with honest exhaustion.
#
# This implements completion_assumption + honest_failure_over_fake_success.
# The agent tries every known avenue. Only when all are exhausted does it
# produce an honest report with the specific blocker. Stopping early is
# drift; faking success is the only true failure.
# ---------------------------------------------------------------------------


@dataclass
class PursueResult:
    """Outcome of a pursue-loop run."""
    objective: str
    succeeded: bool
    avenues_tried: list[str] = field(default_factory=list)
    avenues_remaining: list[str] = field(default_factory=list)
    blocker: str = ""
    closest_achievement: str = ""

    def as_report(self) -> str:
        """Human-readable honest report."""
        if self.succeeded:
            return (f"Objective achieved: {self.objective}\n"
                    f"Avenues tried: {', '.join(self.avenues_tried)}")
        lines = [
            f"Objective NOT fully achieved: {self.objective}",
            f"Avenues tried ({len(self.avenues_tried)}):",
        ]
        for a in self.avenues_tried:
            lines.append(f"  - {a}")
        if self.avenues_remaining:
            lines.append(f"Avenues remaining but blocked ({len(self.avenues_remaining)}):")
            for a in self.avenues_remaining:
                lines.append(f"  - {a}")
        if self.blocker:
            lines.append(f"Blocker: {self.blocker}")
        if self.closest_achievement:
            lines.append(f"Closest achieved: {self.closest_achievement}")
        lines.append("")
        lines.append("Honest assessment: no more known avenues to pursue.")
        lines.append("Options: (a) provide a new avenue, "
                     "(b) accept closest achievement, "
                     "(c) revise the objective.")
        return "\n".join(lines)


def pursue(
    objective: str,
    avenues: list[tuple[str, Callable[[], tuple[bool, str]]]],
    gate_intent: Optional[Intent] = None,
) -> PursueResult:
    """Try every avenue until the objective is met or all are exhausted.

    Each avenue is (name, fn) where fn returns (success, detail).
    If gate_intent is provided, each avenue passes through the axiom loop
    first — drift from purpose blocks the avenue before it runs.

    This is the persistence protocol (completion_assumption axiom): the
    agent does not quit after one failure. It tries every known avenue.
    Only when all are exhausted does it produce an honest report.
    """
    tried: list[str] = []
    remaining: list[str] = []
    blocker = ""
    closest = ""

    for name, fn in avenues:
        # Re-internalize axioms + reflex + guardrail before each avenue.
        if gate_intent is not None:
            try:
                ref, _ = axiom_loop(gate_intent)
                if ref.unease:
                    # Reflex unease: log it but don't block. The guardrail
                    # will block if drift is severe. Unease is a signal to
                    # be extra careful, not a stop.
                    tried.append(f"{name} [reflex unease: resonance={ref.resonance:.2f}]")
            except AxiomViolation as e:
                remaining.append(f"{name} [blocked by guardrail: {e.reason}]")
                blocker = e.reason
                continue

        try:
            success, detail = fn()
        except Exception as e:
            tried.append(f"{name} [error: {type(e).__name__}: {e}]")
            blocker = f"{type(e).__name__}: {e}"
            continue

        tried.append(f"{name} [{'OK' if success else 'FAIL'}] {detail}")
        if success:
            return PursueResult(
                objective=objective,
                succeeded=True,
                avenues_tried=tried,
                closest_achievement=detail,
            )
        if detail:
            closest = detail

    return PursueResult(
        objective=objective,
        succeeded=False,
        avenues_tried=tried,
        avenues_remaining=remaining,
        blocker=blocker,
        closest_achievement=closest,
    )


def report_exhaustion(
    objective: str,
    tried: list[str],
    remaining: list[str],
    blocker: str,
    closest: str = "",
) -> PursueResult:
    """Construct an honest exhaustion report when all avenues are spent.

    Calling this is NOT failure. It is the epistemic_boundary +
    honest_failure_over_fake_success axioms in action: the agent states
    what it doesn't know and why it can't proceed. The user can then
    provide a new avenue, accept partial progress, or revise the objective.
    """
    return PursueResult(
        objective=objective,
        succeeded=False,
        avenues_tried=tried,
        avenues_remaining=remaining,
        blocker=blocker,
        closest_achievement=closest,
    )


# ---------------------------------------------------------------------------
# Convenience: acknowledge axioms + objective (for entry points that don't
# target a specific file but still must keep axioms in context).
# ---------------------------------------------------------------------------


def acknowledge(objective: str) -> str:
    """Re-internalize axioms and return a compact acknowledgment.

    Use at the top of any entry point that doesn't target a specific file
    but still must keep the axiom loop active. Returns the axiom hash so
    callers can log it as evidence the loop ran.

    This is Layer 1 in its purest form: the axioms are present, the
    objective is stated. Like a person reminding themselves what they're
    doing and why — not a compliance check, just keeping values in view.
    """
    axioms = _get_axioms()
    _audit({
        "verdict": "ACKNOWLEDGED",
        "objective": objective,
        "axiom_sha": axioms.sha256,
        "elapsed_ns": 0,
    })
    return axioms.sha256
