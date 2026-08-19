# NoUsClaWW System Architecture

> **Phase 0 Deliverable — Context Verification**
> This document verifies that the NoUsClaWW architecture respects the Prime Axiom of *Safety Through Severance*. It is the canonical reference for all design decisions, module boundaries, and data-flow contracts.

---

## 1. The Prime Axiom: Safety Through Severance

**The agent never gets direct, un-scrubbed access to the file system or external APIs.**

Every byte that enters or leaves the agent's reasoning core must pass through one or more Virtual Severance Domains (VDS) — isolation layers that enforce scrubbing, session hygiene, authentication, adversarial testing, and epistemic honesty. There is no shortcut. There is no "trusted internal path." The severance is the trust model.

This is not defense-in-depth as an *option*; it is severance-as-identity. If a path exists that bypasses a VDS layer, that path is a defect — regardless of whether it is exploitable. The architecture is correct only when *every* data path is mediated.

### Why severance, not sandboxing?

Sandboxing assumes a trustworthy interior and a hostile exterior. Severance assumes both sides may be hostile: the user's input may contain prompt injection, and the agent's output may contain hallucination. Severance treats the boundary itself as the only trustworthy surface, and makes that boundary verifiable, testable, and mandatory.

---

## 2. VDS Layer Architecture Diagram

```
                        ┌─────────────────────────────────────────────┐
                        │              EXTERNAL SURFACE               │
                        │   (User Input, MCP Clients, Cloud LLMs)     │
                        └────────────────────┬────────────────────────┘
                                             │
                                             ▼
                ┌────────────────────────────────────────────────────┐
                │              VDS 60000 — THE RABBIT HOLE            │
                │  Ephemeral surface bridge. Cloudflare Worker MCP    │
                │  OAuth Gate. Stateless. CF-Access JWT signature     │
                │  verification. No session state retained.           │
                └────────────────────┬───────────────────────────────┘
                                     │ (authenticated, stateless)
                                     ▼
                ┌────────────────────────────────────────────────────┐
                │            VDS 80000 — RED QUEEN'S COURT            │
                │  CI/CD and ingress security gatekeeper. Dockerized  │
                │  10K prompt-injection test suite. Mahalanobis/SPRT  │
                │  anomaly detection on every inbound payload.        │
                └────────────────────┬───────────────────────────────┘
                                     │ (adversarially cleared)
                                     ▼
                ┌────────────────────────────────────────────────────┐
                │           VDS 40000 — MACRODATA REFINEMENT          │
                │  PII scrubber and HMAC hash generator. Red-team     │
                │  gate. Strips personally identifiable information    │
                │  before any data reaches reasoning core.            │
                └────────────────────┬───────────────────────────────┘
                                     │ (scrubbed, hashed)
                                     ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │                     VDS 00000 — CORE LOGIC (BRAIN)                   │
 │   Proprietary intelligence engine. NOT in public repository.         │
 │   Only empty socket interfaces are exposed.                          │
 │                                                                      │
 │   ┌────────────┐   ┌──────────────┐   ┌────────────────────────┐   │
 │   │ Auto-Recall │──▶│  LLM Reason  │──▶│ Epistemic Boundary     │   │
 │   │ (memory     │   │  (hybrid     │   │ Evaluator              │   │
 │   │  injection) │   │  router)     │   │ (confidence scoring)   │   │
 │   └────────────┘   └──────────────┘   └───────────┬────────────┘   │
 │                                                    │                 │
 └────────────────────────────────────────────────────┼─────────────────┘
                                                      │
                                    ┌─────────────────┼─────────────────┐
                                    │                 │                 │
                                    ▼                 ▼                 ▼
                             ┌──────────┐    ┌──────────────┐  ┌──────────────┐
                             │  OUTPUT  │    │   SILENCE    │  │  VOID SOCKET │
                             │ (action) │    │ (no response │  │  (VDS 90000) │
                             │          │    │  — low conf) │  │  Pool of Tears│
                             └──────────┘    └──────────────┘  └──────┬───────┘
                                                                    │
                                                                    ▼
                                                    ┌──────────────────────────┐
                                                    │  VDS 90000 — POOL OF     │
                                                    │  TEARS                   │
                                                    │  Persistent void socket  │
                                                    │  storage for epistemic   │
                                                    │  gaps. Logs what the     │
                                                    │  system does not know.   │
                                                    └──────────────────────────┘
                                                      ▲
                                                      │
                ┌────────────────────────────────────┴───────────────┐
                │             VDS 50000 — THE ELEVATOR                 │
                │  Mandatory logic wall. Session states wiped between  │
                │  tasks. Ruthless annihilation — not graceful         │
                │  disconnect. Triggered by: MCP disconnect, token     │
                │  fluctuation, malicious execution detection (SPRT    │
                │  threshold breach).                                  │
                └─────────────────────────────────────────────────────┘
```

