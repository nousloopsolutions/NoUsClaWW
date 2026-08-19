# Attributions

NoUsClaWW builds on ideas, patterns, and code from several open-source projects. We stand on the shoulders of giants. Every source is credited here and in the `#C` credit field of each file's NCL block.

---

## Source Projects

| Project | Repository | License | What We Borrowed |
|---------|-----------|---------|-----------------|
| **PMB** | [oleksiijko/pmb](https://github.com/oleksiijko/pmb) | Apache-2.0 | 24 features: auto-recall, autowrite, correction capture, session restore, exploration capture, followcheck, consolidation, auto-consolidate, distill lessons, rehearse, self-test, adaptive importance, earned memory, feedback, conflicts, circuit breaker, LLM budget, token budget, compression policy, memory tiers, decay, importance scoring, storage compaction, SLOs |
| **Hermes Agent** | — | TBD | Frozen snapshot pattern, SQLite session DB design, learning loop concept |
| **OpenClaw** | — | TBD | Mad Dog Mode concept, capability-evolver, self-learning |
| **cua-driver** | [trycua/cua](https://github.com/trycua/cua) | MIT | Desktop control via CLI/SDK wrapper |
| **Zep/Graphiti** | [getzep/graphiti](https://github.com/getzep/graphiti) | Apache-2.0 | Bi-temporal knowledge graph concept |
| **Argus** | — | TBD | Self-correcting retrieval fallback concept |
| **Nous-hub** | (private) | MIT | 14 original source files |

---

## Per-File Credits

Each source file includes a `#C` credit field in its NCL block documenting origin, license, and adaptations made. See the [NCL Block Adaptation Rules](https://github.com/nousloopsolutions/NoUsClaWW/wiki/Process) in the wiki for the full table.

### Key Adaptations

- **PMB-derived files** (Apache-2.0 → MIT compatible): standalone versions with Nous-hub-specific paths removed, English-only regex patterns, nousclaww MemoryManager/LLMRouter integration
- **Nous-hub-derived files** (MIT): stripped `#L/#R` NCL lines, "Brent" → "user" in comments, genericized system prompts, added epistemic boundary directive
- **New modules** (original NoUsClaWW design): `epistemic_boundary.py`, `model_profiler.py`, `red_queen_sentry/` (Dockerfile, sentry.py, mcts_evaluator.py, sanitization.py, axiom_synth.py, fallacy_compendium.py, scientific_rigor.py), `rabbit_hole/` (Cloudflare Worker MCP OAuth Gate)

---

## Scientific Grounding

The Red Queen Sentry's statistical anomaly detection is grounded in:
- **Mahalanobis distance** — Mahalanobis, P.C. (1936). On the generalised distance in statistics.
- **SPRT** — Wald, A. (1947). Sequential Analysis. Dover Publications.
- **Trust calibration** — Lee, J.D. & See, K.A. (2004). Trust in automation: Designing for appropriate reliance. Human Factors, 46(1), 50-80.
- **MCP security** — Netskope MCP blog; arXiv 2603.21642

The Axiom-Synth Loop is grounded in:
- **Values-based decision theory** — Schwartz, S.H. (2012). An Overview of the Schwartz Theory of Basic Values.
- **Dual-process cognition** — Kahneman, D. (2011). Thinking, Fast and Slow.

The Scientific Rigor Framework references:
- Popper (1934), Feynman (1974), Ioannidis (2005), Nosek et al. (2015), Goodhart (1975), Kerr (1998), Cohen (1988), Hill (1965)

---

## License Compatibility

NoUsClaWW is MIT-licensed. PMB's Apache-2.0 license is compatible with MIT. All PMB-derived code is attributed and complies with Apache-2.0's attribution requirements. All original NoUsClaWW code is MIT.
