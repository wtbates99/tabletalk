"""
Tests for tabletalk/utils.py

Covers:
  - initialize_project
  - apply_schema  (full round-trip with SQLite)
  - check_manifest_staleness
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import yaml


# ── initialize_project ────────────────────────────────────────────────────────

class TestInitializeProject:
    def test_creates_config_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from tabletalk.utils import initialize_project

        initialize_project()
        assert (tmp_path / "tabletalk.yaml").exists()

    def test_creates_contexts_folder(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from tabletalk.utils import initialize_project

        initialize_project()
        assert (tmp_path / "contexts").is_dir()

    def test_creates_manifest_folder(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from tabletalk.utils import initialize_project

        initialize_project()
        assert (tmp_path / "manifest").is_dir()

    def test_creates_sample_context(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from tabletalk.utils import initialize_project

        initialize_project()
        ctx = tmp_path / "contexts" / "default_context.yaml"
        assert ctx.exists()
        content = yaml.safe_load(ctx.read_text())
        assert "name" in content
        assert "datasets" in content

    def test_config_is_valid_yaml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from tabletalk.utils import initialize_project

        initialize_project()
        config = yaml.safe_load((tmp_path / "tabletalk.yaml").read_text())
        assert isinstance(config, dict)
        assert "provider" in config
        assert "llm" in config
        assert "contexts" in config
        assert "output" in config

    def test_idempotent_when_already_initialized(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        from tabletalk.utils import initialize_project

        initialize_project()
        initialize_project()  # second call — should not overwrite

        captured = capsys.readouterr()
        assert "Already initialized" in captured.out

    def test_does_not_overwrite_existing_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from tabletalk.utils import initialize_project

        original = "my_custom_config: true\n"
        (tmp_path / "tabletalk.yaml").write_text(original)
        initialize_project()
        assert (tmp_path / "tabletalk.yaml").read_text() == original


# ── apply_schema ──────────────────────────────────────────────────────────────

class TestApplySchema:
    def test_generates_manifests(self, project_dir):
        from tabletalk.utils import apply_schema

        apply_schema(project_dir)
        manifest_dir = Path(project_dir) / "manifest"
        txts = list(manifest_dir.glob("*.txt"))
        assert len(txts) == 4

    def test_manifest_has_correct_filenames(self, project_dir):
        from tabletalk.utils import apply_schema

        apply_schema(project_dir)
        names = {f.name for f in (Path(project_dir) / "manifest").glob("*.txt")}
        assert names == {"customers.txt", "orders.txt", "inventory.txt", "marketing.txt"}

    def test_manifest_content_is_non_empty(self, project_dir):
        from tabletalk.utils import apply_schema

        apply_schema(project_dir)
        for txt in (Path(project_dir) / "manifest").glob("*.txt"):
            assert txt.stat().st_size > 0

    def test_applies_with_inline_provider(self, tmp_path, ecommerce_sqlite):
        """apply_schema works with an inline provider block in tabletalk.yaml."""
        config = {
            "provider": {"type": "sqlite", "database_path": ecommerce_sqlite},
            "llm": {"provider": "openai", "api_key": "test"},
            "description": "test",
            "contexts": "contexts",
            "output": "manifest",
        }
        (tmp_path / "tabletalk.yaml").write_text(yaml.dump(config))
        (tmp_path / "contexts").mkdir()
        (tmp_path / "contexts" / "c.yaml").write_text(
            yaml.dump(
                {
                    "name": "c",
                    "description": "test",
                    "datasets": [
                        {"name": "main", "tables": [{"name": "customers"}]}
                    ],
                }
            )
        )
        (tmp_path / "manifest").mkdir()

        from tabletalk.utils import apply_schema

        apply_schema(str(tmp_path))
        assert (tmp_path / "manifest" / "c.txt").exists()

    def test_creates_manifest_dir_if_missing(self, tmp_path, ecommerce_sqlite):
        """apply_schema creates the manifest directory if it doesn't exist."""
        config = {
            "provider": {"type": "sqlite", "database_path": ecommerce_sqlite},
            "llm": {"provider": "openai", "api_key": "test"},
            "description": "test",
            "contexts": "contexts",
            "output": "manifest",
        }
        (tmp_path / "tabletalk.yaml").write_text(yaml.dump(config))
        (tmp_path / "contexts").mkdir()
        (tmp_path / "contexts" / "c.yaml").write_text(
            yaml.dump(
                {
                    "name": "c",
                    "description": "test",
                    "datasets": [{"name": "main", "tables": [{"name": "customers"}]}],
                }
            )
        )
        # Do NOT create manifest dir — apply_schema should handle it

        from tabletalk.utils import apply_schema

        apply_schema(str(tmp_path))
        assert (tmp_path / "manifest").exists()


# ── check_manifest_staleness ──────────────────────────────────────────────────

class TestCheckManifestStaleness:
    def test_stale_when_manifest_dir_missing(self, project_dir):
        """No manifest dir → always stale."""
        import shutil
        from tabletalk.utils import check_manifest_staleness

        shutil.rmtree(Path(project_dir) / "manifest")
        assert check_manifest_staleness(project_dir) is True

    def test_stale_when_manifest_file_missing(self, project_with_manifest):
        """A context YAML with no corresponding manifest → stale."""
        import shutil
        from tabletalk.utils import check_manifest_staleness

        # Remove one manifest file
        (Path(project_with_manifest) / "manifest" / "customers.txt").unlink()
        assert check_manifest_staleness(project_with_manifest) is True

    def test_not_stale_when_manifests_are_fresh(self, project_with_manifest):
        from tabletalk.utils import check_manifest_staleness

        # Manifests were just generated — should not be stale
        assert check_manifest_staleness(project_with_manifest) is False

    def test_stale_when_context_newer_than_manifest(self, project_with_manifest):
        """Touching a context file makes it stale."""
        from tabletalk.utils import check_manifest_staleness

        ctx_file = Path(project_with_manifest) / "contexts" / "customers.yaml"
        # Sleep briefly to ensure newer mtime
        time.sleep(0.05)
        ctx_file.touch()
        assert check_manifest_staleness(project_with_manifest) is True

    def test_not_stale_with_no_contexts(self, tmp_path):
        """Empty contexts folder → nothing is stale."""
        from tabletalk.utils import check_manifest_staleness

        (tmp_path / "contexts").mkdir()
        (tmp_path / "manifest").mkdir()
        assert check_manifest_staleness(str(tmp_path)) is False

    def test_stale_when_contexts_folder_missing(self, tmp_path):
        """Missing contexts folder → not stale (nothing to regenerate)."""
        from tabletalk.utils import check_manifest_staleness

        (tmp_path / "manifest").mkdir()
        # No contexts/ dir
        assert check_manifest_staleness(str(tmp_path)) is False
