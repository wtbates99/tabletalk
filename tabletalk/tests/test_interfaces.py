"""
Tests for tabletalk/interfaces.py

Covers:
  - _encode_field helper
  - _format_results_for_llm helper
  - QuerySession._clean_sql
  - QuerySession._build_messages
  - QuerySession.generate_sql  (mocked LLM)
  - QuerySession.generate_sql_stream  (mocked LLM)
  - QuerySession.generate_sql_conversational  (mocked LLM)
  - QuerySession.explain_results_stream  (mocked LLM)
  - QuerySession.fix_sql_stream  (mocked LLM)
  - QuerySession.suggest_questions  (mocked LLM)
  - QuerySession.save_history / get_history
  - QuerySession.save_favorite / get_favorites / delete_favorite
  - QuerySession.load_manifest
  - Parser.apply_schema  (full round-trip with SQLite)
"""
from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
import yaml

from tabletalk.interfaces import (
    Parser,
    QuerySession,
    _encode_field,
    _format_results_for_llm,
)
from tabletalk.tests.conftest import MockLLMProvider


# ── _encode_field ─────────────────────────────────────────────────────────────

class TestEncodeField:
    def test_simple_field(self):
        assert _encode_field({"n": "name", "t": "S"}) == "name:S"

    def test_primary_key(self):
        assert _encode_field({"n": "id", "t": "I", "pk": True}) == "id:I[PK]"

    def test_foreign_key(self):
        field = {"n": "customer_id", "t": "I", "fk": "customers.id"}
        assert _encode_field(field) == "customer_id:I[FK:customers.id]"

    def test_pk_and_fk(self):
        # unusual but valid
        field = {"n": "id", "t": "I", "pk": True, "fk": "other.id"}
        assert _encode_field(field) == "id:I[PK,FK:other.id]"

    def test_no_annotations(self):
        field = {"n": "created_at", "t": "TS"}
        assert _encode_field(field) == "created_at:TS"

    def test_float_type(self):
        assert _encode_field({"n": "price", "t": "F"}) == "price:F"

    def test_pk_false_not_annotated(self):
        # pk=False should not add [PK]
        field = {"n": "id", "t": "I", "pk": False}
        assert _encode_field(field) == "id:I"


# ── _format_results_for_llm ───────────────────────────────────────────────────

class TestFormatResultsForLLM:
    def test_empty_results(self):
        assert _format_results_for_llm([]) == "(empty)"

    def test_single_row(self):
        rows = [{"id": 1, "name": "Alice"}]
        result = _format_results_for_llm(rows)
        assert "id" in result
        assert "name" in result
        assert "Alice" in result

    def test_limit_enforced(self):
        rows = [{"n": i} for i in range(20)]
        result = _format_results_for_llm(rows, limit=5)
        assert "15 more rows" in result

    def test_no_truncation_when_under_limit(self):
        rows = [{"n": i} for i in range(5)]
        result = _format_results_for_llm(rows, limit=10)
        assert "more rows" not in result

    def test_long_values_truncated(self):
        rows = [{"col": "x" * 100}]
        result = _format_results_for_llm(rows)
        # Values are truncated at 30 chars
        assert "x" * 100 not in result
        assert "x" * 30 in result


# ── QuerySession helpers ───────────────────────────────────────────────────────

class TestQuerySessionCleanSQL:
    def test_strips_sql_fence(self):
        sql = "```sql\nSELECT 1\n```"
        assert QuerySession._clean_sql(sql) == "SELECT 1"

    def test_strips_plain_fence(self):
        sql = "```\nSELECT 1\n```"
        assert QuerySession._clean_sql(sql) == "SELECT 1"

    def test_no_fence(self):
        sql = "SELECT * FROM users"
        assert QuerySession._clean_sql(sql) == "SELECT * FROM users"

    def test_strips_whitespace(self):
        sql = "  SELECT 1  "
        assert QuerySession._clean_sql(sql) == "SELECT 1"

    def test_multiline_query(self):
        sql = "```sql\nSELECT id,\n  name\nFROM users\n```"
        assert QuerySession._clean_sql(sql) == "SELECT id,\n  name\nFROM users"


# ── QuerySession init & config ────────────────────────────────────────────────

