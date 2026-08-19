# Contributing to NoUsClaWW

Thank you for your interest in contributing to NoUsClaWW. This document defines the rules and process for all contributors — human and AI.

---

## Good Starter Tasks (Well-Scoped, Isolated)

1. **Git signals** — capture git commits into memory (subprocess + EventLog)
2. **Code AST extraction** — Python `ast` module to graph entities (pure stdlib)
3. **Person extraction** — regex + dict, no ML (clear spec from PMB's persons.py)
4. **Dashboard** — stdlib HTTP server + HTML (self-contained, reads from modules)
5. **Memory graph visualization** — standalone HTML, canvas-based (no CDN)
6. **Doctor CLI** — health check that runs existing diagnostics
7. **Workspace encryption** — AES + scrypt (self-contained crypto module)
8. **Git sync** — push/pull workspace to git remote (subprocess + file copy)
9. **Language packs** — YAML files with regex patterns (no code changes)
10. **Multilingual embedding** — config option to switch embedding model

---

## How to Contribute

1. Find an issue with the `contrib` label (or `study-branch` for research)
2. Comment on the issue saying you're working on it
3. Fork the repo, create a branch, write a failing test first
4. Implement until the test passes
5. Run the full test suite: `pytest tests/ -v`
6. Create a PR referencing the issue
7. Respond to review feedback

---

## Development Setup

```bash
git clone https://github.com/nousloopsolutions/NoUsClaWW.git
cd NoUsClaWW
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest
```

### Run Red Queen 10K Test Suite

```bash
docker build -t nousclaww-red-queen ./red_queen_sentry
docker run --rm nousclaww-red-queen
```

---

## Conventions

- **NCL blocks:** Every file has an NCL block with a `#C` credit field attributing the source
- **Failing-test-first:** Every feature starts with a failing test
- **Every decision is documented** in `DECISION_LOG.md`
- **Every source is credited** in `ATTRIBUTIONS.md`
- **No secrets in code** (secret redaction at write boundary)
- **No cloud dependencies by default** (local-first)
- **Type hints:** All public functions must have type annotations
- **Docstrings:** All public modules, classes, and functions must have docstrings
- **No emojis in code, commit messages, or PR titles**

---

## Open Core Boundary Rules

| Zone | Path | Visibility | Import Rule |
|------|------|-----------|------------|
| Community | `/src/community/` | Public, MIT | May import `sovereign_sockets`. **May NOT import `proprietary_core`.** |
| Sovereign Sockets | `/src/sovereign_sockets/` | Public, MIT | Immutable. Changes require an ADR. No internal deps except standard library. |
| Proprietary Core | `/proprietary_core/` | Gitignored | Implements sovereign socket interfaces. Never imported by community code. |

CI runs a dependency-graph check on every PR. If community code imports from `proprietary_core`, the build fails.

---

## Security Rules for Contributors

1. **Red Queen Sentry scans all untrusted input** — every PR body, issue body, and external contribution is scanned for prompt injection. A single breach in 10,000 iterations blocks the merge.
2. **GITHUB_TOKEN read-only for untrusted-input workflows** — no write permissions on workflows that process PR text or issue bodies.
3. **JWT signature verification required** — the Cloudflare Worker MCP OAuth Gate must verify JWT signatures cryptographically.
4. **No un-scrubbed data reaches the brain** — all data must pass through VDS 40000 (PII scrub + HMAC hash).

---

## Branch Protection

The `main` branch is protected:
- **PR required** — no direct commits to `main`
- **1 approving review required** — from an independent reviewer
- **Red Queen 10K Test must pass** — the CI check is mandatory
- **Linear history** — no merge commits, rebase only
- **No force pushes** — history is immutable
- **No branch deletion** — `main` cannot be deleted

---

## Related Documents

- [AGENTS.md](AGENTS.md) — Agent development rules and project identity
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Full architecture map
- [DECISION_LOG.md](DECISION_LOG.md) — Architecture Decision Records
- [ATTRIBUTIONS.md](ATTRIBUTIONS.md) — Source project attributions
