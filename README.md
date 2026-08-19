# NoUsClaWW

[![CI](https://img.shields.io/badge/CI-pending-lightgrey)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)]()
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)]()

**Pronounced "nous-claw"**

A local-first, LLM-agnostic autonomous agent stack with desktop control, hybrid LLM routing, self-healing, and a multi-layer memory system that learns. Built on the Epistemic Boundary and Silence Protocol.

---

## The Four Axioms

NoUsClaWW is built on four non-negotiable principles. Every design decision traces back to at least one of these.

### 1. Local-First

The agent runs on your machine. Your data stays on your machine. Local LLMs (via Ollama) are the primary reasoning engine. Cloud LLMs are a fallback, not a default. No telemetry, no phone-home, no cloud dependency unless you explicitly enable it.

### 2. LLM-Agnostic

NoUsClaWW does not lock you into a single model provider. The hybrid LLM router selects between local and cloud models based on task complexity, model capability profiling, and your configuration. Swap models without rewriting your agent logic.

### 3. Open Process

The architecture, decision log, and research findings are public. Every architectural decision is recorded as an ADR (Architecture Decision Record). The community can inspect, question, and build on the design. The proprietary reasoning core is separate, but the contracts it honors are open.

### 4. Epistemic Boundary + Silence Protocol

The agent knows what it doesn't know. When confidence is low, the agent stays silent rather than hallucinating. When confidence is below the silence threshold, the gap is logged to the Pool of Tears (VDS 90000) — a persistent record of ignorance that informs future learning. The agent never bluffs.

---

## Key Features

- **Multi-tier memory system** — Short-Term Memory (STM), Medium-Term Memory (MTM), Long-Term Memory (LTM), and a Void Socket for epistemic gaps. Memories consolidate and decay based on relevance and recency.
- **Auto-recall** — Relevant memories are automatically injected into the LLM's context before reasoning. No manual retrieval calls. The agent remembers what matters, when it matters.
- **Autowrite** — Experiences are automatically persisted to memory. The agent learns from every task without explicit "save" instructions.
- **Memory consolidation** — STM promotes to MTM promotes to LTM based on access frequency and importance scoring. Old, unused memories decay.
- **Hybrid retrieval** — Combines semantic similarity search with keyword/FTS5 matching (Argus-style hybrid retrieval) for robust memory recall.
- **Self-healing** — The agent monitors its own health and can recover from failures, retry failed operations, and degrade gracefully when components are unavailable.
- **Self-reflection and self-improvement** — Post-task reflection loops analyze performance, identify knowledge gaps from the Pool of Tears, and feed improvements back into the agent's capabilities.
- **Red Queen Sentry** — A dockerized 10,000-prompt-injection test suite runs in CI. Every PR is scanned for adversarial vulnerabilities. Mahalanobis distance and SPRT anomaly detection guard live payloads. Currently at **0 breaches across 10,000 payloads**.
- **Axiom-Synth Loop** — An immutable three-layer self-regulation system (internalize → reflex → guardrail) that makes the ten axioms intrinsic to every decision. Every action passes through all three layers. No layer can be skipped.
- **Bayesian Integrity Tracker** — A calibrated trust score (P(on-purpose | evidence)) using Wald's SPRT. Innovation doesn't cost integrity. Hiding gaps does. Rushing to "done" with nothing inside is the ultimate betrayal. The score is the agent's conscience, visible to the user.
- **Fallacy Compendium** — The agent scans its own output against 26 logical fallacies (circular reasoning, sunk cost, hasty generalization, hallucination confidence, etc.). Detecting a fallacy in its own work raises integrity. Alerting the user and responding to their feedback builds trust.
- **Scientific Rigor Framework** — The agent checks its work against 12 principles of scientific method (falsifiability, reproducibility, control comparison, confounder awareness, cargo cult science, etc.). Every claim must be falsifiable. Every test needs a control. Every result reports uncertainty.
- **Model capability profiling** — The agent profiles each LLM's strengths and weaknesses, routing tasks to the model best suited for them. Profiling data drives adaptive scaling decisions.
- **Hybrid LLM routing** — Local models (Ollama) for privacy-sensitive and routine tasks. Cloud models for complex reasoning when local capacity is insufficient. You control the routing policy.
- **Desktop control** — Optional desktop automation via a cua-driver wrapper. The agent can interact with your desktop when you explicitly enable it.
- **Loopback-only sidecar** — The FastAPI sidecar binds to localhost only. No external network exposure. The agent's API is your agent's API — not a public endpoint.
- **Silence Protocol** — When the epistemic boundary evaluator scores confidence below threshold, the agent says nothing rather than guessing. Silence is a feature, not a bug.
- **Session wipe (VDS 50000)** — Ruthless annihilation of all ephemeral state between tasks. No session bleed, no context leakage, no persistent caches.
- **Open Core** — Community code is public and MIT-licensed. The proprietary reasoning core is separate. The sovereign socket layer defines the immutable contract between them.

