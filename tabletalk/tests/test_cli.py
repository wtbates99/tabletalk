"""
Tests for tabletalk/cli.py

Uses Click's CliRunner to exercise commands without a real terminal.
All database commands use the ecommerce SQLite fixture.
LLM calls are mocked so no API key is needed.

Commands tested:
  tabletalk init
  tabletalk apply
  tabletalk history
  tabletalk profiles list
  tabletalk profiles delete
  tabletalk profiles test
  tabletalk connect  (--from-dbt, --test-only)
  tabletalk serve    (startup only — we don't let it actually bind)

Helpers tested:
  _default_profile_name
  _test_connection
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from tabletalk.cli import _default_profile_name, _test_connection, cli


# ── helpers ────────────────────────────────────────────────────────────────────

@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli_project(project_with_manifest, monkeypatch):
    """Change cwd to a fully-initialised project with manifests."""
    monkeypatch.chdir(project_with_manifest)
    return project_with_manifest


# ── _default_profile_name ──────────────────────────────────────────────────────

class TestDefaultProfileName:
    def test_postgres(self):
        assert _default_profile_name({"type": "postgres", "user": "alice", "database": "analytics"}) == "alice_analytics"

    def test_mysql(self):
        assert _default_profile_name({"type": "mysql", "user": "root", "database": "mydb"}) == "root_mydb"

    def test_snowflake(self):
        name = _default_profile_name({"type": "snowflake", "user": "bob", "database": "PROD"})
        assert name == "bob_prod"

    def test_duckdb_with_path(self):
        name = _default_profile_name({"type": "duckdb", "database_path": "/data/analytics.duckdb"})
        assert name == "analytics"

    def test_duckdb_memory(self):
        # ":memory:" has no meaningful basename, so the function returns it as-is
        name = _default_profile_name({"type": "duckdb", "database_path": ":memory:"})
        # The function strips the extension from the basename; ":memory:" → ":memory:"
        assert name is not None and len(name) > 0

    def test_sqlite(self):
        name = _default_profile_name({"type": "sqlite", "database_path": "/tmp/myapp.db"})
        assert name == "myapp"

    def test_azuresql(self):
        name = _default_profile_name({"type": "azuresql", "database": "reporting"})
        assert name == "reporting_azuresql"

    def test_bigquery(self):
        name = _default_profile_name({"type": "bigquery", "project_id": "my-gcp-project"})
        assert name == "my_gcp_project"

    def test_unknown_type(self):
        name = _default_profile_name({"type": "oracle"})
        assert name == "my_oracle"


# ── _test_connection ───────────────────────────────────────────────────────────

class TestTestConnection:
    def test_sqlite_in_memory(self):
        ok, msg = _test_connection({"type": "sqlite", "database_path": ":memory:"})
        assert ok is True
        assert "successful" in msg.lower()

    def test_duckdb_in_memory(self):
        pytest.importorskip("duckdb")
        ok, msg = _test_connection({"type": "duckdb", "database_path": ":memory:"})
        assert ok is True

    def test_unsupported_type_fails(self):
        ok, msg = _test_connection({"type": "oracle"})
        assert ok is False
        assert "failed" in msg.lower() or "error" in msg.lower() or "unsupported" in msg.lower()

    def test_missing_driver_returns_false(self):
        """If a required driver isn't installed, returns False with an install hint."""
        with patch("tabletalk.factories.get_db_provider", side_effect=ImportError("No module named 'psycopg2'")):
            ok, msg = _test_connection({"type": "postgres", "host": "x"})
        assert ok is False
        assert "Missing driver" in msg or "psycopg2" in msg


# ── tabletalk init ─────────────────────────────────────────────────────────────

