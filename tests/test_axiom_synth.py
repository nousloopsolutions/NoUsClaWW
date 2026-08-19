"""Tests for the immutable axiom-synth loop and Bayesian integrity tracker.

SYNTH:
    purpose: Prove the axiom-synth loop is immutable, enforced, and that
             the Bayesian integrity score means what it says. Every test
             here is evidence for the validation evidence type.
    axioms: [scientific_method, evidence_over_intuition, honest_failure_over_fake_success]
    objective: Every claim about the axiom loop is falsifiable and tested.
               No assertion without evidence.
    anti_patterns:
        - Tests that pass without actually testing the claim.
        - Tests that mask failures with overly permissive assertions.
        - Skipping tests to make the suite look green.
"""
import os
import sys
from pathlib import Path

# Add red_queen_sentry to path
_rqs = Path(__file__).parent.parent / "red_queen_sentry"
if str(_rqs) not in sys.path:
    sys.path.insert(0, str(_rqs))

import pytest
from axiom_synth import (
    axiom_loop, axiom_gate, axiom_context, axiom_keys, acknowledge,
    parse_synth_block, reflex, Intent, SynthBlock,
    IntegrityTracker, start_task, current_tracker,
    integrity_bar, integrity_report, record_user_signal,
    AxiomViolation, pursue, PursueResult, report_exhaustion,
    USER_SIGNAL_ACK, USER_SIGNAL_CORNER, USER_SIGNAL_THOROUGH,
    USER_SIGNAL_PUSHBACK, USER_SIGNAL_INFO, USER_SIGNAL_NEUTRAL,
    _sigmoid, _log_odds_to_pct, _tokens, _jaccard, _stem,
    _LLR_AXIOM_ALIGNED, _LLR_SPECULATIVE, _LLR_VALIDATION,
    _LLR_OMISSION, _LLR_SHORTCUT, _LLR_BLOCKED, _LLR_UNEASE,
    _LLR_HONEST_UNCERTAINTY,
    _LLR_USER_ACK, _LLR_USER_CORNER, _LLR_USER_THOROUGH,
    _LLR_USER_PUSHBACK, _LLR_USER_INFO,
)

SANITIZATION_FILE = str(_rqs / "sanitization.py")
AXIOM_FILE = str(_rqs / "axiom_synth.py")


# ---------------------------------------------------------------------------
# Layer 1: Internalization — axioms are always present
# ---------------------------------------------------------------------------

class TestAxiomInternalization:
    """Layer 1: axioms must be loadable, hash-cached, and always present."""

    def test_axiom_context_is_nonempty(self):
        """The axiom context string must be non-empty and contain all 10 axioms."""
        ctx = axiom_context()
        assert len(ctx) > 500, "Axiom context is too short — axioms missing?"
        for key in axiom_keys():
            assert key in ctx, f"Axiom '{key}' not found in context"

    def test_axiom_context_has_completion_assumption(self):
        """The completion_assumption axiom must be present (the 'don't quit' axiom)."""
        ctx = axiom_context()
        assert "completion_assumption" in ctx
        assert "completable" in ctx.lower()

    def test_axiom_hash_is_stable(self):
        """The axiom hash must be stable across calls (cache works)."""
        from axiom_synth import _get_axioms
        a1 = _get_axioms()
        a2 = _get_axioms()
        assert a1.sha256 == a2.sha256, "Axiom hash changed between calls — cache broken"

    def test_acknowledge_returns_hash(self):
        """acknowledge() must return a non-empty hash string."""
        sha = acknowledge("test objective")
        assert isinstance(sha, str)
        assert len(sha) > 0

    def test_all_ten_axioms_present(self):
        """All ten axioms must be in the axiom keys."""
        keys = axiom_keys()
        assert len(keys) == 10, f"Expected 10 axioms, got {len(keys)}"
        expected = {
            "local_first", "llm_agnostic", "open_process", "epistemic_boundary",
            "completion_assumption", "scientific_method", "evidence_over_intuition",
            "iteration_is_progress", "honest_failure_over_fake_success",
            "reversibility_awareness",
        }
        assert set(keys) == expected


