"""Replay identity must not depend on the checkout path or on dbt's per-run metadata."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pytest
import yaml

from tabletalk.manifest import Manifest
from tabletalk.project import Project

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "dbt-analytics"
QUESTION = "What was recognized revenue in July 2026?"


def _checkout(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name / "dbt-analytics"
    shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns(".tabletalk", "*.duckdb*", "run_results.json"))
    duckdb.connect(str(target / "analytics.duckdb")).close()  # The runtime opens it read-only.
    return target


def _edit_manifest(root: Path, **metadata: str) -> None:
    path = root / "target" / "manifest.json"
    payload = json.loads(path.read_text())
    payload["metadata"].update(metadata)
    payload["nodes"]["model.analytics.fct_orders"]["created_at"] = 1.0
    path.write_text(json.dumps(payload))


def test_manifest_digest_ignores_per_invocation_metadata(tmp_path: Path) -> None:
    root = _checkout(tmp_path, "a")
    before = Manifest.load(root / "target" / "manifest.json").digest

    _edit_manifest(root, generated_at="2030-01-01T00:00:00Z", invocation_id="other-run")

    assert Manifest.load(root / "target" / "manifest.json").digest == before


def test_manifest_digest_still_tracks_project_content(tmp_path: Path) -> None:
    root = _checkout(tmp_path, "a")
    path = root / "target" / "manifest.json"
    before = Manifest.load(path).digest
    payload = json.loads(path.read_text())
    payload["nodes"]["model.analytics.fct_orders"]["description"] = "Changed."
    path.write_text(json.dumps(payload))

    assert Manifest.load(path).digest != before


def test_fingerprint_matches_across_checkouts_and_dbt_reparses(tmp_path: Path) -> None:
    first, second = _checkout(tmp_path, "laptop"), _checkout(tmp_path, "ci-runner")
    _edit_manifest(second, generated_at="2030-01-01T00:00:00Z", invocation_id="ci")

    prints = [
        Project.load(root).runtime("revenue").fingerprint(QUESTION) for root in (first, second)
    ]

    assert prints[0].digest == prints[1].digest
    assert (
        prints[0].execution_config["warehouse_identity"] == "duckdb;database_path=analytics.duckdb"
    )


def test_inference_store_can_live_in_the_repository(tmp_path: Path) -> None:
    root = _checkout(tmp_path, "a")
    config_path = root / "tabletalk.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["inference"] = {"store": "fixtures/inference"}
    config_path.write_text(yaml.safe_dump(config))

    store = Project.load(root).runtime("revenue").inference_store

    assert store is not None and store.root == (root / "fixtures" / "inference").resolve()

    config["inference"] = {"store": "../outside"}
    config_path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="must stay inside the project"):
        Project.load(root).runtime("revenue")


def _rewrite_node(root: Path, unique_id: str, **fields: str) -> None:
    path = root / "target" / "manifest.json"
    payload = json.loads(path.read_text())
    payload["nodes"][unique_id].update(fields)
    path.write_text(json.dumps(payload))


def test_fingerprint_survives_dbt_changes_outside_agent_scope(tmp_path: Path) -> None:
    root = _checkout(tmp_path, "a")
    before = Project.load(root).runtime("revenue").fingerprint(QUESTION).digest

    # shared_calendar is not in group:finance, so the revenue agent never sees it.
    _rewrite_node(root, "model.shared_models.shared_calendar", description="Reworded.")

    after = Project.load(root)
    assert after.manifest.digest != Manifest.load(EXAMPLE / "target" / "manifest.json").digest
    assert after.runtime("revenue").fingerprint(QUESTION).digest == before


def test_fingerprint_changes_when_agent_scope_changes(tmp_path: Path) -> None:
    root = _checkout(tmp_path, "a")
    before = Project.load(root).runtime("revenue").fingerprint(QUESTION).digest

    _rewrite_node(root, "model.analytics.fct_orders", description="One row per shipment.")

    assert Project.load(root).runtime("revenue").fingerprint(QUESTION).digest != before
