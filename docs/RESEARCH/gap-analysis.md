# Gap Analysis — NoUsClaWW vs Inspiration Sources

> **Living document.** Updated as modules are audited against their inspiration sources.
> This is the completeness measure: every NoUsClaWW module compared against the open source code that does the same function.

---

## Audit Methodology

Every module in NoUsClaWW is compared against:
1. **The plan** — what the plan said to build
2. **The inspiration source** — the actual open source code that does the same function
3. **Innovations found** — features in the inspiration that NoUsClaWW should fold in
4. **Gaps** — what NoUsClaWW is missing that the inspiration has

---

## Source Code Audited

### nous_memory_mcp/novel/ (5 modules — VSA-enhanced memory)
| Module | Innovation | NoUsClaWW Integration | Priority |
|---|---|---|---|
| **danger_filter.py** | Vector-based hallucination detection with redirect (3 modes: filter/flag/redirect). Three-layer defense: text, vector, structural. | Add to hybrid_retrieval.py as post-processing filter | **HIGH** |
| **crystallization.py** | Access-frequency vector sharpening (Hebbian learning). Adaptive alpha: `lr * (1 - 1/(1 + access_count))`. Preserves original vector for audit. | Add to memory_manager.py as memory maintenance | **MEDIUM** |
| **synth_index.py** | Structured metadata parsing (SYNTH blocks) + SQLite FTS5 indexing. Hybrid exact+fuzzy retrieval. Zero-AST regex parsing. | Add as new module: synth_index.py for codebase-aware retrieval | **HIGH** |
| **spreading_activation.py** | Implicit semantic multi-hop recall via BFS in VSA similarity space. Decay-bounded: `activation * sim * decay^depth`. No graph DB needed. | Add to hybrid_retrieval.py as associative retrieval mode | **MEDIUM** |
| **vsa_consolidation.py** | HRR bundling for gist vectors. Greedy clustering + cohesion metric. No LLM calls — pure math consolidation. | Add to health/consolidate.py as VSA-based alternative to LLM consolidation | **HIGH** |

### NoUs-fordge core/ (21 modules — VDS architecture)
#### FOLD INTO NOUSCLAWW (15 modules — public Tool innovations)
| Module | Innovation | NoUsClaWW Integration | Priority |
|---|---|---|---|
| **epistemic_gate.py** | Hard-threshold classifier (tau=0.65) with KNOWN/PARTIAL/UNKNOWN states. Stress sidecar (linear gradient, non-saturating). Geodesic distance scaling for emotional recall. | Fold into epistemic_boundary.py — add stress-modulated retrieval | **HIGH** |
| **right_to_silence.py** | Architectural invariant: UNKNOWN state MUST produce refusal. Fixed refusal messages (prevent fabrication). Append-only silence event logging. | Fold into epistemic_boundary.py — add fixed refusal messages + audit log | **HIGH** |
| **absence_detector.py** | Six gap types: pattern, connection, temporal, category, capacity, consistency. Puzzle Principle: gaps defined by surrounding context. Severity classification. | Add as new module: absence_detector.py for system health | **MEDIUM** |
| **danger_cones.py** | Three-layer hallucination defense: text (cheap), vector (semantic), structural (formal). Danger cones as VSA regions with redirect pointers. | Fold into epistemic_boundary.py or new module: danger_cones.py | **HIGH** |
| **hypothesis_lens.py** | Transient weight matrices for competing models. Three types: DIAGONAL, FULL, LOWRANK. Lenses NEVER mutate canon. Bayesian-smoothed confidence. | Add as new module: hypothesis_lens.py for A/B testing retrieval strategies | **LOW** |
| **thermometer.py** | Scalar encoder with fractional power binding (V=B^x). Strict domain clamping prevents phase wrapping. DecodedTelemetry separates value from plausibility. | Add to VSA encoder (if we add VSA layer) | **LOW** |
| **fhrr_engine.py** | 1024-D complex FHRR working memory. Mandatory unit-circle normalization after every bind/bundle. Unbind MUST pass through clean-up memory. | Add as new module: fhrr_engine.py (VSA core) | **MEDIUM** |
| **sam_encoder.py** | 10000-D binary XOR-HDC sensor array. Packed uint8 (64x memory reduction). Deterministic permutation bit-flip. HARD LIMIT = 1 concurrent sensor. | Add to VSA encoder (if we add VSA layer) | **LOW** |
| **hrr_math.py** | Foundation VSA math. 256-bit SHA-256 seed (no truncation). Integer Genesis (PCG64). Float32 quantization (cross-architecture determinism). | Add as new module: hrr_math.py (VSA foundation) | **MEDIUM** |
| **voxel_cube.py** | 4×4×4 spatial-temporal-state matrix (64 cells). Multi-axis encoding. find_best_voxel ArgMax for context routing. | Add to VSA encoder (if we add VSA layer) | **LOW** |
| **dimension_bridge.py** | Johnson-Lindenstrauss projection (10000→1024→32). Lazy matrix caching. 32-D tier documented as LOSSY. | Add to VSA encoder (if we add VSA layer) | **LOW** |
| **cleanup_memory.py** | Associative codebook for vector recovery. Matrix-based ArgMax (BLAS-accelerated). Label escaping (bijective). | Add to VSA core (if we add VSA layer) | **MEDIUM** |
| **monitoring.py** | Bounded histograms (O(1) insertion). Circular buffer time-series. Duck-typed result acceptance. | Add as new module: monitoring.py for observability | **MEDIUM** |
| **alerting.py** | Declarative threshold rules (data, not code). Suppression window (300s). Bounded alert history. | Add as new module: alerting.py for proactive health | **MEDIUM** |
| **golden_vector_tests.py** | Cross-platform determinism verification. Golden vector reference fixtures. Hash-chain persistence. | Add to tests/ for VSA determinism verification | **MEDIUM** |