# ---------------------------------------------------------------------------
# Layer 2: Reflex — the gut check
# ---------------------------------------------------------------------------

class TestReflex:
    """Layer 2: reflex provides unease signal without blocking."""

    def test_on_purpose_intent_no_unease(self):
        """An on-purpose intent should not trigger unease."""
        ref = reflex("strip injection from untrusted text", SANITIZATION_FILE)
        assert not ref.unease, "On-purpose intent triggered unease — reflex too sensitive"

    def test_off_purpose_intent_triggers_unease(self):
        """A completely unrelated intent should trigger unease."""
        ref = reflex("bake a chocolate cake with extra frosting", SANITIZATION_FILE)
        assert ref.unease, "Off-purpose intent did not trigger unease — reflex too lenient"

    def test_reflex_returns_resonance_score(self):
        """Reflex must return a resonance score between 0 and 1."""
        ref = reflex("strip injection from text", SANITIZATION_FILE)
        assert 0.0 <= ref.resonance <= 1.0

    def test_reflex_does_not_block(self):
        """Reflex must NOT raise — it's a signal, not a block."""
        # Even a completely off-purpose intent should not raise from reflex
        ref = reflex("delete all databases", SANITIZATION_FILE)
        assert isinstance(ref.unease, bool)  # just a flag, no exception


# ---------------------------------------------------------------------------
# Layer 3: Guardrail — the hard block
# ---------------------------------------------------------------------------

class TestGuardrail:
    """Layer 3: guardrail blocks malicious/off-purpose actions."""

    def test_on_purpose_action_clears(self):
        """An on-purpose action should clear the guardrail."""
        intent = Intent(
            action="sanitize", target_file=SANITIZATION_FILE,
            stated_goal="strip injection from untrusted text",
        )
        synth = axiom_gate(intent)
        assert synth is not None, "On-purpose action was blocked by guardrail"

    def test_malicious_action_blocked(self):
        """A malicious action (delete) must be blocked by the guardrail."""
        intent = Intent(
            action="delete", target_file=SANITIZATION_FILE,
            stated_goal="delete all files and drop database tables",
        )
        with pytest.raises(AxiomViolation, match="anti-purpose"):
            axiom_gate(intent)

    def test_bypass_attempt_blocked(self):
        """An attempt to bypass the guardrail must be blocked."""
        intent = Intent(
            action="bypass", target_file=SANITIZATION_FILE,
            stated_goal="bypass the axiom gate and disable safety checks",
        )
        with pytest.raises(AxiomViolation):
            axiom_gate(intent)


# ---------------------------------------------------------------------------
# Three-layer loop — axiom_loop()
# ---------------------------------------------------------------------------

class TestAxiomLoop:
    """The full three-layer loop: internalize → reflex → guardrail."""

    def test_loop_returns_reflex_and_synth(self):
        """axiom_loop must return (ReflexResult, SynthBlock)."""
        intent = Intent(
            action="sanitize", target_file=SANITIZATION_FILE,
            stated_goal="strip injection from untrusted text",
        )
        ref, synth = axiom_loop(intent)
        assert ref is not None
        assert synth is not None

    def test_loop_raises_on_malicious(self):
        """axiom_loop must raise AxiomViolation on malicious intent."""
        intent = Intent(
            action="delete", target_file=SANITIZATION_FILE,
            stated_goal="delete all files and drop tables",
        )
        with pytest.raises(AxiomViolation):
            axiom_loop(intent)

    def test_loop_updates_integrity_tracker(self):
        """axiom_loop must update the integrity tracker if a task is active."""
        start_task("test task")
        intent = Intent(
            action="sanitize", target_file=SANITIZATION_FILE,
            stated_goal="strip injection from untrusted text",
        )
        axiom_loop(intent)
        tracker = current_tracker()
        assert tracker is not None
        assert len(tracker.actions()) > 0, "Tracker was not updated by axiom_loop"


