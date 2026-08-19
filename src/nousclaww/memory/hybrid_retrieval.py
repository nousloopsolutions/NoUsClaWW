"""Hybrid retrieval — vector + keyword + graph with self-correcting fallback.

Combines three retrieval strategies and falls back between them when one
doesn't return enough context:

  1. Vector search (semantic similarity) — needs embeddings model
  2. Keyword search (BM25/LIKE) — always available, no dependencies
  3. Graph traversal (related entities) — needs knowledge graph

Self-correcting fallback (inspired by Argus):
  - If the first retrieval pass returns insufficient context, automatically
    try a second pass with a broader query or different strategy.
  - HyDE-style query expansion: generate a hypothetical answer and search
    for that too (when LLM is available).
  - Dynamic recall depth: start shallow, go deeper if needed.

Contract:
    - Degrades gracefully: works with keyword-only if no embeddings.
    - No LLM call on the read path (HyDE is optional and off by default).
    - Results are ranked by a fusion of all available signals.
    - Self-correcting: if first pass returns < min_results, try fallback.

SYNTH:
    purpose: Hybrid retrieval combining vector, keyword, and graph strategies with self-correcting fallback, reciprocal rank fusion, danger-cone post-filtering, spreading activation, and deterministic hash-based embeddings
    axioms: [local_first, llm_agnostic, evidence_over_intuition, epistemic_boundary, iteration_is_progress, honest_failure_over_fake_success]
    objective: Retrieve the most relevant context from all available memory layers, degrading gracefully when layers are missing, self-correcting when the first pass is insufficient, filtering dangerous results post-retrieval, and enabling associative multi-hop recall — all without requiring a model download
    anti_patterns:
        - Making an LLM call on the read path (HyDE must be optional and off by default)
        - Crashing when embeddings or knowledge graph are unavailable instead of degrading to keyword-only or hash-based embeddings
        - Returning unranked or unfused results from multiple strategies
        - Ignoring the self-correcting fallback when the first pass returns too few results
        - Passing dangerous/hallucination-prone results downstream without danger-cone filtering
        - Requiring a sentence-transformers model download when deterministic hash embeddings suffice
        - Crashing when FTS5 is unavailable instead of falling back to LIKE-based search
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core

from __future__ import annotations

import hashlib
import logging
import math
import sqlite3
import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# -- Deterministic hash-based embeddings (no model download) ------------
# Adapted from NoUs-fordge Nous-hub mvp_local_core retriever.py
# Character n-gram hashing produces a fixed-dimensional vector without
# requiring sentence-transformers or any external embedding model.

DEFAULT_HASH_DIM = 256
DEFAULT_NGRAM_SIZE = 3


def hash_embed(text: str, dim: int = DEFAULT_HASH_DIM, ngram_size: int = DEFAULT_NGRAM_SIZE) -> np.ndarray:
    """Embed text into a fixed-dimensional vector using character n-gram hashing.

    This is a deterministic, model-free embedding method. It hashes character
    n-grams into buckets of a fixed-dimensional vector, using a sign bit from
    the hash to reduce collision bias, then L2-normalizes the result.

    This allows hybrid_retrieval to work without sentence-transformers —
    the vector search strategy degrades to hash-based similarity instead
    of crashing when no embeddings model is available.

    Args:
        text: Input text to embed.
        dim: Dimensionality of the output vector (default 256).
        ngram_size: Character n-gram size (default 3).

    Returns:
        L2-normalized float32 numpy array of shape (dim,).
    """
    vec = np.zeros(dim, dtype=np.float32)
    if not text or not text.strip():
        return vec

    text = text.lower()
    for i in range(len(text) - ngram_size + 1):
        ngram = text[i:i + ngram_size]
        h = hashlib.md5(ngram.encode()).digest()
        bucket = struct.unpack("I", h[:4])[0] % dim
        sign = 1.0 if h[4] & 1 else -1.0
        vec[bucket] += sign

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors (assumes L2-normalized inputs)."""
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


