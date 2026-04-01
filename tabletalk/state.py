"""
state.py — Manifest state management.

item  7: Remote state — store/load manifests from local filesystem, S3, or GCS.
item  8: Manifest locking — .tabletalk.lock pins the exact schema fingerprint.
item  9: Environment promotion — copy manifests between project environments.
item 10: Rollback — restore a previous manifest from .tabletalk_history/.

Storage backends
----------------
local://   Default. Manifests live in project manifest/ directory (current behaviour).
s3://      Requires boto3: uv add 'tabletalk[s3]'
gcs://     Requires google-cloud-storage: uv add 'tabletalk[gcs]'

Configure in tabletalk.yaml:
  state:
    backend: local                  # local | s3 | gcs
    bucket: my-tabletalk-state      # s3 / gcs only
    prefix: projects/myproject      # optional key prefix
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("tabletalk")

# ── Storage backends ──────────────────────────────────────────────────────────


class _LocalBackend:
    """Default — manifests are files on the local filesystem."""

    def __init__(self, manifest_dir: str) -> None:
        self.manifest_dir = manifest_dir

    def read(self, key: str) -> Optional[str]:
        path = os.path.join(self.manifest_dir, key)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return f.read()

    def write(self, key: str, content: str) -> None:
        os.makedirs(self.manifest_dir, exist_ok=True)
        with open(os.path.join(self.manifest_dir, key), "w") as f:
            f.write(content)

    def list_keys(self, prefix: str = "") -> List[str]:
        if not os.path.isdir(self.manifest_dir):
            return []
        return [
            f for f in os.listdir(self.manifest_dir)
            if f.startswith(prefix) and f.endswith(".txt")
        ]

    def delete(self, key: str) -> None:
        path = os.path.join(self.manifest_dir, key)
        if os.path.exists(path):
            os.remove(path)


class _S3Backend:
    """S3-backed manifest storage.  Requires boto3."""

    def __init__(self, bucket: str, prefix: str = "") -> None:
        try:
            import boto3  # type: ignore
        except ImportError:
            raise ImportError(
                "boto3 is required for S3 state backend. "
                "Install with: uv add 'tabletalk[s3]'"
            )
        self.s3 = boto3.client("s3")
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")

    def _key(self, name: str) -> str:
        return f"{self.prefix}/{name}" if self.prefix else name

    def read(self, key: str) -> Optional[str]:
        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=self._key(key))
            return resp["Body"].read().decode("utf-8")
        except Exception:
            return None

    def write(self, key: str, content: str) -> None:
        self.s3.put_object(
            Bucket=self.bucket,
            Key=self._key(key),
            Body=content.encode("utf-8"),
            ContentType="text/plain",
        )

    def list_keys(self, prefix: str = "") -> List[str]:
        paginator = self.s3.get_paginator("list_objects_v2")
        full_prefix = self._key(prefix)
        keys = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if self.prefix:
                    k = k[len(self.prefix) + 1:]
                if k.endswith(".txt"):
                    keys.append(k)
        return keys

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=self._key(key))


class _GCSBackend:
    """GCS-backed manifest storage.  Requires google-cloud-storage."""

    def __init__(self, bucket: str, prefix: str = "") -> None:
        try:
            from google.cloud import storage as gcs  # type: ignore
        except ImportError:
            raise ImportError(
                "google-cloud-storage is required for GCS state backend. "
                "Install with: uv add 'tabletalk[gcs]'"
            )
        self.client = gcs.Client()
        self.bucket = self.client.bucket(bucket)
        self.prefix = prefix.rstrip("/")

    def _blob_name(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def read(self, key: str) -> Optional[str]:
        blob = self.bucket.blob(self._blob_name(key))
        try:
            return blob.download_as_text()
        except Exception:
            return None

    def write(self, key: str, content: str) -> None:
        blob = self.bucket.blob(self._blob_name(key))
        blob.upload_from_string(content, content_type="text/plain")

    def list_keys(self, prefix: str = "") -> List[str]:
        full_prefix = self._blob_name(prefix)
        keys = []
        for blob in self.client.list_blobs(self.bucket, prefix=full_prefix):
            name = blob.name
            if self.prefix:
                name = name[len(self.prefix) + 1:]
            if name.endswith(".txt"):
                keys.append(name)
        return keys

    def delete(self, key: str) -> None:
        self.bucket.blob(self._blob_name(key)).delete()


def _get_backend(project_folder: str) -> _LocalBackend:
    """
    Return the configured storage backend.
    Reads state.backend from tabletalk.yaml; defaults to local.
    """
    config_path = os.path.join(project_folder, "tabletalk.yaml")
    manifest_dir = os.path.join(project_folder, "manifest")
    if not os.path.exists(config_path):
        return _LocalBackend(manifest_dir)

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    state_cfg = config.get("state", {})
    backend_type = state_cfg.get("backend", "local")
    manifest_dir = os.path.join(project_folder, config.get("output", "manifest"))

    if backend_type == "s3":
        return _S3Backend(  # type: ignore[return-value]
            bucket=state_cfg["bucket"],
            prefix=state_cfg.get("prefix", ""),
        )
    if backend_type == "gcs":
        return _GCSBackend(  # type: ignore[return-value]
            bucket=state_cfg["bucket"],
            prefix=state_cfg.get("prefix", ""),
        )
    return _LocalBackend(manifest_dir)


# ── Locking (item 8) ──────────────────────────────────────────────────────────


def _manifest_fingerprint(manifest_dir: str) -> Dict[str, str]:
    """Return {filename: sha256} for all manifests in the directory."""
    fp: Dict[str, str] = {}
    if not os.path.isdir(manifest_dir):
        return fp
    for f in sorted(os.listdir(manifest_dir)):
        if not f.endswith(".txt"):
            continue
        path = os.path.join(manifest_dir, f)
        with open(path, "rb") as fh:
            fp[f] = hashlib.sha256(fh.read()).hexdigest()
    return fp


def write_lock(project_folder: str) -> str:
    """
    Write .tabletalk.lock with SHA-256 fingerprints of the current manifests.
    Returns the lock file path.
    """
    manifest_dir = os.path.join(project_folder, "manifest")
    lock_path = os.path.join(project_folder, ".tabletalk.lock")
    lock: Dict[str, Any] = {
        "version": "1",
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "manifests": _manifest_fingerprint(manifest_dir),
    }
    with open(lock_path, "w") as f:
        json.dump(lock, f, indent=2)
    logger.info(f"Lock written: {lock_path}")
    return lock_path


def check_lock(project_folder: str) -> List[str]:
    """
    Compare current manifests against the lock file.
    Returns a list of drift messages (empty = clean).
    """
    lock_path = os.path.join(project_folder, ".tabletalk.lock")
    if not os.path.exists(lock_path):
        return []
    with open(lock_path) as f:
        lock = json.load(f)

    manifest_dir = os.path.join(project_folder, "manifest")
    current = _manifest_fingerprint(manifest_dir)
    locked: Dict[str, str] = lock.get("manifests", {})

    drifts: List[str] = []
    for name, locked_hash in locked.items():
        if name not in current:
            drifts.append(f"MISSING  {name} (was locked)")
        elif current[name] != locked_hash:
            drifts.append(f"CHANGED  {name}")
    for name in current:
        if name not in locked:
            drifts.append(f"ADDED    {name} (not in lock)")
    return drifts


# ── History / Rollback (item 10) ──────────────────────────────────────────────

_HISTORY_DIR = ".tabletalk_history"


def snapshot_manifests(project_folder: str) -> str:
    """
    Copy all current manifests to .tabletalk_history/<timestamp>/.
    Returns the snapshot directory path.
    """
    manifest_dir = os.path.join(project_folder, "manifest")
    if not os.path.isdir(manifest_dir):
        raise FileNotFoundError(f"Manifest directory not found: {manifest_dir}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snap_dir = os.path.join(project_folder, _HISTORY_DIR, ts)
    shutil.copytree(manifest_dir, snap_dir)
    logger.info(f"Snapshot saved: {snap_dir}")
    return snap_dir


def list_snapshots(project_folder: str) -> List[str]:
    """Return snapshot timestamps sorted newest-first."""
    history_dir = os.path.join(project_folder, _HISTORY_DIR)
    if not os.path.isdir(history_dir):
        return []
    return sorted(
        [d for d in os.listdir(history_dir) if os.path.isdir(os.path.join(history_dir, d))],
        reverse=True,
    )


def rollback(project_folder: str, steps: int = 1) -> str:
    """
    Restore manifests from N snapshots ago. Returns the restored snapshot label.
    The current manifests are auto-snapshotted before the rollback.
    """
    snapshots = list_snapshots(project_folder)
    if len(snapshots) < steps:
        raise IndexError(
            f"Only {len(snapshots)} snapshot(s) available; cannot roll back {steps} step(s)."
        )
    target = snapshots[steps - 1]
    snap_dir = os.path.join(project_folder, _HISTORY_DIR, target)

    manifest_dir = os.path.join(project_folder, "manifest")

    # Auto-snapshot current state before overwriting
    snapshot_manifests(project_folder)

    # Replace manifests with snapshot
    if os.path.exists(manifest_dir):
        shutil.rmtree(manifest_dir)
    shutil.copytree(snap_dir, manifest_dir)
    logger.info(f"Rolled back to snapshot: {target}")
    return target


# ── Environment promotion (item 9) ───────────────────────────────────────────


def promote(
    source_project: str,
    target_project: str,
    manifests: Optional[List[str]] = None,
) -> List[str]:
    """
    Copy compiled manifests from source_project to target_project.
    Validates that target has a tabletalk.yaml before writing.
    Returns list of promoted manifest filenames.
    """
    if not os.path.exists(os.path.join(target_project, "tabletalk.yaml")):
        raise FileNotFoundError(
            f"Target project has no tabletalk.yaml: {target_project}. "
            "Run 'tabletalk init' in the target directory first."
        )

    src_manifest_dir = os.path.join(source_project, "manifest")
    if not os.path.isdir(src_manifest_dir):
        raise FileNotFoundError(
            f"Source has no manifest directory: {src_manifest_dir}. "
            "Run 'tabletalk apply' in the source project first."
        )

    # Load target config to find its output dir
    with open(os.path.join(target_project, "tabletalk.yaml")) as f:
        target_config = yaml.safe_load(f) or {}
    tgt_manifest_dir = os.path.join(target_project, target_config.get("output", "manifest"))
    os.makedirs(tgt_manifest_dir, exist_ok=True)

    available = [f for f in os.listdir(src_manifest_dir) if f.endswith(".txt")]
    to_promote = [m for m in available if manifests is None or m in manifests]

    if not to_promote:
        raise ValueError(
            f"No matching manifests found in {src_manifest_dir}. "
            f"Available: {available}"
        )

    promoted = []
    for name in to_promote:
        shutil.copy2(
            os.path.join(src_manifest_dir, name),
            os.path.join(tgt_manifest_dir, name),
        )
        promoted.append(name)
        logger.info(f"Promoted: {name} → {tgt_manifest_dir}")

    # Write lock in target after promotion
    write_lock(target_project)
    return promoted