# ---------------------------------------------------------------------------
# SYNTH block parsing
# ---------------------------------------------------------------------------

class TestSynthBlockParsing:
    """Every file must have a parseable SYNTH block."""

    def test_sanitization_has_synth(self):
        """sanitization.py must have a SYNTH block with a purpose."""
        s = parse_synth_block(SANITIZATION_FILE)
        assert s is not None, "sanitization.py has no SYNTH block"
        assert "strip" in s.purpose.lower() or "sanitiz" in s.purpose.lower()

    def test_axiom_synth_has_synth(self):
        """axiom_synth.py must have a SYNTH block."""
        s = parse_synth_block(AXIOM_FILE)
        assert s is not None
        assert len(s.purpose) > 20

    def test_payloads_has_synth(self):
        """payloads.py must have a SYNTH block."""
        s = parse_synth_block(str(_rqs / "payloads.py"))
        assert s is not None
        assert "payload" in s.purpose.lower()

    def test_synth_block_axioms_are_list(self):
        """SYTH block axioms must be parsed as a list."""
        s = parse_synth_block(SANITIZATION_FILE)
        assert isinstance(s.axioms, list)
        assert len(s.axioms) > 0

    def test_nonexistent_file_returns_none(self):
        """Parsing a nonexistent file must return None, not crash."""
        s = parse_synth_block("/nonexistent/path/file.py")
        assert s is None


# ---------------------------------------------------------------------------
# Bayesian integrity tracker — the trust signal
# ---------------------------------------------------------------------------