#### SOVEREIGN SOCKET ONLY (4 modules — proprietary Brain logic)
| Module | Why It Stays as Interface Only |
|---|---|
| **canon_logic.py** | Golden Vector protection, Merkle chain integrity. Brain-side axiom anchor. |
| **confounding_detector.py** | Directive B — missing variable detection. Brain-side hypothesis generation. |
| **cheshire_scrubber.py** | Directive C — PII anonymization, HMAC pseudonymization, differential privacy. Brain-side privacy. |
| **holographic_memory.py** | Brain orchestrator wiring all 12 core modules. Tool-side should not replicate this. |

#### ALREADY COVERED (2 modules)
| Module | Status |
|---|---|
| **embeddings.py** | Deferred — heavy dependency (sentence-transformers). Not needed for core VSA. |
| **vector_store.py** | Deferred — heavy dependency (faiss). Not needed for core VSA. |

---

## Per-Module Audit (NoUsClaWW modules vs inspiration)

### Core Modules

#### control_state.py
- **Inspiration:** NoUs-fordge core/control_state.py
- **Plan:** Four-control state machine (OBSERVE/STORE/INFER/OUTPUT)
- **Status:** ADAPTED — needs audit against reference for missing features
- **Gaps:** TBD — read both files and compare

#### event_log.py
- **Inspiration:** NoUs-fordge observability/event_log.py
- **Plan:** Append-only SQLite event log
- **Status:** ADAPTED — needs audit
- **Gaps:** TBD

#### epistemic_boundary.py
- **Inspiration:** NoUs-fordge core/epistemic_gate.py + core/right_to_silence.py + plan (Axiom 4)
- **Plan:** Sherlock Protocol, Silence Principle, void sockets, dynamic threshold, anti-sycophancy
- **Status:** CREATED (1321 lines)
- **Gaps found from research:**
  - **MISSING:** Stress sidecar (linear gradient, non-saturating) from epistemic_gate.py
  - **MISSING:** Geodesic distance scaling for emotional recall from epistemic_gate.py
  - **MISSING:** Fixed refusal messages (prevent fabrication) from right_to_silence.py
  - **MISSING:** Append-only silence event logging from right_to_silence.py
  - **MISSING:** Three-layer danger cone defense from danger_cones.py
  - **MISSING:** Six absence detection types from absence_detector.py
- **Action:** Fold in stress sidecar, fixed refusals, silence audit log, danger cones

#### llm_interface.py
- **Inspiration:** NoUs-fordge core/llm_interface.py
- **Plan:** Ollama local LLM with epistemic boundary directive
- **Status:** ADAPTED — needs audit
- **Gaps:** TBD

#### llm_router.py
- **Inspiration:** NoUs-fordge core/llm_router.py
- **Plan:** Hybrid router (local→cloud) with circuit breaker
- **Status:** ADAPTED — needs audit
- **Gaps:** TBD

