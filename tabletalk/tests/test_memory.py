"""Tests for tabletalk/memory.py"""
from __future__ import annotations

import pytest

from tabletalk.memory import (
    clear_facts,
    delete_fact,
    get_fact,
    list_agents_with_memory,
    list_facts,
    set_fact,
)


class TestSetAndGetFact:
    def test_set_and_get(self, tmp_path):
        set_fact(str(tmp_path), "alice", "tz", "UTC")
        assert get_fact(str(tmp_path), "alice", "tz") == "UTC"

    def test_get_missing_returns_default(self, tmp_path):
        assert get_fact(str(tmp_path), "alice", "missing") is None

    def test_get_missing_custom_default(self, tmp_path):
        assert get_fact(str(tmp_path), "alice", "missing", default="fallback") == "fallback"

    def test_overwrite_fact(self, tmp_path):
        set_fact(str(tmp_path), "alice", "x", "v1")
        set_fact(str(tmp_path), "alice", "x", "v2")
        assert get_fact(str(tmp_path), "alice", "x") == "v2"

    def test_different_agents_isolated(self, tmp_path):
        set_fact(str(tmp_path), "alice", "k", "alice_val")
        set_fact(str(tmp_path), "bob", "k", "bob_val")
        assert get_fact(str(tmp_path), "alice", "k") == "alice_val"
        assert get_fact(str(tmp_path), "bob", "k") == "bob_val"

    def test_dict_value(self, tmp_path):
        set_fact(str(tmp_path), "alice", "prefs", {"theme": "dark"})
        val = get_fact(str(tmp_path), "alice", "prefs")
        assert val == {"theme": "dark"}

    def test_list_value(self, tmp_path):
        set_fact(str(tmp_path), "alice", "items", [1, 2, 3])
        assert get_fact(str(tmp_path), "alice", "items") == [1, 2, 3]


class TestDeleteFact:
    def test_delete_existing(self, tmp_path):
        set_fact(str(tmp_path), "alice", "k", "v")
        assert delete_fact(str(tmp_path), "alice", "k") is True
        assert get_fact(str(tmp_path), "alice", "k") is None

    def test_delete_missing_returns_false(self, tmp_path):
        assert delete_fact(str(tmp_path), "alice", "nope") is False


class TestListFacts:
    def test_list_empty(self, tmp_path):
        assert list_facts(str(tmp_path), "alice") == []

    def test_list_multiple(self, tmp_path):
        set_fact(str(tmp_path), "alice", "a", 1)
        set_fact(str(tmp_path), "alice", "b", 2)
        facts = list_facts(str(tmp_path), "alice")
        assert len(facts) == 2
        keys = [f["key"] for f in facts]
        assert "a" in keys and "b" in keys

    def test_list_includes_updated_at(self, tmp_path):
        set_fact(str(tmp_path), "alice", "k", "v")
        facts = list_facts(str(tmp_path), "alice")
        assert facts[0]["updated_at"] is not None


class TestClearFacts:
    def test_clear_all(self, tmp_path):
        set_fact(str(tmp_path), "alice", "a", 1)
        set_fact(str(tmp_path), "alice", "b", 2)
        count = clear_facts(str(tmp_path), "alice")
        assert count == 2
        assert list_facts(str(tmp_path), "alice") == []

    def test_clear_empty(self, tmp_path):
        assert clear_facts(str(tmp_path), "alice") == 0


class TestListAgentsWithMemory:
    def test_no_agents(self, tmp_path):
        assert list_agents_with_memory(str(tmp_path)) == []

    def test_returns_agent_names(self, tmp_path):
        set_fact(str(tmp_path), "alice", "k", "v")
        set_fact(str(tmp_path), "bob", "k", "v")
        agents = list_agents_with_memory(str(tmp_path))
        assert "alice" in agents
        assert "bob" in agents

    def test_sorted(self, tmp_path):
        set_fact(str(tmp_path), "zeta", "k", "v")
        set_fact(str(tmp_path), "alpha", "k", "v")
        agents = list_agents_with_memory(str(tmp_path))
        assert agents == sorted(agents)