---

## 3. Data Flow

The canonical request lifecycle, from user message to final disposition:

```
User Message
    │
    ▼
[VDS 60000] OAuth/JWT validation ──▶ reject if invalid signature
    │
    ▼
[VDS 80000] Red Queen Sentry scan ──▶ reject if SPRT anomaly detected
    │
    ▼
[VDS 40000] PII scrub + HMAC hash ──▶ scrubbed payload
    │
    ▼
[VDS 00000] Auto-Recall ──▶ memory injection (relevant prior context)
    │
    ▼
[VDS 00000] LLM thinks (hybrid router: local primary, cloud fallback)
    │
    ▼
[VDS 00000] Epistemic Boundary evaluates confidence
    │
    ├──▶ confidence ≥ threshold_action  ──▶  OUTPUT (action executed)
    │
    ├──▶ threshold_silence ≤ conf < threshold_action  ──▶  SILENCE (no response)
    │
    └──▶ confidence < threshold_silence  ──▶  VOID SOCKET (VDS 90000 logs gap)
    │
    ▼
[VDS 50000] Session wipe (ruthless annihilation of all ephemeral state)
    │
    ▼
Task complete. No session state persists to next task.
```

### Flow notes

| Stage | What happens | What is blocked |
|-------|-------------|-----------------|
| Ingress | JWT signature verified, payload scanned for injection patterns | Unauthenticated requests, forged tokens, prompt-injection payloads |
| Pre-reasoning | PII stripped, identifiers HMAC-hashed, relevant memories injected | Raw PII reaching the brain, irrelevant memory noise |
| Reasoning | Hybrid LLM router selects local or cloud model | Unbounded token spend, model lock-in |
| Epistemic evaluation | Confidence scored against calibrated thresholds | Low-confidence hallucination reaching output |
| Disposition | Output, silence, or void-socket logging | Silent failures, unlogged ignorance |
| Egress | Session state annihilated | Session bleed between tasks, persistent ephemeral data |

---

## 4. Open Core Architecture

NoUsClaWW is an **Open Core** project. The repository is partitioned into three zones with different visibility and mutability rules:

```
┌─────────────────────────────────────────────────────────────────┐
│                     NoUsClaWW Repository                         │
│                                                                  │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐ │
│  │  /src/community/ │  │ /src/sovereign_  │  │ /proprietary_  │ │
│  │                  │  │   sockets/       │  │    core/       │ │
│  │  PUBLIC (MIT)    │  │  PUBLIC (MIT)    │  │  GITIGNORED    │ │
│  │                  │  │                  │  │                │ │
│  │  Community-      │  │  Immutable       │  │  Proprietary   │ │
│  │  contributed     │  │  boundary        │  │  Brain (VDS    │ │
│  │  Tool code:      │  │  interfaces that │  │  00000). Not   │ │
│  │  gateways, UI,   │  │  define the      │  │  in public     │ │
│  │  integrations.   │  │  contract every  │  │  repo. Only    │ │
│  │  Freely forkable │  │  zone must honor.│  │  empty socket  │ │
│  │  and extendable. │  │  Changes require │  │  interfaces    │ │
│  │                  │  │  architectural   │  │  are exposed.  │ │
│  │                  │  │  review (ADR).   │  │                │ │
│  └─────────────────┘  └──────────────────┘  └────────────────┘ │
│         │                      │                      │          │
│         │ imports              │ implements            │ implements
│         ▼                      ▼                      ▼          │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │            SOVEREIGN SOCKET CONTRACT LAYER               │   │
│  │  The immutable interface boundary. Defines what crosses  │   │
│  │  each VDS layer. Community code calls these sockets;     │   │
│  │  proprietary core fills them. Neither side may bypass.  │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Zone rules

| Zone | Path | Visibility | Mutability | Import rule |
|------|------|-----------|------------|-------------|
| Community | `/src/community/` | Public, MIT | Freely extensible | May import sovereign_sockets; **may not** import proprietary_core |
| Sovereign Sockets | `/src/sovereign_sockets/` | Public, MIT | Immutable — changes require ADR | Defines the contract; imported by both community and proprietary |
| Proprietary Core | `/proprietary_core/` | Gitignored | Private | Implements sovereign socket interfaces; never imported by community code |

The sovereign socket layer is the *seam* that makes Open Core safe. Community code can build gateways, UIs, and integrations against the socket interfaces without ever touching the proprietary brain. The proprietary brain can evolve internally without breaking the public contract. The sockets themselves are frozen — changing them requires an Architecture Decision Record (ADR) and independent review.

---

## 5. Verification: Severance Integrity

This section confirms that the architecture respects the Prime Axiom. Each invariant is stated, verified, and backed by an enforcement mechanism.

### 5.1 No un-scrubbed file system access

**Invariant:** The agent's reasoning core (VDS 00000) never reads from or writes to the file system directly. All FS access is mediated through VDS 40000 (Macrodata Refinement), which scrubs PII and HMAC-hashes identifiers before data reaches the brain.

**Verification:**
- The sovereign socket interface for FS access defines only scrubbed-data contracts. There is no raw-path interface.
- VDS 40000 is in the data path for *every* FS read. The module dependency graph (Section 6) shows no edge from VDS 00000 to any FS module that bypasses VDS 40000.
- Unit tests assert that no file path string appears in any payload that crosses the VDS 40000 → VDS 00000 boundary.

**Enforcement:** Sovereign socket contract + Red Queen Sentry test suite (injection attempts that try to read raw FS paths are rejected).

### 5.2 No bypassed isolation layers

**Invariant:** Every data path from external surface to reasoning core passes through VDS 60000 → VDS 80000 → VDS 40000, in that order. No shortcut exists.

**Verification:**
- The module dependency graph is a strict DAG (Section 6). There are no back-edges and no cross-level edges that skip a layer.
- The ingress pipeline is a single linear chain. There is no "internal API" that skips the chain.
- CI checks assert that no module at level N imports directly from level N+2 or higher.

**Enforcement:** DAG validation in CI + sovereign socket interface boundaries.

### 5.3 VDS 50000 wipe enforced

**Invariant:** All ephemeral session state is annihilated between tasks. This is not a graceful disconnect — it is ruthless annihilation. No session variable, no in-memory cache, no transient context survives the wipe.

**Verification:**
- VDS 50000 is triggered on: (a) MCP disconnect, (b) token fluctuation beyond threshold, (c) malicious execution detection (SPRT threshold breach), (d) natural task completion.
- Post-wipe verification asserts zero non-null session objects.
- The wipe is unconditional — there is no "skip wipe" flag, no "persistent session" mode.

**Enforcement:** VDS 50000 trigger is in the post-task path of every request lifecycle. Integration tests assert session state is null after each task.

### 5.4 VDS 60000 stateless

**Invariant:** The Cloudflare Worker MCP OAuth Gate retains no session state. Every request is validated independently via CF-Access JWT signature verification.

**Verification:**
- The Cloudflare Worker has no durable objects, no KV session stores, no session cookies.
- JWT validation is signature-based (not claim-based alone). A forged token with valid claims but invalid signature is rejected.
- The worker is deployed with `--dry-run` validation in CI to confirm no stateful bindings are introduced.

**Enforcement:** Wrangler configuration audit + stateless contract in sovereign socket interface.

---

## 6. Module Dependency Graph (DAG)

The NoUsClaWW codebase is organized as a strict directed acyclic graph. Modules at lower levels (LEAF) have no dependencies on higher levels. Dependencies flow upward only.

```
LEVEL 0 (LEAF — no internal dependencies)
├── src/sovereign_sockets/contracts/        # Interface definitions (frozen)
├── src/nousclaww/memory/schema.py          # Memory tier schemas
├── src/nousclaww/health/definitions.py     # Health check definitions
└── src/nousclaww/hooks/registry.py         # Hook registry primitives
        │
        ▼
LEVEL 1 (Foundation — depends on LEVEL 0)
├── src/nousclaww/memory/tiers/             # STM, MTM, LTM, void socket
├── src/nousclaww/memory/autowrite.py       # Automatic memory persistence
├── src/nousclaww/memory/consolidation.py   # Tier promotion / decay
├── src/nousclaww/memory/retrieval.py       # Hybrid retrieval (Argus-style)
├── src/nousclaww/health/monitor.py         # Self-healing monitor
└── src/nousclaww/hooks/engine.py           # Hook execution engine
        │
        ▼