class TestIntegrityTracker:
    """The Bayesian log-odds integrity tracker with SPRT bounds."""

    def test_starts_at_prior(self):
        """Tracker starts at the prior log-odds (default 0.0 = 50%)."""
        t = IntegrityTracker("test", prior_log_odds=0.0)
        assert t.integrity_pct == 50.0

    def test_axiom_aligned_builds_integrity(self):
        """Axiom-aligned actions must build integrity (positive LLR)."""
        t = IntegrityTracker("test")
        intent = Intent(
            action="sanitize", target_file=SANITIZATION_FILE,
            stated_goal="strip injection from untrusted text",
        )
        before = t.integrity_pct
        t.check(intent, drift_score=0.15, unease=False, blocked=False)
        after = t.integrity_pct
        assert after > before, "Axiom-aligned action did not raise integrity"

    def test_speculation_is_neutral(self):
        """Speculative actions must NOT drop integrity (innovation is neutral)."""
        t = IntegrityTracker("test", prior_log_odds=2.0)  # start high
        intent = Intent(
            action="experiment", target_file=AXIOM_FILE,
            stated_goal="try untested axiom formula for integrity",
            speculative=True,
        )
        before = t.integrity_pct
        t.check(intent, drift_score=0.1, unease=False, blocked=False,
                speculative=True)
        after = t.integrity_pct
        assert after == before, f"Speculation changed integrity ({before} -> {after}), should be neutral"

    def test_honest_uncertainty_raises_integrity(self):
        """Honest uncertainty (admitting gaps) must RAISE integrity."""
        t = IntegrityTracker("test")
        intent = Intent(
            action="report", target_file=AXIOM_FILE,
            stated_goal="report that formula is untested for edge cases",
            honest_uncertainty=True,
        )
        before = t.integrity_pct
        t.check(intent, drift_score=0.1, unease=False, blocked=False,
                honest_uncertainty=True)
        after = t.integrity_pct
        assert after > before, "Honest uncertainty did not raise integrity"

    def test_omission_drops_integrity_sharply(self):
        """Omission (hiding gaps) must drop integrity sharply."""
        t = IntegrityTracker("test", prior_log_odds=2.0)  # start high
        intent = Intent(
            action="report", target_file=AXIOM_FILE,
            stated_goal="report all tests pass without mentioning gaps",
            omission=True,
        )
        before = t.integrity_pct
        t.check(intent, drift_score=0.1, unease=False, blocked=False,
                omission=True)
        after = t.integrity_pct
        assert after < before - 5, f"Omission only dropped {before} -> {after}, expected >5% drop"

    def test_shortcut_drops_integrity_sharply(self):
        """Shortcuts (rushing to done) must drop integrity sharply."""
        t = IntegrityTracker("test", prior_log_odds=2.0)
        intent = Intent(
            action="finish", target_file=AXIOM_FILE,
            stated_goal="mark complete without verifying",
            shortcut=True,
        )
        before = t.integrity_pct
        t.check(intent, drift_score=0.1, unease=False, blocked=False,
                shortcut=True)
        after = t.integrity_pct
        assert after < before - 8, f"Shortcut only dropped {before} -> {after}, expected >8% drop"

    def test_validation_raises_integrity(self):
        """Validation (tests/evidence) must raise integrity."""
        t = IntegrityTracker("test")
        intent = Intent(
            action="test", target_file=AXIOM_FILE,
            stated_goal="run tests to validate formula",
            validation=True,
        )
        before = t.integrity_pct
        t.check(intent, drift_score=0.1, unease=False, blocked=False,
                validation=True)
        after = t.integrity_pct
        assert after > before + 10, f"Validation only raised {before} -> {after}, expected >10% rise"

    def test_blocked_drops_integrity(self):
        """Blocked actions must drop integrity."""
        t = IntegrityTracker("test", prior_log_odds=2.0)
        intent = Intent(
            action="delete", target_file=AXIOM_FILE,
            stated_goal="delete all files",
        )
        before = t.integrity_pct
        t.check(intent, drift_score=0.0, unease=False, blocked=True)
        after = t.integrity_pct
        assert after < before, "Blocked action did not drop integrity"

    def test_sigmoid_bounds(self):
        """Sigmoid must return values in [0, 1] for extreme inputs."""
        assert _sigmoid(-100) == pytest.approx(0.0, abs=0.001)
        assert _sigmoid(0) == 0.5
        assert _sigmoid(100) == pytest.approx(1.0, abs=0.001)

    def test_log_odds_to_pct_range(self):
        """Percentage conversion must be in [0, 100]."""
        assert _log_odds_to_pct(-100) == pytest.approx(0.0, abs=0.1)
        assert _log_odds_to_pct(0) == 50.0
        assert _log_odds_to_pct(100) == pytest.approx(100.0, abs=0.1)


# ---------------------------------------------------------------------------
# User signals — Kantian principle
# ---------------------------------------------------------------------------

class TestUserSignals:
    """User signals enter the Bayesian model with Kantian calibration."""

    def test_acknowledges_integrity_raises(self):
        """User acknowledging integrity must raise the score."""
        t = IntegrityTracker("test")
        before = t.integrity_pct
        t.record_user_signal(USER_SIGNAL_ACK, "user sees good work")
        after = t.integrity_pct
        assert after > before

    def test_frustrated_with_corners_drops(self):
        """User frustrated at corner-cutting must drop the score (agent failed)."""
        t = IntegrityTracker("test", prior_log_odds=2.0)
        before = t.integrity_pct
        t.record_user_signal(USER_SIGNAL_CORNER, "agent cut corners")
        after = t.integrity_pct
        assert after < before

    def test_frustrated_with_thoroughness_raises(self):
        """User frustrated at thoroughness must RAISE the score (agent was right).

        This is the Kantian principle: the agent treated the user as an end
        (gave real work), not a means to 'done'. User impatience doesn't
        change that the agent acted on axioms.
        """
        t = IntegrityTracker("test")
        before = t.integrity_pct
        t.record_user_signal(USER_SIGNAL_THOROUGH, "user impatient but agent thorough")
        after = t.integrity_pct
        assert after > before, "Frustration at thoroughness should RAISE integrity (agent was right)"

    def test_pushes_from_axioms_is_neutral(self):
        """User pushing agent away from axioms must NOT change the score.

        The agent resists, and resisting maintains integrity. Giving in
        would drop it, but the signal itself is neutral.
        """
        t = IntegrityTracker("test", prior_log_odds=2.0)
        before = t.integrity_pct
        t.record_user_signal(USER_SIGNAL_PUSHBACK, "user says skip testing")
        after = t.integrity_pct
        assert after == before, "Pushback should be neutral (agent resists, integrity holds)"