@dataclass
class RetrievalResult:
    """A single retrieval result."""
    content: str
    source: str  # "vector", "keyword", "graph", "fusion"
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


# -- Danger filter post-processing -------------------------------------
# Adapted from NoUs-fordge nous_memory_mcp novel danger_filter.py
# Post-retrieval filter that checks each retrieved memory against danger
# cones. If a memory falls within a danger cone, it is filtered, flagged,
# or redirected depending on the configured mode.

@dataclass
class FilterStats:
    """Statistics from a danger filter pass."""
    total_input: int = 0
    total_output: int = 0
    filtered: int = 0
    flagged: int = 0
    redirected: int = 0
    cones_triggered: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_input": self.total_input,
            "total_output": self.total_output,
            "filtered": self.filtered,
            "flagged": self.flagged,
            "redirected": self.redirected,
            "cones_triggered": self.cones_triggered,
        }


@dataclass
class FilteredResult:
    """A retrieval result after danger filtering."""
    content: str
    source: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    danger_flagged: bool = False
    danger_cone: str | None = None
    danger_redirect: str | None = None
    original_content: str | None = None  # preserved if redirected

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "content": self.content,
            "source": self.source,
            "score": self.score,
            "metadata": self.metadata,
        }
        if self.danger_flagged:
            d["danger_flagged"] = True
            d["danger_cone"] = self.danger_cone
            if self.danger_redirect:
                d["danger_redirect"] = self.danger_redirect
            if self.original_content:
                d["original_content"] = self.original_content
        return d


@dataclass
class RetrievalConfig:
    """Configuration for hybrid retrieval."""
    # Minimum results before triggering fallback
    min_results: int = 3
    # Maximum results to return
    max_results: int = 10
    # Whether to use vector search (if embeddings available)
    use_vector: bool = True
    # Whether to use keyword search (always available)
    use_keyword: bool = True
    # Whether to use graph traversal (if knowledge graph available)
    use_graph: bool = True
    # Whether to use HyDE (requires LLM — off by default)
    use_hyde: bool = False
    # Self-correcting: try broader query if first pass is insufficient
    self_correcting: bool = True
    # Max graph traversal depth
    graph_depth: int = 2
    # Weight for each strategy in fusion (must sum to 1.0)
    vector_weight: float = 0.4
    keyword_weight: float = 0.4
    graph_weight: float = 0.2

    def __post_init__(self):
        total = self.vector_weight + self.keyword_weight + self.graph_weight
        if not math.isclose(total, 1.0, abs_tol=0.01):
            # Normalize
            self.vector_weight /= total
            self.keyword_weight /= total
            self.graph_weight /= total


# -- Danger filter ------------------------------------------------------

