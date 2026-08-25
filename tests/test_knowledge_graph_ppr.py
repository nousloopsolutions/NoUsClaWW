"""Tests for PPR (personalized PageRank) graph traversal on the knowledge graph."""
from __future__ import annotations

import pytest


@pytest.fixture
def kg(tmp_path):
    from nousclaww.memory.knowledge_graph import KnowledgeGraph

    return KnowledgeGraph(str(tmp_path / "ppr_kg.db"))


@pytest.fixture
def small_graph(kg):
    """hub -- a,b,c; b -- d. Returns entity ids."""
    hub = kg.add_entity(name="hub", entity_type="concept")
    a = kg.add_entity(name="a", entity_type="concept")
    b = kg.add_entity(name="b", entity_type="concept")
    c = kg.add_entity(name="c", entity_type="concept")
    d = kg.add_entity(name="d", entity_type="concept")
    for target in (a, b, c):
        kg.add_relationship(source_id=hub, target_id=target,
                            relation_type="relates_to")
    kg.add_relationship(source_id=b, target_id=d, relation_type="relates_to")
    return {"hub": hub, "a": a, "b": b, "c": c, "d": d}


class TestPersonalizedPageRank:
    def test_returns_scores_for_all_reachable_nodes(self, kg, small_graph):
        scores = kg.personalized_pagerank([small_graph["hub"]])
        assert set(scores.keys()) >= set(small_graph.values())
        assert all(s >= 0.0 for s in scores.values())

    def test_hub_ranks_highest_from_self_restart(self, kg, small_graph):
        scores = kg.personalized_pagerank([small_graph["hub"]])
        assert scores[small_graph["hub"]] == max(scores.values())

    def test_direct_neighbors_outrank_second_hop(self, kg, small_graph):
        scores = kg.personalized_pagerank([small_graph["hub"]])
        # a and b are direct neighbors; d is a second hop through b.
        assert scores[small_graph["a"]] > scores[small_graph["d"]]

    def test_multiple_personalization_nodes(self, kg, small_graph):
        scores = kg.personalized_pagerank(
            [small_graph["a"], small_graph["c"]], damping=0.85
        )
        assert scores[small_graph["a"]] > 0
        assert scores[small_graph["c"]] > 0

    def test_deterministic_given_same_graph(self, kg, small_graph):
        s1 = kg.personalized_pagerank([small_graph["hub"]])
        s2 = kg.personalized_pagerank([small_graph["hub"]])
        assert s1 == s2

    def test_empty_personalization_raises(self, kg):
        with pytest.raises(ValueError):
            kg.personalized_pagerank([])

    def test_isolated_node_gets_only_restart_mass(self, kg, small_graph):
        lone = kg.add_entity(name="lone", entity_type="concept")
        scores = kg.personalized_pagerank([lone])
        assert scores[lone] == pytest.approx(1.0)
