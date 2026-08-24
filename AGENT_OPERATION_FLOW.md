# NoUsClaWW Agent Operation Flow

**Status:** Canonical — referenced by `.nous-governance.md`.
**Authority:** Brent (hovlandbr) — sole merge authority.

---

## 1. Purpose

Define the step-by-step workflow for all agents operating on NoUsClaWW. The flow is maximally automated — Brent only touches the final merge decision.

## 2. Standard Operation Flow

```text
1. RECEIVE TASK (GitHub issue with phase label)
2. CREATE BRANCH (from main or alpha)
3. IMPLEMENT (write code + tests, failing-test-first)
4. LOCAL TEST (pytest, lint, type check)
5. PUSH + OPEN PR
6. CI GATES RUN AUTOMATICALLY
   ├── CI / Lint (ruff)
   ├── CI / Type Check (mypy)
   ├── CI / Unit Tests (pytest)
   ├── Nous-Hub Agent Verification (axiom-synth, epistemic, DAG, sovereign sockets)
   └── Injection Scan (issue/PR text)
7. IF CI FAILS → fix autonomously, push, retest (loop)
8. CI GREEN → freeze CANDIDATE_SHA
9. AUTOMATED REVIEW (axiom-synth, DAG, security checks)
10. REVIEW AGENTS (independent, blinded, sealed)
11. RESOLVE FINDINGS (fix blocking issues, push evidence-only changes)
12. ADJUDICATION (final readiness assessment)
13. POST READY_FOR_PHASE_REVIEW
14. BRENT REVIEWS → MERGE or RETURN
```

## 3. Task Reception

- Tasks arrive as GitHub issues with phase labels (`phase-0` through `phase-13`)
- Agent reads the issue, linked plan, and relevant docs
- Agent creates a branch named `phase-N/feature-description`

## 4. Implementation Rules

- **Failing-test-first** — write the test that fails before writing the feature
- **Exact-SHA evidence** — all evidence references exact commit SHAs
- **Local-first development** — all testing is local before pushing
- **NCL blocks** — adapted code includes `#C` attribution comments
- **SYNTH blocks** — synthetic code includes `#SYNTH:` provenance comments

## 5. CI Gate Rules

- CI gates are **mandatory** and **zero-tolerance**
- Injection scan: silent drop, no acknowledgment to attacker
- GITHUB_TOKEN: read-only for all untrusted-input workflows
- If any gate fails, the agent fixes and retries autonomously

## 6. Autonomous Repair Loop

When CI or review finds issues:

1. Read the failure output
2. Identify root cause
3. Implement minimal fix
4. Test locally
5. Push fix
6. Wait for CI
7. Repeat until green

**No Brent notification for ordinary failures.** Only post when `READY_FOR_PHASE_REVIEW` or `BLOCKED_BRENT_AUTHORITY`.

## 7. Evidence Recording

After CI is green on CANDIDATE_SHA:

- Record `BASE_SHA` (branch point from main/alpha)
- Record `CANDIDATE_SHA` (first green CI commit)
- No executable/workflow changes after freeze
- Evidence-only changes (docs, test evidence) allowed after freeze
- Record `EVIDENCE_HEAD_SHA` (final evidence commit)

## 8. Ready-for-Review Format

```text
READY_FOR_PHASE_REVIEW
PHASE: <phase number>
BASE_SHA: <sha>
CANDIDATE_SHA: <sha>
EVIDENCE_HEAD_SHA: <sha>
HEALTHY_CI_RUN: <run URL>
REVIEW_SUMMARY: <link to review evidence>
NON_BLOCKING_DEFERRALS: <list or none>
MERGE_AUTHORITY: BRENT_ONLY
```

## 9. Blocked Format

```text
BLOCKED_BRENT_AUTHORITY
REASON: <one of: merge, deploy, secret, axiom change, destructive, conflict>
BLOCKER: <description>
ATTEMPTED: <what was tried>
EVIDENCE: <links>
```

## 10. Event-Driven Execution

- No permanent polling
- GitHub events trigger bounded sessions
- Permitted triggers: trusted label, ready-for-review transition, CI failure, manual dispatch
- `pull_request.opened` and `pull_request.synchronize` are NOT triggers (prevents mirror loops)
- One session per PR head SHA, max 3 sessions per PR
