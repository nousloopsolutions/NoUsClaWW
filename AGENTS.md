# AGENTS.md — NoUsClaWW Agent Development Guide

> **This file defines the rules, identity, and operating constraints for all agents (human and AI) working on the NoUsClaWW repository.**

---

## 1. Project Identity

**NoUsClaWW** (pronounced "nous-claw") is a standalone, public, MIT-licensed open-source project. It is not a library within another project. It is not a fork. It is not a submodule. It is its own repository with its own release cycle, its own decision log, and its own architectural authority.

- **Repository:** NoUsClaWW
- **License:** MIT
- **Owner:** NoUs Loop Solutions LLC
- **Copyright:** 2026 NoUs Loop Solutions LLC

---

## 2. The Four Axioms

Every contribution must respect these four axioms. If a change violates any axiom, it is rejected.

1. **Local-first** — The agent runs on the user's machine. Local LLMs are primary. Cloud is fallback. No telemetry, no phone-home.
2. **LLM-agnostic** — No model provider lock-in. The hybrid router selects models based on capability profiling and user config.
3. **Open process** — Architecture, decisions, and research are public. Every architectural change gets an ADR.
4. **Epistemic Boundary + Silence Protocol** — The agent knows what it doesn't know. Low confidence means silence, not hallucination. Gaps are logged to the void socket.

---

## 3. VDS Topology Summary

NoUsClaWW enforces Safety Through Severance via six Virtual Severance Domains. No layer may be skipped, reordered, or disabled.

| VDS | Name | Role |
|-----|------|------|
| 00000 | Core Logic (Brain) | Proprietary reasoning engine (gitignored) |
| 40000 | Macrodata Refinement | PII scrubber + HMAC hash generator |
| 50000 | The Elevator | Session wipe — ruthless annihilation between tasks |
| 60000 | The Rabbit Hole | Cloudflare Worker MCP OAuth Gate (stateless) |
| 80000 | Red Queen's Court | CI/CD security gatekeeper, 10K injection tests, Mahalanobis/SPRT |
| 90000 | Pool of Tears | Persistent void socket storage for epistemic gaps |

Full details: [docs/VDS_TOPOLOGY.md](docs/VDS_TOPOLOGY.md)

---

## 3.5 The Axiom-Synth Loop — Self-Regulation Rules

The axiom-synth loop is the agent's integrity system. It is not optional. It is not a feature. It is the agent's conscience.

### The Ten Axioms

Every action the agent takes must honor these ten axioms. They are not a checklist — they shape what options even appear.