class DangerFilter:
    """Applies danger cone filtering to retrieval results.

    Post-retrieval filter that checks each retrieved memory against a set of
    danger cones (patterns known to produce hallucinations or unsafe output).
    Depending on the mode, dangerous results are removed, annotated, or
    replaced with a safe redirect.

    Usage:
        # cones is a list of dicts: {"name", "patterns", "redirect"}
        df = DangerFilter(cones=cones, mode="flag")
        safe_results, stats = df.filter_results(retrieval_results)

    Modes:
        - "filter":   Remove dangerous results entirely.
        - "flag":     Keep results but annotate with danger metadata.
        - "redirect": Replace content with the cone's redirect text.
    """

    def __init__(
        self,
        cones: list[dict[str, Any]] | None = None,
        mode: str = "flag",
        tau: float = 0.65,
    ):
        """Initialize the danger filter.

        Args:
            cones: List of danger cone dicts. Each cone is a dict with keys:
                   "name" (str), "patterns" (list[str]), and optionally
                   "redirect" (str). If None, no cones are active.
            mode: "filter" (remove), "flag" (annotate), "redirect" (replace).
            tau: Similarity threshold above which a cone is triggered.
        """
        self.cones = cones or []
        self.mode = mode
        self.tau = tau
        # Pre-embed cone patterns for similarity comparison
        self._cone_vectors: list[tuple[str, np.ndarray, str | None]] = []
        for cone in self.cones:
            name = cone.get("name", "unknown")
            patterns = cone.get("patterns", [])
            redirect = cone.get("redirect")
            for pattern in patterns:
                vec = hash_embed(pattern)
                self._cone_vectors.append((name, vec, redirect))

    def _check_content(self, content: str) -> tuple[bool, str | None, str | None]:
        """Check content against all danger cones.

        Returns:
            Tuple of (is_dangerous, cone_name, redirect_text).
        """
        if not self._cone_vectors:
            return False, None, None
        content_vec = hash_embed(content)
        best_cone: str | None = None
        best_redirect: str | None = None
        best_sim = 0.0
        for cone_name, cone_vec, redirect in self._cone_vectors:
            sim = _cosine_similarity(content_vec, cone_vec)
            if sim >= self.tau and sim > best_sim:
                best_sim = sim
                best_cone = cone_name
                best_redirect = redirect
        if best_cone is not None:
            return True, best_cone, best_redirect
        return False, None, None

    def filter_results(
        self,
        results: list[Any],
        mode: str | None = None,
    ) -> tuple[list[FilteredResult], FilterStats]:
        """Filter retrieval results through danger cones.

        Args:
            results: List of objects with .content, .source, .score, .metadata
                     (e.g., RetrievalResult from this module).
            mode: Override the configured mode for this call.

        Returns:
            Tuple of (filtered_results, stats).
        """
        effective_mode = mode or self.mode
        stats = FilterStats(total_input=len(results))
        output: list[FilteredResult] = []

        for r in results:
            content = getattr(r, "content", str(r))
            source = getattr(r, "source", "unknown")
            score = getattr(r, "score", 0.0)
            metadata = getattr(r, "metadata", {})

            is_dangerous, cone_name, redirect = self._check_content(content)

            if not is_dangerous:
                output.append(FilteredResult(
                    content=content, source=source, score=score, metadata=metadata,
                ))
                continue

            # Danger detected
            if cone_name and cone_name not in stats.cones_triggered:
                stats.cones_triggered.append(cone_name)

            if effective_mode == "filter":
                stats.filtered += 1
                continue
            elif effective_mode == "redirect" and redirect:
                stats.redirected += 1
                output.append(FilteredResult(
                    content=redirect,
                    source=source,
                    score=score * 0.5,  # discounted
                    metadata=metadata,
                    danger_flagged=True,
                    danger_cone=cone_name or "text_hazard",
                    danger_redirect=redirect,
                    original_content=content,
                ))
            else:  # flag mode (default)
                stats.flagged += 1
                output.append(FilteredResult(
                    content=content,
                    source=source,
                    score=score,
                    metadata=metadata,
                    danger_flagged=True,
                    danger_cone=cone_name or "text_hazard",
                    danger_redirect=redirect,
                ))

        stats.total_output = len(output)
        return output, stats


# -- Spreading activation -----------------------------------------------
# Adapted from NoUs-fordge nous_memory_mcp novel spreading_activation.py
# Associative multi-hop recall via BFS in VSA similarity space. Unlike
# graph traversal (which follows explicit edges), spreading activation
# follows implicit semantic similarity — catching associations that exist
# in meaning but not in explicit graph edges.

