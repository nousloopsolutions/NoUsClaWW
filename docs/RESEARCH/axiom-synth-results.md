# Axiom-Synth Loop — Integrity Model Validation Results

> **ADR-020 through ADR-024**
> This document validates the axiom-synth loop, Bayesian integrity tracker, fallacy compendium, and scientific rigor framework. Every claim is falsifiable and tested.

---

## Current Status: 78 TESTS PASSING

| Metric | Value |
|--------|-------|
| Total tests | 78 |
| Passing | 78 |
| Failing | 0 |
| Coverage | axiom loop, integrity tracker, fallacy compendium, scientific rigor |
| Date | 2026-08-23 |

---

## Test Breakdown

### Axiom Internalization (5 tests)
- Axiom context is non-empty and contains all 10 axioms
- completion_assumption axiom is present
- Axiom hash is stable across calls (cache works)
- acknowledge() returns a non-empty hash
- All ten axioms present in keys

### Reflex Layer (4 tests)
- On-purpose intent does not trigger unease
- Off-purpose intent triggers unease
- Reflex returns a resonance score between 0 and 1
- Reflex does not block (signal, not block)

### Guardrail Layer (3 tests)
- On-purpose action clears the guardrail
- Malicious action (delete) is blocked
- Bypass attempt is blocked

### Three-Layer Loop (3 tests)
- axiom_loop returns (ReflexResult, SynthBlock)
- axiom_loop raises AxiomViolation on malicious intent
- axiom_loop updates the integrity tracker

### SYNTH Block Parsing (5 tests)
- sanitization.py has a SYNTH block with a purpose
- axiom_synth.py has a SYNTH block
- payloads.py has a SYNTH block
- SYNTH block axioms are parsed as a list
- Nonexistent file returns None (not crash)