class TestQuerySessionInit:
    def test_missing_config_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            QuerySession(str(tmp_path))

    def test_invalid_yaml_raises(self, tmp_path):
        (tmp_path / "tabletalk.yaml").write_text("not: valid: yaml: [")
        with pytest.raises(Exception):
            QuerySession(str(tmp_path))

    def test_missing_llm_config_raises(self, tmp_path):
        (tmp_path / "tabletalk.yaml").write_text("provider:\n  type: sqlite\n")
        with pytest.raises(ValueError, match="LLM configuration missing"):
            QuerySession(str(tmp_path))

    def test_loads_config_successfully(self, project_dir):
        """QuerySession initialises from a valid project directory."""
        with patch("tabletalk.factories.get_llm_provider") as mock_factory:
            mock_factory.return_value = MockLLMProvider()
            qs = QuerySession(project_dir)
        assert qs.project_folder == project_dir
        assert "provider" in qs.config


# ── QuerySession._build_messages ──────────────────────────────────────────────

class TestBuildMessages:
    def _make_qs(self, tmp_path, llm):
        config = {
            "provider": {"type": "sqlite", "database_path": ":memory:"},
            "llm": {"provider": "openai", "api_key": "test", "model": "gpt-4o"},
            "contexts": "contexts",
            "output": "manifest",
        }
        (tmp_path / "tabletalk.yaml").write_text(yaml.dump(config))
        with patch("tabletalk.factories.get_llm_provider", return_value=llm):
            qs = QuerySession(str(tmp_path))
        qs.llm_provider = llm
        return qs

    def test_no_history(self, tmp_path, mock_llm):
        qs = self._make_qs(tmp_path, mock_llm)
        msgs = qs._build_messages("schema content", "how many users?")
        assert msgs[0]["role"] == "system"
        assert "schema content" in msgs[0]["content"]
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "how many users?"

    def test_with_history(self, tmp_path, mock_llm):
        qs = self._make_qs(tmp_path, mock_llm)
        history = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "SELECT 1"},
        ]
        msgs = qs._build_messages("schema", "follow-up?", history)
        # system + 2 history + new user = 4 messages
        assert len(msgs) == 4
        assert msgs[1]["content"] == "first question"
        assert msgs[2]["content"] == "SELECT 1"
        assert msgs[3]["content"] == "follow-up?"


# ── QuerySession SQL generation ────────────────────────────────────────────────

class TestSQLGeneration:
    @pytest.fixture
    def qs(self, tmp_path, mock_llm):
        config = {
            "provider": {"type": "sqlite", "database_path": ":memory:"},
            "llm": {"provider": "openai", "api_key": "test"},
            "contexts": "contexts",
            "output": "manifest",
        }
        (tmp_path / "tabletalk.yaml").write_text(yaml.dump(config))
        with patch("tabletalk.factories.get_llm_provider", return_value=mock_llm):
            session = QuerySession(str(tmp_path))
        session.llm_provider = mock_llm
        return session

    def test_generate_sql_returns_string(self, qs):
        sql = qs.generate_sql("schema", "how many customers?")
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_generate_sql_cleans_fences(self, qs):
        qs.llm_provider = MockLLMProvider(default_response="```sql\nSELECT 1\n```")
        sql = qs.generate_sql("schema", "test")
        assert "```" not in sql

    def test_generate_sql_stream_yields_chunks(self, qs):
        chunks = list(qs.generate_sql_stream("schema", "test"))
        assert len(chunks) > 0
        assert all(isinstance(c, str) for c in chunks)

    def test_generate_sql_conversational_with_history(self, qs):
        history = [
            {"role": "user", "content": "first q"},
            {"role": "assistant", "content": "SELECT 1"},
        ]
        chunks = list(qs.generate_sql_conversational("schema", "follow up", history))
        assert len(chunks) > 0

    def test_generate_sql_llm_error_raises(self, qs):
        qs.llm_provider = MockLLMProvider()

        def _explode(messages):
            raise RuntimeError("LLM down")

        qs.llm_provider.generate_chat_stream = _explode
        with pytest.raises(RuntimeError, match="Error generating SQL"):
            qs.generate_sql("schema", "test")


# ── QuerySession explain / fix / suggest ─────────────────────────────────────

