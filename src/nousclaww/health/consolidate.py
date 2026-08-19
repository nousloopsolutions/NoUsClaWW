"""Consolidation engine — cluster related events and extract durable facts.

Takes a batch of raw events, clusters them by semantic similarity (using
token-overlap as a local-first embedding proxy when no embedding model is
available), and asks the LLM to extract a single durable fact or rule per
cluster. This is the core "sleep" operation that generalizes from many
specific observations into a compact, reusable knowledge base.

A VSA-based (Vector Symbolic Architecture) consolidation path is also
provided as a math-based alternative that requires no LLM call. It uses
deterministic text-derived vectors, greedy clustering, and bundling into
gist vectors with cohesion metrics.

Contract:
    - Clustering is local-first: token-overlap Jaccard similarity is used
      as the default embedding proxy. A real embeddings interface can be
      injected for richer clustering.
    - One LLM call per cluster — never one per event.
    - Every generated fact includes the source event_ids for traceability.
    - Events that cannot be clustered (singleton clusters) are still
      consolidated into a fact if they carry meaningful content.
    - The LLM is asked to produce a single concise statement per cluster;
      if it returns nothing useful, the cluster's best event content is
      used as the fact (honest fallback, never fabricated).
    - VSA consolidation (vsa_consolidate) is a pure-math alternative —
      no LLM calls, deterministic, idempotent.

SYNTH:
    purpose: Cluster related events by embedding similarity and extract one durable fact per cluster via LLM, with an optional VSA-based math consolidation path that requires no LLM.
    axioms: [local_first, evidence_over_intuition, epistemic_boundary, honest_failure_over_fake_success, iteration_is_progress]
    objective: Generalize from multiple related raw events into compact, durable facts that capture the underlying rule or pattern, with both LLM-based and VSA-math-based consolidation paths.
    anti_patterns:
        - Making one LLM call per event instead of per cluster
        - Fabricating facts when the LLM returns empty or low-quality output
        - Dropping singleton events instead of consolidating them
        - Clustering without a similarity threshold (producing giant meaningless clusters)
        - Losing traceability to source event_ids
        - VSA consolidation producing non-deterministic gists on repeated runs

#C Inspired by PMB (Project Memory Bank) sleep engine
#C VSA consolidation inspired by nous_memory_mcp/novel/vsa_consolidation.py
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any

from nousclaww.memory.memory_manager import MemoryManager
from nousclaww.llm_router import LLMRouter
from nousclaww.text_match import distinctive_tokens

logger = logging.getLogger(__name__)


class Consolidator:
    """Cluster related events and extract durable facts via LLM.

    Usage:
        consolidator = Consolidator()
        result = consolidator.consolidate(events, llm_router)
        for fact in result["facts"]:
            memory_manager.store_memory(fact)
        memory_manager.mark_events_consolidated(result["consolidated_event_ids"])
    """

    def __init__(
        self,
        similarity_threshold: float = 0.15,
        min_cluster_size: int = 1,
        max_cluster_size: int = 20,
        embeddings: Any | None = None,
    ) -> None:
        """Initialize the consolidator.

        Args:
            similarity_threshold: Minimum Jaccard overlap (or cosine
                similarity if embeddings provided) for two events to be
                placed in the same cluster. Lower values produce larger,
                looser clusters.
            min_cluster_size: Minimum number of events to form a cluster.
                Singleton events below this threshold are still processed
                individually if they have content.
            max_cluster_size: Maximum events per cluster before splitting.
                Prevents giant clusters that would overload the LLM prompt.
            embeddings: Optional embeddings interface with an ``embed(text)``
                method. When provided, cosine similarity is used instead of
                token-overlap Jaccard.
        """
        self.similarity_threshold = float(similarity_threshold)
        self.min_cluster_size = int(min_cluster_size)
        self.max_cluster_size = int(max_cluster_size)
        self.embeddings = embeddings

    # ── Public API ─────────────────────────────────────────────────────────

    def consolidate(
        self,
        events: list[dict[str, Any]],
        llm_router: LLMRouter,
    ) -> dict[str, Any]:
        """Cluster events and extract one durable fact per cluster.

        Args:
            events: List of event dicts, each with at least 'event_id'
                and 'content' keys. Metadata and type are used for
                additional context.
            llm_router: An LLMRouter instance used to extract facts from
                each cluster.

        Returns:
            A dict with keys:
                - 'facts': list of fact dicts ready for store_memory(),
                  each with 'type', 'content', 'subject', 'importance',
                  and 'metadata' (including 'source_event_ids').
                - 'consolidated_event_ids': list of event_ids that were
                  processed.
                - 'clusters': list of cluster dicts for debugging
                  (event_ids and similarity info).
                - 'llm_errors': list of error strings from failed LLM calls.
        """
        if not events:
            return {
                "facts": [],
                "consolidated_event_ids": [],
                "clusters": [],
                "llm_errors": [],
            }

        clusters = self._cluster_events(events)
        facts: list[dict[str, Any]] = []
        consolidated_ids: list[str] = []
        llm_errors: list[str] = []

        for cluster in clusters:
            event_ids = [e["event_id"] for e in cluster["events"]]
            consolidated_ids.extend(event_ids)

            fact = self._extract_fact(cluster["events"], llm_router, llm_errors)
            if fact:
                fact["metadata"] = fact.get("metadata", {})
                fact["metadata"]["source_event_ids"] = event_ids
                fact["metadata"]["cluster_size"] = len(cluster["events"])
                fact["metadata"]["cluster_method"] = cluster["method"]
                facts.append(fact)

        logger.info(
            f"Consolidated {len(consolidated_ids)} events into "
            f"{len(facts)} facts across {len(clusters)} clusters"
        )

        return {
            "facts": facts,
            "consolidated_event_ids": consolidated_ids,
            "clusters": clusters,
            "llm_errors": llm_errors,
        }

    # ── Clustering ─────────────────────────────────────────────────────────

    def _cluster_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Cluster events by similarity using greedy agglomerative grouping.

        Each event is compared to existing cluster centroids. If similarity
        to any centroid exceeds the threshold, the event joins that cluster
        (the closest one). Otherwise it starts a new cluster. Clusters that
        exceed max_cluster_size are split.

        Returns:
            List of cluster dicts with 'events', 'method', and 'centroid'
            keys.
        """
        if self.embeddings is not None:
            return self._cluster_with_embeddings(events)
        return self._cluster_with_tokens(events)

    def _cluster_with_tokens(
        self, events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Cluster events using token-overlap Jaccard similarity."""
        # Precompute token sets for all events
        token_sets: list[set[str]] = []
        for e in events:
            content = e.get("content", "")
            tokens = distinctive_tokens(content) if content else set()
            token_sets.append(tokens)

        clusters: list[list[int]] = []  # list of event indices
        centroids: list[set[str]] = []

        for i, tokens in enumerate(token_sets):
            best_cluster = -1
            best_sim = 0.0
            for ci, centroid in enumerate(centroids):
                sim = self._jaccard(tokens, centroid)
                if sim > best_sim:
                    best_sim = sim
                    best_cluster = ci

            if best_cluster >= 0 and best_sim >= self.similarity_threshold:
                clusters[best_cluster].append(i)
                # Update centroid to union of all member tokens
                centroids[best_cluster] = centroids[best_cluster] | tokens
            else:
                clusters.append([i])
                centroids.append(set(tokens))

        return self._build_cluster_dicts(events, clusters, "token_jaccard")

    def _cluster_with_embeddings(
        self, events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Cluster events using cosine similarity over embeddings."""
        # Compute embeddings for all events
        embeddings: list[list[float]] = []
        for e in events:
            content = e.get("content", "")
            try:
                vec = self.embeddings.embed(content) if content else None
            except Exception as exc:
                logger.warning(f"Embedding failed for event: {exc}")
                vec = None
            embeddings.append(vec if vec is not None else [])

        clusters: list[list[int]] = []
        centroids: list[list[float]] = []

        for i, vec in enumerate(embeddings):
            best_cluster = -1
            best_sim = 0.0
            for ci, centroid in enumerate(centroids):
                sim = self._cosine(vec, centroid)
                if sim > best_sim:
                    best_sim = sim
                    best_cluster = ci

            if best_cluster >= 0 and best_sim >= self.similarity_threshold:
                clusters[best_cluster].append(i)
                # Update centroid as average
                centroids[best_cluster] = self._average_vectors(
                    [embeddings[j] for j in clusters[best_cluster]]
                )
            else:
                clusters.append([i])
                centroids.append(list(vec))

        return self._build_cluster_dicts(events, clusters, "embedding_cosine")

    def _build_cluster_dicts(
        self,
        events: list[dict[str, Any]],
        index_clusters: list[list[int]],
        method: str,
    ) -> list[dict[str, Any]]:
        """Convert index-based clusters into event-based cluster dicts.

        Splits clusters that exceed max_cluster_size into smaller chunks.
        Filters out empty clusters.
        """
        result: list[dict[str, Any]] = []
        for indices in index_clusters:
            if len(indices) < self.min_cluster_size:
                # Still include singletons if they have content
                if not indices:
                    continue
                event = events[indices[0]]
                if not event.get("content", "").strip():
                    continue

            # Split oversized clusters
            for start in range(0, len(indices), self.max_cluster_size):
                chunk = indices[start:start + self.max_cluster_size]
                if not chunk:
                    continue
                cluster_events = [events[j] for j in chunk]
                result.append({
                    "events": cluster_events,
                    "method": method,
                    "event_ids": [e["event_id"] for e in cluster_events],
                })
        return result

    # ── Fact extraction ─────────────────────────────────────────────────────

    def _extract_fact(
        self,
        cluster_events: list[dict[str, Any]],
        llm_router: LLMRouter,
        llm_errors: list[str],
    ) -> dict[str, Any] | None:
        """Extract a single durable fact from a cluster of events.

        Asks the LLM to synthesize the events into one concise statement.
        Falls back to the highest-content event if the LLM fails or returns
        empty output.
        """
        # Build the prompt from event contents
        event_summaries = self._format_events_for_prompt(cluster_events)
        system_prompt = (
            "You are a memory consolidation engine. Your job is to read a "
            "cluster of related events and extract a single, concise, durable "
            "fact or rule that generalizes across all of them. "
            "Output ONLY the fact as a single sentence. No preamble, no "
            "explanation, no markdown. If the events are contradictory or "
            "contain no useful pattern, output exactly: NONE"
        )
        question = (
            f"Events ({len(cluster_events)}):\n{event_summaries}\n\n"
            f"Extract one durable fact or rule that captures the pattern "
            f"across these events."
        )

        try:
            result = llm_router.ask(question, system_prompt=system_prompt)
            text = result.text.strip() if result and result.text else ""
        except Exception as exc:
            llm_errors.append(f"LLM call failed for cluster: {exc}")
            text = ""

        # Check for honest "no pattern" signal
        if text.upper() == "NONE" or not text:
            # Fallback: use the event with the most content
            best_event = max(
                cluster_events,
                key=lambda e: len(e.get("content", "")),
            )
            content = best_event.get("content", "").strip()
            if not content:
                return None
            return {
                "type": "fact",
                "content": content,
                "subject": self._extract_subject(best_event),
                "importance": 0.4,
                "metadata": {
                    "extraction_method": "fallback_best_event",
                    "llm_available": bool(text),
                },
            }

        # LLM produced a fact — clean it up
        # Remove common LLM artifacts
        text = text.strip('"').strip("'").strip("*").strip()
        # Take only the first sentence if multiple were returned
        if ". " in text:
            text = text.split(". ")[0] + "."

        return {
            "type": "fact",
            "content": text,
            "subject": self._extract_subject_from_text(text),
            "importance": self._estimate_importance(cluster_events),
            "metadata": {
                "extraction_method": "llm_synthesis",
                "llm_provider": result.provider if result else "unknown",
            },
        }

    def _format_events_for_prompt(
        self, events: list[dict[str, Any]],
    ) -> str:
        """Format cluster events into a numbered list for the LLM prompt."""
        lines: list[str] = []
        for i, e in enumerate(events, 1):
            etype = e.get("event_type", e.get("type", "event"))
            content = e.get("content", "").strip()
            if not content:
                continue
            lines.append(f"  {i}. [{etype}] {content}")
        return "\n".join(lines) if lines else "  (no content)"

    def _estimate_importance(self, events: list[dict[str, Any]]) -> float:
        """Estimate importance of a fact based on cluster evidence.

        Larger clusters and events with explicit importance metadata
        produce higher importance scores. Clamped to [0.3, 0.9].
        """
        base = 0.5
        # More events in cluster = more evidence = higher importance
        cluster_bonus = min(0.2, len(events) * 0.03)
        # Check for explicit importance in event metadata
        explicit_scores: list[float] = []
        for e in events:
            meta = e.get("metadata", {})
            if isinstance(meta, dict) and "importance" in meta:
                try:
                    explicit_scores.append(float(meta["importance"]))
                except (TypeError, ValueError):
                    pass
        explicit_avg = sum(explicit_scores) / len(explicit_scores) if explicit_scores else 0.0
        explicit_bonus = explicit_avg * 0.2
        importance = base + cluster_bonus + explicit_bonus
        return max(0.3, min(0.9, importance))

    # ── Subject extraction ──────────────────────────────────────────────────

    def _extract_subject(self, event: dict[str, Any]) -> str:
        """Extract a subject string from an event's metadata or type."""
        meta = event.get("metadata", {})
        if isinstance(meta, dict):
            for key in ("subject", "topic", "entity", "category"):
                val = meta.get(key)
                if val and isinstance(val, str):
                    return val
        return event.get("event_type", event.get("type", ""))

    def _extract_subject_from_text(self, text: str) -> str:
        """Heuristically extract a subject from a fact statement.

        Takes the first few distinctive tokens as a rough subject label.
        """
        tokens = distinctive_tokens(text)
        if not tokens:
            return ""
        # Take up to 3 distinctive tokens as subject
        return " ".join(list(tokens)[:3])

    # ── Similarity primitives ───────────────────────────────────────────────

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        """Compute Jaccard similarity between two token sets."""
        union = a | b
        if not union:
            return 0.0
        intersection = a & b
        return len(intersection) / len(union)

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(y * y for y in b))
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)

    @staticmethod
    def _average_vectors(vectors: list[list[float]]) -> list[float]:
        """Compute the element-wise average of a list of vectors."""
        if not vectors:
            return []
        length = len(vectors[0])
        if length == 0:
            return []
        result = [0.0] * length
        for vec in vectors:
            if len(vec) == length:
                for i in range(length):
                    result[i] += vec[i]
        count = len(vectors)
        return [v / count for v in result]


