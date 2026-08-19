# VDS Topology — Virtual Severance Domain Glossary

> **Canonical reference for all VDS layers in NoUsClaWW.**
> Every isolation layer, its purpose, its enforcement mechanism, what crosses it, and what it blocks.

---

## Overview

NoUsClaWW enforces the Prime Axiom — *Safety Through Severance* — through six Virtual Severance Domains (VDS). Each layer is numbered, has a single responsibility, and is non-optional. Data flows through them in a strict order. No layer may be skipped, bypassed, or disabled.

| VDS | Name | Role |
|-----|------|------|
| 00000 | Core Logic (Brain) | Proprietary intelligence engine |
| 40000 | Macrodata Refinement | PII scrubber and HMAC hash generator |
| 50000 | The Elevator | Session wipe — ruthless annihilation |
| 60000 | The Rabbit Hole | Cloudflare Worker MCP OAuth Gate |
| 80000 | Red Queen's Court | CI/CD and ingress security gatekeeper |
| 90000 | Pool of Tears | Persistent void socket storage |

---

## VDS 00000 — Core Logic (Brain)

### Purpose

The proprietary intelligence engine. This is where reasoning happens: auto-recall injects relevant memory, the hybrid LLM router selects a model, the LLM generates a response, and the epistemic boundary evaluator scores confidence. The brain is the only component that decides whether to output, stay silent, or log to the void socket.

### What it is

- **Not in the public repository.** The `/proprietary_core/` directory is gitignored.
- Only **empty socket interfaces** are exposed to the public codebase, defined in `/src/sovereign_sockets/`.
- Community code calls these sockets; the proprietary core fills them. Neither side may bypass the seam.

### Enforcement mechanism

- **Open Core boundary:** `/src/community/` may not import from `/proprietary_core/`. CI validates this on every PR.
- **Sovereign socket contracts:** The interface layer (`/src/sovereign_sockets/`) is frozen. Changes require an ADR and independent review.
- **No direct FS/API access:** The brain receives only scrubbed, hashed data from VDS 40000. It has no raw file-system or external API interface.

### What crosses it

- **Inbound:** Scrubbed payloads (PII removed, identifiers HMAC-hashed) from VDS 40000, plus injected memory context from auto-recall.
- **Outbound:** A disposition decision — output (action), silence (no response), or void socket (epistemic gap logged to VDS 90000).

### What's blocked

- Raw file-system paths or file contents (mediated by VDS 40000).
- Unauthenticated or un-scanned external input (mediated by VDS 60000 and VDS 80000).
- Any direct network call to an external API that bypasses the sovereign socket contract.

---

## VDS 40000 — Macrodata Refinement

### Purpose

The PII scrubber and HMAC hash generator. This is the red-team gate that ensures no personally identifiable information — no names, no email addresses, no phone numbers, no file paths containing user identifiers — ever reaches the reasoning core. Every identifier is replaced with an HMAC hash before the payload proceeds.

### Enforcement mechanism

- **Mandatory position in the ingress chain:** VDS 40000 sits between VDS 80000 (security scan) and VDS 00000 (brain). No data path skips it.
- **Regex + ML scrubbing:** A combination of pattern-based PII detection and statistical anomaly detection identifies and redacts sensitive data.
- **HMAC hashing:** Identifiers that must be correlated across sessions are HMAC-hashed with a rotating key, not stored in plaintext.
- **Red-team gate:** The scrubber is tested against a corpus of adversarial inputs designed to leak PII through encoding, obfuscation, and Unicode tricks.

### What crosses it

- **Inbound:** Adversarially cleared payloads from VDS 80000 (already scanned for injection, but may still contain PII).
- **Outbound:** Scrubbed payloads with PII removed and identifiers HMAC-hashed, ready for VDS 00000.

### What's blocked

- Any payload containing detected PII patterns (names, emails, phone numbers, SSNs, credit card numbers, file paths with user identifiers).
- Obfuscated PII (Base64-encoded, Unicode-homoglyph-substituted, split across fields).
- Raw identifier strings that have not been HMAC-hashed.

---

## VDS 50000 — The Elevator

### Purpose

The mandatory logic wall. Session states are wiped between tasks — not gracefully disconnected, but *ruthlessly annihilated*. No ephemeral state survives: no session variables, no in-memory caches, no transient context windows, no partial reasoning traces. The elevator ensures every task starts from a clean slate.