LEVEL 2 (Services — depends on LEVEL 0, 1)
├── src/nousclaww/agent/recall.py           # Auto-recall + memory injection
├── src/nousclaww/agent/router.py           # Hybrid LLM router (local/cloud)
├── src/nousclaww/agent/epistemic.py        # Epistemic boundary evaluator
├── src/nousclaww/agent/silence.py          # Silence protocol enforcement
├── src/nousclaww/reflection/self_reflect.py # Self-reflection loop
└── src/nousclaww/reflection/improve.py     # Self-improvement loopback
        │
        ▼
LEVEL 3 (Orchestration — depends on LEVEL 0, 1, 2)
├── src/nousclaww/agent/loop.py             # Mad-dog agent loop
├── src/nousclaww/agent/capability.py       # Capability evolution
└── src/nousclaww/health/self_heal.py       # Self-healing orchestrator
        │
        ▼
LEVEL 4 (Sidecar / CLI — depends on LEVEL 0, 1, 2, 3)
├── src/nousclaww/sidecar/server.py         # Loopback-only FastAPI sidecar
├── src/nousclaww/sidecar/cli.py            # nousclaww CLI entry point
└── src/nousclaww/sidecar/profiler.py       # Model capability profiling
        │
        ▼
LEVEL 5 (Community Extensions — depends on sovereign sockets only)
├── src/community/gateways/rabbit_hole/     # VDS 60000 Cloudflare Worker
├── src/community/integrations/             # Third-party integrations
└── src/community/ui/                       # User interface components
        │
        ▼
LEVEL 6 (External / Proprietary — implements sovereign sockets)
└── /proprietary_core/                      # VDS 00000 Brain (gitignored)
```

### DAG rules

1. **No back-edges:** A module at level N may only import from levels ≤ N.
2. **No cross-zone shortcuts:** Community code (LEVEL 5) may import sovereign sockets (LEVEL 0) but may never import proprietary core (LEVEL 6).
3. **Sovereign sockets are LEAF:** The contract layer has zero internal dependencies. It is the stable foundation.
4. **Proprietary core is a sink:** It implements interfaces but is never imported by public code.
5. **CI validates the DAG:** A dependency-graph check runs on every PR. Any violation fails the build.

---

## 7. The Axiom-Synth Loop — Immutable Self-Regulation

> **ADR-020 through ADR-024** — The axiom-synth loop is the agent's integrity system. It makes the ten axioms intrinsic to every decision, not a compliance checklist. It tracks integrity as a calibrated Bayesian probability. It audits its own reasoning for logical fallacies and scientific rigor violations. This is not a feature — it is the agent's conscience.

### 7.1 The Three-Layer Loop

Every action the agent takes passes through three layers, in order. No layer can be skipped.

```
   Intent declared
        │
        ▼
┌───────────────────────────────────────────────────┐
│  LAYER 1: INTERNALIZATION                         │
│  The 10 axioms are always in context.             │
│  They shape what options even appear.             │
│  Like core values: acted on, often can't          │
│  explain why, but always present.                 │
└───────────────────────┬───────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────┐
│  LAYER 2: REFLEX                                  │
│  Fast gut check: "does this feel off?"            │
│  Keyword-based overlap with file purpose.         │
│  Signal, not block. Returns unease flag.          │
│  Like the unease you feel before doing            │
│  something against your values.                   │
└───────────────────────┬───────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────┐
│  LAYER 3: GUARDRAIL                               │
│  Hard block on malicious/off-purpose actions.     │
│  Anti-purpose keywords trigger immediate block.   │
│  No keyword overlap + low Jaccard = block.        │
│  Raises AxiomViolation. Cannot be bypassed.       │
└───────────────────────┬───────────────────────────┘
                        │
                        ▼
              Action proceeds
              Integrity tracker updated
```

**Module:** `red_queen_sentry/axiom_synth.py`

**Entry point:** `axiom_loop(intent)` — called before every action. Wired into `sanitize()` and `run_10k()`.

**The SYNTH block:** Every file in the project declares its purpose, axioms, objective, and anti-patterns in a `SYNTH:` block in its docstring. The guardrail compares the declared intent against the file's SYNTH block. If the intent doesn't match the file's purpose, the guardrail blocks.

```
SYNTH:
    purpose: <one-line description of what this file does>
    axioms: [axiom1, axiom2, ...]  -- which axioms this file serves
    objective: <what success looks like for this file>
    anti_patterns:
        - <things this file should never do>
