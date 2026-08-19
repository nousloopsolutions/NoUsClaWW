"""Self-test engine — measure memory recall quality over time.

Generates test queries from stored memory content, runs them against the
memory retrieval system, and measures whether the correct memory is found
in the top-K results. This provides an empirical signal for memory health:
if recall quality degrades over time, it indicates that the memory store
is growing noisy, importance scores are drifting, or retrieval is failing.

The self-test is a scientific measurement, not a training loop:
    - Queries are generated deterministically from memory content (no LLM
      needed for generation, keeping it local-first).
    - Each query has a known expected memory_id (the ground truth).
    - Recall is measured at K=5 (accuracy@5): was the correct memory in
      the top 5 results?
    - Results include per-query detail for debugging which memories are
      hard to retrieve.

Contract:
    - Query generation is local (no LLM calls, no network).
    - The test uses memory_manager.search_memories() for retrieval.
    - Results are returned as a structured dict with aggregate metrics
      and per-query detail.
    - If the memory store is empty, returns zeros with total=0.
    - Never fabricates results — missed queries are reported as missed.

SYNTH:
    purpose: Measure memory recall quality by generating test queries from memory content and checking retrieval accuracy at K
    axioms: [evidence_over_intuition, scientific_method, honest_failure_over_fake_success, epistemic_boundary, local_first]
    objective: Provide an empirical, reproducible signal for memory retrieval health so degradation can be detected and addressed
    anti_patterns:
        - Using LLM to generate queries (breaks local-first and reproducibility)
        - Measuring recall without a ground truth (unfalsifiable)
        - Hiding missed queries or inflating accuracy
        - Running self-test on an empty memory store without reporting total=0
        - Modifying memories during the test (side effects)

#C Inspired by PMB (Project Memory Bank) sleep engine
"""

from __future__ import annotations

import logging
import re
from typing import Any

from nousclaww.memory.memory_manager import MemoryManager
from nousclaww.text_match import tokenize, distinctive_tokens

logger = logging.getLogger(__name__)