# ── VSA-based consolidation (no LLM required) ──────────────────────────────
# Adapted from nous_memory_mcp/novel/vsa_consolidation.py
# Pure-math alternative: deterministic text vectors, greedy clustering,
# bundling into gist vectors with cohesion metrics. No external deps.

VSA_DEFAULT_DIM = 1024


def _deterministic_vector(text: str, dim: int = VSA_DEFAULT_DIM) -> list[float]:
    """Generate a deterministic unit vector from text content.

    Uses SHA-256 hashing of tokens to seed vector dimensions, producing
    a reproducible vector for the same input text. This is a lightweight
    VSA encoding that requires no external model.

    Args:
        text: The input text to encode.
        dim: Vector dimensionality.

    Returns:
        A list of floats representing the unit vector.
    """
    if not text or not text.strip():
        return [0.0] * dim
    vec = [0.0] * dim
    tokens = distinctive_tokens(text)
    if not tokens:
        # Fall back to hashing the raw text
        tokens = {text.strip().lower()}
    for token in tokens:
        h = hashlib.sha256(token.encode("utf-8")).hexdigest()
        # Use first 8 hex chars as a dimension index
        idx = int(h[:8], 16) % dim
        # Use next 8 hex chars as a sign/magnitude
        val_bits = int(h[8:16], 16)
        sign = 1.0 if (val_bits & 1) else -1.0
        magnitude = ((val_bits >> 1) & 0x7FFFFFFF) / float(0x7FFFFFFF)
        vec[idx] += sign * magnitude
    # Normalize to unit length
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0.0:
        vec = [v / norm for v in vec]
    return vec


