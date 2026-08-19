# NoUsClaWW Review Protocol

**Status:** Canonical — referenced by `.nous-governance.md`.
**Authority:** Brent (hovlandbr) — sole merge authority.

---

## 1. Purpose

Define the review protocol for all PRs targeting `main` or `alpha`. Reviews are automated by default. Brent reviews only the final merge decision.

## 2. Review Pipeline

```
PR Opened
  -> CI Gates (Red Queen 10K, Nous-Hub Verification, Injection Scan)
  -> Automated Code Review (axiom-synth, DAG, sovereign socket checks)
  -> Review Thread Resolution (all comments must be resolved)
  -> 2 Approving Reviews Required
  -> READY_FOR_PHASE_REVIEW
  -> Brent merge decision
```

## 3. Verdict Vocabulary

All reviewers (automated and human) use the same fail-closed verdict vocabulary:

- **`PASS_TO_NEXT_STAGE`** — all checks passed, proceed to next gate
- **`CONDITIONAL_PASS`** — non-blocking findings noted, proceed with deferrals
- **`RETURN_TO_BUILDER`** — blocking findings, must fix before resubmission

## 4. Automated Review Checks

### 4.1 Security Gates (CI-enforced, zero tolerance)

- **Red Queen 10K Test** — 10,000 prompt injection attempts, zero breaches
- **Injection Scan** — issue/PR text scanned for prompt injection
- **GITHUB_TOKEN Read-Only** — untrusted-input workflows must not have write permissions

### 4.2 Architecture Gates

- **DAG Integrity** — no community code imports proprietary core
- **Sovereign Socket Immutability** — changes to `/src/sovereign_sockets/` require ADR reference
- **Open Core Boundary** — no file in `/proprietary_core/` may be committed

### 4.3 Quality Gates

- **Axiom-Synth Loop** — three-layer self-regulation integrity verified
- **Epistemic Boundary** — Sherlock Protocol, Silence Protocol, void sockets tested
- **Fallacy Compendium** — 26 logical fallacy self-audit passes
- **Scientific Rigor** — 12 scientific method principles enforced
- **Full Test Suite** — all tests in `tests/` pass

## 5. Review Requirements by Branch

| Branch | Required Reviews | Required CI Checks | Linear History |
|--------|-----------------|---------------------|----------------|
| `main` | 2 approving | All 3 CI gates | Yes |
| `alpha` | 2 approving | All 3 CI gates | Yes |

## 6. Review Thread Resolution

- All review comments must be resolved before merge
- Stale reviews are dismissed on new pushes
- Unresolved threads block merge automatically

## 7. Brent-Only Decisions

The following require explicit Brent approval and cannot be automated:

- Merge to `main`
- Production deployment or DNS change
- Secret or credential rotation
- Change to a prime axiom, architecture decision, or privacy boundary
- Destructive or irreversible operation
- Use of private/personal/identifiable data

## 8. What Does NOT Require Brent

Agents handle autonomously:

- Test failures (fix and retest)
- Lint/type/format violations (fix and retest)
- Dependency upgrades with safe semver (Dependabot PRs)
- Documentation drift (fix and push)
- CI syntax errors (fix and push)
- Code defects found in review (fix and push)
- Evidence mistakes (fix and push)

## 9. Communication

- Do not post progress chatter or per-commit updates
- Post a PR comment only when `READY_FOR_PHASE_REVIEW` or `BLOCKED_BRENT_AUTHORITY`
- Keep working notes in GitHub, not in chat
- Consolidate evidence into one final packet