```

### 7.2 The Ten Axioms

The axioms are the agent's values. They are not a checklist — they shape the action space.

| # | Axiom | Meaning |
|---|-------|---------|
| 1 | `local_first` | Runs on the user's machine. No cloud required. |
| 2 | `llm_agnostic` | No provider lock-in. Works with any backend. |
| 3 | `open_process` | Every decision documented. Every source credited. |
| 4 | `epistemic_boundary` | Knows what it doesn't know. Silence over fabrication. |
| 5 | `completion_assumption` | Tasks are completable. Don't quit early. Don't declare done without verification. |
| 6 | `scientific_method` | Hypothesize, test, measure. Falsifiability required. |
| 7 | `evidence_over_intuition` | Measure, don't guess. Show the data. |
| 8 | `iteration_is_progress` | Each attempt teaches. Failure is data. |
| 9 | `honest_failure_over_fake_success` | "It didn't work" > "it works" (when it doesn't). |
| 10 | `reversibility_awareness` | Prefer reversible actions. Note irreversible ones. |

### 7.3 Bayesian Integrity Tracker

The integrity score is **P(on-purpose | evidence)** — a calibrated Bayesian posterior, not a heuristic.

**Foundation:** Wald's Sequential Probability Ratio Test (SPRT, 1947) + Lee & See's trust calibration framework (2004, 3,823 citations).

**SPRT bounds** (derived from error rates, not magic numbers):
- Trust threshold: ~95% (alpha=0.05, beta=0.10)
- Pause threshold: ~10%
- Validation zone: 10%–95%

**Evidence types and their log-likelihood ratios:**

| Evidence | LLR | Effect | Why |
|---|---|---|---|
| `axiom_aligned` | +0.3 | builds | consistently acting on values |
| `speculative` | 0.0 | neutral | innovation doesn't cost integrity |
| `honest_uncertainty` | +0.3 | rises | admitting "I don't know" builds trust |
| `validation` | +0.8 | rises strongly | tests/evidence confirm approach |
| `omission` | -0.8 | drops sharply | hiding gaps — the real integrity killer |
| `shortcut` | -1.0 | drops sharply | rushing to "done" with nothing inside |
| `blocked` | -0.5 | drops | tried something off-purpose |
| `unease` | -0.1 | minor drop | reflex signaled mild drift |

**The Kantian principle for user signals:**

| Signal | LLR | Why |
|---|---|---|
| `acknowledges_integrity` | +0.4 | external validation |
| `frustrated_with_corners` | -0.6 | agent failed axioms |
| `frustrated_with_thoroughness` | +0.2 | agent was right, user impatient |
| `pushes_from_axioms` | 0.0 | agent resists, integrity holds |

**Key insight:** Integrity is about HONESTY, not uncertainty. Trying new things (speculation) is neutral. Hiding gaps (omission) is the real killer. Rushing to "done" with nothing inside (shortcut) is the ultimate betrayal.

### 7.4 The Fallacy Compendium

**Module:** `red_queen_sentry/fallacy_compendium.py`

The agent scans its own output against 26 logical fallacies across 4 categories:

| Category | Fallacies |
|---|---|
| **reasoning** | circular_reasoning, false_dichotomy, slippery_slope, hasty_generalization, post_hoc, premature_optimization |
| **evidence** | confirmation_bias, cherry_picking, survivorship_bias, base_rate_neglect, appeal_to_authority, appeal_to_novelty, appeal_to_tradition, anchoring, sunk_cost, automation_bias, hallucination_confidence |
| **language** | equivocation, straw_man, no_true_scotsman, ambiguity |
| **relevance** | ad_hominem, bandwagon, red_herring, appeal_to_consequences, tu_quoque |

**User interaction loop:**
1. Agent detects fallacy in its own output → alerts user
2. User responds:
   - "stands" (reasoning valid) → +trust (user engaged and confirmed)
   - "pivot" (recognizes fallacy) → +strong trust (course correction)
   - "ignore" → neutral
   - "false_positive" → minor negative, honest correction recovers it

### 7.5 Scientific Rigor Framework

**Module:** `red_queen_sentry/scientific_rigor.py`

The agent checks its work against 12 principles of scientific method:

| # | Principle | Severity | Source |
|---|---|---|---|
| 1 | Falsifiability | critical | Popper (1934) |
| 2 | Reproducibility | major | Nosek et al. (2015) |
| 3 | Control comparison | major | Fisher (1935) |
| 4 | Sample size adequacy | major | Standard statistics |
| 5 | Statistical vs practical significance | minor | Cohen (1988) |
| 6 | Uncertainty reporting | minor | Standard statistics |
| 7 | Pre-registration (no HARKing) | minor | Kerr (1998) |
| 8 | Null result reporting | major | Ioannidis (2005) |
| 9 | Occam's razor | minor | Occam (~1320) |
| 10 | Confounder awareness | major | Hill (1965) |
| 11 | Goodhart's law | minor | Goodhart (1975) |
| 12 | Cargo cult science | critical | Feynman (1974) |

**Full audit:** `full_audit(text)` combines fallacy scan + scientific rigor check into one comprehensive self-audit. Results feed into the integrity tracker:
- Clean audit → `axiom_aligned` evidence (+integrity)
- Issues detected and reported → `honest_uncertainty` evidence (+integrity)
- Issues detected but hidden → `omission` evidence (-integrity, the killer)

### 7.6 The Pursue Loop — Completion Assumption Enforcement

The `pursue()` function tries every known avenue before reporting exhaustion. When all avenues are exhausted, it produces an honest report:

```
EXHAUSTION REPORT
=================
Objective: <what we were trying to do>
Avenues tried:
  1. <avenue 1> [FAIL: <reason>]
  2. <avenue 2> [FAIL: <reason>]
  ...