class TestExplainFixSuggest:
    @pytest.fixture
    def qs(self, tmp_path, mock_llm):
        config = {
            "provider": {"type": "sqlite", "database_path": ":memory:"},
            "llm": {"provider": "openai", "api_key": "test"},
            "contexts": "contexts",
            "output": "manifest",
        }
        (tmp_path / "tabletalk.yaml").write_text(yaml.dump(config))
        with patch("tabletalk.factories.get_llm_provider", return_value=mock_llm):
            session = QuerySession(str(tmp_path))
        session.llm_provider = mock_llm
        return session

    def test_explain_results_stream(self, qs):
        results = [{"id": 1, "name": "Alice"}]
        chunks = list(qs.explain_results_stream("who are the customers?", "SELECT * FROM c", results))
        assert len(chunks) > 0
        full = "".join(chunks)
        assert len(full) > 0

    def test_explain_empty_results(self, qs):
        chunks = list(qs.explain_results_stream("test?", "SELECT * FROM t", []))
        assert len(chunks) > 0

    def test_fix_sql_stream(self, qs):
        qs.llm_provider = MockLLMProvider(
            responses={"fix": "SELECT id, name FROM main.customers WHERE active = 1"}
        )
        chunks = list(qs.fix_sql_stream("SELECT x FROM y", "no such table", "schema"))
        assert len(chunks) > 0

    def test_suggest_questions_parses_json(self, qs):
        qs.llm_provider = MockLLMProvider(
            default_response='["Q1?", "Q2?", "Q3?"]'
        )
        questions = qs.suggest_questions("schema")
        assert len(questions) == 3
        assert questions[0] == "Q1?"

    def test_suggest_questions_returns_empty_on_bad_json(self, qs):
        qs.llm_provider = MockLLMProvider(default_response="not json at all")
        questions = qs.suggest_questions("schema")
        assert questions == []

    def test_suggest_questions_with_history(self, qs):
        qs.llm_provider = MockLLMProvider(
            default_response='["Q1?", "Q2?", "Q3?"]'
        )
        history = [
            {"role": "user", "content": "prev question"},
            {"role": "assistant", "content": "SELECT 1"},
        ]
        questions = qs.suggest_questions("schema", history)
        assert len(questions) <= 3


# ── QuerySession history ───────────────────────────────────────────────────────

class TestHistory:
    @pytest.fixture
    def qs(self, tmp_path, mock_llm):
        config = {
            "provider": {"type": "sqlite", "database_path": ":memory:"},
            "llm": {"provider": "openai", "api_key": "test"},
            "contexts": "contexts",
            "output": "manifest",
        }
        (tmp_path / "tabletalk.yaml").write_text(yaml.dump(config))
        with patch("tabletalk.factories.get_llm_provider", return_value=mock_llm):
            session = QuerySession(str(tmp_path))
        session.llm_provider = mock_llm
        return session

    def test_empty_history(self, qs):
        assert qs.get_history() == []

    def test_save_and_retrieve(self, qs):
        qs.save_history("customers.txt", "how many customers?", "SELECT COUNT(*) FROM c")
        entries = qs.get_history()
        assert len(entries) == 1
        assert entries[0]["question"] == "how many customers?"
        assert entries[0]["sql"] == "SELECT COUNT(*) FROM c"
        assert entries[0]["manifest"] == "customers.txt"
        assert "timestamp" in entries[0]

    def test_multiple_entries(self, qs):
        for i in range(5):
            qs.save_history("ctx.txt", f"question {i}", f"SELECT {i}")
        entries = qs.get_history()
        assert len(entries) == 5

    def test_limit_respected(self, qs):
        for i in range(10):
            qs.save_history("ctx.txt", f"q{i}", f"SELECT {i}")
        entries = qs.get_history(limit=3)
        assert len(entries) == 3

    def test_returns_most_recent(self, qs):
        for i in range(5):
            qs.save_history("ctx.txt", f"q{i}", f"SELECT {i}")
        entries = qs.get_history(limit=2)
        # get_history returns last N
        assert entries[-1]["question"] == "q4"

    def test_corrupted_lines_skipped(self, qs):
        """Malformed JSON lines in history file are gracefully ignored."""
        qs.save_history("ctx.txt", "valid", "SELECT 1")
        history_path = os.path.join(qs.project_folder, ".tabletalk_history.jsonl")
        with open(history_path, "a") as f:
            f.write("not json\n")
        entries = qs.get_history()
        assert len(entries) == 1


# ── QuerySession favorites ─────────────────────────────────────────────────────