class TestInitCommand:
    def test_creates_project_structure(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert Path("tabletalk.yaml").exists()
            assert Path("contexts").is_dir()
            assert Path("manifest").is_dir()

    def test_creates_sample_context(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            assert Path("contexts/default_context.yaml").exists()

    def test_idempotent(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            r1 = runner.invoke(cli, ["init"])
            r2 = runner.invoke(cli, ["init"])
            assert r1.exit_code == 0
            assert r2.exit_code == 0
            assert "Already initialized" in r2.output

    def test_does_not_overwrite_existing(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            Path("tabletalk.yaml").write_text("my_custom: true\n")
            runner.invoke(cli, ["init"])
            # Should not overwrite
            assert "my_custom: true" in Path("tabletalk.yaml").read_text()


# ── tabletalk apply ────────────────────────────────────────────────────────────

class TestApplyCommand:
    def test_generates_manifests(self, runner, project_dir):
        result = runner.invoke(cli, ["apply", project_dir])
        assert result.exit_code == 0
        assert "Manifests updated" in result.output
        manifests = list((Path(project_dir) / "manifest").glob("*.txt"))
        assert len(manifests) == 4

    def test_missing_directory_error(self, runner):
        result = runner.invoke(cli, ["apply", "/nonexistent/path/to/project"])
        assert result.exit_code == 0  # Click doesn't exit(1) — just prints error
        assert "Not a directory" in result.output

    def test_missing_yaml_error(self, runner, tmp_path):
        result = runner.invoke(cli, ["apply", str(tmp_path)])
        assert result.exit_code == 0
        assert "tabletalk.yaml not found" in result.output

    def test_verbose_flag(self, runner, project_dir):
        result = runner.invoke(cli, ["--verbose", "apply", project_dir])
        assert result.exit_code == 0

    def test_apply_twice_is_idempotent(self, runner, project_dir):
        runner.invoke(cli, ["apply", project_dir])
        first = (Path(project_dir) / "manifest" / "customers.txt").read_text()
        runner.invoke(cli, ["apply", project_dir])
        second = (Path(project_dir) / "manifest" / "customers.txt").read_text()
        assert first == second


# ── tabletalk history ──────────────────────────────────────────────────────────

class TestHistoryCommand:
    def test_empty_history(self, runner, project_with_manifest):
        with patch("tabletalk.factories.get_llm_provider") as mock_llm:
            mock_llm.return_value = MagicMock()
            result = runner.invoke(cli, ["history", project_with_manifest])
        assert result.exit_code == 0
        assert "No history" in result.output

    def test_shows_entries_after_save(self, runner, project_with_manifest):
        from tabletalk.interfaces import QuerySession

        with patch("tabletalk.factories.get_llm_provider") as mock_llm:
            mock_llm.return_value = MagicMock()
            qs = QuerySession(project_with_manifest)
        qs.save_history("customers.txt", "how many customers?", "SELECT COUNT(*) FROM c")

        with patch("tabletalk.factories.get_llm_provider") as mock_llm:
            mock_llm.return_value = MagicMock()
            result = runner.invoke(cli, ["history", project_with_manifest])
        assert result.exit_code == 0
        assert "how many customers?" in result.output

    def test_limit_option(self, runner, project_with_manifest):
        from tabletalk.interfaces import QuerySession

        with patch("tabletalk.factories.get_llm_provider") as mock_llm:
            mock_llm.return_value = MagicMock()
            qs = QuerySession(project_with_manifest)
        for i in range(10):
            qs.save_history("ctx.txt", f"question {i}", f"SELECT {i}")

        with patch("tabletalk.factories.get_llm_provider") as mock_llm:
            mock_llm.return_value = MagicMock()
            result = runner.invoke(cli, ["history", project_with_manifest, "--limit", "3"])
        assert result.exit_code == 0

    def test_invalid_project_dir(self, runner):
        result = runner.invoke(cli, ["history", "/does/not/exist"])
        assert result.exit_code == 0
        assert "Not a directory" in result.output

    def test_llm_error_handled(self, runner, project_with_manifest):
        with patch("tabletalk.factories.get_llm_provider", side_effect=RuntimeError("No key")):
            result = runner.invoke(cli, ["history", project_with_manifest])
        assert result.exit_code == 0
        assert "No key" in result.output or "error" in result.output.lower()


# ── tabletalk profiles list ────────────────────────────────────────────────────

class TestProfilesListCommand:
    @pytest.fixture(autouse=True)
    def isolated_profiles(self, tmp_path, monkeypatch):
        """Redirect profile I/O to a temp file for isolation."""
        import tabletalk.profiles as pm

        monkeypatch.setattr(pm, "PROFILES_DIR", tmp_path)
        monkeypatch.setattr(pm, "PROFILES_FILE", tmp_path / "profiles.yml")

    def test_empty_profiles(self, runner):
        result = runner.invoke(cli, ["profiles", "list"])
        assert result.exit_code == 0
        assert "No profiles" in result.output

    def test_lists_saved_profiles(self, runner):
        from tabletalk.profiles import save_profile

        save_profile("my_pg", {"type": "postgres", "host": "db.local", "user": "alice", "database": "prod"})
        result = runner.invoke(cli, ["profiles", "list"])
        assert result.exit_code == 0
        assert "my_pg" in result.output
        assert "postgres" in result.output

    def test_lists_multiple_types(self, runner):
        from tabletalk.profiles import save_profile

        save_profile("duck", {"type": "duckdb", "database_path": "/data/a.duckdb"})
        save_profile("snow", {"type": "snowflake", "account": "abc", "user": "u", "database": "DB"})
        result = runner.invoke(cli, ["profiles", "list"])
        assert "duck" in result.output
        assert "snow" in result.output


# ── tabletalk profiles delete ──────────────────────────────────────────────────

class TestProfilesDeleteCommand:
    @pytest.fixture(autouse=True)
    def isolated_profiles(self, tmp_path, monkeypatch):
        import tabletalk.profiles as pm

        monkeypatch.setattr(pm, "PROFILES_DIR", tmp_path)
        monkeypatch.setattr(pm, "PROFILES_FILE", tmp_path / "profiles.yml")

    def test_deletes_existing(self, runner):
        from tabletalk.profiles import get_profile, save_profile

        save_profile("tmp_profile", {"type": "sqlite"})
        result = runner.invoke(cli, ["profiles", "delete", "tmp_profile"])
        assert result.exit_code == 0
        assert "Deleted" in result.output
        assert get_profile("tmp_profile") is None

    def test_error_on_missing(self, runner):
        result = runner.invoke(cli, ["profiles", "delete", "nonexistent"])
        assert result.exit_code == 0
        assert "not found" in result.output


# ── tabletalk profiles test ────────────────────────────────────────────────────

class TestProfilesTestCommand:
    @pytest.fixture(autouse=True)
    def isolated_profiles(self, tmp_path, monkeypatch):
        import tabletalk.profiles as pm

        monkeypatch.setattr(pm, "PROFILES_DIR", tmp_path)
        monkeypatch.setattr(pm, "PROFILES_FILE", tmp_path / "profiles.yml")

    def test_successful_connection(self, runner):
        from tabletalk.profiles import save_profile

        save_profile("local_db", {"type": "sqlite", "database_path": ":memory:"})
        result = runner.invoke(cli, ["profiles", "test", "local_db"])
        assert result.exit_code == 0
        assert "successful" in result.output.lower() or "✓" in result.output

    def test_missing_profile_error(self, runner):
        result = runner.invoke(cli, ["profiles", "test", "ghost_profile"])
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_failed_connection_shown(self, runner):
        from tabletalk.profiles import save_profile

        save_profile("bad_pg", {"type": "postgres", "host": "definitely.does.not.exist", "database": "x", "user": "u", "password": "p"})
        result = runner.invoke(cli, ["profiles", "test", "bad_pg"])
        assert result.exit_code == 0
        # Either psycopg2 missing or connection refused
        assert "✗" in result.output or "failed" in result.output.lower() or "Missing driver" in result.output


# ── tabletalk connect --test-only ──────────────────────────────────────────────

class TestConnectTestOnly:
    @pytest.fixture(autouse=True)
    def isolated_profiles(self, tmp_path, monkeypatch):
        import tabletalk.profiles as pm

        monkeypatch.setattr(pm, "PROFILES_DIR", tmp_path)
        monkeypatch.setattr(pm, "PROFILES_FILE", tmp_path / "profiles.yml")

    def test_test_only_missing_profile(self, runner):
        result = runner.invoke(cli, ["connect", "--test-only", "no_such_profile"])
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_test_only_good_profile(self, runner):
        from tabletalk.profiles import save_profile

        save_profile("sqlite_mem", {"type": "sqlite", "database_path": ":memory:"})
        result = runner.invoke(cli, ["connect", "--test-only", "sqlite_mem"])
        assert result.exit_code == 0
        assert "successful" in result.output.lower() or "✓" in result.output


# ── tabletalk connect --from-dbt ───────────────────────────────────────────────

class TestConnectFromDbt:
    @pytest.fixture(autouse=True)
    def isolated_profiles(self, tmp_path, monkeypatch):
        import tabletalk.profiles as pm

        monkeypatch.setattr(pm, "PROFILES_DIR", tmp_path)
        monkeypatch.setattr(pm, "PROFILES_FILE", tmp_path / "profiles.yml")

    def test_import_duckdb_from_dbt(self, runner, tmp_path):
        dbt_dir = tmp_path / ".dbt"
        dbt_dir.mkdir()
        (dbt_dir / "profiles.yml").write_text(
            yaml.dump(
                {
                    "my_project": {
                        "outputs": {
                            "dev": {
                                "type": "duckdb",
                                "path": ":memory:",
                            }
                        }
                    }
                }
            )
        )

        def fake_home():
            return tmp_path

        with patch.object(Path, "home", staticmethod(fake_home)):
            import importlib
            import tabletalk.profiles as pm
            importlib.reload(pm)

            result = runner.invoke(
                cli,
                ["connect", "--from-dbt", "my_project", "--target", "dev"],
                input="my_dbt_duckdb\n",  # profile name prompt
            )
        assert result.exit_code == 0

    def test_import_nonexistent_dbt_profile(self, runner, tmp_path):
        dbt_dir = tmp_path / ".dbt"
        dbt_dir.mkdir()
        (dbt_dir / "profiles.yml").write_text(yaml.dump({"other_project": {}}))

        def fake_home():
            return tmp_path

        with patch.object(Path, "home", staticmethod(fake_home)):
            import importlib
            import tabletalk.profiles as pm
            importlib.reload(pm)

            result = runner.invoke(
                cli,
                ["connect", "--from-dbt", "missing_project"],
                input="\n",
            )
        assert result.exit_code == 0
        assert "Could not import" in result.output or "not found" in result.output.lower()


# ── tabletalk serve ────────────────────────────────────────────────────────────

class TestServeCommand:
    def test_serve_invokes_flask(self, runner):
        """serve command calls Flask app.run — mock it so we don't actually bind."""
        with patch("tabletalk.app.app.run") as mock_run:
            result = runner.invoke(cli, ["serve", "--port", "9999"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("port") == 9999 or mock_run.call_args[0][1] == 9999 or 9999 in mock_run.call_args[1].values() or 9999 in mock_run.call_args[0]

    def test_serve_debug_flag(self, runner):
        with patch("tabletalk.app.app.run") as mock_run:
            result = runner.invoke(cli, ["serve", "--debug"])
        assert result.exit_code == 0
        call_kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
        call_args = mock_run.call_args[0] if mock_run.call_args[0] else ()
        # debug=True should be passed somewhere
        assert True in call_args or call_kwargs.get("debug") is True


# ── CLI integration: init → apply → history ────────────────────────────────────

class TestCLIIntegration:
    def test_full_workflow(self, runner, tmp_path, ecommerce_sqlite):
        """End-to-end: init → add context → apply [dir] → history [dir]."""
        project = tmp_path / "my_project"
        project.mkdir()

        # init
        result = runner.invoke(cli, ["init"], catch_exceptions=False, env={"PWD": str(project)})
        # init uses os.getcwd() internally, so chdir first
        import os

        orig = os.getcwd()
        try:
            os.chdir(project)
            result = runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert (project / "tabletalk.yaml").exists()
        finally:
            os.chdir(orig)

        # Update config to use our ecommerce SQLite
        config = {
            "provider": {"type": "sqlite", "database_path": ecommerce_sqlite},
            "llm": {"provider": "openai", "api_key": "test"},
            "description": "test",
            "contexts": "contexts",
            "output": "manifest",
        }
        (project / "tabletalk.yaml").write_text(yaml.dump(config))

        # Add a real context
        (project / "contexts" / "customers.yaml").write_text(
            yaml.dump(
                {
                    "name": "customers",
                    "description": "Customer data",
                    "datasets": [{"name": "main", "tables": [{"name": "customers"}]}],
                }
            )
        )

        # apply — pass explicit path so we don't use the stale os.getcwd() default
        result = runner.invoke(cli, ["apply", str(project)])
        assert result.exit_code == 0, result.output
        assert "Manifests updated" in result.output
        assert (project / "manifest" / "customers.txt").exists()

        # history — pass explicit path
        with patch("tabletalk.factories.get_llm_provider") as mock_llm:
            mock_llm.return_value = MagicMock()
            result = runner.invoke(cli, ["history", str(project)])
        assert result.exit_code == 0
        assert "No history" in result.output
