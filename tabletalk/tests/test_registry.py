"""Tests for tabletalk/registry.py"""
from __future__ import annotations

import pytest

from tabletalk.registry import (
    agent_has_permission,
    get_agent,
    list_agents,
    ping_agent,
    register_agent,
    remove_agent,
)


class TestRegisterAgent:
    def test_register_creates_entry(self, tmp_path):
        entry = register_agent(str(tmp_path), "alice")
        assert entry["name"] == "alice"
        assert "registered_at" in entry
        assert "updated_at" in entry

    def test_register_with_manifest_and_permissions(self, tmp_path):
        entry = register_agent(
            str(tmp_path), "bob",
            manifest="sales.txt",
            permissions=["read", "execute"],
            description="Finance bot",
        )
        assert entry["manifest"] == "sales.txt"
        assert "execute" in entry["permissions"]
        assert entry["description"] == "Finance bot"

    def test_default_permissions(self, tmp_path):
        entry = register_agent(str(tmp_path), "charlie")
        assert entry["permissions"] == ["read"]

    def test_update_existing_agent(self, tmp_path):
        register_agent(str(tmp_path), "alice", description="v1")
        updated = register_agent(str(tmp_path), "alice", description="v2")
        assert updated["description"] == "v2"

    def test_multiple_agents(self, tmp_path):
        register_agent(str(tmp_path), "a")
        register_agent(str(tmp_path), "b")
        agents = list_agents(str(tmp_path))
        names = [a["name"] for a in agents]
        assert "a" in names and "b" in names


class TestGetAgent:
    def test_get_existing(self, tmp_path):
        register_agent(str(tmp_path), "x")
        entry = get_agent(str(tmp_path), "x")
        assert entry is not None
        assert entry["name"] == "x"

    def test_get_missing_returns_none(self, tmp_path):
        assert get_agent(str(tmp_path), "nobody") is None


class TestListAgents:
    def test_empty_registry(self, tmp_path):
        assert list_agents(str(tmp_path)) == []

    def test_sorted_by_name(self, tmp_path):
        register_agent(str(tmp_path), "zeta")
        register_agent(str(tmp_path), "alpha")
        names = [a["name"] for a in list_agents(str(tmp_path))]
        assert names == sorted(names)


class TestRemoveAgent:
    def test_remove_existing(self, tmp_path):
        register_agent(str(tmp_path), "del_me")
        assert remove_agent(str(tmp_path), "del_me") is True
        assert get_agent(str(tmp_path), "del_me") is None

    def test_remove_missing_returns_false(self, tmp_path):
        assert remove_agent(str(tmp_path), "nobody") is False


class TestPingAgent:
    def test_ping_updates_last_seen(self, tmp_path):
        register_agent(str(tmp_path), "pinger")
        entry = ping_agent(str(tmp_path), "pinger")
        assert entry is not None
        assert "last_seen" in entry

    def test_ping_unknown_agent_returns_none(self, tmp_path):
        assert ping_agent(str(tmp_path), "ghost") is None


class TestAgentHasPermission:
    def test_read_permission_granted(self, tmp_path):
        register_agent(str(tmp_path), "reader", permissions=["read"])
        assert agent_has_permission(str(tmp_path), "reader", "read") is True

    def test_permission_denied(self, tmp_path):
        register_agent(str(tmp_path), "reader", permissions=["read"])
        assert agent_has_permission(str(tmp_path), "reader", "execute") is False

    def test_admin_passes_all(self, tmp_path):
        register_agent(str(tmp_path), "admin_bot", permissions=["admin"])
        assert agent_has_permission(str(tmp_path), "admin_bot", "execute") is True
        assert agent_has_permission(str(tmp_path), "admin_bot", "read") is True

    def test_unknown_agent_denied(self, tmp_path):
        assert agent_has_permission(str(tmp_path), "nobody", "read") is False
