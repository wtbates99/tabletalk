"""
Tests for tabletalk/profiles.py

Covers:
  - load_profiles
  - get_profile
  - save_profile
  - delete_profile
  - list_profiles
  - import_from_dbt
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest
import yaml


# All tests use a patched PROFILES_FILE so they never touch the real ~/.tabletalk/profiles.yml


@pytest.fixture
def profiles_path(tmp_path: Path) -> Path:
    return tmp_path / "profiles.yml"


@pytest.fixture(autouse=True)
def patch_profiles_file(profiles_path, tmp_path):
    """Redirect all profile I/O to a temp file so tests are isolated."""
    import tabletalk.profiles as profiles_mod

    original_dir = profiles_mod.PROFILES_DIR
    original_file = profiles_mod.PROFILES_FILE

    profiles_mod.PROFILES_DIR = tmp_path
    profiles_mod.PROFILES_FILE = profiles_path

    yield

    profiles_mod.PROFILES_DIR = original_dir
    profiles_mod.PROFILES_FILE = original_file


# ── load_profiles ─────────────────────────────────────────────────────────────

class TestLoadProfiles:
    def test_empty_when_no_file(self):
        from tabletalk.profiles import load_profiles

        result = load_profiles()
        assert result == {}

    def test_loads_yaml(self, profiles_path):
        profiles_path.write_text(
            yaml.dump({"my_db": {"type": "postgres", "host": "localhost"}})
        )
        from tabletalk.profiles import load_profiles

        result = load_profiles()
        assert "my_db" in result
        assert result["my_db"]["host"] == "localhost"

    def test_returns_empty_dict_on_empty_file(self, profiles_path):
        profiles_path.write_text("")
        from tabletalk.profiles import load_profiles

        assert load_profiles() == {}


# ── get_profile ───────────────────────────────────────────────────────────────

class TestGetProfile:
    def test_returns_none_when_missing(self):
        from tabletalk.profiles import get_profile

        assert get_profile("nonexistent") is None

    def test_returns_profile(self, profiles_path):
        profiles_path.write_text(
            yaml.dump({"snowflake_prod": {"type": "snowflake", "account": "abc123"}})
        )
        from tabletalk.profiles import get_profile

        profile = get_profile("snowflake_prod")
        assert profile is not None
        assert profile["account"] == "abc123"


# ── save_profile ──────────────────────────────────────────────────────────────

class TestSaveProfile:
    def test_creates_file(self, profiles_path):
        from tabletalk.profiles import save_profile

        save_profile("new_profile", {"type": "sqlite", "database_path": ":memory:"})
        assert profiles_path.exists()

    def test_saves_correct_data(self, profiles_path):
        from tabletalk.profiles import get_profile, save_profile

        save_profile("pg", {"type": "postgres", "host": "db.example.com", "port": 5432})
        profile = get_profile("pg")
        assert profile is not None
        assert profile["host"] == "db.example.com"
        assert profile["port"] == 5432

    def test_overwrites_existing(self, profiles_path):
        from tabletalk.profiles import get_profile, save_profile

        save_profile("pg", {"type": "postgres", "host": "old-host"})
        save_profile("pg", {"type": "postgres", "host": "new-host"})
        profile = get_profile("pg")
        assert profile is not None
        assert profile["host"] == "new-host"

    def test_multiple_profiles_preserved(self, profiles_path):
        from tabletalk.profiles import load_profiles, save_profile

        save_profile("a", {"type": "sqlite"})
        save_profile("b", {"type": "duckdb"})
        save_profile("c", {"type": "postgres"})
        profiles = load_profiles()
        assert set(profiles.keys()) == {"a", "b", "c"}


# ── delete_profile ────────────────────────────────────────────────────────────

class TestDeleteProfile:
    def test_returns_false_when_not_found(self):
        from tabletalk.profiles import delete_profile

        assert delete_profile("ghost") is False

    def test_deletes_existing(self, profiles_path):
        from tabletalk.profiles import delete_profile, get_profile, save_profile

        save_profile("temp", {"type": "sqlite"})
        result = delete_profile("temp")
        assert result is True
        assert get_profile("temp") is None

    def test_does_not_delete_others(self, profiles_path):
        from tabletalk.profiles import delete_profile, get_profile, save_profile

        save_profile("keep", {"type": "duckdb"})
        save_profile("remove", {"type": "sqlite"})
        delete_profile("remove")
        assert get_profile("keep") is not None


# ── list_profiles ─────────────────────────────────────────────────────────────

class TestListProfiles:
    def test_empty_list(self):
        from tabletalk.profiles import list_profiles

        assert list_profiles() == []

    def test_returns_sorted_names(self, profiles_path):
        from tabletalk.profiles import list_profiles, save_profile

        save_profile("zebra", {"type": "sqlite"})
        save_profile("alpha", {"type": "duckdb"})
        save_profile("mango", {"type": "postgres"})
        names = list_profiles()
        assert names == ["alpha", "mango", "zebra"]


# ── import_from_dbt ───────────────────────────────────────────────────────────

class TestImportFromDbt:
    @pytest.fixture
    def dbt_profiles_path(self, tmp_path):
        return tmp_path / ".dbt" / "profiles.yml"

    def _write_dbt_profiles(self, path: Path, content: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(content))

    def test_returns_none_when_no_dbt_file(self, tmp_path):
        """No ~/.dbt/profiles.yml → returns None."""
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        with patch("tabletalk.profiles.Path") as mock_path:
            mock_path.home.return_value = fake_home
            from tabletalk.profiles import import_from_dbt
            # Re-import to pick up patched Path
        # Direct test: patch dbt_file.exists()
        with patch("tabletalk.profiles.Path") as MockPath:
            fake = MockPath.home.return_value / ".dbt" / "profiles.yml"
            fake.exists.return_value = False
            from tabletalk.profiles import import_from_dbt

            result = import_from_dbt("any_project")
        assert result is None

    def test_imports_postgres(self, tmp_path, monkeypatch):
        dbt_dir = tmp_path / ".dbt"
        dbt_dir.mkdir()
        dbt_file = dbt_dir / "profiles.yml"
        dbt_file.write_text(
            yaml.dump(
                {
                    "my_project": {
                        "outputs": {
                            "dev": {
                                "type": "postgres",
                                "host": "localhost",
                                "port": 5432,
                                "dbname": "analytics",
                                "user": "admin",
                                "password": "secret",
                            }
                        }
                    }
                }
            )
        )

        import tabletalk.profiles as profiles_mod

        with patch.object(
            profiles_mod, "PROFILES_FILE", dbt_dir / "tt_profiles.yml"
        ):
            # Patch the dbt path lookup
            original_home = Path.home

            def fake_home():
                return tmp_path

            with patch.object(Path, "home", staticmethod(fake_home)):
                from importlib import reload

                reload(profiles_mod)
                result = profiles_mod.import_from_dbt("my_project", "dev")

        assert result is not None
        assert result["type"] == "postgres"
        assert result["host"] == "localhost"
        assert result["database"] == "analytics"

    def test_import_fallback_to_dev_target(self, tmp_path):
        """If requested target doesn't exist, falls back to 'dev'."""
        dbt_dir = tmp_path / ".dbt"
        dbt_dir.mkdir()
        (dbt_dir / "profiles.yml").write_text(
            yaml.dump(
                {
                    "proj": {
                        "outputs": {
                            "dev": {
                                "type": "duckdb",
                                "path": "/data/analytics.duckdb",
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
            import tabletalk.profiles as profiles_mod

            importlib.reload(profiles_mod)
            result = profiles_mod.import_from_dbt("proj", "prod")  # prod doesn't exist

        assert result is not None
        assert result["type"] == "duckdb"

    def test_unsupported_adapter_returns_none(self, tmp_path):
        dbt_dir = tmp_path / ".dbt"
        dbt_dir.mkdir()
        (dbt_dir / "profiles.yml").write_text(
            yaml.dump(
                {
                    "proj": {
                        "outputs": {
                            "dev": {
                                "type": "redshift",
                                "host": "cluster.redshift.amazonaws.com",
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
            import tabletalk.profiles as profiles_mod

            importlib.reload(profiles_mod)
            result = profiles_mod.import_from_dbt("proj", "dev")

        assert result is None

    def test_imports_snowflake(self, tmp_path):
        dbt_dir = tmp_path / ".dbt"
        dbt_dir.mkdir()
        (dbt_dir / "profiles.yml").write_text(
            yaml.dump(
                {
                    "sf_proj": {
                        "outputs": {
                            "prod": {
                                "type": "snowflake",
                                "account": "xy12345.us-east-1",
                                "user": "dbt_user",
                                "password": "hunter2",
                                "database": "ANALYTICS",
                                "warehouse": "TRANSFORMING",
                                "schema": "PUBLIC",
                                "role": "TRANSFORMER",
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
            import tabletalk.profiles as profiles_mod

            importlib.reload(profiles_mod)
            result = profiles_mod.import_from_dbt("sf_proj", "prod")

        assert result is not None
        assert result["type"] == "snowflake"
        assert result["account"] == "xy12345.us-east-1"
        assert result["role"] == "TRANSFORMER"

    def test_imports_bigquery(self, tmp_path):
        dbt_dir = tmp_path / ".dbt"
        dbt_dir.mkdir()
        (dbt_dir / "profiles.yml").write_text(
            yaml.dump(
                {
                    "bq_proj": {
                        "outputs": {
                            "dev": {
                                "type": "bigquery",
                                "method": "oauth",
                                "project": "my-gcp-project",
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
            import tabletalk.profiles as profiles_mod

            importlib.reload(profiles_mod)
            result = profiles_mod.import_from_dbt("bq_proj", "dev")

        assert result is not None
        assert result["type"] == "bigquery"
        assert result["project_id"] == "my-gcp-project"
        assert result["use_default_credentials"] is True