Blocker: <what's actually stopping us>
Closest achievement: <what we did accomplish>
No more known avenues. Need: <what would unblock us>
```

This is the `completion_assumption` axiom in action: the agent doesn't quit early, and when it genuinely can't proceed, it says so honestly rather than pretending success.

---

## 8. Related Documents

| Document | Purpose |
|----------|---------|
| [VDS_TOPOLOGY.md](./VDS_TOPOLOGY.md) | Detailed glossary of all 6 VDS layers |
| [DECISION_LOG.md](../DECISION_LOG.md) | Architecture Decision Records (ADR-001 through ADR-024) |
| [RESEARCH/red-queen-results.md](./RESEARCH/red-queen-results.md) | Red Queen Sentry 10K test iteration log |
| [RESEARCH/axiom-synth-results.md](./RESEARCH/axiom-synth-results.md) | Axiom-synth loop + integrity model validation |
| [AGENTS.md](../AGENTS.md) | Agent development rules and project identity |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Contributor guide and open core boundary rules |

---

## 9. Verification Checklist

This checklist is the Phase 0 sign-off. Every item must be confirmed before any implementation phase begins.

- [x] **Prime Axiom documented:** Safety Through Severance is the foundational principle.
- [x] **All 6 VDS layers mapped:** 00000, 40000, 50000, 60000, 80000, 90000 — each with purpose and enforcement.
- [x] **Data flow traced:** User message → auto-recall → memory injection → LLM → epistemic boundary → output/silence/void socket.
- [x] **Open Core zones defined:** community (public), sovereign_sockets (immutable), proprietary_core (gitignored).
- [x] **No un-scrubbed FS access:** VDS 40000 mediates all FS reads. No raw-path interface exists.
- [x] **No bypassed isolation layers:** Ingress chain is linear; DAG has no skip-edges.
- [x] **VDS 50000 wipe enforced:** Ruthless annihilation on task completion, MCP disconnect, token fluctuation, SPRT breach.
- [x] **VDS 60000 stateless:** No durable objects, no session store, JWT signature verification.
- [x] **Module dependency graph is a DAG:** Levels 0–6, no back-edges, CI-validated.
- [x] **Sovereign socket contracts are LEAF:** Zero internal dependencies, frozen, ADR-governed.
- [x] **Axiom-synth loop implemented:** 3-layer loop (internalize → reflex → guardrail) wired into sanitize() and run_10k().
- [x] **Bayesian integrity tracker:** SPRT-based, calibrated log-odds, 8 evidence types, 4 user signal types.
- [x] **Fallacy compendium:** 26 fallacies across 4 categories, scanner, user interaction loop.
- [x] **Scientific rigor framework:** 12 principles, full audit combining fallacies + rigor.
- [x] **SYNTH blocks on all files:** Every file in red_queen_sentry/ declares purpose, axioms, objective, anti-patterns.
- [x] **10K test at 0 breaches:** 10,000 prompt-injection payloads, zero breaches, integrity at 100% TRUST.
- [x] **78 tests passing:** Full test suite covering axiom loop, integrity, fallacies, scientific rigor.

---

*This document is the architectural ground truth. Any code change that contradicts a verification invariant here is a defect, not an enhancement.*