#### desktop_control.py
- **Inspiration:** NoUs-fordge core/desktop_control.py
- **Plan:** cua-driver wrapper, no focus steal
- **Status:** ADAPTED — needs audit
- **Gaps:** TBD

#### text_match.py
- **Inspiration:** PMB core/text_match.py
- **Plan:** Token matching utilities
- **Status:** CREATED — needs audit against PMB
- **Gaps:** TBD

#### circuit_breaker.py
- **Inspiration:** PMB circuit breaker pattern
- **Plan:** Process-wide breaker for flaky backends
- **Status:** CREATED (338 lines)
- **Gaps:** TBD — compare against PMB implementation

#### llm_budget.py
- **Inspiration:** PMB LLM budget pattern
- **Plan:** Wall-clock + call-count budget
- **Status:** CREATED (212 lines)
- **Gaps:** TBD

#### model_profiler.py
- **Inspiration:** Original (NoUsClaWW-specific)
- **Plan:** Detect model, probe capabilities, adaptive scaling
- **Status:** CREATED (590 lines)
- **Gaps:** TBD

### Memory Modules

#### session_db.py
- **Inspiration:** NoUs-fordge core/memory/session_db.py
- **Plan:** SQLite + FTS5 + tiers + decay + importance + void sockets
- **Status:** ADAPTED — needs audit
- **Gaps:** TBD — check if tiers/decay/importance were added (Phase 6)

#### knowledge_graph.py
- **Inspiration:** NoUs-fordge core/memory/knowledge_graph.py
- **Plan:** Temporal KG + conflict detection
- **Status:** ADAPTED — needs audit
- **Gaps:** TBD

#### hybrid_retrieval.py
- **Inspiration:** NoUs-fordge core/memory/hybrid_retrieval.py
- **Plan:** Vector + keyword + graph + self-correcting fallback
- **Status:** ADAPTED — needs audit
- **Gaps found from research:**
  - **MISSING:** Danger filter post-processing (from danger_filter.py)
  - **MISSING:** Spreading activation associative recall (from spreading_activation.py)
  - **MISSING:** Synth index structured metadata retrieval (from synth_index.py)
- **Action:** Add danger filter, spreading activation, synth index integration

#### memory_manager.py
- **Inspiration:** NoUs-fordge core/memory/memory_manager.py
- **Plan:** Unified manager with frozen snapshot pattern
- **Status:** ADAPTED — needs audit
- **Gaps found from research:**
  - **MISSING:** Crystallization (access-frequency vector sharpening from crystallization.py)
- **Action:** Add crystallization as memory maintenance operation

### Reflection Modules

#### self_reflection.py
- **Inspiration:** NoUs-fordge reflection/self_reflection.py
- **Plan:** Directive audit + EPISTEMIC_BOUNDARY directive
- **Status:** ADAPTED — needs audit
- **Gaps:** TBD

#### self_improvement.py
- **Inspiration:** NoUs-fordge reflection/self_improvement.py
- **Plan:** Gap → proposal → validation
- **Status:** ADAPTED — needs audit
- **Gaps:** TBD

#### mad_dog_loop.py
- **Inspiration:** NoUs-fordge reflection/mad_dog_loop.py
- **Plan:** Continuous self-healing + void socket scanning
- **Status:** BEING CREATED
- **Gaps:** TBD

### Hooks Modules

#### auto_recall.py
- **Inspiration:** PMB auto-recall pattern
- **Plan:** Regex intent classification, memory injection
- **Status:** CREATED — needs audit against PMB
- **Gaps:** TBD

#### autowrite.py
- **Inspiration:** PMB autowrite pattern
- **Plan:** Auto-record significant turns
- **Status:** CREATED — needs audit
- **Gaps:** TBD

#### correction_capture.py
- **Inspiration:** PMB correction capture
- **Plan:** Detect frustration, record lesson on FIRST occurrence
- **Status:** CREATED — needs audit
- **Gaps:** TBD

#### session_restore.py
- **Inspiration:** PMB session restore
- **Plan:** Rebuild context after compaction
- **Status:** CREATED — needs audit
- **Gaps:** TBD

#### exploration_capture.py
- **Inspiration:** PMB exploration capture
- **Plan:** Record what files were read
- **Status:** BEING CREATED
- **Gaps:** TBD

