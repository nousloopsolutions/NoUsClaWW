# NoUsClaWW Invariants

**Status:** Canonical — referenced by `.nous-governance.md`.
**Authority:** Brent (hovlandbr) — sole merge authority.
**Nature:** These invariants are immutable. No agent, review, automation, or phase may override them. Any change requires explicit Brent authorization via `BLOCKED_BRENT_AUTHORITY`.

---

## 1. Prime Axiom Invariant

**Safety Through Severance** — the agent never directly handles untrusted data. Six VDS layers enforce isolation. This is the highest-order invariant; all other invariants derive from it.

## 2. Open Core Boundary Invariants

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| INV-OC-1 | `/proprietary_core/` is gitignored and never committed | .gitignore + CI check |
| INV-OC-2 | `/src/community/` code never imports from `/proprietary_core/` | DAG integrity CI check |
| INV-OC-3 | `/src/sovereign_sockets/` changes require ADR reference | CI check + review |
| INV-OC-4 | The boundary between Tool (public) and Brain (private) is immutable | Architectural review |

## 3. Security Invariants

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| INV-SEC-1 | Red Queen 10K test must have zero breaches | CI gate (zero tolerance) |
| INV-SEC-2 | Untrusted-input workflows use read-only GITHUB_TOKEN | CI verification step |
| INV-SEC-3 | No workflow with write permissions handles issue/PR text | CI check |
| INV-SEC-4 | Docker containers run as non-root (uid 10001) | CI verification |
| INV-SEC-5 | Docker containers have no sudo access | CI verification |
| INV-SEC-6 | Docker containers run with --network=none --read-only | CI workflow |
| INV-SEC-7 | Injection scan silently drops malicious payloads | sentry.py exit 0 always |

## 4. Epistemic Boundary Invariants

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| INV-EB-1 | Agent never claims confidence it cannot justify | Epistemic boundary tests |
| INV-EB-2 | Agent stays silent on low-confidence outputs | Silence Protocol |
| INV-EB-3 | Agent logs knowledge gaps, never hides them | Void socket mapping |
| INV-EB-4 | Agent cannot be bullied into false confidence | Dynamic confidence thresholds |
| INV-EB-5 | Agent never deceives the user | Honesty Model |

## 5. Axiom-Synth Loop Invariants

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| INV-AS-1 | Three-layer self-regulation (internalize, reflex, guardrail) is immutable | axiom_synth.py tests |
| INV-AS-2 | Bayesian Integrity Tracker produces calibrated trust scores | Integrity tracker tests |
| INV-AS-3 | 26 logical fallacies are self-scanned before output | Fallacy compendium tests |
| INV-AS-4 | 12 scientific method principles are enforced | Scientific rigor tests |

## 6. Branch Protection Invariants

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| INV-BP-1 | `main` requires 2 approving reviews | GitHub ruleset |
| INV-BP-2 | `alpha` requires 2 approving reviews | GitHub ruleset |
| INV-BP-3 | Linear history required on `main` and `alpha` | GitHub ruleset |
| INV-BP-4 | No force push to `main` or `alpha` | GitHub ruleset |
| INV-BP-5 | All 3 CI checks required on `main` and `alpha` | GitHub ruleset |
| INV-BP-6 | Review thread resolution required | GitHub ruleset |

## 7. Agent Separation Invariants

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| INV-SEP-1 | No agent reviews its own code | Agent Separation Protocol |
| INV-SEP-2 | No agent approves its own PR | Agent Separation Protocol |
| INV-SEP-3 | Adjudication runs after sealed reviews | Phase workflow |
| INV-SEP-4 | Builder cannot supply independent verdict | Agent Separation Protocol |

## 8. Communication Invariants

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| INV-COM-1 | PR comments only for terminal states | Process charter |
| INV-COM-2 | No progress chatter or per-commit updates | Process charter |
| INV-COM-3 | Evidence is SHA-bound and immutable | Phase workflow |
| INV-COM-4 | Control comments only from trusted allowlist | Control comment safety |

## 9. Violation Handling

Any invariant violation:

1. The action is immediately blocked
2. The violation is logged as a governance breach
3. Brent is notified via `BLOCKED_BRENT_AUTHORITY`
4. The violating change is reverted or the PR is returned to builder
5. No automation may override, suppress, or bypass an invariant