class SelfTest:
    """Generate test queries from memories and measure retrieval accuracy.

    Usage:
        tester = SelfTest()
        memories = memory_manager.get_all_memories()
        queries = tester.generate_queries(memories)
        results = tester.run(memory_manager, n_queries=20)
        print(f"Accuracy@5: {results['acc_at_5']:.1%} ({results['found']}/{results['total']})")
    """

    # Default K for accuracy@K measurement.
    DEFAULT_K = 5

    # Minimum content length for a memory to be used in self-test.
    MIN_CONTENT_LEN = 10

    # Minimum number of distinctive tokens in content to generate a query.
    MIN_DISTINCTIVE_TOKENS = 2

    def __init__(self, k: int = DEFAULT_K) -> None:
        """Initialize the self-test engine.

        Args:
            k: The K value for accuracy@K measurement. Default 5.
                A memory is "found" if it appears in the top K search
                results for its generated query.
        """
        self.k = max(1, int(k))

    # ── Public API ─────────────────────────────────────────────────────────

    def generate_queries(self, memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create test queries from memory content.

        For each memory, generates one or more queries by extracting
        the most distinctive tokens from its content. Each query dict
        contains the query string and the expected memory_id (ground truth).

        Query generation strategies (applied in order, first viable wins):
            1. Subject-based: if the memory has a 'subject' field, use it
               as the query (tests subject-based retrieval).
            2. Distinctive-token query: take the top N distinctive tokens
               from the content and join them as a query.
            3. Content-prefix query: take the first few words of the
               content as a query (tests prefix matching).

        Args:
            memories: List of memory dicts to generate queries from.

        Returns:
            List of query dicts, each with:
                - 'query': the search string
                - 'expected_memory_id': the ground truth memory_id
                - 'expected_content': the memory content (for debugging)
                - 'strategy': which generation strategy was used
        """
        queries: list[dict[str, Any]] = []

        for memory in memories:
            memory_id = memory.get("memory_id")
            if not memory_id:
                continue

            content = memory.get("content", "").strip()
            if len(content) < self.MIN_CONTENT_LEN:
                continue

            # Strategy 1: Subject-based query
            subject = memory.get("subject", "").strip()
            if subject and len(subject) >= 3:
                queries.append({
                    "query": subject,
                    "expected_memory_id": memory_id,
                    "expected_content": content,
                    "strategy": "subject",
                })
                continue

            # Strategy 2: Distinctive-token query
            tokens = distinctive_tokens(content)
            if len(tokens) >= self.MIN_DISTINCTIVE_TOKENS:
                # Take up to 5 distinctive tokens, sorted by length
                # (longer tokens are more specific)
                sorted_tokens = sorted(tokens, key=len, reverse=True)
                query = " ".join(sorted_tokens[:5])
                queries.append({
                    "query": query,
                    "expected_memory_id": memory_id,
                    "expected_content": content,
                    "strategy": "distinctive_tokens",
                })
                continue

            # Strategy 3: Content-prefix query
            words = content.split()
            if len(words) >= 2:
                # Take first 3-4 words as query
                prefix_len = min(4, len(words))
                query = " ".join(words[:prefix_len])
                queries.append({
                    "query": query,
                    "expected_memory_id": memory_id,
                    "expected_content": content,
                    "strategy": "content_prefix",
                })

        return queries

    def run(
        self,
        memory_manager: MemoryManager,
        n_queries: int = 20,
    ) -> dict[str, Any]:
        """Run the self-test and return accuracy metrics.

        Generates queries from all stored memories, samples up to
        n_queries of them, runs each through memory_manager.search_memories(),
        and checks whether the expected memory appears in the top K results.

        Args:
            memory_manager: The MemoryManager to test.
            n_queries: Maximum number of queries to run. Default 20.
                Set to 0 or negative to run all generated queries.

        Returns:
            A dict with:
                - 'acc_at_5': float, fraction of queries where the
                  expected memory was found in top K results.
                - 'total': int, number of queries run.
                - 'found': int, number of queries where the expected
                  memory was in top K.
                - 'missed': int, number of queries where it was not.
                - 'k': the K value used.
                - 'details': list of per-query dicts with 'query',
                  'expected_memory_id', 'found' (bool), 'rank' (int or
                  None), and 'top_results' (list of memory_ids).
        """
        # Fetch all memories
        memories = memory_manager.get_all_memories()

        if not memories:
            logger.info("SelfTest: no memories to test")
            return {
                "acc_at_5": 0.0,
                "total": 0,
                "found": 0,
                "missed": 0,
                "k": self.k,
                "details": [],
            }

        # Generate queries
        all_queries = self.generate_queries(memories)

        if not all_queries:
            logger.info("SelfTest: no viable queries could be generated")
            return {
                "acc_at_5": 0.0,
                "total": 0,
                "found": 0,
                "missed": 0,
                "k": self.k,
                "details": [],
            }

        # Sample queries (deterministic — take evenly spaced)
        if n_queries > 0 and len(all_queries) > n_queries:
            queries = self._sample_queries(all_queries, n_queries)
        else:
            queries = all_queries

        # Run each query and check results
        found = 0
        missed = 0
        details: list[dict[str, Any]] = []

        for query_info in queries:
            query = query_info["query"]
            expected_id = query_info["expected_memory_id"]

            # Search using the memory manager's search
            try:
                results = memory_manager.search_memories(query, limit=self.k)
            except Exception as exc:
                logger.warning(
                    f"SelfTest: search failed for query '{query}': {exc}"
                )
                results = []

            result_ids = [r.get("memory_id") for r in results]

            # Check if expected memory is in top K
            if expected_id in result_ids:
                rank = result_ids.index(expected_id) + 1
                found += 1
                details.append({
                    "query": query,
                    "expected_memory_id": expected_id,
                    "found": True,
                    "rank": rank,
                    "top_results": result_ids,
                    "strategy": query_info.get("strategy", "unknown"),
                })
            else:
                missed += 1
                details.append({
                    "query": query,
                    "expected_memory_id": expected_id,
                    "found": False,
                    "rank": None,
                    "top_results": result_ids,
                    "strategy": query_info.get("strategy", "unknown"),
                })

        total = found + missed
        acc_at_5 = found / total if total > 0 else 0.0

        logger.info(
            f"SelfTest: accuracy@{self.k} = {acc_at_5:.1%} "
            f"({found}/{total} found, {missed} missed)"
        )

        return {
            "acc_at_5": acc_at_5,
            "total": total,
            "found": found,
            "missed": missed,
            "k": self.k,
            "details": details,
        }

    # ── Internal helpers ───────────────────────────────────────────────────

    @staticmethod
    def _sample_queries(
        queries: list[dict[str, Any]], n: int,
    ) -> list[dict[str, Any]]:
        """Deterministically sample n queries from the full list.

        Uses even spacing to get a representative sample across all
        memories rather than just the first N (which would be biased
        toward high-importance memories since get_all_memories returns
        them sorted by importance).
        """
        if n >= len(queries):
            return queries
        if n <= 0:
            return []

        step = len(queries) / n
        sampled: list[dict[str, Any]] = []
        for i in range(n):
            index = int(i * step)
            # Avoid duplicates when step < 1
            if index >= len(queries):
                index = len(queries) - 1
            sampled.append(queries[index])
        return sampled

    # ── Diagnostics ─────────────────────────────────────────────────────────

    def get_query_summary(
        self, memory_manager: MemoryManager,
    ) -> dict[str, Any]:
        """Get a summary of queries that would be generated without running the test.

        Useful for understanding what the self-test will probe.

        Args:
            memory_manager: The MemoryManager to inspect.

        Returns:
            A dict with:
                - 'total_memories': total memory count
                - 'query_count': number of queries that would be generated
                - 'by_strategy': dict mapping strategy name to count
                - 'sample_queries': first 5 query strings for preview
        """
        memories = memory_manager.get_all_memories()
        queries = self.generate_queries(memories)

        by_strategy: dict[str, int] = {}
        for q in queries:
            strategy = q.get("strategy", "unknown")
            by_strategy[strategy] = by_strategy.get(strategy, 0) + 1

        sample = [q["query"] for q in queries[:5]]

        return {
            "total_memories": len(memories),
            "query_count": len(queries),
            "by_strategy": by_strategy,
            "sample_queries": sample,
        }