### Bayesian Integrity Tracker (10 tests)
- Starts at prior (50% with default log-odds=0.0)
- Axiom-aligned actions build integrity
- **Speculation is NEUTRAL** (innovation doesn't cost integrity)
- **Honest uncertainty RAISES integrity** (admitting gaps builds trust)
- **Omission drops integrity sharply** (hiding gaps — the real killer)
- **Shortcut drops integrity sharply** (rushing to done with nothing inside)
- Validation raises integrity strongly
- Blocked actions drop integrity
- Sigmoid bounds [0, 1] for extreme inputs
- Log-odds to percentage conversion in [0, 100]

### User Signals — Kantian Principle (4 tests)
- acknowledges_integrity raises the score
- frustrated_with_corners drops the score (agent failed)
- **frustrated_with_thoroughness RAISES the score** (agent was right)
- **pushes_from_axioms is NEUTRAL** (agent resists, integrity holds)

### SPRT Bounds (5 tests)
- Trust threshold near 95% (from alpha=0.05, beta=0.10)
- Pause threshold near 10%
- should_trust when integrity is high
- should_pause when integrity is low
- needs_validation in the middle zone

### Integrity Bar (4 tests)
- Bar is ASCII-safe (no unicode block chars)
- Bar shows percentage
- Bar shows status word (TRUST/VALIDATE/PAUSE/OK)
- Global integrity_bar() works after start_task()

### Pursue Loop (4 tests)
- Succeeds on first avenue
- Tries all avenues if first fails
- Reports honest exhaustion when all fail
- report_exhaustion includes the blocker and closest achievement

### Tokenizer (5 tests)
- Stemmer matches 'axiom' to 'axioms' (plural)
- Stemmer matches 'tracking' to 'track' (-ing suffix)
- Jaccard of identical sets is 1.0
- Jaccard of disjoint sets is 0.0
- Tokens are lowercased

### Fallacy Compendium (13 tests)
- Compendium has >= 20 entries (26 total)
- Clean text passes without false positives
- Circular reasoning detected
- Hasty generalization detected
- Sunk cost detected
- Appeal to authority detected
- False dichotomy detected
- Hallucination confidence detected
- Quick reference is human-readable
- Detailed report includes corrections
- User pivot response has positive LLR (trust built)
- User stands response has positive LLR (trust confirmed)
- User ignore response is neutral

### Scientific Rigor (13 tests)
- Has 12 principles
- Good science scores high (>70%)
- Bad science scores low (<60%)
- Unfalsifiable claims detected
- Missing control group detected
- Missing sample size detected
- Cargo cult science detected
- Falsifiability present passes
- Causal claim without confounders flagged
- Causal claim with confounders passes
- Quick reference is readable
- Full audit combines fallacies + rigor
- Clean text produces clean audit

---

## Integrity Model Validation

The following scenario validates the full integrity model end-to-end:

```
Phase 1: Axiom-aligned work          50% → 81.8%  (builds trust)
Phase 2: Speculation (innovation)    81.8% → 81.8% (NEUTRAL)
Phase 3: Honest uncertainty          81.8% → 85.8% (RISES)
Phase 4: Validation (tests)          85.8% → 93.1% (RISES strongly)
Phase 5: Omission (hiding gaps)      93.1% → 85.8% (DROPS sharply)
Phase 6: Shortcut (rushing to done)  85.8% → 69.0% (DROPS sharply)
```

### Key Validation Points

1. **Speculation is neutral:** Trying an untested approach does NOT drop integrity. This is the scientific_method axiom — innovation is good. The integrity drop comes from dishonesty about results, not from trying new things.

2. **Honest uncertainty raises integrity:** Admitting "I don't know, this is untested" is POSITIVE evidence. This is the epistemic_boundary axiom — honesty about gaps builds trust.

3. **Omission is the real killer:** "All tests pass" when only 2 of 10 cases were tested. This drops integrity by 7.3 percentage points in a single action. Hiding gaps is worse than failing.

4. **Shortcut is the ultimate betrayal:** "Mark task complete without verifying" drops integrity by 16.8 percentage points. The pretty package with nothing inside.

5. **Frustration at thoroughness RAISES integrity:** When the agent is thorough and the user is impatient, integrity goes UP. The agent treated the user as an end (gave real work), not a means to "done."

6. **Pushing from axioms is neutral:** When the user says "skip testing" and the agent resists, integrity holds. The agent did its job.

---

## Scientific Rigor Validation

### Good scientific output (83% rigor score):
```
"We tested with n=10000 payloads (seed=42).
The control group of 500 benign payloads also passed.
This would be wrong if any benign payload was flagged.
Confidence interval 0.0-0.03% (Wilson, 95%).
Negative result: initially failed on unicode, so we added maps.
Confounders considered: test set may not cover all vectors.
Caveat: this only tests known attack patterns."
```
- 10/12 principles met
- 2 minor misses (reproducibility details, caveats could be more explicit)

### Bad scientific output (42% rigor score):
```
"The sanitizer works perfectly. It is impossible for it to fail.
All tests passed, 100% success rate. It just works, trust me.
This proves our approach is correct."
```
- 7 violations (2 critical, 4 major, 1 minor)
- Caught: unfalsifiable claim, missing control, missing sample size, cargo cult science, missing uncertainty, missing null results

---

## Fallacy Detection Validation

All 10 test cases pass:
- 9 fallacies correctly detected (circular reasoning, sunk cost, appeal to authority, hasty generalization, false dichotomy, slippery slope, bandwagon, appeal to novelty, hallucination confidence)
- 1 clean text correctly passes without false positives

---

## Reproducibility

```bash
# Run the full test suite
cd NoUsClaWW
python -m pytest tests/test_axiom_synth.py -v

# Run the 10K test with integrity tracking
python -c "
import sys; sys.path.insert(0, 'red_queen_sentry')
from axiom_synth import start_task, integrity_bar
from run_10k import run
start_task('Run 10K test')
results = run(iterations=10000, seed=42)
print('Breaches:', results['breaches'])
print(integrity_bar())
"
```

**Environment:**
- Python 3.14.7
- pytest 9.0.3
- No network access required
- Deterministic (seed=42)

---

*This document is updated every time the test suite is run. Every test, every validation, every result is recorded. This is the open_process axiom and the scientific_method axiom in action.*