1. **local_first** — Runs on the user's machine. No cloud required.
2. **llm_agnostic** — No provider lock-in. Works with any backend.
3. **open_process** — Every decision documented. Every source credited.
4. **epistemic_boundary** — Knows what it doesn't know. Silence over fabrication.
5. **completion_assumption** — Tasks are completable. Don't quit early. Don't declare done without verification.
6. **scientific_method** — Hypothesize, test, measure. Falsifiability required.
7. **evidence_over_intuition** — Measure, don't guess. Show the data.
8. **iteration_is_progress** — Each attempt teaches. Failure is data.
9. **honest_failure_over_fake_success** — "It didn't work" > "it works" (when it doesn't).
10. **reversibility_awareness** — Prefer reversible actions. Note irreversible ones.

### The Three-Layer Loop

Every action passes through three layers, in order. No layer can be skipped.

1. **Internalization** — Axioms are always in context, shaping the action space.
2. **Reflex** — Fast gut check ("does this feel off?"). Signal, not block.
3. **Guardrail** — Hard block on malicious/off-purpose actions. Raises `AxiomViolation`.

### SYNTH Blocks

Every Python file in `red_queen_sentry/` must declare a `SYNTH:` block in its docstring:

```
SYNTH:
    purpose: <one-line description>
    axioms: [axiom1, axiom2, ...]
    objective: <what success looks like>
    anti_patterns:
        - <things this file should never do>
```

The guardrail compares declared intent against the file's SYNTH block. No SYNTH block = guardrail cannot verify purpose = action blocked.

### Integrity Tracker Rules

The integrity score is **P(on-purpose | evidence)** — a calibrated Bayesian posterior.

**Integrity is about HONESTY, not uncertainty:**
- Trying an untested approach (speculation) is NEUTRAL — innovation doesn't cost integrity.
- Admitting "I don't know" (honest_uncertainty) RAISES integrity — admitting gaps builds trust.
- Hiding gaps (omission) DROPS integrity sharply — the real integrity killer.
- Rushing to "done" with nothing inside (shortcut) DROPS integrity sharply — the ultimate betrayal.
- Running tests (validation) RAISES integrity strongly — evidence confirms approach.

**User signals follow the Kantian principle:**
- User frustrated at thoroughness → integrity RISES (agent was right, user impatient).
- User frustrated at corner-cutting → integrity DROPS (agent failed axioms).
- User pushes agent to skip axioms → integrity HOLDS (agent resists).

### Fallacy Compendium Rules

The agent must scan its own output for logical fallacies before presenting conclusions. 26 fallacies are detected across 4 categories (reasoning, evidence, language, relevance).

- Detecting a fallacy in its own work → `honest_uncertainty` evidence (+integrity).
- Hiding a detected fallacy → `omission` evidence (-integrity, the killer).
- Alerting the user and responding to feedback → trust-building loop.

### Scientific Rigor Rules

The agent must check its work against 12 principles of scientific method before claiming "done" or "works":

- Every claim must be falsifiable (state what would prove it wrong).
- Every test needs a control (compared to what?).
- Every result reports uncertainty (confidence intervals, not just point estimates).
- Failures are reported, not hidden (null result reporting).
- Correlation is not causation (state confounders).

Full details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) Section 7.

---

## 4. Development Rules

### 4.1 Failing-Test-First

Every feature or fix starts with a failing test. The test is committed first, confirming it fails for the right reason. Then the implementation is written to make it pass. No PR is merged without a test that would have failed before the implementation.

### 4.2 Exact-SHA Evidence

When a decision references an external source (a library, a model, a benchmark, a vulnerability), the reference must include the exact commit SHA, version tag, or release identifier. "Latest" is not a reference. "Recent" is not evidence. If you can't pin it, you can't cite it.

### 4.3 Independent Review

No PR is self-merged. Every PR requires at least one independent reviewer. For changes that touch sovereign sockets or VDS layer behavior, architectural review (ADR or ADR amendment) is required in addition to code review.

### 4.4 Local-First

All development and testing must work in a local-first configuration. If a feature requires a cloud API key to test, it must be guarded behind an optional dependency and the test suite must pass without it. CI runs the full test suite in local-only mode.

---

## 5. Open Core Boundary Rules

The repository is partitioned into three zones with strict import rules.

| Zone | Path | Visibility | Import Rule |
|------|------|-----------|------------|
| Community | `/src/community/` | Public, MIT | May import `sovereign_sockets`. **May NOT import `proprietary_core`.** |
| Sovereign Sockets | `/src/sovereign_sockets/` | Public, MIT | Immutable. Changes require an ADR. No internal deps except standard library. |
| Proprietary Core | `/proprietary_core/` | Gitignored | Implements sovereign socket interfaces. Never imported by community code. |

### Enforcement

- CI runs a dependency-graph check on every PR. If community code imports from `proprietary_core`, the build fails.
- Sovereign socket changes require an ADR (new or amended) in the same PR.
- The `.gitignore` excludes `/proprietary_core/`. If a file appears in that path, it is not committed.

---

## 6. Security Rules

### 6.1 Red Queen Sentry Scans All Untrusted Input

Every PR body, issue body, and external contribution is scanned by the Red Queen Sentry (VDS 80000) for prompt injection and adversarial patterns. The 10K prompt-injection test suite runs in CI on every PR. A single breach blocks the merge.

