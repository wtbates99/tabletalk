"""Tests for tabletalk/cache.py"""
from __future__ import annotations

import time

import pytest

from tabletalk.cache import ResultCache, get_default_cache


class TestResultCache:
    def test_get_miss_returns_none(self):
        cache = ResultCache()
        assert cache.get("sales.txt", "SELECT 1") is None

    def test_set_and_get(self):
        cache = ResultCache()
        rows = [{"id": 1, "name": "Alice"}]
        cache.set("sales.txt", "SELECT * FROM orders", rows)
        result = cache.get("sales.txt", "SELECT * FROM orders")
        assert result == rows

    def test_case_insensitive_sql_keys(self):
        cache = ResultCache()
        rows = [{"x": 1}]
        cache.set("m.txt", "select 1", rows)
        assert cache.get("m.txt", "SELECT 1") == rows
        assert cache.get("m.txt", "select 1") == rows

    def test_different_manifests_isolated(self):
        cache = ResultCache()
        rows_a = [{"a": 1}]
        rows_b = [{"b": 2}]
        cache.set("a.txt", "SELECT 1", rows_a)
        cache.set("b.txt", "SELECT 1", rows_b)
        assert cache.get("a.txt", "SELECT 1") == rows_a
        assert cache.get("b.txt", "SELECT 1") == rows_b

    def test_expiry(self):
        cache = ResultCache(ttl=1)
        cache.set("m.txt", "SELECT 1", [{"x": 1}])
        assert cache.get("m.txt", "SELECT 1") is not None
        time.sleep(1.05)
        assert cache.get("m.txt", "SELECT 1") is None

    def test_stats_hit_rate(self):
        cache = ResultCache()
        cache.set("m.txt", "SELECT 1", [{"x": 1}])
        cache.get("m.txt", "SELECT 1")  # hit
        cache.get("m.txt", "SELECT 2")  # miss
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_stats_size(self):
        cache = ResultCache()
        cache.set("m.txt", "SELECT 1", [])
        cache.set("m.txt", "SELECT 2", [])
        assert cache.stats()["size"] == 2

    def test_invalidate_all(self):
        cache = ResultCache()
        cache.set("a.txt", "SELECT 1", [{"x": 1}])
        cache.set("b.txt", "SELECT 2", [{"y": 2}])
        removed = cache.invalidate()
        assert removed == 2
        assert cache.get("a.txt", "SELECT 1") is None

    def test_sweep_removes_expired(self):
        cache = ResultCache(ttl=1)
        cache.set("m.txt", "SELECT 1", [{"x": 1}])
        time.sleep(1.05)
        removed = cache.sweep()
        assert removed == 1
        assert cache.stats()["size"] == 0

    def test_max_entries_eviction(self):
        cache = ResultCache(max_entries=3)
        for i in range(5):
            cache.set("m.txt", f"SELECT {i}", [{"x": i}])
        # Should not exceed max_entries
        assert cache.stats()["size"] <= 3

    def test_whitespace_normalization(self):
        cache = ResultCache()
        rows = [{"x": 1}]
        cache.set("m.txt", "  SELECT   1  ", rows)
        assert cache.get("m.txt", "SELECT 1") == rows


class TestGetDefaultCache:
    def test_returns_same_instance(self):
        c1 = get_default_cache()
        c2 = get_default_cache()
        assert c1 is c2
