# NoUsClaWW Process Charter

**Status:** Canonical — referenced by `.nous-governance.md`.
**Authority:** Brent (hovlandbr) — sole merge authority.

---

## 1. Commitments

This charter binds all agents operating on NoUsClaWW to the following process commitments:

### 1.1 Automation First

- Agents handle all routine work autonomously: tests, lint, dependencies, docs, CI fixes
- Brent touches only final merge decisions and authority-reserved actions
- No agent asks Brent to choose between routine technical remedies
- Agents select the smallest safe, reversible fix and verify it

### 1.2 Evidence Over Assertions

- Every claim is backed by exact-SHA evidence
- Every test result links to a CI run URL
- Every review verdict names the candidate SHA
- No agent claims readiness while a required field is missing, stale, or inferred

### 1.3 Minimal Communication

- PR comments only for terminal states: `READY_FOR_PHASE_REVIEW` or `BLOCKED_BRENT_AUTHORITY`
- No progress chatter, no per-commit updates, no status pings
- Working notes stay in GitHub (issues, PRs, evidence files), not in chat
- One final packet per phase; link details instead of reproducing logs

### 1.4 Failing-Test-First

- Write the test that fails before writing the feature
- Never merge a test that was written after the code it tests
- Tests are evidence; code is hypothesis

### 1.5 Reproducibility

- Every phase is reproducible from its evidence packet
- Exact SHAs, exact commands, exact environment
- Anyone with the repo can reproduce the results

## 2. Development Rules

### 2.1 Branch Naming

- Feature branches: `phase-N/feature-description`
- Fix branches: `fix/description`
- CI branches: `ci/description`
- Doc branches: `docs/description`

### 2.2 Commit Messages

- Conventional commits: `type(scope): description`
- Types: `feat`, `fix`, `deps`, `ci`, `docs`, `test`, `refactor`
- Dependabot commits: auto-generated with `deps(scope):` prefix

### 2.3 Code Conventions

- Python: PEP 8, type hints, docstrings on public functions
- NCL blocks: adapted code includes `#C` attribution
- SYNTH blocks: synthetic code includes `#SYNTH:` provenance
- No comments deleted unless explicitly requested
- No emojis in code or documentation unless explicitly requested

### 2.4 Testing

- All tests in `tests/` directory
- Test files: `test_*.py`
- Test functions: `test_*`
- pytest with `-v --tb=short`
- 100% pass rate required for merge

## 3. Open Core Rules

- `/src/community/` — public, community-contributable
- `/proprietary_core/` — private, gitignored, never committed
- `/src/sovereign_sockets/` — immutable interface, ADR required for changes
- No community code imports proprietary code (CI-enforced DAG)

## 4. Security Rules

- Red Queen 10K: zero breaches, zero tolerance
- GITHUB_TOKEN: read-only for untrusted-input workflows
- Docker: non-root (uid 10001), no sudo, no network, read-only filesystem
- Injection scan: silent drop, no acknowledgment to attacker
- No secrets in code, ever

## 5. Branch Protection

- `main`: 2 reviews, 3 CI checks, linear history, no force push
- `alpha`: 2 reviews, 3 CI checks, linear history, no force push
- CODEOWNERS: all paths owned by `@nousloopsolutions`
- Stale reviews dismissed on push
- Review thread resolution required

## 6. Dependabot

- Weekly updates: Monday 06:00 CST
- Ecosystems: pip (root + sentry), docker, github-actions
- Grouped by category (dev, scientific, web)
- All PRs assigned to `nousloopsolutions` for review
- Safe semver upgrades handled autonomously by agents

## 7. Violation Consequences

- Invariant violation: action blocked, breach logged, Brent notified
- Separation violation: review invalidated, PR returned
- Communication violation: comment removed, agent reminded
- No automation may override, suppress, or bypass any rule in this charter