def _bundle_vectors(vectors: list[list[float]]) -> list[float]:
    """Bundle (superimpose) a list of VSA vectors.

    Bundling is element-wise addition followed by normalization, which
    produces a gist vector that captures the common semantic features.

    Args:
        vectors: List of vectors to bundle.

    Returns:
        The normalized bundled vector.
    """
    if not vectors:
        return []
    dim = len(vectors[0])
    result = [0.0] * dim
    for vec in vectors:
        if len(vec) == dim:
            for i in range(dim):
                result[i] += vec[i]
    norm = math.sqrt(sum(v * v for v in result))
    if norm > 0.0:
        result = [v / norm for v in result]
    return result


class CohesionMetric:
    """Compute cohesion (average pairwise similarity) within a cluster.

    Cohesion measures how tightly the members of a cluster are related.
    Higher cohesion means the cluster is more semantically coherent.

    Usage::

        metric = CohesionMetric()
        score = metric.compute(vectors)
        # score in [-1.0, 1.0] (cosine similarity range)
    """

    def __init__(self, similarity_fn: Any | None = None) -> None:
        """Initialize the cohesion metric.

        Args:
            similarity_fn: Optional custom similarity function that
                takes two vectors and returns a float. Defaults to
                cosine similarity.
        """
        self._similarity_fn = similarity_fn or Consolidator._cosine

    def compute(self, vectors: list[list[float]]) -> float:
        """Compute the average pairwise similarity within a set of vectors.

        Args:
            vectors: List of vectors to compare.

        Returns:
            The average pairwise similarity. Returns 1.0 for a single
            vector (trivially cohesive) and 0.0 for an empty set.
        """
        n = len(vectors)
        if n == 0:
            return 0.0
        if n == 1:
            return 1.0
        sims: list[float] = []
        for i in range(n):
            for j in range(i + 1, n):
                sims.append(self._similarity_fn(vectors[i], vectors[j]))
        return sum(sims) / len(sims) if sims else 1.0