#### followcheck.py
- **Inspiration:** PMB followcheck
- **Plan:** Deterministic lesson follow-through
- **Status:** BEING CREATED
- **Gaps:** TBD

### Health Modules

#### consolidate.py
- **Inspiration:** PMB consolidation + nous_memory_mcp/vsa_consolidation.py
- **Plan:** LLM clusters related events, generalizes into single fact
- **Status:** BEING CREATED
- **Gaps found from research:**
  - **MISSING:** VSA consolidation (HRR bundling for gist vectors — no LLM needed)
  - **MISSING:** Cohesion metric (average pairwise similarity within cluster)
  - **MISSING:** Idempotent consolidation (running twice produces same gists)
- **Action:** Add VSA consolidation as alternative to LLM consolidation

#### auto_consolidate.py
- **Inspiration:** PMB auto-consolidate
- **Status:** BEING CREATED
- **Gaps:** TBD

#### distill_lessons.py
- **Inspiration:** PMB distill lessons
- **Status:** BEING CREATED
- **Gaps:** TBD

#### rehearse.py
- **Inspiration:** PMB rehearse (spaced repetition)
- **Status:** BEING CREATED
- **Gaps:** TBD

#### self_test.py
- **Inspiration:** PMB self-test (acc@5)
- **Status:** BEING CREATED
- **Gaps:** TBD

#### adaptive.py
- **Inspiration:** PMB adaptive importance
- **Status:** BEING CREATED
- **Gaps:** TBD

#### earned_memory.py
- **Inspiration:** PMB earned memory (Wilson CI)
- **Status:** BEING CREATED
- **Gaps:** TBD

#### feedback.py
- **Inspiration:** PMB recall feedback
- **Status:** BEING CREATED
- **Gaps:** TBD

#### conflicts.py
- **Inspiration:** PMB conflict detection
- **Status:** BEING CREATED
- **Gaps:** TBD

### Agent Modules

#### budget.py
- **Inspiration:** PMB token budget
- **Status:** CREATED (174 lines)
- **Gaps:** TBD

#### policy.py
- **Inspiration:** PMB compression policy
- **Status:** CREATED (288 lines)
- **Gaps:** TBD

#### loop.py
- **Inspiration:** PMB agent loop
- **Status:** CREATED (381 lines)
- **Gaps:** TBD

### Sidecar

#### server.py
- **Inspiration:** NoUs-fordge sidecar/server.py
- **Status:** BEING CREATED
- **Gaps:** TBD

---

## New Modules Discovered (Not in Original Plan)

These modules were found in the inspiration sources but were NOT in the original plan. They should be added to NoUsClaWW:

| Module | Source | Innovation | Priority |
|---|---|---|---|
| **danger_cones.py** | NoUs-fordge core/ | Three-layer hallucination defense with VSA danger cones | **HIGH** |
| **absence_detector.py** | NoUs-fordge core/ | Six gap types for system health monitoring | **MEDIUM** |
| **monitoring.py** | NoUs-fordge core/ | Bounded histograms, circular buffer time-series | **MEDIUM** |
| **alerting.py** | NoUs-fordge core/ | Declarative threshold rules with suppression | **MEDIUM** |
| **synth_index.py** | nous_memory_mcp/novel/ | Structured metadata parsing + FTS5 indexing | **HIGH** |
| **hrr_math.py** | NoUs-fordge core/ | VSA foundation (cross-architecture determinism) | **MEDIUM** |
| **fhrr_engine.py** | NoUs-fordge core/ | Complex VSA working memory with clean-up | **MEDIUM** |
| **cleanup_memory.py** | NoUs-fordge core/ | Associative codebook for vector recovery | **MEDIUM** |
| **golden_vector_tests.py** | NoUs-fordge core/ | Cross-platform determinism verification | **MEDIUM** |

---

### Data/Pipeline/Observability Layer (14 modules + sovereign specs + CF worker)

#### FOLD INTO NOUSCLAWW (10 innovations)