class TestFavorites:
    @pytest.fixture
    def qs(self, tmp_path, mock_llm):
        config = {
            "provider": {"type": "sqlite", "database_path": ":memory:"},
            "llm": {"provider": "openai", "api_key": "test"},
            "contexts": "contexts",
            "output": "manifest",
        }
        (tmp_path / "tabletalk.yaml").write_text(yaml.dump(config))
        with patch("tabletalk.factories.get_llm_provider", return_value=mock_llm):
            session = QuerySession(str(tmp_path))
        session.llm_provider = mock_llm
        return session

    def test_empty_favorites(self, qs):
        assert qs.get_favorites() == []

    def test_save_and_retrieve(self, qs):
        qs.save_favorite("my query", "ctx.txt", "how many?", "SELECT COUNT(*) FROM t")
        favs = qs.get_favorites()
        assert len(favs) == 1
        assert favs[0]["name"] == "my query"
        assert favs[0]["sql"] == "SELECT COUNT(*) FROM t"

    def test_overwrite_same_name(self, qs):
        qs.save_favorite("q", "ctx.txt", "old?", "SELECT 1")
        qs.save_favorite("q", "ctx.txt", "new?", "SELECT 2")
        favs = qs.get_favorites()
        assert len(favs) == 1
        assert favs[0]["sql"] == "SELECT 2"

    def test_delete_existing(self, qs):
        qs.save_favorite("q", "ctx.txt", "?", "SELECT 1")
        deleted = qs.delete_favorite("q")
        assert deleted is True
        assert qs.get_favorites() == []

    def test_delete_nonexistent(self, qs):
        deleted = qs.delete_favorite("does_not_exist")
        assert deleted is False

    def test_multiple_favorites(self, qs):
        for i in range(3):
            qs.save_favorite(f"q{i}", "ctx.txt", f"q{i}?", f"SELECT {i}")
        assert len(qs.get_favorites()) == 3


# ── QuerySession.load_manifest ────────────────────────────────────────────────

class TestLoadManifest:
    @pytest.fixture
    def qs(self, project_with_manifest, mock_llm):
        with patch("tabletalk.factories.get_llm_provider", return_value=mock_llm):
            session = QuerySession(project_with_manifest)
        session.llm_provider = mock_llm
        return session

    def test_loads_existing_manifest(self, qs):
        content = qs.load_manifest("customers.txt")
        assert isinstance(content, str)
        assert len(content) > 0
        assert "customers" in content.lower()

    def test_missing_manifest_raises(self, qs):
        with pytest.raises(FileNotFoundError):
            qs.load_manifest("nonexistent.txt")

    def test_manifest_contains_schema_info(self, qs):
        content = qs.load_manifest("orders.txt")
        assert "DATA_SOURCE" in content
        assert "CONTEXT" in content
        assert "TABLES" in content


# ── Parser.apply_schema ────────────────────────────────────────────────────────

class TestParserApplySchema:
    def test_creates_manifest_files(self, project_dir):
        """apply_schema creates a .txt manifest for every context YAML."""
        from tabletalk.utils import apply_schema

        apply_schema(project_dir)

        manifest_dir = Path(project_dir) / "manifest"
        assert manifest_dir.exists()
        txt_files = list(manifest_dir.glob("*.txt"))
        assert len(txt_files) == 4  # customers, orders, inventory, marketing

    def test_manifest_content_structure(self, project_dir):
        """Each manifest has DATA_SOURCE, CONTEXT, DATASET, and TABLES sections."""
        from tabletalk.utils import apply_schema

        apply_schema(project_dir)
        content = (Path(project_dir) / "manifest" / "customers.txt").read_text()

        assert "DATA_SOURCE:" in content
        assert "CONTEXT:" in content
        assert "DATASET:" in content
        assert "TABLES:" in content

    def test_manifest_encodes_pk(self, project_dir):
        """Primary key columns are encoded with [PK] annotation."""
        from tabletalk.utils import apply_schema

        apply_schema(project_dir)
        content = (Path(project_dir) / "manifest" / "customers.txt").read_text()
        assert "[PK]" in content

    def test_manifest_encodes_fk(self, project_dir):
        """Foreign key columns are encoded with [FK:...] annotation."""
        from tabletalk.utils import apply_schema

        apply_schema(project_dir)
        content = (Path(project_dir) / "manifest" / "orders.txt").read_text()
        assert "[FK:" in content

    def test_manifest_respects_yaml_context_description(self, project_dir):
        """Context-level description from YAML appears in the manifest header."""
        from tabletalk.utils import apply_schema

        apply_schema(project_dir)
        content = (Path(project_dir) / "manifest" / "inventory.txt").read_text()
        # The CONTEXT line includes the context description from inventory.yaml
        assert "Stock levels and warehouse management" in content

    def test_missing_contexts_folder_is_graceful(self, tmp_path):
        """Parser doesn't crash if contexts folder is empty."""
        config = {
            "provider": {"type": "sqlite", "database_path": ":memory:"},
            "llm": {"provider": "openai", "api_key": "test"},
            "description": "test",
            "contexts": "contexts",
            "output": "manifest",
        }
        (tmp_path / "tabletalk.yaml").write_text(yaml.dump(config))
        (tmp_path / "contexts").mkdir()
        (tmp_path / "manifest").mkdir()

        from tabletalk.providers.sqlite_provider import SQLiteProvider
        from tabletalk.interfaces import Parser

        db = SQLiteProvider(":memory:")
        parser = Parser(str(tmp_path), db)
        # Should not raise
        parser.apply_schema()

    def test_apply_schema_idempotent(self, project_dir):
        """Running apply_schema twice produces identical output."""
        from tabletalk.utils import apply_schema

        apply_schema(project_dir)
        first = (Path(project_dir) / "manifest" / "customers.txt").read_text()
        apply_schema(project_dir)
        second = (Path(project_dir) / "manifest" / "customers.txt").read_text()
        assert first == second


