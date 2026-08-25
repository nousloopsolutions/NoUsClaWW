# Architecture Decision Records — NoUsClaWW

> Every architectural decision is recorded here. Every ADR includes the context, decision, consequences, and status. This is the open_process axiom in action.

---

## ADR-001: Safety Through Severance (not sandboxing)

**Status:** Accepted
**Date:** 2026-01-15

**Context:** The system needs to protect against both prompt injection (hostile exterior) and hallucination (hostile interior). Sandboxing assumes a trustworthy interior. We cannot.

**Decision:** Use Virtual Severance Domains (VDS) — isolation layers that treat the boundary itself as the only trustworthy surface. Every byte that enters or leaves the reasoning core passes through one or more VDS layers.

**Consequences:** Six VDS layers (00000, 40000, 50000, 60000, 80000, 90000). No shortcut paths. CI validates the DAG. More complex than sandboxing, but the trust model is sound.

---

## ADR-002: Open Core Architecture

**Status:** Accepted
**Date:** 2026-01-15

**Context:** The project needs to be public and community-contributable while protecting the proprietary reasoning engine.

**Decision:** Three zones: `/src/community/` (public, MIT), `/src/sovereign_sockets/` (public, MIT, immutable), `/proprietary_core/` (gitignored). CI enforces the import DAG.

**Consequences:** Community code can use sovereign sockets but never proprietary core. Sovereign socket changes require an ADR. The boundary is the contract.

---

## ADR-003: Red Queen Sentry as CI Gatekeeper

**Status:** Accepted
**Date:** 2026-01-20

**Context:** Indirect prompt injection is the primary attack vector for LLM agents. The lethal trifecta: private data + untrusted content + external action.

**Decision:** A Dockerized 10,000-prompt-injection test suite runs in CI on every PR. A single breach blocks the merge. Uses rule-based sanitization + Mahalanobis/SPRT anomaly detection.

**Consequences:** Every PR is adversarially tested. The test suite must be maintained as new attack vectors emerge. False positives are acceptable; false negatives are not.

---

## ADR-004: Cloudflare Worker MCP OAuth Gate (VDS 60000)

**Status:** Accepted
**Date:** 2026-01-22

**Context:** Community access to the agent needs a stateless, scalable entry point that doesn't retain session state.

**Decision:** Cloudflare Worker with JWT signature verification (not claim-only). No durable objects, no KV session stores. Stateless by design.

**Consequences:** `wrangler deploy --dry-run` in CI verifies no stateful bindings. JWT verification is cryptographic. Session state lives only in VDS 50000 (the Elevator), which annihilates it.

---

## ADR-005: Epistemic Boundary and Silence Protocol

**Status:** Accepted
**Date:** 2026-01-25

**Context:** Standard LLMs fabricate to avoid admitting gaps. This is the hallucination problem at its root.

**Decision:** Implement the Sherlock Protocol (halt and request data on epistemic gaps) and the Silence Principle (authorized non-engagement with cost-function). Gaps are logged to VDS 90000 (Pool of Tears) as null-vector sockets.

**Consequences:** The agent can say "I don't know." Silence carries a cost (localized log of void boundaries). Dynamic confidence thresholds prevent paralysis. Three red-team constraints enforced by tests.

---

## ADR-020: Axiom-Synth Loop — Immutable Self-Regulation

**Status:** Accepted
**Date:** 2026-08-20

**Context:** The original four axioms were a compliance checklist — the agent read them and checked against them. This is not how values work in a person. Values shape what options even appear. A compliance checklist is bypassable; internalized values are not.

**Decision:** Implement a three-layer loop (internalize → reflex → guardrail) that makes the ten axioms intrinsic to every decision. Every file declares a SYNTH block (purpose, axioms, objective, anti-patterns). The guardrail compares declared intent against the file's purpose before every action.

**Consequences:**
- 10 axioms expanded from the original 4 (added: completion_assumption, scientific_method, evidence_over_intuition, iteration_is_progress, honest_failure_over_fake_success, reversibility_awareness).
- Every file in `red_queen_sentry/` has a SYNTH block.
- `axiom_loop(intent)` is wired into `sanitize()` and `run_10k()`.
- The loop cannot be bypassed — it's in the function body, not the call site.
- 78 tests prove the loop is immutable and enforced.