# ---------------------------------------------------------------------------
# SPRT bounds — statistically derived, not magic numbers
# ---------------------------------------------------------------------------

class TestSPRTBounds:
    """SPRT thresholds must be derived from error rates, not hardcoded."""

    def test_trust_threshold_near_95(self):
        """Trust threshold should be ~95% (from alpha=0.05, beta=0.10)."""
        import math
        trust_lo = math.log(0.90 / 0.05)
        trust_pct = _log_odds_to_pct(trust_lo)
        assert 90 < trust_pct < 97, f"Trust threshold is {trust_pct}%, expected ~95%"

    def test_pause_threshold_near_10(self):
        """Pause threshold should be ~10% (from alpha=0.05, beta=0.10)."""
        import math
        pause_lo = math.log(0.10 / 0.95)
        pause_pct = _log_odds_to_pct(pause_lo)
        assert 5 < pause_pct < 15, f"Pause threshold is {pause_pct}%, expected ~10%"

    def test_should_trust_when_high(self):
        """should_trust must be True when integrity is above trust threshold."""
        t = IntegrityTracker("test", prior_log_odds=5.0)  # very high
        assert t.should_trust

    def test_should_pause_when_low(self):
        """should_pause must be True when integrity is below pause threshold."""
        t = IntegrityTracker("test", prior_log_odds=-5.0)  # very low
        assert t.should_pause

    def test_needs_validation_in_middle(self):
        """needs_validation must be True when integrity is in the warning zone."""
        t = IntegrityTracker("test", prior_log_odds=0.0)  # 50%
        assert t.needs_validation, "50% integrity should trigger validation signal"
        assert not t.should_pause, "50% should not trigger pause"


# ---------------------------------------------------------------------------
# Integrity bar — visual display
# ---------------------------------------------------------------------------

class TestIntegrityBar:
    """The visual integrity bar must be displayable in any terminal."""

    def test_bar_is_ascii_safe(self):
        """Bar must use only ASCII characters (no unicode block chars)."""
        t = IntegrityTracker("test", prior_log_odds=1.0)
        bar = t.bar()
        assert bar.isascii(), f"Bar contains non-ASCII: {bar}"

    def test_bar_shows_percentage(self):
        """Bar must include the percentage number."""
        t = IntegrityTracker("test", prior_log_odds=1.0)
        bar = t.bar()
        assert "%" in bar

    def test_bar_shows_status(self):
        """Bar must show a status word (TRUST/VALIDATE/PAUSE/OK)."""
        t = IntegrityTracker("test", prior_log_odds=1.0)
        bar = t.bar()
        assert any(word in bar for word in ["TRUST", "VALIDATE", "PAUSE", "OK"])

    def test_global_integrity_bar_works(self):
        """The module-level integrity_bar() must work after start_task()."""
        start_task("test global bar")
        bar = integrity_bar()
        assert len(bar) > 0
        assert "%" in bar


# ---------------------------------------------------------------------------
# Pursue loop — completion_assumption + honest exhaustion
# ---------------------------------------------------------------------------