---

## Quick Start

### Prerequisites

- Python 3.11 or later
- [Ollama](https://ollama.ai) installed and running (for local LLM support)

### Install

```bash
pip install nousclaww
```

For local development:

```bash
git clone https://github.com/NoUsLoopSolutions/NoUsClaWW.git
cd NoUsClaWW
pip install -e ".[dev]"
```

### Start Ollama

```bash
ollama serve
```

Pull a model (optional — NoUsClaWW will use whatever Ollama has available):

```bash
ollama pull llama3.1
```

### Run

```bash
nousclaww
```

This starts the loopback-only sidecar and drops you into an interactive session. The agent will auto-recall relevant memories, route to your local LLM, and apply the epistemic boundary to every response.

### Configuration

NoUsClaWW stores its memory database and configuration in `~/.nousclaww/`. This directory is created automatically on first run. See the [configuration documentation](docs/ARCHITECTURE.md) for details.

---

## Architecture Overview

NoUsClaWW enforces the Prime Axiom — **Safety Through Severance** — through six Virtual Severance Domains (VDS). The agent never gets direct, un-scrubbed access to the file system or external APIs. All access is mediated through isolation layers.

```
User → VDS 60000 (OAuth Gate) → VDS 80000 (Security Scan) → VDS 40000 (PII Scrub)
    → VDS 00000 (Brain: recall → LLM → epistemic boundary)
    → OUTPUT | SILENCE | VDS 90000 (Void Socket)
    → VDS 50000 (Session Wipe)
```

For the full architecture map, verification, and module dependency graph, see **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

For a detailed glossary of each VDS layer, see **[docs/VDS_TOPOLOGY.md](docs/VDS_TOPOLOGY.md)**.

---

## Open Core

NoUsClaWW is an **Open Core** project. The repository is partitioned into three zones:

| Zone | Path | What it is |
|------|------|-----------|
| **Community** | `/src/community/` | Public, MIT-licensed, freely forkable. Gateways, UI, integrations. |
| **Sovereign Sockets** | `/src/sovereign_sockets/` | Public, MIT-licensed, but **immutable**. The contract layer that defines what crosses each VDS boundary. Changes require an ADR. |
| **Proprietary Core** | `/proprietary_core/` | Gitignored. The proprietary reasoning brain (VDS 00000). Not in the public repo. Only empty socket interfaces are exposed. |

Community code may import sovereign sockets but **may never import** proprietary core. The sovereign socket layer is the seam that makes Open Core safe — community code builds against the contract, the proprietary core fills it, and neither side may bypass the boundary.

---

## Security

### Red Queen Sentry

The Red Queen Sentry (VDS 80000) is NoUsClaWW's security gatekeeper:

- **10K prompt-injection test suite** — 10,000 adversarial prompt-injection attacks run in a Docker container in CI. Every PR must pass with zero breaches.
- **Mahalanobis anomaly detection** — Inbound payloads are embedded into a feature space. Outliers (Mahalanobis distance above threshold) are flagged and blocked.
- **SPRT anomaly detection** — During live execution, the Sequential Probability Ratio Test monitors token usage, response patterns, and timing. A threshold breach triggers immediate session annihilation via VDS 50000.

### VDS 50000 Session Wipe

All ephemeral session state is **ruthlessly annihilated** between tasks. Not a graceful disconnect — a scorched-earth wipe. Triggered by task completion, MCP disconnect, token fluctuation, or SPRT threshold breach. No session state survives.

### JWT Signature Verification

The Cloudflare Worker MCP OAuth Gate (VDS 60000) validates CF-Access JWTs with full cryptographic signature verification. A forged token with valid claims but an invalid signature is rejected. The worker is stateless — no session store, no durable objects, no cookies.

### GITHUB_TOKEN Read-Only Enforcement

All GitHub Actions workflows that process untrusted input (PR text, issue bodies, external contributions) use a read-only `GITHUB_TOKEN`. No untrusted-input workflow has write permissions. This prevents supply-chain attacks via CI.

---

## Contributing

We welcome contributions from the community. See **[CONTRIBUTING.md](CONTRIBUTING.md)** for:

- How to fork, branch, and submit PRs
- Good starter tasks from the future feature set
- Development setup and test commands
- Code conventions (NCL blocks with `#C` credit fields, failing-test-first)
- Security rules for contributors
- Open Core boundary rules

---

## License

MIT License. See [LICENSE](LICENSE) for the full text.

Copyright (c) 2026 NoUs Loop Solutions LLC

---

## Attributions

NoUsClaWW builds on ideas and patterns from several open-source projects. See [ATTRIBUTIONS.md](ATTRIBUTIONS.md) for the full list.

---

## Decision Log

Every architectural decision is recorded as an Architecture Decision Record (ADR). See [DECISION_LOG.md](DECISION_LOG.md) for ADR-001 through ADR-019.