# ── QuerySession manifest caching ────────────────────────────────────────────

class TestManifestCaching:
    @pytest.fixture
    def qs(self, project_with_manifest, mock_llm):
        with patch("tabletalk.factories.get_llm_provider", return_value=mock_llm):
            session = QuerySession(project_with_manifest)
        session.llm_provider = mock_llm
        return session

    def test_second_load_uses_cache(self, qs):
        """A second load_manifest call should not hit the filesystem."""
        content1 = qs.load_manifest("customers.txt")
        content2 = qs.load_manifest("customers.txt")
        assert content1 is content2  # same object — cache hit

    def test_invalidate_clears_cache(self, qs):
        """invalidate_manifest_cache should force a fresh read."""
        content1 = qs.load_manifest("customers.txt")
        qs.invalidate_manifest_cache()
        content2 = qs.load_manifest("customers.txt")
        # Content should be equal but a different object after cache clear
        assert content1 == content2
        assert content1 is not content2

    def test_cache_independent_per_file(self, qs):
        """Different manifest files are cached independently."""
        c = qs.load_manifest("customers.txt")
        o = qs.load_manifest("orders.txt")
        assert c != o
        assert qs.load_manifest("customers.txt") is c


# ── QuerySession safe_mode ────────────────────────────────────────────────────

class TestSafeMode:
    @pytest.fixture
    def safe_qs(self, project_with_manifest, mock_llm, ecommerce_sqlite):
        """QuerySession with safe_mode enabled and a real SQLite DB for execution."""
        import yaml
        from tabletalk.providers.sqlite_provider import SQLiteProvider

        config_path = os.path.join(project_with_manifest, "tabletalk.yaml")
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        cfg["safe_mode"] = True
        with open(config_path, "w") as f:
            yaml.dump(cfg, f)

        with patch("tabletalk.factories.get_llm_provider", return_value=mock_llm):
            session = QuerySession(project_with_manifest)
        session.llm_provider = mock_llm
        # Inject the real SQLite provider (ecommerce_sqlite is a db file path)
        session._db_provider = SQLiteProvider(ecommerce_sqlite)
        session._db_loaded = True
        return session

    def test_select_allowed(self, safe_qs):
        results = safe_qs.execute_sql("SELECT COUNT(*) AS n FROM customers")
        assert results[0]["n"] >= 0

    def test_with_clause_allowed(self, safe_qs):
        sql = "WITH c AS (SELECT id FROM customers) SELECT COUNT(*) AS n FROM c"
        results = safe_qs.execute_sql(sql)
        assert results[0]["n"] >= 0

    def test_mutating_sql_blocked(self, safe_qs):
        with pytest.raises(ValueError, match="safe_mode"):
            safe_qs.execute_sql("DROP TABLE customers")

    def test_is_read_only_sql_helper(self):
        assert QuerySession._is_read_only_sql("SELECT 1") is True
        assert QuerySession._is_read_only_sql("WITH cte AS (...) SELECT 1") is True
        assert QuerySession._is_read_only_sql("EXPLAIN SELECT 1") is True
        assert QuerySession._is_read_only_sql("DELETE FROM t") is False
        assert QuerySession._is_read_only_sql("UPDATE t SET x=1") is False
        assert QuerySession._is_read_only_sql("DROP TABLE t") is False
        assert QuerySession._is_read_only_sql("INSERT INTO t VALUES (1)") is False
