"""
Tests for tabletalk/factories.py

Covers:
  - resolve_env_vars
  - _resolve_profile
  - get_llm_provider  (openai, anthropic, ollama)
  - get_db_provider   (sqlite, duckdb — no external services needed)
  - Error paths: unsupported types, missing env vars, unknown profiles
"""
from __future__ import annotations

import os
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from tabletalk.factories import _resolve_profile, get_db_provider, get_llm_provider, resolve_env_vars


# ── resolve_env_vars ──────────────────────────────────────────────────────────

class TestResolveEnvVars:
    def test_no_placeholders(self):
        assert resolve_env_vars("hello world") == "hello world"

    def test_single_placeholder(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret")
        assert resolve_env_vars("${MY_KEY}") == "secret"

    def test_multiple_placeholders(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "5432")
        result = resolve_env_vars("${HOST}:${PORT}")
        assert result == "localhost:5432"

    def test_missing_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        with pytest.raises(ValueError, match="Environment variable 'MISSING_VAR' is not set"):
            resolve_env_vars("${MISSING_VAR}")

    def test_non_string_passthrough(self):
        # Non-strings should not be processed (type: ignore in practice, but test the int path)
        assert resolve_env_vars(42) == 42  # type: ignore[arg-type]

    def test_partial_replacement(self, monkeypatch):
        monkeypatch.setenv("DB", "mydb")
        result = resolve_env_vars("postgres://${DB}/schema")
        assert result == "postgres://mydb/schema"

    def test_value_without_braces_not_replaced(self):
        """$VAR without braces is left as-is."""
        result = resolve_env_vars("$NO_BRACES")
        assert result == "$NO_BRACES"


# ── _resolve_profile ──────────────────────────────────────────────────────────

class TestResolveProfile:
    def test_no_profile_key_returns_config_unchanged(self):
        config = {"type": "sqlite", "database_path": ":memory:"}
        result = _resolve_profile(config)
        assert result == config

    def test_resolves_existing_profile(self):
        fake_profile = {"type": "postgres", "host": "db.example.com"}
        with patch("tabletalk.profiles.get_profile", return_value=fake_profile):
            result = _resolve_profile({"profile": "my_profile"})
        assert result == fake_profile

    def test_missing_profile_raises(self):
        with patch("tabletalk.profiles.get_profile", return_value=None):
            with pytest.raises(ValueError, match="Profile 'ghost' not found"):
                _resolve_profile({"profile": "ghost"})

    def test_empty_profile_key_returns_config(self):
        config = {"profile": "", "type": "sqlite"}
        result = _resolve_profile(config)
        assert result == config


# ── get_llm_provider ──────────────────────────────────────────────────────────

class TestGetLLMProvider:
    def test_openai_provider(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        with patch("tabletalk.providers.openai_provider.OpenAI"):
            provider = get_llm_provider(
                {
                    "provider": "openai",
                    "api_key": "${OPENAI_API_KEY}",
                    "model": "gpt-4o",
                    "max_tokens": 500,
                    "temperature": 0,
                }
            )
        from tabletalk.providers.openai_provider import OpenAIProvider
        assert isinstance(provider, OpenAIProvider)

    def test_anthropic_provider(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_KEY", "sk-ant-test")
        with patch("tabletalk.providers.anthropic_provider.Anthropic"):
            provider = get_llm_provider(
                {
                    "provider": "anthropic",
                    "api_key": "${ANTHROPIC_KEY}",
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 1000,
                    "temperature": 0,
                }
            )
        from tabletalk.providers.anthropic_provider import AnthropicProvider
        assert isinstance(provider, AnthropicProvider)

    def test_ollama_provider(self):
        with patch("tabletalk.providers.openai_provider.OpenAI"):
            provider = get_llm_provider(
                {
                    "provider": "ollama",
                    "model": "llama3",
                    "base_url": "http://localhost:11434/v1",
                }
            )
        from tabletalk.providers.openai_provider import OpenAIProvider
        assert isinstance(provider, OpenAIProvider)

    def test_unsupported_provider_raises(self):
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            get_llm_provider({"provider": "grok", "api_key": "x"})

    def test_defaults_applied(self, monkeypatch):
        """max_tokens and temperature get sensible defaults."""
        monkeypatch.setenv("KEY", "test")
        with patch("tabletalk.providers.openai_provider.OpenAI"):
            provider = get_llm_provider({"provider": "openai", "api_key": "${KEY}"})
        from tabletalk.providers.openai_provider import OpenAIProvider
        assert isinstance(provider, OpenAIProvider)
        assert provider.max_tokens == 1000
        assert provider.temperature == 0.0

    def test_custom_max_tokens(self, monkeypatch):
        monkeypatch.setenv("KEY", "test")
        with patch("tabletalk.providers.openai_provider.OpenAI"):
            provider = get_llm_provider(
                {"provider": "openai", "api_key": "${KEY}", "max_tokens": 2000}
            )
        assert provider.max_tokens == 2000


# ── get_db_provider ───────────────────────────────────────────────────────────

class TestGetDBProviderSQLite:
    def test_sqlite_provider(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        provider = get_db_provider({"type": "sqlite", "database_path": db_path})
        from tabletalk.providers.sqlite_provider import SQLiteProvider
        assert isinstance(provider, SQLiteProvider)

    def test_sqlite_memory(self):
        provider = get_db_provider({"type": "sqlite", "database_path": ":memory:"})
        from tabletalk.providers.sqlite_provider import SQLiteProvider
        assert isinstance(provider, SQLiteProvider)

    def test_sqlite_can_execute(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        provider = get_db_provider({"type": "sqlite", "database_path": db_path})
        results = provider.execute_query("SELECT 42 AS answer")
        assert results[0]["answer"] == 42


class TestGetDBProviderDuckDB:
    def test_duckdb_in_memory(self):
        pytest.importorskip("duckdb")
        provider = get_db_provider({"type": "duckdb", "database_path": ":memory:"})
        from tabletalk.providers.duckdb_provider import DuckDBProvider
        assert isinstance(provider, DuckDBProvider)

    def test_duckdb_default_path(self):
        pytest.importorskip("duckdb")
        # No database_path key — should default to :memory:
        provider = get_db_provider({"type": "duckdb"})
        from tabletalk.providers.duckdb_provider import DuckDBProvider
        assert isinstance(provider, DuckDBProvider)

    def test_duckdb_can_execute(self):
        pytest.importorskip("duckdb")
        provider = get_db_provider({"type": "duckdb", "database_path": ":memory:"})
        results = provider.execute_query("SELECT 'hello' AS greeting")
        assert results[0]["greeting"] == "hello"


class TestGetDBProviderErrors:
    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported database provider"):
            get_db_provider({"type": "oracle"})

    def test_profile_resolution_on_db_provider(self):
        fake_profile = {"type": "sqlite", "database_path": ":memory:"}
        with patch("tabletalk.profiles.get_profile", return_value=fake_profile):
            provider = get_db_provider({"profile": "my_profile"})
        from tabletalk.providers.sqlite_provider import SQLiteProvider
        assert isinstance(provider, SQLiteProvider)

    def test_env_var_resolved_in_db_config(self, monkeypatch, tmp_path):
        db_path = str(tmp_path / "test.db")
        monkeypatch.setenv("DB_PATH", db_path)
        provider = get_db_provider({"type": "sqlite", "database_path": "${DB_PATH}"})
        from tabletalk.providers.sqlite_provider import SQLiteProvider
        assert isinstance(provider, SQLiteProvider)