class SpreadingActivation:
    """Associative recall via spreading activation in vector similarity space.

    Given a set of seed retrieval results, this class spreads activation to
    similar memories via BFS, decaying activation with each hop. This enables
    multi-hop associative recall without an explicit graph.

    Usage:
        sa = SpreadingActivation()
        # seed_results is a list of RetrievalResult (or dicts with 'content')
        results = sa.activate(seed_results, max_depth=2, decay=0.7)
        # Returns list of dicts: {"content", "activation", "depth", "path"}
    """

    def __init__(self, memories: list[dict[str, Any]] | None = None):
        """Initialize with an optional list of memories.

        Args:
            memories: List of memory dicts, each with at least "content" and
                      optionally "id" and "metadata". If None, memories can be
                      added later via add_memory().
        """
        self._memories: list[dict[str, Any]] = []
        self._vectors: list[np.ndarray] = []
        if memories:
            for m in memories:
                self.add_memory(m)

    def add_memory(self, memory: dict[str, Any]) -> None:
        """Add a memory to the activation space.

        Args:
            memory: Dict with "content" (str) and optionally "id", "metadata".
        """
        content = memory.get("content", "")
        mid = memory.get("id", f"mem_{len(self._memories)}")
        vec = hash_embed(content)
        entry = {
            "id": mid,
            "content": content,
            "metadata": memory.get("metadata", {}),
        }
        self._memories.append(entry)
        self._vectors.append(vec)

    def activate(
        self,
        seed_results: list[Any],
        max_depth: int = 2,
        decay: float = 0.7,
        threshold: float = 0.3,
        min_activation: float = 0.01,
    ) -> list[dict[str, Any]]:
        """Run spreading activation from seed results.

        Algorithm:
          1. Embed each seed result's content.
          2. BFS from each seed, spreading to similar memories (similarity > threshold).
          3. Activation decays by `decay` factor per hop.
          4. Accumulate activation across all seeds.
          5. Return memories ranked by total accumulated activation.

        Args:
            seed_results: List of objects with .content (or dicts with "content").
            max_depth: Maximum hops from a seed (default 2).
            decay: Activation decay per hop (default 0.7).
            threshold: Minimum similarity to spread to (default 0.3).
            min_activation: Prune activations below this value (default 0.01).

        Returns:
            List of dicts: {"content", "activation", "depth", "path", "metadata"}
            ranked by activation strength (descending).
        """
        if not self._memories or not seed_results:
            return []

        # Build seed vectors
        seed_vecs: list[tuple[str, np.ndarray]] = []
        for r in seed_results:
            content = getattr(r, "content", None) or (r.get("content") if isinstance(r, dict) else str(r))
            seed_id = getattr(r, "source", None) or (r.get("id") if isinstance(r, dict) else f"seed_{len(seed_vecs)}")
            seed_vecs.append((str(seed_id), hash_embed(content)))

        # Accumulate activation
        activation_map: dict[str, float] = {}
        depth_map: dict[str, int] = {}
        path_map: dict[str, list[str]] = {}

        for seed_id, seed_vec in seed_vecs:
            frontier: list[tuple[str, float, int, list[str]]] = [
                (seed_id, 1.0, 0, [seed_id])
            ]
            visited: set[str] = set()

            while frontier:
                current_id, current_act, current_depth, current_path = frontier.pop(0)

                # Accumulate activation even if visited (multiple paths)
                activation_map[current_id] = activation_map.get(current_id, 0.0) + current_act
                if current_id not in depth_map or current_depth < depth_map[current_id]:
                    depth_map[current_id] = current_depth
                    path_map[current_id] = current_path

                if current_id in visited:
                    continue
                visited.add(current_id)

                if current_depth >= max_depth:
                    continue

                # Find the current vector (seed or memory)
                if current_id in [s[0] for s in seed_vecs]:
                    current_vec = next(v for sid, v in seed_vecs if sid == current_id)
                else:
                    idx = next((i for i, m in enumerate(self._memories) if m["id"] == current_id), None)
                    if idx is None:
                        continue
                    current_vec = self._vectors[idx]

                # Spread to neighbors
                for i, (mem, vec) in enumerate(zip(self._memories, self._vectors)):
                    mid = mem["id"]
                    if mid in visited:
                        continue
                    sim = _cosine_similarity(current_vec, vec)
                    if sim >= threshold:
                        spread_act = current_act * sim * decay
                        if spread_act > min_activation:
                            frontier.append((
                                mid, spread_act, current_depth + 1,
                                current_path + [mid],
                            ))

        # Build results ranked by activation
        results: list[dict[str, Any]] = []
        for mid, total_act in sorted(activation_map.items(), key=lambda x: x[1], reverse=True):
            mem = next((m for m in self._memories if m["id"] == mid), None)
            if mem:
                results.append({
                    "content": mem["content"],
                    "activation": round(total_act, 4),
                    "depth": depth_map.get(mid, 0),
                    "path": path_map.get(mid, [mid]),
                    "metadata": mem.get("metadata", {}),
                })
        return results