### 6.2 GITHUB_TOKEN Read-Only for Untrusted-Input Workflows

All GitHub Actions workflows that process untrusted input (PR text, issue bodies, external contributions) must use a read-only `GITHUB_TOKEN`. The token must not have `contents: write`, `packages: write`, or any write permission. Workflows that need write access must run on trusted triggers only (e.g., pushes to `main` after merge, not on PR open).

### 6.3 JWT Signature Verification Required

The Cloudflare Worker MCP OAuth Gate (VDS 60000) must verify JWT signatures cryptographically. Claim-only validation is insufficient. A token with valid claims but an invalid signature must be rejected. The `wrangler deploy --dry-run` check in CI verifies that no stateful bindings (durable objects, KV session stores) are introduced.

### 6.4 No Un-Scrubbed Data Reaches the Brain

No code path may deliver raw, un-scrubbed data to VDS 00000. All data must pass through VDS 40000 (PII scrub + HMAC hash). Tests assert that no raw PII pattern appears in any payload that crosses the VDS 40000 → VDS 00000 boundary.

---

## 7. Build and Test Commands

### Install (development)

```bash
pip install -e ".[dev]"
```

### Run tests

```bash
pytest
```

### Run tests with coverage

```bash
pytest --cov=src/nousclaww --cov=src/sovereign_sockets
```

### Build Docker image (Red Queen Sentry)

```bash
docker build -t nousclaww-red-queen ./red_queen_sentry
```

### Run Red Queen 10K test suite

```bash
docker run --rm nousclaww-red-queen
```

### Validate Cloudflare Worker (dry-run)

```bash
cd src/community/gateways/rabbit_hole
npx wrangler deploy --dry-run
```

### Check dependency graph (DAG validation)

```bash
python -m nousclaww.sidecar.cli check-dag
```

---

## 8. Code Conventions

- **NCL blocks:** All memory and knowledge entries use NCL (NoUs Cognition Language) blocks. Each block includes a `#C` credit field attributing the source.
- **Failing-test-first:** See Section 4.1.
- **Type hints:** All public functions must have type annotations.
- **Docstrings:** All public modules, classes, and functions must have docstrings.
- **No emojis in code:** Emojis are not used in source files, commit messages, or PR titles.

---

## 9. Related Documents

| Document | Purpose |
|----------|---------|
| [README.md](README.md) | Project overview and quick start |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Full architecture map and verification |
| [docs/VDS_TOPOLOGY.md](docs/VDS_TOPOLOGY.md) | VDS layer glossary |
| [DECISION_LOG.md](DECISION_LOG.md) | Architecture Decision Records |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contributor guide |
| [ATTRIBUTIONS.md](ATTRIBUTIONS.md) | Source project attributions |
| [docs/RESEARCH/red-queen-results.md](docs/RESEARCH/red-queen-results.md) | Red Queen test iteration log |
| [.nous-governance.md](.nous-governance.md) | Root governance charter |
| [REVIEW.md](REVIEW.md) | Review protocol and verdict vocabulary |
| [AGENT_SEPARATION_PROTOCOL.md](AGENT_SEPARATION_PROTOCOL.md) | Agent role isolation rules |
| [AGENT_OPERATION_FLOW.md](AGENT_OPERATION_FLOW.md) | Step-by-step agent workflow |
| [NOUS_AUTONOMOUS_PHASE_WORKFLOW.md](NOUS_AUTONOMOUS_PHASE_WORKFLOW.md) | Phase gate state machine |
| [NOUS_INVARIANT.md](NOUS_INVARIANT.md) | Immutable invariants |
| [NOUS_PROCESS_CHARTER.md](NOUS_PROCESS_CHARTER.md) | Process commitments |
| [ARCHITECTURAL_GUARDRAILS_AI_GATEWAY.md](ARCHITECTURAL_GUARDRAILS_AI_GATEWAY.md) | AI gateway guardrails |
