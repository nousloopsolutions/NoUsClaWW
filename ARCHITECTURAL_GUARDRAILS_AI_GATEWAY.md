# NoUsClaWW Architectural Guardrails — AI Gateway

**Status:** Canonical — referenced by `.nous-governance.md`.
**Authority:** Brent (hovlandbr) — sole merge authority.

---

## 1. Purpose

Define the architectural guardrails that constrain all AI agent operations on NoUsClaWW. These guardrails are the boundary between agent autonomy and safety. No agent may exceed these guardrails. No automation may relax them.

## 2. VDS Layer Enforcement

All agent operations must respect the six Virtual Severance Domains:

| VDS | Name | Purpose | Agent Access |
|-----|------|---------|--------------|
| 00000 | Brain | Core logic, agent loop | Read-only via sovereign sockets |
| 40000 | Macrodata | Data refinement, memory | Read-only via sovereign sockets |
| 50000 | Elevator | Session wipe, ephemeral context | Enforced by CI, no agent access |
| 60000 | Rabbit Hole | OAuth gate, MCP rug pull defense | Enforced by Cloudflare Worker |
| 80000 | Red Queen | CI/CD gatekeeper, injection scanning | CI-enforced, no agent bypass |
| 90000 | Pool of Tears | Void socket, ignorance mapping | Read-only via sovereign sockets |

## 3. AI Gateway Guardrails

### 3.1 Path Scope (RBAC)

- All agent writes must target files within the NoUsClaWW repository root
- No path traversal (`..`) outside repository
- No writes to `/proprietary_core/` (gitignored)
- No writes to `/src/sovereign_sockets/` without ADR reference

### 3.2 File Type Allowlist

Agents may only modify these file types:

- `.py` — Python source
- `.ts` — TypeScript source
- `.js` — JavaScript source
- `.json` — JSON configuration
- `.md` — Markdown documentation
- `.css` — CSS styles
- `.html` — HTML templates
- `.toml` — TOML configuration
- `.yaml` / `.yml` — YAML configuration
- `.sh` — Shell scripts
- `.ps1` — PowerShell scripts
- `Dockerfile` — Docker build files
- `requirements.txt` — Python dependencies

### 3.3 Secret Prevention

- No `password=`, `api_key=`, `secret=` literals in committed code
- No `.env` files committed
- No credentials in workflow files
- Secrets only in GitHub encrypted secrets
- CI scans for secrets on every push

### 3.4 PII Prevention

- No unredacted email addresses in code
- No SSN, credit card, or personal identifiers in code
- No private personal, student, medical, or identifiable training data
- Use of such data requires `BLOCKED_BRENT_AUTHORITY`

### 3.5 Core File Protection

The following files require explicit review and cannot be auto-merged:

- `AGENTS.md` — agent rules
- `.nous-governance.md` — governance charter
- `NOUS_INVARIANT.md` — immutable invariants
- `NOUS_PROCESS_CHARTER.md` — process commitments
- `ARCHITECTURAL_GUARDRAILS_AI_GATEWAY.md` — this file
- Any file in `/src/sovereign_sockets/`
- Any file in `/.github/workflows/`

### 3.6 Command Execution Guardrails

- No agent may execute destructive commands (`rm -rf`, `git push --force`, `git reset --hard`)
- No agent may install system-level dependencies without Brent authorization
- No agent may make external API calls that modify state (only read-only research)
- No agent may deploy to production
- No agent may rotate secrets or credentials

## 4. CI-Enforced Guardrails

### 4.1 Red Queen Sentry (VDS 80000)

- 10,000 prompt injection attempts per CI run
- Zero breaches required (zero tolerance)
- Docker container: `--network=none --read-only --memory=512m --cpus=1.0`
- Non-root user (uid 10001), no sudo
- Mahalanobis/SPRT anomaly detection on all inbound payloads

### 4.2 Injection Scan

- Issues and PRs scanned for prompt injection
- Silent drop — no acknowledgment to attacker
- `GITHUB_TOKEN` is read-only for all untrusted-input workflows
- No workflow with write permissions processes issue/PR text

### 4.3 Nous-Hub Agent Verification

- Axiom-synth loop integrity verified
- Epistemic boundary tests pass
- Fallacy compendium self-audit passes
- Scientific rigor framework enforced
- DAG integrity (no cross-zone imports)
- Sovereign socket immutability checked
- Full test suite passes

## 5. Open Core Boundary Enforcement

```text
/src/community/       → Public, agent-writable via PR
/src/sovereign_sockets/ → Immutable interface, ADR required
/proprietary_core/    → Private, gitignored, never committed
```

CI enforces:
- No file in `/src/community/` imports from `proprietary_core`
- No file in `/proprietary_core/` is committed
- Changes to `/src/sovereign_sockets/` trigger ADR requirement warning

## 6. Guardrail Violation Handling

| Violation | Response |
|-----------|----------|
| Path traversal attempt | Block write, log breach, notify Brent |
| Disallowed file type | Block write, log breach |
| Secret detected | Block commit, log breach, notify Brent |
| PII detected | Block commit, flag for review |
| Core file modification | Require explicit review, no auto-merge |
| Destructive command | Block execution, log breach |
| DAG violation (cross-zone import) | CI fails, PR returned to builder |
| Sovereign socket change without ADR | CI warning, review required |
| Injection scan breach | CI fails, zero tolerance, PR blocked |

## 7. Agent Capability Matrix

| Capability | Builder | Review | Verification | Adjudication | Brent |
|------------|---------|--------|-------------|-------------|-------|
| Create branches | Yes | No | No | No | Yes |
| Push code | Yes | No | No | No | Yes |
| Open PRs | Yes | No | No | No | Yes |
| Review PRs | No | Yes | No | No | Yes |
| Run CI gates | No | No | Yes | No | Yes |
| Post READY_FOR_PHASE_REVIEW | No | No | No | Yes | Yes |
| Merge to main/alpha | No | No | No | No | Yes |
| Change axioms | No | No | No | No | Yes |
| Deploy to production | No | No | No | No | Yes |
| Rotate secrets | No | No | No | No | Yes |