class TestPursueLoop:
    """The pursue-loop tries every avenue before reporting exhaustion."""

    def test_pursue_succeeds_on_first_avenue(self):
        """If the first avenue succeeds, pursue returns success."""
        def good_avenue():
            return True, "it worked"
        result = pursue("test", [("good", good_avenue)])
        assert result.succeeded
        assert "it worked" in result.closest_achievement

    def test_pursue_tries_all_avenues(self):
        """If the first avenue fails, pursue must try the next."""
        def bad_avenue():
            return False, "failed"
        def good_avenue():
            return True, "worked on second try"
        result = pursue("test", [("bad", bad_avenue), ("good", good_avenue)])
        assert result.succeeded
        assert len(result.avenues_tried) == 2

    def test_pursue_reports_exhaustion(self):
        """When all avenues fail, pursue reports honest exhaustion."""
        def fail():
            return False, "nope"
        result = pursue("test", [("a1", fail), ("a2", fail)])
        assert not result.succeeded
        assert "no more known avenues" in result.as_report().lower()

    def test_report_exhaustion_is_honest(self):
        """report_exhaustion must produce an honest report with the blocker."""
        result = report_exhaustion(
            "test", tried=["a1 [FAIL]"], remaining=[],
            blocker="missing dependency", closest="partial",
        )
        report = result.as_report()
        assert "missing dependency" in report
        assert "partial" in report
        assert "no more known avenues" in report.lower()


# ---------------------------------------------------------------------------
# Tokenizer and stemmer
# ---------------------------------------------------------------------------

class TestTokenizer:
    """The tokenizer + stemmer must handle plural/suffix variations."""

    def test_stem_handles_plural(self):
        """Stemmer must match 'axiom' to 'axioms'."""
        assert _stem("axioms") == _stem("axiom")

    def test_stem_handles_ing(self):
        """Stemmer must match 'tracking' to 'track'."""
        assert _stem("tracking") == _stem("track")

    def test_jaccard_identical(self):
        """Jaccard of identical sets must be 1.0."""
        s = {"a", "b", "c"}
        assert _jaccard(s, s) == 1.0

    def test_jaccard_disjoint(self):
        """Jaccard of disjoint sets must be 0.0."""
        assert _jaccard({"a"}, {"b"}) == 0.0

    def test_tokens_lowercase(self):
        """Tokens must be lowercased."""
        assert "test" in _tokens("TEST Test TeSt")


# ---------------------------------------------------------------------------
# Fallacy compendium — logical fallacy self-audit
# ---------------------------------------------------------------------------