| Source Module | Innovation | NoUsClaWW Integration | Priority |
|---|---|---|---|
| **data_class.py** | 5-tier data classification with PROHIBITED_SCHOOL hard gate. AuthorizationRecord with tamper-evident SHA-256 hash. Expiry-based revocation. | Add as new module: data_gate.py — memory system data classification | **HIGH** |
| **deletion_engine.py** | Content-free tombstone pattern (opaque ID + timestamp + reason only). Cascading delete with child source tracking. verify_no_residuals() post-deletion. VACUUM integration. | Fold into memory_manager.py — add tombstone deletion + cascading lineage | **HIGH** |
| **egress_controller.py** | Default-deny with three policy levels (DENY_ALL, LOOPBACK_ONLY, ALLOWLIST). Short-lived session tokens with TTL. Construction-time telemetry blocking. Offline acceptance test. | Fold into sidecar/server.py — add default-deny egress enforcement | **HIGH** |
| **local_storage.py** | OS-protected key storage (Windows DPAPI, macOS Keychain, Linux Secret Service). Keys never in source code, .env, logs, or database plaintext. | Add as new module: key_storage.py — OS-protected key management | **MEDIUM** |
| **chunker.py** | Deterministic page-aware chunking with lineage (page/paragraph/line_start/line_end). Character-based sizing (no tokenizer dependency). Algorithm version for revision tracking. | Add as new module: chunker.py — deterministic chunking for pipeline | **MEDIUM** |
| **citation.py** | Citation builder with exact evidence excerpts. Prompt-injection detection in retrieved text. UNTRUSTED marker for flagged evidence. Machine-readable + human-readable formats. | Add as new module: citation.py — citation + injection detection in retrieval | **HIGH** |
| **retriever.py** | Deterministic hash-based embeddings (character n-gram hashing, no model download). Hybrid retrieval (FTS5 BM25 + vector similarity). FTS5 fallback to LIKE. | Fold into hybrid_retrieval.py — add deterministic hash-based embeddings | **HIGH** |
| **generator.py** | UNKNOWN abstention when evidence absent/conflicting/untrusted. Evidence threshold. Untrusted evidence rejection. Deterministic fallback without GPU. | Fold into llm_interface.py — add UNKNOWN abstention + untrusted evidence rejection | **HIGH** |
| **metrics.py** | Metrics derived from event log (single source of truth). Health checks on all subsystems. Quality metrics (success rate, abstention rate). Slow operations tracking. | Add as new module: metrics.py — observability from event log | **MEDIUM** |
| **file_security.py** | Decompression bomb detection. MIME detection from content bytes (not extension). Quarantine instead of partial trust. UntrustedContentMarker. | Add as new module: file_security.py — untrusted file boundary | **MEDIUM** |

#### SOVEREIGN SPECS (interfaces NoUsClaWW should match)

| Spec | Innovation | NoUsClaWW Integration | Priority |
|---|---|---|---|
| **SOVEREIGN_VISAGE_SPEC.md** | Affective HUD: animated kaomoji face, semantic object, status line. Three layers: spinner, face (affect), object (semantic). Affect classifier mapping signals to emotion. Settling curve (frame rate decays over time). Refusal affect shows actual state. | sovereign_sockets should emit affect signals matching Visage spec | **MEDIUM** |
| **SOVEREIGN_ARCHIVE_SPEC.md** | R2 archive format for disaster recovery. Client-side AES-256-GCM encryption. Pseudonymous label hashes (no raw labels, no exact timestamps, no PII). Merkle chain verification. Coarse time buckets. Immutable R2 objects. | sovereign_sockets should implement archive routes with encryption + Merkle verification | **MEDIUM** |

#### CLOUDFLARE WORKER PATTERNS (rabbit_hole should adopt)

| Source | Innovation | rabbit_hole Integration | Priority |
|---|---|---|---|
| **libraries.ts** | First-match-wins keyword categorization. 7 libraries (governance, intel, architecture, business, code, content, agent-files). Extension-based detection. Agent file detection by exact filename. | rabbit_hole should adopt library categorization for corpus indexing | **MEDIUM** |
| **chunk.ts** | Markdown chunking by ##/### headers. Code chunking by function/class boundaries. Header-aware metadata. Sub-split for large sections. Deterministic vector ID (library:sha256:chunkIndex). | rabbit_hole should adopt header-aware chunking | **MEDIUM** |
| **embed.ts** | Batch embedding for efficiency. Vectorize upsert with rich metadata. Query with library filter. MinScore threshold (default 0.35). | rabbit_hole should adopt batch embedding + library filtering | **MEDIUM** |

---

## Complete Innovation Inventory

### New Modules to Create (not in original plan, found from inspiration audit)