def vsa_consolidate(
    memories: list[dict[str, Any]],
    similarity_threshold: float = 0.65,
) -> list[dict[str, Any]]:
    """Consolidate memories into gist vectors using VSA bundling (no LLM).

    This is a pure-math alternative to LLM-based consolidation. It:
        1. Encodes each memory as a deterministic vector from its content.
        2. Greedy-clusters memories by cosine similarity.
        3. Bundles each cluster into a gist vector.
        4. Computes cohesion (average pairwise similarity) per cluster.
        5. Produces a deterministic gist_id from member content hashes.

    The function is idempotent: running it twice on the same input
    produces the same gists (same gist_ids, same members, same cohesion).

    Args:
        memories: List of memory dicts, each with at least an 'id' (or
            'memory_id') key and a 'content' key. Metadata is preserved.
        similarity_threshold: Minimum cosine similarity for two memories
            to be placed in the same cluster. Default 0.65.

    Returns:
        A list of gist dicts, each with keys:
            - 'gist_id': Deterministic ID (hash of sorted member IDs).
            - 'label': Truncated content of the first member.
            - 'member_ids': List of member memory IDs.
            - 'member_count': Number of members.
            - 'cohesion': Average pairwise similarity (float).
            - 'gist_vector': The bundled gist vector (list[float]).
            - 'method': Always 'vsa_bundle'.
            - 'consolidation': 'vsa' (distinguishes from LLM consolidation).
    """
    if not memories:
        return []

    # Encode all memories as deterministic vectors
    encoded: list[tuple[str, dict[str, Any], list[float]]] = []
    for mem in memories:
        mid = mem.get("id") or mem.get("memory_id") or ""
        content = mem.get("content", "")
        vec = _deterministic_vector(content)
        encoded.append((mid, mem, vec))

    # Greedy clustering by cosine similarity
    clusters: list[list[tuple[str, dict[str, Any], list[float]]]] = []
    used: set[str] = set()

    for i, (mid, mem, vec) in enumerate(encoded):
        if mid in used:
            continue
        cluster = [(mid, mem, vec)]
        used.add(mid)
        for j in range(i + 1, len(encoded)):
            other_mid, other_mem, other_vec = encoded[j]
            if other_mid in used:
                continue
            sim = Consolidator._cosine(vec, other_vec)
            if sim >= similarity_threshold:
                cluster.append((other_mid, other_mem, other_vec))
                used.add(other_mid)
        clusters.append(cluster)

    # Build gist vectors for clusters with 2+ members
    cohesion_metric = CohesionMetric()
    gists: list[dict[str, Any]] = []

    for cluster in clusters:
        if len(cluster) < 2:
            # Single memories don't need consolidation into a gist
            continue

        member_ids = sorted(m[0] for m in cluster)
        vectors = [m[2] for m in cluster]

        # Bundle into gist vector
        gist_vec = _bundle_vectors(vectors)

        # Compute cohesion
        cohesion = cohesion_metric.compute(vectors)

        # Deterministic gist_id from sorted member IDs
        id_hash = hashlib.sha256(
            "|".join(member_ids).encode("utf-8"),
        ).hexdigest()[:16]
        gist_id = f"vsa-gist-{id_hash}"

        # Label from first member's content (truncated)
        label = cluster[0][1].get("content", "")[:80]

        gists.append({
            "gist_id": gist_id,
            "label": label,
            "member_ids": member_ids,
            "member_count": len(member_ids),
            "cohesion": round(cohesion, 4),
            "gist_vector": gist_vec,
            "method": "vsa_bundle",
            "consolidation": "vsa",
        })

        logger.info(
            f"VSA consolidated {len(member_ids)} memories into "
            f"gist {gist_id} (cohesion={cohesion:.3f})"
        )

    return gists