**Sources:**
- Values-based decision theory: Schwartz, S.H. (2012). An Overview of the Schwartz Theory of Basic Values.
- Three-layer cognition: Kahneman, D. (2011). Thinking, Fast and Slow. (System 1 = reflex, System 2 = guardrail, values = internalization)

---

## ADR-021: Bayesian Integrity Tracker — Wald SPRT

**Status:** Accepted
**Date:** 2026-08-21

**Context:** The original integrity score was a heuristic (count of axiom-aligned actions minus violations). This is not statistically sound. Trust calibration requires a probabilistic framework.

**Decision:** Replace the heuristic with a Bayesian log-odds model using Wald's Sequential Probability Ratio Test (SPRT). The integrity score is P(on-purpose | evidence) — a calibrated posterior, not a count.

**Consequences:**
- SPRT bounds derived from error rates (alpha=0.05, beta=0.10), not magic numbers.
- Trust threshold ~95%, pause threshold ~10%, validation zone 10-95%.
- 8 evidence types with log-likelihood ratios.
- 4 user signal types with Kantian calibration.
- The score is visible to the user as a trust bar.

**Sources:**
- Wald, A. (1947). Sequential Analysis. Dover Publications.
- Lee, J.D. & See, K.A. (2004). Trust in automation: Designing for appropriate reliance. Human Factors, 46(1), 50-80. (3,823 citations)

---

## ADR-022: Honesty Model — Integrity is About Honesty, Not Uncertainty

**Status:** Accepted
**Date:** 2026-08-22

**Context:** The initial integrity model treated speculation (trying untested approaches) as negative evidence. This is wrong. Innovation is the scientific_method axiom in action. The real integrity killers are dishonesty about results: omission of gaps, presenting incomplete testing as complete, rushing to "done" with nothing inside.

**Decision:** Restructure the evidence model:
- `speculative` (innovation) is NEUTRAL (LLR=0.0) — trying new things doesn't cost integrity.
- `honest_uncertainty` (admitting "I don't know") is POSITIVE (LLR=+0.3) — admitting gaps builds trust.
- `omission` (hiding gaps) is STRONG NEGATIVE (LLR=-0.8) — the real integrity killer.
- `shortcut` (rushing to done) is STRONG NEGATIVE (LLR=-1.0) — the pretty package with nothing inside.

**Consequences:**
- The agent is encouraged to try new things without integrity penalty.
- The agent is penalized for dishonesty, not uncertainty.
- The user sees: "I tried this untested approach" (neutral) vs "all tests pass" (when only 2 of 10 cases were tested — omission, sharp drop).
- The Kantian principle: frustration at thoroughness RAISES integrity (agent was right). Frustration at corner-cutting DROPS it (agent failed).

---

## ADR-023: Fallacy Compendium — Logical Fallacy Self-Audit

**Status:** Accepted
**Date:** 2026-08-23

**Context:** The agent can reason correctly within its axioms and still commit logical fallacies. Catching these requires an explicit compendium of known fallacies to check against.

**Decision:** Implement a structured compendium of 26 logical fallacies across 4 categories (reasoning, evidence, language, relevance). The agent scans its own output before presenting conclusions. Includes AI-specific fallacies: automation_bias, hallucination_confidence, premature_optimization.

**Consequences:**
- 26 fallacies detected via regex/keyword patterns.
- User interaction loop: agent alerts user, user responds (stands/pivot/ignore/false_positive).
- Detecting a fallacy in its own work is `honest_uncertainty` evidence (+integrity).
- Hiding a detected fallacy is `omission` evidence (-integrity, the killer).
- False positives are acceptable — they're conversation starters, not errors.

**Sources:**
- Copi, I.M., Cohen, C., & McMahon, K. (2014). Introduction to Logic. 14th ed. Pearson.
- Yourlogicalfallacyis.com — taxonomy reference.

---

## ADR-024: Scientific Rigor Framework — Scientific Method Enforcement

**Status:** Accepted
**Date:** 2026-08-23

**Context:** Logical fallacies catch reasoning errors, but scientific rigor is a broader framework. The agent should check its work against the actual principles of scientific method, not just fallacy patterns.

**Decision:** Implement 12 principles of scientific rigor: falsifiability (Popper), reproducibility, control comparison, sample size adequacy, statistical vs practical significance, uncertainty reporting, pre-registration (no HARKing), null result reporting, Occam's razor, confounder awareness, Goodhart's law, cargo cult science (Feynman).