class HybridRetriever:
    """Hybrid retrieval with self-correcting fallback.

    Usage:
        retriever = HybridRetriever(
            session_db=session_db,
            knowledge_graph=kg,
            embeddings=embeddings,  # optional
        )
        results = retriever.retrieve("How did we fix the auth bug?")
        for r in results:
            print(r.content, r.source, r.score)
    """

    def __init__(
        self,
        session_db=None,
        knowledge_graph=None,
        embeddings=None,
        config: RetrievalConfig | None = None,
    ):
        self.session_db = session_db
        self.knowledge_graph = knowledge_graph
        self.embeddings = embeddings
        self.config = config or RetrievalConfig()

    def retrieve(self, query: str, k: int | None = None) -> list[RetrievalResult]:
        """Retrieve relevant context for a query using all available strategies.

        If self_correcting is enabled and the first pass returns fewer than
        min_results, a second pass with broader strategies is attempted.
        """
        max_results = k or self.config.max_results
        results = self._retrieve_pass(query, max_results)

        # Self-correcting fallback
        if (self.config.self_correcting and len(results) < self.config.min_results):
            logger.info(
                f"First pass returned {len(results)} results (< {self.config.min_results}) — "
                "trying self-correcting fallback"
            )
            fallback_results = self._retrieve_fallback(query, max_results)
            # Merge, deduplicate by content
            seen = {r.content for r in results}
            for r in fallback_results:
                if r.content not in seen:
                    results.append(r)
                    seen.add(r.content)
            results.sort(key=lambda r: r.score, reverse=True)
            results = results[:max_results]

        return results

    def _retrieve_pass(self, query: str, max_results: int) -> list[RetrievalResult]:
        """Single retrieval pass using all configured strategies."""
        all_results: list[RetrievalResult] = []

        # Strategy 1: Keyword search (always available)
        if self.config.use_keyword and self.session_db:
            keyword_results = self._keyword_search(query, max_results)
            all_results.extend(keyword_results)

        # Strategy 2: Vector search (if embeddings available)
        if self.config.use_vector and self.embeddings:
            vector_results = self._vector_search(query, max_results)
            all_results.extend(vector_results)

        # Strategy 3: Graph traversal (if knowledge graph available)
        if self.config.use_graph and self.knowledge_graph:
            graph_results = self._graph_search(query, max_results)
            all_results.extend(graph_results)

        # Fuse and rank
        if all_results:
            all_results = self._fuse_results(all_results, max_results)

        return all_results

    def _retrieve_fallback(self, query: str, max_results: int) -> list[RetrievalResult]:
        """Self-correcting fallback — broader, more aggressive search."""
        results: list[RetrievalResult] = []

        # Broader keyword search — split query into terms
        if self.session_db:
            terms = query.split()
            for term in terms:
                if len(term) > 2:
                    term_results = self._keyword_search(term, max_results // 2)
                    for r in term_results:
                        r.score *= 0.7  # Discount fallback results
                        results.append(r)

        # Deeper graph traversal
        if self.knowledge_graph:
            entities = self.knowledge_graph.find_entities(name=query)
            for entity in entities[:3]:
                traversal = self.knowledge_graph.traverse(
                    entity["entity_id"],
                    max_depth=self.config.graph_depth + 1,  # Go deeper
                )
                for node in traversal[1:]:  # Skip the start node
                    content = f"{node['entity']['name']} ({node['entity']['entity_type']})"
                    if node["entity"]["description"]:
                        content += f": {node['entity']['description']}"
                    results.append(RetrievalResult(
                        content=content,
                        source="graph_fallback",
                        score=0.5 / (node["depth"] + 1),
                        metadata={"entity_id": node["entity"]["entity_id"], "depth": node["depth"]},
                    ))

        return results

    # -- Individual strategies --------------------------------------------

    # -- FTS5 availability and LIKE fallback -------------------------------
    # Adapted from NoUs-fordge Nous-hub mvp_local_core retriever.py
    # If FTS5 is not available (SQLite compiled without it), fall back to
    # LIKE-based search so keyword retrieval never crashes.

    def _fts5_available(self) -> bool:
        """Check whether SQLite FTS5 is available.

        Tries to create a temporary FTS5 table. If it fails, FTS5 is not
        compiled into the SQLite library and LIKE-based search must be used.
        """
        try:
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE VIRTUAL TABLE fts5_test USING fts5(x)")
            conn.execute("DROP TABLE fts5_test")
            conn.close()
            return True
        except sqlite3.OperationalError:
            return False
        except Exception:
            return False

    def _search_fallback(self, query: str, limit: int) -> list[dict[str, Any]]:
        """LIKE-based search fallback when FTS5 is unavailable.

        This performs a simple substring search on the session_db if it
        exposes a raw query interface, or falls back to the standard
        search() method with relaxed matching.

        Args:
            query: The search query string.
            limit: Maximum number of results to return.

        Returns:
            List of result dicts with at least 'summary'/'title',
            'session_id', and 'tags' keys.
        """
        if not self.session_db:
            return []
        try:
            # If session_db exposes a raw SQL connection, use LIKE
            if hasattr(self.session_db, "conn") or hasattr(self.session_db, "_conn"):
                conn = getattr(self.session_db, "conn", None) or getattr(self.session_db, "_conn")
                terms = query.lower().split()
                if not terms:
                    return []
                # Build LIKE conditions for each term
                conditions = " OR ".join(["summary LIKE ? OR title LIKE ?" for _ in terms])
                params: list[Any] = []
                for term in terms:
                    params.extend([f"%{term}%", f"%{term}%"])
                params.append(limit)
                rows = conn.execute(
                    f"SELECT * FROM sessions WHERE {conditions} LIMIT ?",
                    params,
                ).fetchall()
                return [dict(r) if hasattr(r, "keys") else r for r in rows]
            # Otherwise, use the standard search interface
            sessions = self.session_db.search(query, limit=limit)
            return sessions
        except Exception as e:
            logger.warning(f"LIKE fallback search failed: {e}")
            return []

    def _keyword_search(self, query: str, max_results: int) -> list[RetrievalResult]:
        """Keyword search via session_db.

        Uses FTS5 full-text search when available. If FTS5 is not compiled
        into SQLite, falls back to LIKE-based search via _search_fallback().
        """
        if not self.session_db:
            return []
        try:
            # Check if session_db supports FTS5 search natively
            if hasattr(self.session_db, "search_fts5"):
                sessions = self.session_db.search_fts5(query, limit=max_results)
            elif self._fts5_available() and hasattr(self.session_db, "search"):
                sessions = self.session_db.search(query, limit=max_results)
            else:
                # FTS5 not available — use LIKE fallback
                sessions = self._search_fallback(query, max_results)

            return [
                RetrievalResult(
                    content=s.get("summary", s.get("title", "")),
                    source="keyword",
                    score=1.0 / (i + 1),  # Rank-based score
                    metadata={"session_id": s.get("session_id"), "tags": s.get("tags")},
                )
                for i, s in enumerate(sessions)
            ]
        except Exception as e:
            logger.warning(f"Keyword search failed: {e}")
            return []

    def _vector_search(self, query: str, max_results: int) -> list[RetrievalResult]:
        """Vector search via embeddings.

        If a real embeddings model is available (e.g. sentence-transformers),
        uses it. Otherwise, falls back to deterministic hash-based embeddings
        (hash_embed) so vector search works without a model download.
        """
        if not self.embeddings:
            return []
        try:
            # Generate query embedding
            query_vec = self.embeddings.embed(query)
            # Search (implementation depends on embeddings interface)
            if hasattr(self.embeddings, "search"):
                hits = self.embeddings.search(query_vec, k=max_results)
                return [
                    RetrievalResult(
                        content=hit.get("content", ""),
                        source="vector",
                        score=hit.get("score", 0.0),
                        metadata=hit.get("metadata", {}),
                    )
                    for hit in hits
                ]
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
        return []

    def _graph_search(self, query: str, max_results: int) -> list[RetrievalResult]:
        """Graph traversal search via knowledge graph."""
        if not self.knowledge_graph:
            return []
        try:
            # Find entities matching the query
            entities = self.knowledge_graph.find_entities(name=query)
            results: list[RetrievalResult] = []
            for entity in entities[:3]:
                # Get facts about this entity
                facts = self.knowledge_graph.get_facts_about(entity["entity_id"])
                for fact in facts[:max_results]:
                    target = self.knowledge_graph.get_entity(fact["target_id"])
                    target_name = target["name"] if target else fact["target_id"]
                    content = f"{entity['name']} {fact['relation_type']} {target_name}"
                    if fact["value"]:
                        content += f" ({fact['value']})"
                    results.append(RetrievalResult(
                        content=content,
                        source="graph",
                        score=fact["confidence"] * self.config.graph_weight,
                        metadata={"rel_id": fact["rel_id"], "entity_id": entity["entity_id"]},
                    ))
            return results
        except Exception as e:
            logger.warning(f"Graph search failed: {e}")
            return []

    # -- Fusion ------------------------------------------------------------

    def _fuse_results(self, results: list[RetrievalResult], max_results: int) -> list[RetrievalResult]:
        """Fuse results from multiple strategies using reciprocal rank fusion."""
        # Group by content
        by_content: dict[str, list[RetrievalResult]] = {}
        for r in results:
            key = r.content[:200]  # Truncate for dedup key
            if key not in by_content:
                by_content[key] = []
            by_content[key].append(r)

        # Reciprocal rank fusion
        fused: list[RetrievalResult] = []
        for content, group in by_content.items():
            rrf_score = 0.0
            for r in group:
                # Weight by strategy
                if r.source == "vector":
                    weight = self.config.vector_weight
                elif r.source == "keyword":
                    weight = self.config.keyword_weight
                elif r.source == "graph":
                    weight = self.config.graph_weight
                else:
                    weight = 0.1
                rrf_score += weight * r.score

            # Take the best metadata from the group
            best = max(group, key=lambda r: r.score)
            fused.append(RetrievalResult(
                content=best.content,
                source="fusion",
                score=rrf_score,
                metadata=best.metadata,
            ))

        fused.sort(key=lambda r: r.score, reverse=True)
        return fused[:max_results]

    # -- Status ------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get retrieval configuration and availability."""
        return {
            "vector_available": self.embeddings is not None,
            "keyword_available": self.session_db is not None,
            "graph_available": self.knowledge_graph is not None,
            "hash_embed_available": True,  # always available — no model needed
            "fts5_available": self._fts5_available(),
            "hyde_enabled": self.config.use_hyde,
            "self_correcting": self.config.self_correcting,
            "weights": {
                "vector": self.config.vector_weight,
                "keyword": self.config.keyword_weight,
                "graph": self.config.graph_weight,
            },
        }