### Enforcement mechanism

- **Unconditional wipe:** The wipe fires on every task completion. There is no "skip wipe" flag, no "persistent session" mode, no opt-out.
- **Triggered annihilation:** The wipe also fires immediately on:
  - **MCP disconnect:** If the MCP connection drops, the session is annihilated.
  - **Token fluctuation:** If token usage spikes beyond a calibrated threshold (possible prompt-injection or runaway generation), the session is annihilated.
  - **Malicious execution detection:** If the SPRT anomaly detector (VDS 80000) flags a threshold breach during execution, the session is annihilated.
- **Post-wipe verification:** Integration tests assert that all session objects are null after the wipe. If any state survives, the test fails.
- **Ruthless, not graceful:** This is not a cleanup routine that closes handles and flushes buffers. It is a scorched-earth wipe. Anything not explicitly persisted to VDS 90000 (void socket) or the memory tiers (STM/MTM/LTM) is destroyed.

### What crosses it

- **Nothing crosses it in the data-flow sense.** The elevator is a *boundary enforcer*, not a data conduit. It sits between tasks and annihilates state.
- The only thing that "crosses" is the signal that the wipe is complete, allowing the next task to begin.

### What's blocked

- Session bleed between tasks (any ephemeral state from task N appearing in task N+1).
- Persistent in-memory caches that could leak context across task boundaries.
- Partial reasoning traces from a failed or interrupted task contaminating the next task.
- Any attempt to "resume" a session that was annihilated.

---

## VDS 60000 — The Rabbit Hole

### Purpose

The ephemeral surface bridge. A Cloudflare Worker that serves as the MCP OAuth Gate — the only public entry point for MCP clients. Every request is authenticated via CF-Access JWT with full signature verification. The worker is stateless: it retains no session data, no cookies, no durable objects.

### Enforcement mechanism

- **CF-Access JWT validation:** Every request must present a valid CF-Access JWT. The signature is cryptographically verified — not just the claims. A token with valid claims but an invalid signature is rejected.
- **Stateless by design:** The Cloudflare Worker has:
  - No durable objects
  - No KV session stores
  - No session cookies
  - No server-side session state of any kind
- **Wrangler configuration audit:** CI runs `wrangler deploy --dry-run` to confirm no stateful bindings are introduced. If a PR adds a durable object or KV session binding, CI fails.
- **Sovereign socket contract:** The Rabbit Hole implements a stateless ingress socket. The contract explicitly forbids session retention.

### What crosses it

- **Inbound:** MCP client requests with CF-Access JWT bearer tokens.
- **Outbound:** Authenticated requests forwarded to VDS 80000 for security scanning. The JWT is validated and stripped; no session token is passed forward.

### What's blocked

- Unauthenticated requests (no JWT or expired JWT).
- Requests with forged JWT signatures (valid claims, invalid signature).
- Replay attacks (stateless validation means no nonce store, but the short JWT TTL and signature binding make replay impractical).
- Any attempt to establish a stateful session through the worker.

---

## VDS 80000 — Red Queen's Court

### Purpose

The CI/CD and ingress security gatekeeper. Every inbound payload is scanned for prompt injection, adversarial patterns, and statistical anomalies. The Red Queen Sentry runs a dockerized 10,000-prompt injection test suite in CI, and uses Mahalanobis distance and SPRT (Sequential Probability Ratio Test) for real-time anomaly detection on live payloads.

### Enforcement mechanism

- **10K prompt-injection test suite:** A Docker container runs 10,000 adversarial prompt-injection attacks against the agent. Every PR must pass the full suite with zero breaches. The suite is continuously expanded.
- **Mahalanobis distance anomaly detection:** Inbound payloads are embedded into a feature space. The Mahalanobis distance from the distribution centroid is computed. Payloads exceeding a threshold are flagged.
- **SPRT (Sequential Probability Ratio Test):** During live execution, the SPRT monitors a running statistic (token usage, response length, timing patterns). If the log-likelihood ratio crosses the upper threshold (SPRT Lambda > threshold_b), the session is flagged as malicious and VDS 50000 annihilates it immediately.
- **CI/CD gate:** The Red Queen Sentry runs in CI. A PR that introduces a vulnerability detected by the 10K suite cannot merge.

