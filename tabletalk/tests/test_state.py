"""Tests for tabletalk/state.py"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tabletalk.state import (
    _LocalBackend,
    _manifest_fingerprint,
    check_lock,
    list_snapshots,
    promote,
    rollback,
    snapshot_manifests,
    write_lock,
)


# ── _LocalBackend ─────────────────────────────────────────────────────────────


class TestLocalBackend:
    def test_read_missing_returns_none(self, tmp_path):
        b = _LocalBackend(str(tmp_path))
        assert b.read("nonexistent.txt") is None

    def test_write_and_read(self, tmp_path):
        b = _LocalBackend(str(tmp_path))
        b.write("hello.txt", "world")
        assert b.read("hello.txt") == "world"

    def test_list_keys_empty(self, tmp_path):
        b = _LocalBackend(str(tmp_path / "manifests"))
        assert b.list_keys() == []

    def test_list_keys_filters_txt(self, tmp_path):
        manifest_dir = tmp_path / "manifests"
        manifest_dir.mkdir()
        (manifest_dir / "a.txt").write_text("x")
        (manifest_dir / "b.yaml").write_text("y")
        b = _LocalBackend(str(manifest_dir))
        keys = b.list_keys()
        assert "a.txt" in keys
        assert "b.yaml" not in keys

    def test_delete(self, tmp_path):
        b = _LocalBackend(str(tmp_path))
        b.write("del.txt", "data")
        assert b.read("del.txt") == "data"
        b.delete("del.txt")
        assert b.read("del.txt") is None

    def test_delete_missing_no_error(self, tmp_path):
        b = _LocalBackend(str(tmp_path))
        b.delete("nonexistent.txt")  # should not raise


# ── _manifest_fingerprint ─────────────────────────────────────────────────────


class TestManifestFingerprint:
    def test_empty_dir_returns_empty(self, tmp_path):
        assert _manifest_fingerprint(str(tmp_path)) == {}

    def test_missing_dir_returns_empty(self, tmp_path):
        assert _manifest_fingerprint(str(tmp_path / "nope")) == {}

    def test_fingerprints_txt_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.yaml").write_text("skipped")
        fp = _manifest_fingerprint(str(tmp_path))
        assert "a.txt" in fp
        assert "b.yaml" not in fp
        assert len(fp["a.txt"]) == 64  # sha256 hex

    def test_same_content_same_hash(self, tmp_path):
        (tmp_path / "a.txt").write_text("content")
        (tmp_path / "b.txt").write_text("content")
        fp = _manifest_fingerprint(str(tmp_path))
        assert fp["a.txt"] == fp["b.txt"]

    def test_different_content_different_hash(self, tmp_path):
        (tmp_path / "a.txt").write_text("aaa")
        (tmp_path / "b.txt").write_text("bbb")
        fp = _manifest_fingerprint(str(tmp_path))
        assert fp["a.txt"] != fp["b.txt"]


# ── write_lock / check_lock ───────────────────────────────────────────────────


class TestLock:
    def _setup_project(self, tmp_path, content="SELECT 1"):
        manifest_dir = tmp_path / "manifest"
        manifest_dir.mkdir()
        (manifest_dir / "sales.txt").write_text(content)
        (tmp_path / "tabletalk.yaml").write_text("output: manifest\n")
        return str(tmp_path)

    def test_write_lock_creates_file(self, tmp_path):
        project = self._setup_project(tmp_path)
        path = write_lock(project)
        assert os.path.exists(path)

    def test_write_lock_content(self, tmp_path):
        project = self._setup_project(tmp_path)
        path = write_lock(project)
        with open(path) as f:
            lock = json.load(f)
        assert lock["version"] == "1"
        assert "locked_at" in lock
        assert "sales.txt" in lock["manifests"]

    def test_check_lock_clean(self, tmp_path):
        project = self._setup_project(tmp_path)
        write_lock(project)
        drifts = check_lock(project)
        assert drifts == []

    def test_check_lock_no_lockfile(self, tmp_path):
        project = self._setup_project(tmp_path)
        drifts = check_lock(project)
        assert drifts == []  # no lock = no drift reported

    def test_check_lock_changed(self, tmp_path):
        project = self._setup_project(tmp_path)
        write_lock(project)
        # Modify manifest after locking
        (tmp_path / "manifest" / "sales.txt").write_text("MODIFIED")
        drifts = check_lock(project)
        assert any("CHANGED" in d for d in drifts)

    def test_check_lock_added(self, tmp_path):
        project = self._setup_project(tmp_path)
        write_lock(project)
        # Add a new manifest after locking
        (tmp_path / "manifest" / "new.txt").write_text("new")
        drifts = check_lock(project)
        assert any("ADDED" in d for d in drifts)

    def test_check_lock_missing(self, tmp_path):
        project = self._setup_project(tmp_path)
        write_lock(project)
        # Remove a manifest after locking
        os.remove(str(tmp_path / "manifest" / "sales.txt"))
        drifts = check_lock(project)
        assert any("MISSING" in d for d in drifts)


# ── snapshot_manifests / rollback ─────────────────────────────────────────────


class TestSnapshot:
    def _setup_project(self, tmp_path):
        manifest_dir = tmp_path / "manifest"
        manifest_dir.mkdir()
        (manifest_dir / "sales.txt").write_text("v1")
        return str(tmp_path)

    def test_snapshot_creates_directory(self, tmp_path):
        project = self._setup_project(tmp_path)
        snap_dir = snapshot_manifests(project)
        assert os.path.isdir(snap_dir)

    def test_snapshot_copies_files(self, tmp_path):
        project = self._setup_project(tmp_path)
        snap_dir = snapshot_manifests(project)
        assert os.path.exists(os.path.join(snap_dir, "sales.txt"))

    def test_list_snapshots_empty(self, tmp_path):
        assert list_snapshots(str(tmp_path)) == []

    def test_list_snapshots_sorted_newest_first(self, tmp_path):
        project = self._setup_project(tmp_path)
        snap1 = snapshot_manifests(project)
        time.sleep(0.01)
        snap2 = snapshot_manifests(project)
        snaps = list_snapshots(project)
        assert snaps[0] > snaps[1]  # newer first (ISO timestamp sort)

    def test_snapshot_missing_manifest_dir(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            snapshot_manifests(str(tmp_path))


class TestRollback:
    def _setup_project(self, tmp_path):
        manifest_dir = tmp_path / "manifest"
        manifest_dir.mkdir()
        (manifest_dir / "sales.txt").write_text("v1")
        return str(tmp_path)

    def test_rollback_restores_content(self, tmp_path):
        project = self._setup_project(tmp_path)
        # Take a snapshot of v1
        snapshot_manifests(project)
        # Overwrite to v2
        (tmp_path / "manifest" / "sales.txt").write_text("v2")
        # Rollback 1 step
        label = rollback(project, steps=1)
        content = (tmp_path / "manifest" / "sales.txt").read_text()
        assert content == "v1"

    def test_rollback_raises_if_not_enough_snapshots(self, tmp_path):
        project = self._setup_project(tmp_path)
        snapshot_manifests(project)
        with pytest.raises(IndexError):
            rollback(project, steps=10)

    def test_rollback_auto_snapshots_before_overwrite(self, tmp_path):
        project = self._setup_project(tmp_path)
        snapshot_manifests(project)
        # Initial snapshot count
        before = len(list_snapshots(project))
        (tmp_path / "manifest" / "sales.txt").write_text("v2")
        rollback(project, steps=1)
        after = len(list_snapshots(project))
        # An auto-snapshot of v2 should have been created
        assert after > before


# ── promote ───────────────────────────────────────────────────────────────────


class TestPromote:
    def _setup_source(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        manifest = src / "manifest"
        manifest.mkdir()
        (manifest / "sales.txt").write_text("sales manifest")
        (src / "tabletalk.yaml").write_text("output: manifest\n")
        return str(src)

    def _setup_target(self, tmp_path):
        tgt = tmp_path / "tgt"
        tgt.mkdir()
        (tgt / "tabletalk.yaml").write_text("output: manifest\n")
        return str(tgt)

    def test_promote_copies_manifests(self, tmp_path):
        src = self._setup_source(tmp_path)
        tgt = self._setup_target(tmp_path)
        promoted = promote(src, tgt)
        assert "sales.txt" in promoted
        assert os.path.exists(os.path.join(tgt, "manifest", "sales.txt"))

    def test_promote_writes_lock(self, tmp_path):
        src = self._setup_source(tmp_path)
        tgt = self._setup_target(tmp_path)
        promote(src, tgt)
        assert os.path.exists(os.path.join(tgt, ".tabletalk.lock"))

    def test_promote_target_missing_yaml(self, tmp_path):
        src = self._setup_source(tmp_path)
        tgt = tmp_path / "notgt"
        tgt.mkdir()
        with pytest.raises(FileNotFoundError, match="tabletalk.yaml"):
            promote(str(src), str(tgt))

    def test_promote_source_no_manifests(self, tmp_path):
        src = tmp_path / "empty_src"
        src.mkdir()
        (src / "tabletalk.yaml").write_text("")
        tgt = self._setup_target(tmp_path)
        with pytest.raises(FileNotFoundError, match="manifest directory"):
            promote(str(src), str(tgt))

    def test_promote_specific_manifest(self, tmp_path):
        src = self._setup_source(tmp_path)
        # Add another manifest
        (Path(src) / "manifest" / "customers.txt").write_text("customers")
        tgt = self._setup_target(tmp_path)
        promoted = promote(src, tgt, manifests=["sales.txt"])
        assert promoted == ["sales.txt"]
        assert not os.path.exists(os.path.join(tgt, "manifest", "customers.txt"))
