# NoUsClaWW Agent Separation Protocol

**Status:** Canonical — referenced by `.nous-governance.md`.
**Authority:** Brent (hovlandbr) — sole merge authority.

---

## 1. Purpose

Enforce strict separation between agent roles to prevent self-review, self-approval, or circular verification. No agent may both build and approve its own work.

## 2. Role Definitions

### Builder Agent

- Creates feature branches from `main` or `alpha`
- Implements code, writes tests, fixes defects
- Pushes commits and opens PRs
- **Cannot** approve PRs, pass CI gates, or post `READY_FOR_PHASE_REVIEW`

### Review Agent

- Performs independent code review on PRs
- Checks for axiom violations, DAG breaches, security issues
- Posts review verdicts (`PASS_TO_NEXT_STAGE`, `CONDITIONAL_PASS`, `RETURN_TO_BUILDER`)
- **Cannot** build or push code to the PR branch
- **Cannot** merge

### Verification Agent (Nous-Hub)

- Runs CI gates: Red Queen 10K, injection scan, axiom-synth checks
- Verifies DAG integrity, sovereign socket immutability
- Posts check run results
- **Cannot** approve PRs or merge
- **Cannot** modify code

### Adjudication Agent

- Runs after all reviews are sealed
- Performs final readiness assessment
- Posts `READY_FOR_PHASE_REVIEW` or `BLOCKED_BRENT_AUTHORITY`
- **Cannot** merge
- **Cannot** supply its own independent verdict on code it built

### Brent (hovlandbr)

- Sole merge authority
- Reviews `READY_FOR_PHASE_REVIEW` packets
- Approves axiom/scope changes
- **Cannot** be bypassed by any automation

## 3. Separation Rules

1. **No self-review** — an agent that built the code cannot review it
2. **No self-approval** — an agent that opened the PR cannot approve it
3. **No circular verification** — Review Agent A cannot review Builder Agent A's work if they share the same session/context
4. **Sealed reviews** — all reviews are blinded initially; reviewers cannot see each other's verdicts until sealed
5. **Adjudication independence** — the adjudication agent runs after all reviews are sealed; it cannot be the builder

## 4. Context Isolation

- Each agent role operates in a separate session/context
- Builder sessions cannot access review tools
- Review sessions cannot push code
- Verification sessions are stateless CI runs
- Adjudication sessions are launched separately from review sessions

## 5. Provenance Tracking

Every review and verdict must record:

- Agent identity (role + session ID)
- Candidate SHA being reviewed
- Timestamp
- Verdict
- Blocking findings (if any)

## 6. Violation Handling

If separation is violated:

- The review is invalidated
- The PR is returned to builder
- The violation is logged as a governance breach
- Brent is notified via `BLOCKED_BRENT_AUTHORITY`
