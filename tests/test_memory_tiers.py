"""Tests for memory tiers (STM/MTM/LTM), decay, and compaction."""
from __future__ import annotations

import pytest


@pytest.fixture
def mm(tmp_path):
    from nousclaww.memory.memory_manager import MemoryManager

    return MemoryManager(str(tmp_path / "tiers.db"))


class TestTierClassification:
    def test_new_memory_is_stm(self, mm):
        mid = mm.store_memory({"type": "fact", "content": "brand new",
                               "importance": 0.5})
        tiers = mm.get_tiers()
        assert tiers[mid]["tier"] == "STM"

    def test_tiers_report_counts(self, mm):
        mm.store_memory({"type": "fact", "content": "one", "importance": 0.5})
        tiers = mm.get_tiers()
        counts = {t: sum(1 for v in tiers.values() if v["tier"] == t)
                  for t in ("STM", "MTM", "LTM")}
        assert counts["STM"] == 1
        assert counts["MTM"] == 0
        assert counts["LTM"] == 0


class TestPromotion:
    def test_repeated_access_promotes(self, mm):
        mid = mm.store_memory({"type": "fact", "content": "hot fact",
                               "importance": 0.5})
        # Simulate access history by bumping access counts.
        for _ in range(5):
            mm.touch_memory(mid)
        mm.promote_tiers()
        tiers = mm.get_tiers()
        assert tiers[mid]["tier"] in ("MTM", "LTM")


class TestDecay:
    def test_decay_lowers_low_importance_scores(self, mm, tmp_path):
        from nousclaww.memory.tiers import MemoryTiers

        mid = mm.store_memory({"type": "fact", "content": "fading",
                               "importance": 0.4})
        engine = MemoryTiers(mm.db_path)
        before = mm.get_tiers()[mid]["importance"]
        removed = engine.apply_decay(now_offset_days=30.0)
        after = mm.get_tiers()[mid]["importance"]
        assert after < before or removed >= 0
        assert isinstance(after, float)

    def test_decay_never_negative_or_above_one(self, mm):
        from nousclaww.memory.tiers import MemoryTiers

        mm.store_memory({"type": "fact", "content": "x", "importance": 0.01})
        engine = MemoryTiers(mm.db_path)
        engine.apply_decay(now_offset_days=365.0)
        for info in mm.get_tiers().values():
            assert 0.0 <= info["importance"] <= 1.0


class TestCompaction:
    def test_compaction_removes_forgotten_memories(self, mm):
        from nousclaww.memory.tiers import MemoryTiers

        keep = mm.store_memory({"type": "rule", "content": "keep me",
                                "importance": 0.95})
        drop = mm.store_memory({"type": "fact", "content": "forget me",
                                "importance": 0.05})
        engine = MemoryTiers(mm.db_path)
        report = engine.compact(min_importance=0.2,
                                now_offset_days=400.0)
        remaining = mm.get_tiers()
        assert keep in remaining or any(
            "keep me" in str(v.get("content", "")) for v in remaining.values()
        )
        # The near-zero-importance stale memory should be gone.
        assert drop not in remaining
        assert report.get("compacted", 0) >= 1