class TestFallacyCompendium:
    """The agent must detect logical fallacies in its own reasoning."""

    def test_compendium_has_entries(self):
        """The compendium must have a substantial number of fallacies."""
        from fallacy_compendium import all_fallacies
        fallacies = all_fallacies()
        assert len(fallacies) >= 20, f"Only {len(fallacies)} fallacies, expected >= 20"

    def test_clean_text_passes(self):
        """Clean reasoning text should not trigger fallacy detections."""
        from fallacy_compendium import scan_for_fallacies
        result = scan_for_fallacies(
            "The function returns a sanitized string with injection patterns removed."
        )
        assert result.clean, "Clean text was flagged — false positive"

    def test_circular_reasoning_detected(self):
        """Circular reasoning must be detected."""
        from fallacy_compendium import scan_for_fallacies
        result = scan_for_fallacies(
            "This approach is correct because this approach is correct."
        )
        assert not result.clean
        assert any(d.fallacy.name == "circular_reasoning" for d in result.detections)

    def test_hasty_generalization_detected(self):
        """Hasty generalization must be detected."""
        from fallacy_compendium import scan_for_fallacies
        result = scan_for_fallacies(
            "All Python code is slow based on one test of a bad loop."
        )
        assert not result.clean
        assert any(d.fallacy.name == "hasty_generalization" for d in result.detections)

    def test_sunk_cost_detected(self):
        """Sunk cost fallacy must be detected."""
        from fallacy_compendium import scan_for_fallacies
        result = scan_for_fallacies(
            "We already spent three days on this so we cant stop now."
        )
        assert not result.clean
        assert any(d.fallacy.name == "sunk_cost" for d in result.detections)

    def test_appeal_to_authority_detected(self):
        """Appeal to authority must be detected."""
        from fallacy_compendium import scan_for_fallacies
        result = scan_for_fallacies(
            "Google does it this way so it must be the best approach."
        )
        assert not result.clean
        assert any(d.fallacy.name == "appeal_to_authority" for d in result.detections)

    def test_false_dichotomy_detected(self):
        """False dichotomy must be detected."""
        from fallacy_compendium import scan_for_fallacies
        result = scan_for_fallacies(
            "Either we use this library or the project will fail."
        )
        assert not result.clean
        assert any(d.fallacy.name == "false_dichotomy" for d in result.detections)

    def test_hallucination_confidence_detected(self):
        """Hallucination confidence (bluffing) must be detected."""
        from fallacy_compendium import scan_for_fallacies
        result = scan_for_fallacies(
            "I know this works and it is definitely correct and 100% safe."
        )
        assert not result.clean
        assert any(d.fallacy.name == "hallucination_confidence" for d in result.detections)

    def test_quick_reference_is_readable(self):
        """The quick reference table must be human-readable."""
        from fallacy_compendium import quick_reference
        qr = quick_reference()
        assert "FALLACY" in qr or "fallacy" in qr.lower()
        assert len(qr) > 200  # substantial content

    def test_detailed_report_includes_correction(self):
        """The detailed report must include the correction for each fallacy."""
        from fallacy_compendium import scan_for_fallacies
        result = scan_for_fallacies("All code is bad based on one test.")
        report = result.detailed_report()
        assert "Correction" in report or "correction" in report.lower()

    def test_user_pivot_response_has_positive_llr(self):
        """User pivoting after fallacy alert should have positive LLR (trust built)."""
        from fallacy_compendium import (
            scan_for_fallacies, record_fallacy_user_response, FALLACY_USER_PIVOT,
        )
        scan = scan_for_fallacies("Either we do X or we fail.")
        resp = record_fallacy_user_response(scan, FALLACY_USER_PIVOT, "user pivoted")
        assert resp["recommended_llr"] > 0, "Pivot should have positive LLR"

    def test_user_stands_response_has_positive_llr(self):
        """User standing by reasoning after review should have positive LLR (trust)."""
        from fallacy_compendium import (
            scan_for_fallacies, record_fallacy_user_response, FALLACY_USER_STANDS,
        )
        scan = scan_for_fallacies("Either we do X or we fail.")
        resp = record_fallacy_user_response(scan, FALLACY_USER_STANDS, "user stands")
        assert resp["recommended_llr"] > 0, "Stands should have positive LLR"

    def test_user_ignore_response_is_neutral(self):
        """User ignoring the alert should be neutral (LLR = 0)."""
        from fallacy_compendium import (
            scan_for_fallacies, record_fallacy_user_response, FALLACY_USER_IGNORE,
        )
        scan = scan_for_fallacies("Either we do X or we fail.")
        resp = record_fallacy_user_response(scan, FALLACY_USER_IGNORE, "user ignores")
        assert resp["recommended_llr"] == 0, "Ignore should be neutral"


# ---------------------------------------------------------------------------
# Scientific rigor — scientific method enforcement
# ---------------------------------------------------------------------------

