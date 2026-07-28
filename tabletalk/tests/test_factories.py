"""
Tests for tabletalk/factories.py

Covers:
  - resolve_env_vars
  - get_llm_provider  (openai-compatible, openai, ollama)
  - get_db_provider   (sqlite, duckdb — no external services needed)
  - Error paths: unsupported types and missing environment variables
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tabletalk.factories import (
    get_db_provider,
    get_llm_provider,
    resolve_env_vars,
)

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
        result = resolve_env_vars("snowflake://${DB}/schema")
        assert result == "snowflake://mydb/schema"

    def test_value_without_braces_not_replaced(self):
        """$VAR without braces is left as-is."""
        result = resolve_env_vars("$NO_BRACES")
        assert result == "$NO_BRACES"


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

    def test_ollama_defaults_to_free_cloud_model(self):
        with patch("tabletalk.providers.openai_provider.OpenAI"):
            provider = get_llm_provider({"provider": "ollama"})

        assert provider.model == "gemma4:31b-cloud"
        assert provider.base_url == "http://localhost:11434/v1"
        assert provider.provider_name == "ollama"
        assert provider.reasoning_effort == "none"

    def test_openai_compatible_requires_explicit_runtime_identity(self):
        with pytest.raises(ValueError, match="base_url, api_key, model"):
            get_llm_provider({"provider": "openai-compatible"})

    def test_openai_compatible_uses_exact_configured_endpoint(self):
        with patch("tabletalk.providers.openai_provider.OpenAI") as client:
            provider = get_llm_provider(
                {
                    "provider": "openai-compatible",
                    "base_url": "https://models.example.test/v1",
                    "api_key": "test-key",
                    "model": "production-model",
                    "request_timeout_seconds": 17,
                }
            )

        assert provider.provider_name == "openai-compatible"
        assert provider.model == "production-model"
        assert provider.base_url == "https://models.example.test/v1"
        client.assert_called_once_with(
            api_key="test-key",
            base_url="https://models.example.test/v1",
            timeout=17.0,
        )

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
        provider = get_db_provider(
            {"type": "sqlite", "database_path": db_path, "read_only": False}
        )
        from tabletalk.providers.sqlite_provider import SQLiteProvider

        assert isinstance(provider, SQLiteProvider)

    def test_sqlite_memory(self):
        provider = get_db_provider({"type": "sqlite", "database_path": ":memory:"})
        from tabletalk.providers.sqlite_provider import SQLiteProvider

        assert isinstance(provider, SQLiteProvider)

    def test_sqlite_can_execute(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        provider = get_db_provider(
            {"type": "sqlite", "database_path": db_path, "read_only": False}
        )
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

    def test_env_var_resolved_in_db_config(self, monkeypatch, tmp_path):
        db_path = str(tmp_path / "test.db")
        monkeypatch.setenv("DB_PATH", db_path)
        provider = get_db_provider(
            {
                "type": "sqlite",
                "database_path": "${DB_PATH}",
                "read_only": False,
            }
        )
        from tabletalk.providers.sqlite_provider import SQLiteProvider

        assert isinstance(provider, SQLiteProvider)