### What crosses it

- **Inbound:** Authenticated requests from VDS 60000 (JWT validated, but payload not yet scanned).
- **Outbound:** Adversarially cleared payloads passed to VDS 40000 for PII scrubbing.

### What's blocked

- Prompt-injection payloads (detected by the 10K test patterns and Mahalanobis anomaly detection).
- Statistically anomalous requests (Mahalanobis distance above threshold).
- Malicious execution patterns (SPRT threshold breach triggers VDS 50000 annihilation).
- Any payload that matches a known attack signature from the Red Queen corpus.

---

## VDS 90000 — Pool of Tears

### Purpose

Persistent void socket storage for epistemic gaps. This is where the system logs what it *doesn't* know. When the epistemic boundary evaluator scores confidence below the silence threshold, the reasoning gap is persisted to the Pool of Tears — not as a failure, but as an honest record of ignorance that can inform future learning.

### What it is

- A persistent storage layer (SQLite with FTS5 full-text search) that records:
  - The query that couldn't be answered with sufficient confidence.
  - The confidence score and the threshold it failed to meet.
  - The context that was available (scrubbed, hashed).
  - A timestamp and run identifier.
- Entries in the Pool of Tears are candidates for future learning. The self-reflection and self-improvement loops can review void socket entries to identify knowledge gaps and prioritize capability evolution.

### Enforcement mechanism

- **Write-only from the brain:** VDS 00000 can write to the void socket but cannot read from it during a live task. Reading void socket entries happens only in the self-reflection loop, which runs post-task (after VDS 50000 wipe).
- **Scrubbed entries:** All void socket entries are PII-scrubbed (by VDS 40000) before persistence. No raw user data is stored.
- **Sovereign socket contract:** The void socket interface defines a strict write-then-forget contract during task execution.

### What crosses it

- **Inbound:** Epistemic gap records from VDS 00000 (low-confidence reasoning outcomes, scrubbed and hashed).
- **Outbound:** Void socket entries read by the self-reflection loop during post-task analysis (never during live execution).

### What's blocked

- Raw PII in void socket entries (scrubbed by VDS 40000 before persistence).
- Live-task reads from the void socket (the brain cannot query its own ignorance during a task — only after the VDS 50000 wipe, in the reflection phase).
- Deletion of void socket entries by the brain (entries are immutable once written; pruning is a separate maintenance operation governed by the consolidation logic).

---

## Layer Ordering — The Ingress Chain

```
External Surface
    │
    ▼
VDS 60000 (Rabbit Hole)     ── authentication
    │
    ▼
VDS 80000 (Red Queen's Court) ── adversarial scan
    │
    ▼
VDS 40000 (Macrodata)        ── PII scrub
    │
    ▼
VDS 00000 (Brain)            ── reasoning
    │
    ├──▶ OUTPUT
    ├──▶ SILENCE
    └──▶ VDS 90000 (Pool of Tears) ── epistemic gap logging
    │
    ▼
VDS 50000 (Elevator)         ── session wipe
    │
    ▼
Next task (clean slate)
```

**No layer may be skipped. No layer may be reordered. No layer may be disabled.**

---

## Cross-Layer Interactions

| From | To | Interaction | Trigger |
|------|----|-------------|---------|
| VDS 80000 | VDS 50000 | Annihilation trigger | SPRT threshold breach during execution |
| VDS 60000 | VDS 50000 | Annihilation trigger | MCP disconnect detected |
| VDS 00000 | VDS 90000 | Void socket write | Confidence below silence threshold |
| VDS 00000 | VDS 40000 | Scrub request | FS read required (rare; most data is pre-scrubbed) |
| VDS 50000 | VDS 90000 | Post-wipe reflection | Self-reflection loop reads void socket entries |

---

## Related Documents

| Document | Purpose |
|----------|---------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Full system architecture map and verification |
| [DECISION_LOG.md](../DECISION_LOG.md) | ADR-013 (VDS Topology Preservation), ADR-014 (Dockerized Red Queen), ADR-015 (Cloudflare Worker MCP OAuth Gate) |
| [RESEARCH/red-queen-results.md](./RESEARCH/red-queen-results.md) | Red Queen 10K test iteration log |

---

*The VDS topology is immutable. Changing a layer's purpose, ordering, or enforcement mechanism requires an ADR and independent architectural review.*