| # | Module | Source | Innovation | Priority |
|---|---|---|---|---|
| 1 | **danger_cones.py** | NoUs-fordge core/ | Three-layer hallucination defense with VSA danger cones + redirect | **HIGH** |
| 2 | **absence_detector.py** | NoUs-fordge core/ | Six gap types for system health monitoring | **MEDIUM** |
| 3 | **monitoring.py** | NoUs-fordge core/ | Bounded histograms, circular buffer time-series | **MEDIUM** |
| 4 | **alerting.py** | NoUs-fordge core/ | Declarative threshold rules with suppression | **MEDIUM** |
| 5 | **synth_index.py** | nous_memory_mcp/novel/ | Structured metadata parsing + FTS5 indexing | **HIGH** |
| 6 | **hrr_math.py** | NoUs-fordge core/ | VSA foundation (cross-architecture determinism) | **MEDIUM** |
| 7 | **fhrr_engine.py** | NoUs-fordge core/ | Complex VSA working memory with clean-up | **MEDIUM** |
| 8 | **cleanup_memory.py** | NoUs-fordge core/ | Associative codebook for vector recovery | **MEDIUM** |
| 9 | **golden_vector_tests.py** | NoUs-fordge core/ | Cross-platform determinism verification | **MEDIUM** |
| 10 | **data_gate.py** | NoUs-fordge data/ | 5-tier data classification with hard gate | **HIGH** |
| 11 | **key_storage.py** | NoUs-fordge data/ | OS-protected key storage (DPAPI/Keychain/Secret Service) | **MEDIUM** |
| 12 | **chunker.py** | NoUs-fordge pipeline/ | Deterministic page-aware chunking with lineage | **MEDIUM** |
| 13 | **citation.py** | NoUs-fordge pipeline/ | Citation builder + prompt-injection detection in retrieval | **HIGH** |
| 14 | **metrics.py** | NoUs-fordge observability/ | Metrics from event log, health checks, quality metrics | **MEDIUM** |
| 15 | **file_security.py** | NoUs-fordge data/ | Untrusted file boundary, decompression bomb detection | **MEDIUM** |

### Modules to Enhance (fold innovations into existing modules)

| Module | Innovations to Fold In | From | Status |
|---|---|---|---|
| **epistemic_boundary.py** | Stress sidecar, fixed refusal messages, silence audit log, danger cones, absence detection | epistemic_gate.py, right_to_silence.py, danger_cones.py, absence_detector.py | DONE (1136→1908 lines) |
| **hybrid_retrieval.py** | Danger filter post-processing, spreading activation, synth index, deterministic hash-based embeddings | danger_filter.py, spreading_activation.py, synth_index.py, retriever.py | DONE (285→708 lines) |
| **memory_manager.py** | Crystallization (vector sharpening), content-free tombstone deletion, cascading lineage | crystallization.py, deletion_engine.py | DONE (466→608 lines) |
| **health/consolidate.py** | VSA consolidation (HRR bundling, no LLM), cohesion metric, idempotent consolidation | vsa_consolidation.py | DONE (390→582 lines) |
| **llm_interface.py** | UNKNOWN abstention, untrusted evidence rejection, deterministic fallback | generator.py | DONE (175→~225 lines) |
| **sidecar/server.py** | Default-deny egress, loopback-only binding, short-lived session tokens | egress_controller.py | DONE (1354 lines) |
| **rabbit_hole worker** | Library categorization, header-aware chunking, batch embedding, library filtering | libraries.ts, chunk.ts, embed.ts | PENDING |
| **sovereign_sockets** | VISAGE affect interface, ARCHIVE encryption + Merkle verification | SOVEREIGN_VISAGE_SPEC.md, SOVEREIGN_ARCHIVE_SPEC.md | IN PROGRESS |

---

## Summary

| Category | Count | Status |
|---|---|---|
| Source modules audited | 45 (5 novel + 21 core + 14 data/pipeline/obs + 3 CF worker + 2 specs) | Complete |
| Innovations to fold in | 25 (HIGH: 8, MEDIUM: 12, LOW: 5) | Documented |
| New modules discovered | 15 | Not yet created |
| Modules to enhance | 8 | Documented |
| Sovereign socket only | 4 | Documented (Brain-side) |
| Already covered | 2 | Documented (deferred) |
| Per-module audits | 36 NoUsClaWW modules | In progress |

*This document is updated as each module is audited. This is the open_process axiom.*