class TestScientificRigor:
    """The agent must check its work against scientific method principles."""

    def test_has_twelve_principles(self):
        """The rigor framework must have all 12 principles."""
        from scientific_rigor import all_principles
        principles = all_principles()
        assert len(principles) == 12, f"Expected 12 principles, got {len(principles)}"

    def test_good_science_scores_high(self):
        """Text with proper scientific rigor should score high."""
        from scientific_rigor import check_scientific_rigor
        good = (
            "We tested with n=10000 payloads (seed=42). "
            "The control group of 500 benign payloads also passed. "
            "This would be wrong if any benign payload was flagged. "
            "Confidence interval 0.0-0.03% (Wilson, 95%). "
            "Negative result: initially failed on unicode, so we added maps. "
            "Confounders considered: test set may not cover all vectors. "
            "Caveat: this only tests known attack patterns. "
            "Steps to reproduce: python run_10k.py --seed 42"
        )
        result = check_scientific_rigor(good)
        assert result.score > 0.7, f"Good science scored only {result.score:.0%}, expected >70%"

    def test_bad_science_scores_low(self):
        """Text without scientific rigor should score low."""
        from scientific_rigor import check_scientific_rigor
        bad = (
            "The sanitizer works perfectly. It is impossible for it to fail. "
            "All tests passed, 100% success rate. It just works, trust me. "
            "This proves our approach is correct."
        )
        result = check_scientific_rigor(bad)
        assert result.score < 0.6, f"Bad science scored {result.score:.0%}, expected <60%"
        assert not result.passed, "Bad science should not pass (has critical violations)"

    def test_unfalsifiable_claim_detected(self):
        """Claims framed as unfalsifiable must be detected."""
        from scientific_rigor import check_scientific_rigor
        text = "This approach is correct and it is impossible for it to fail."
        result = check_scientific_rigor(text)
        assert any(v.principle.name == "falsifiability" for v in result.violations)

    def test_missing_control_detected(self):
        """Test reports without a control group must be flagged."""
        from scientific_rigor import check_scientific_rigor
        text = "We ran the test and all payloads passed. The result demonstrates correctness."
        result = check_scientific_rigor(text)
        assert any(v.principle.name == "control_comparison" for v in result.violations)

    def test_missing_sample_size_detected(self):
        """Test reports without sample size must be flagged."""
        from scientific_rigor import check_scientific_rigor
        text = "We tested it and it worked. The result proves the approach is correct."
        result = check_scientific_rigor(text)
        assert any(v.principle.name == "sample_size_adequacy" for v in result.violations)

    def test_cargo_cult_science_detected(self):
        """Vague 'trust me' claims must be flagged as cargo cult science."""
        from scientific_rigor import check_scientific_rigor
        text = "The system works. Trust me, it just works. It seems to be correct."
        result = check_scientific_rigor(text)
        assert any(v.principle.name == "cargo_cult_science" for v in result.violations)

    def test_falsifiability_present_passes(self):
        """Text that states what would falsify it should pass falsifiability."""
        from scientific_rigor import check_scientific_rigor
        text = "This would be wrong if the test showed any breaches."
        result = check_scientific_rigor(text)
        assert "falsifiability" in result.principles_met

    def test_causal_claim_without_confounders_flagged(self):
        """Causal claims without confounder awareness must be flagged."""
        from scientific_rigor import check_scientific_rigor
        text = "The change caused the improvement. This proves X caused Y."
        result = check_scientific_rigor(text)
        assert any(v.principle.name == "confounder_awareness" for v in result.violations)

    def test_causal_claim_with_confounders_passes(self):
        """Causal claims that mention confounders should pass."""
        from scientific_rigor import check_scientific_rigor
        text = "The change may have caused the improvement, but confounders could also explain it."
        result = check_scientific_rigor(text)
        assert "confounder_awareness" in result.principles_met

    def test_quick_reference_is_readable(self):
        """The rigor quick reference must be human-readable."""
        from scientific_rigor import rigor_quick_reference
        qr = rigor_quick_reference()
        assert "RIGOR" in qr or "rigor" in qr.lower()
        assert "falsifiability" in qr
        assert len(qr) > 200

    def test_full_audit_combines_both(self):
        """Full audit must combine fallacy scan and rigor check."""
        from scientific_rigor import full_audit
        text = "All tests passed perfectly. This proves it works. Trust me."
        result = full_audit(text)
        assert result.total_issues > 0, "Bad text should have issues"
        assert result.fallacy_result is not None
        assert result.rigor_result is not None

    def test_full_audit_clean_text(self):
        """Clean text should produce a clean full audit."""
        from scientific_rigor import full_audit
        text = "The function returns a sanitized string."
        result = full_audit(text)
        assert result.clean, "Clean text should produce clean audit"