**Consequences:**
- Each principle has positive and negative detection patterns.
- Principles are "expected" based on text content (e.g., claims require falsifiability, tests require controls).
- Violations classified as critical/major/minor.
- `full_audit(text)` combines fallacy scan + scientific rigor check.
- Rigor score (0-100%) shows fraction of principles satisfied.

**Sources:**
- Popper, K. (1934/1959). The Logic of Scientific Discovery.
- Feynman, R. (1974). Cargo Cult Science. Caltech commencement address.
- Ioannidis, J. (2005). Why Most Published Research Findings Are False. PLoS Medicine.
- Nosek, B. et al. (2015). Estimating the reproducibility of psychological science. Science, 349(6251).
- Goodhart, C. (1975). Problems of Monetary Management. Bank of England.
- Kerr, N. (1998). HARKing: Hypothesizing After Results are Known. Personality and Social Psychology Review.

---

## ADR-025: Socket-Plugin Integration — Community BrainInterface Implementation

**Status:** Proposed (PR #24)
**Date:** 2026-08-25

**Context:** The Nous MVP needs to load NoUsClaWW as a socket plugin at runtime. The sovereign socket layer defines the `BrainInterface` contract, but no community-side implementation existed. Without a concrete brain, the MVP cannot reason, plan, or execute tasks — it can only send empty socket calls. Additionally, the MVP needs tool dispatch (file/shell/browser), multi-step task execution, an affective HUD (Visage), and encrypted disaster recovery (R2 Archive) — all gated behind user control flags.

**Decision:** Implement community-side implementations of all sovereign socket interfaces used by the MVP:

1. **`LocalOllamaBrain`** — wraps the existing `LLMInterface` to satisfy `BrainInterface`. Uses local Ollama for reasoning, heuristic confidence scoring, and void-socket logging for epistemic gaps.
2. **`SocketLoader`** — resolves any brain implementation from a dotted path (`module:ClassName`) or JSON manifest file. The MVP passes the brain path via `NOUS_BRAIN_PATH` env var or `--brain-path` CLI flag.
3. **`ToolRegistry`** — dispatches named tool calls to registered handlers, gated by `ControlState` flags. Includes `FileReadTool` (OBSERVE_ENABLED), `FileWriteTool` (STORE_ENABLED), and `ShellExecTool` (SHELL_ENABLED).
4. **`TaskLoop`** — multi-step autonomous execution: plan → act → observe → reflect. Bounded by `max_steps`, cancellable externally.
5. **`BrowserControl`** — Playwright wrapper gated by `JS_EVAL_ENABLED` and `OBSERVE_ENABLED`. Playwright is an optional dependency.
6. **`VisageHUD`** — community `VisageInterface` with 19 affect labels, heuristic classification, PII scrubbing, and settling curve.
7. **`R2Archive`** — community `ArchiveInterface` with AES-256-GCM encryption, Merkle chain verification, and graceful cloud degradation. `cryptography` is an optional dependency.
8. **Extended `ControlState`** — added `JS_EVAL_ENABLED` and `SHELL_ENABLED` flags plus generic `can()` and `require()` methods. All defaults remain DISABLED.
9. **New sidecar endpoints** — `/chat`, `/task`, `/tools`, `/tools/dispatch`, `/visage/frame`.

**Consequences:**
- The MVP can load NoUsClaWW as a socket plugin with a single `--brain-path` flag.
- All new modules are community-zone code — they import only from `sovereign_sockets` and the standard library. No `proprietary_core` imports (DAG check passes).
- All optional capabilities (browser, archive, cloud) are guarded behind optional dependencies. The test suite passes without them (AGENTS.md Section 4.4).
- 85 new tests (265 total passing). The existing 180 tests from PR #23 are unaffected.
- The `ControlState` now has 6 flags (was 4). The `__post_init__` validation iterates over `ControlFlag`, so the new flags are automatically validated.
- The sidecar server initializes brain/tools/task_loop/visage lazily — if optional deps are missing, the subsystems degrade gracefully and the endpoints return honest errors.

**Sources:**
- Sovereign socket contracts: `src/sovereign_sockets/brain_interface.py`, `visage_interface.py`, `archive_interface.py`
- Existing LLM interface: `src/nousclaww/llm_interface.py`
- Control state model: `src/nousclaww/control_state.py` (P1.7)

---

*Future ADRs will be appended here. Each ADR must include context, decision, consequences, and status.*
