"""Offline tests for `tabletalk lock` and `tabletalk plan` on the dbt-analytics example."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from tabletalk.cli import EXIT_DRIFT, cli
from tabletalk.evals import load_eval_suite
from tabletalk.plan import COMMENT_MARKER, Plan, case_footprints, compute_plan
from tabletalk.project import Project
from tabletalk.proposals import ProposalRecord
from tabletalk.state import LOCK_FILENAME, LockFile, dump_lock, load_lock, snapshot

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "dbt-analytics"
FCT = "model.analytics.fct_orders"

Mutation = Callable[[dict[str, Any], dict[str, Any]], None]


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    target = tmp_path / "dbt-analytics"
    shutil.copytree(
        EXAMPLE,
        target,
        ignore=shutil.ignore_patterns(".tabletalk", "*.duckdb*", "run_results.json"),
    )
    return target


def _lock(root: Path) -> LockFile:
    project = Project.load(root)
    return snapshot(project.agents(), project.manifest)


def _mutate(root: Path, mutation: Mutation) -> None:
    manifest_path = root / "target" / "manifest.json"
    catalog_path = root / "target" / "catalog.json"
    manifest = json.loads(manifest_path.read_text())
    catalog = json.loads(catalog_path.read_text())
    mutation(manifest, catalog)
    manifest_path.write_text(json.dumps(manifest))
    catalog_path.write_text(json.dumps(catalog))


def _plan(root: Path, locked: LockFile) -> Plan:
    project = Project.load(root)
    suites = [load_eval_suite(path) for path in sorted(project.evals_directory.glob("*.yaml"))]
    return compute_plan(
        locked,
        snapshot(project.agents(), project.manifest),
        case_footprints(suites, project.manifest, project.root),
    )


def _drop_column(manifest: dict[str, Any], catalog: dict[str, Any]) -> None:
    del manifest["nodes"][FCT]["columns"]["recognized_revenue"]
    catalog_columns = catalog["nodes"][FCT]["columns"]
    for key in [key for key in catalog_columns if key.lower() == "recognized_revenue"]:
        del catalog_columns[key]


def _add_customer_model(manifest: dict[str, Any], catalog: dict[str, Any]) -> None:
    node = copy.deepcopy(manifest["nodes"]["model.analytics.stg_orders"])
    node.update(
        unique_id="model.analytics.dim_customers",
        name="dim_customers",
        alias="dim_customers",
        group="finance",
        description="One row per customer.",
        relation_name=node["relation_name"].replace("stg_orders", "dim_customers"),
        columns={
            name: {"name": name, "description": text, "meta": {}, "tags": []}
            for name, text in (("customer_key", "Surrogate key."), ("email", "Contact address."))
        },
    )
    manifest["nodes"][node["unique_id"]] = node


def test_unchanged_manifest_yields_an_empty_plan(project_dir: Path) -> None:
    plan = _plan(project_dir, _lock(project_dir))

    assert plan.changes == []
    assert plan.affected_cases == []
    assert not plan.has_changes


def test_dropped_column_breaks_the_eval_case_that_reads_it(project_dir: Path) -> None:
    locked = _lock(project_dir)
    _mutate(project_dir, _drop_column)

    plan = _plan(project_dir, locked)

    [change] = plan.changes
    assert (change.kind, change.impact, change.target) == (
        "column_removed",
        "breaks",
        "fct_orders.recognized_revenue",
    )
    [case] = plan.affected_cases
    assert (case.suite, case.case) == ("revenue-capability", "july-recognized-revenue")
    assert case.reasons == ["fct_orders.recognized_revenue: column removed"]


def _rename_column(new_name: str, description: str | None = None) -> Mutation:
    def mutation(manifest: dict[str, Any], catalog: dict[str, Any]) -> None:
        columns = manifest["nodes"][FCT]["columns"]
        column = columns.pop("recognized_revenue")
        column["name"] = new_name
        if description is not None:
            column["description"] = description
        columns[new_name] = column
        catalog_columns = catalog["nodes"][FCT]["columns"]
        [key] = [key for key in catalog_columns if key.lower() == "recognized_revenue"]
        physical = catalog_columns.pop(key)
        physical["name"] = new_name.upper() if key.isupper() else new_name
        catalog_columns[physical["name"]] = physical

    return mutation


def test_renamed_column_is_one_breaking_change(project_dir: Path) -> None:
    locked = _lock(project_dir)
    _mutate(project_dir, _rename_column("revenue_recognized"))

    plan = _plan(project_dir, locked)

    [change] = plan.changes
    assert (change.kind, change.impact, change.target, change.detail) == (
        "column_renamed",
        "breaks",
        "fct_orders.recognized_revenue",
        "renamed to revenue_recognized",
    )
    assert [case.case for case in plan.affected_cases] == ["july-recognized-revenue"]


def test_rename_with_new_meaning_stays_a_removal_and_an_addition(project_dir: Path) -> None:
    locked = _lock(project_dir)
    _mutate(project_dir, _rename_column("net_revenue", "Revenue net of returns in USD."))

    kinds = sorted(change.kind for change in _plan(project_dir, locked).changes)

    assert kinds == ["column_added", "column_removed"]


def test_selector_expansion_flags_new_resource_and_sensitive_columns(project_dir: Path) -> None:
    locked = _lock(project_dir)
    _mutate(project_dir, _add_customer_model)

    plan = _plan(project_dir, locked)

    [change] = plan.changes
    assert change.kind == "resource_added"
    assert change.impact == "expands"
    assert change.target == "model.analytics.dim_customers"
    assert change.sensitive
    assert "sensitive: email" in change.detail
    assert plan.affected_cases == []  # Expansion is a security review, not an eval break.


def test_description_change_is_reported_and_reaches_reference_sql_columns(
    project_dir: Path,
) -> None:
    locked = _lock(project_dir)

    def reword(manifest: dict[str, Any], catalog: dict[str, Any]) -> None:
        manifest["nodes"][FCT]["columns"]["order_status"]["description"] = "Shipment state."

    _mutate(project_dir, reword)
    plan = _plan(project_dir, locked)

    [change] = plan.changes
    assert (change.kind, change.target) == ("description_changed", "fct_orders.order_status")
    # order_status appears only in the reference SQL's WHERE clause, not in expect.columns.
    assert [case.case for case in plan.affected_cases] == ["july-recognized-revenue"]


def test_lock_round_trips_byte_identically(project_dir: Path) -> None:
    lock = _lock(project_dir)
    path = project_dir / LOCK_FILENAME
    path.write_text(dump_lock(lock))

    loaded = load_lock(path)

    assert loaded == lock
    assert loaded is not None and dump_lock(loaded) == path.read_text()


def test_cli_plan_check_exits_on_drift_only(project_dir: Path) -> None:
    runner = CliRunner()
    folder = ["--project-folder", str(project_dir)]
    (project_dir / LOCK_FILENAME).unlink()

    missing = runner.invoke(cli, ["plan", "--check", *folder])
    assert missing.exit_code == EXIT_DRIFT
    assert "tabletalk lock" in missing.output

    locked = runner.invoke(cli, ["lock", *folder])
    assert locked.exit_code == 0, locked.output
    clean = runner.invoke(cli, ["plan", "--check", *folder])
    assert clean.exit_code == 0, clean.output
    assert "No changes" in clean.output

    _mutate(project_dir, _drop_column)
    drifted = runner.invoke(cli, ["plan", "--check", *folder])
    assert drifted.exit_code == EXIT_DRIFT, drifted.output
    assert "fct_orders.recognized_revenue" in drifted.output
    assert "july-recognized-revenue" in drifted.output

    as_json = runner.invoke(cli, ["plan", "--format", "json", *folder])
    payload = json.loads(as_json.output)
    assert payload["changes"][0]["kind"] == "column_removed"


def test_approve_locks_only_the_approved_agent(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    folder = ["--project-folder", str(project_dir)]
    assert runner.invoke(cli, ["lock", *folder]).exit_code == 0
    _mutate(project_dir, _drop_column)  # Drift in the already-locked revenue agent.

    proposed = runner.invoke(cli, ["propose", *folder])
    assert proposed.exit_code == 0, proposed.output
    record = ProposalRecord.load(
        next((project_dir / ".tabletalk" / "proposals").glob("prp_*.json"))
    )
    [agent_file] = [item for item in record.files if item.path.startswith("agents/")]
    approved_name = yaml.safe_load(agent_file.content)["name"]
    monkeypatch.setattr("tabletalk.cli._run_approved_evals", lambda project, paths: True)

    approved = runner.invoke(
        cli, ["approve", record.id, "--yes", "--digest", record.digest, "--actor", "t", *folder]
    )

    assert approved.exit_code == 0, approved.output
    assert f"Locked {approved_name}" in approved.output
    lock = load_lock(project_dir / LOCK_FILENAME)
    assert lock is not None and set(lock.agents) == {"revenue", approved_name}
    plan = _plan(project_dir, lock)
    assert {change.agent for change in plan.changes} == {"revenue"}  # Not silently accepted.


def _run_demo(script: str) -> subprocess.CompletedProcess[str]:
    bin_dir = str(Path(sys.executable).parent)
    env = {**os.environ, "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}
    return subprocess.run(
        ["bash", str(EXAMPLE / "demo" / script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


@pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt-duckdb is not installed")
def test_offline_drift_demo_blocks_the_pr() -> None:
    result = _run_demo("drift.sh")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "fct_orders.recognized_revenue" in result.stdout
    assert "renamed to revenue_recognized" in result.stdout
    # Non-terminal output (CI logs) must keep each change on one line.
    assert "scope expands · 2 columns · sensitive: customer_id, email" in result.stdout
    assert "revenue-capability › july-recognized-revenue" in result.stdout


@pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt-duckdb is not installed")
def test_committed_replay_fixtures_replay_in_a_fresh_checkout() -> None:
    result = _run_demo("replay.sh")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS 12/12 eval trials passed" in result.stdout


def test_committed_example_lock_matches_committed_manifest() -> None:
    assert (EXAMPLE / LOCK_FILENAME).read_text() == dump_lock(_lock(EXAMPLE))


def test_markdown_plan_is_a_reviewable_pr_comment(project_dir: Path) -> None:
    runner = CliRunner()
    folder = ["--project-folder", str(project_dir)]
    clean = runner.invoke(cli, ["plan", "--format", "markdown", *folder])
    assert clean.exit_code == 0, clean.output
    assert clean.output.startswith(COMMENT_MARKER)
    assert "**No changes.**" in clean.output

    _mutate(project_dir, _add_customer_model)
    _mutate(project_dir, _rename_column("revenue_recognized"))
    drifted = runner.invoke(cli, ["plan", "--format", "markdown", "--check", *folder])

    assert drifted.exit_code == EXIT_DRIFT
    lines = drifted.output.splitlines()
    assert "**1 breaking · 1 expanding scope** across 1 agent · 1 eval case at risk" in lines
    assert "| − | `fct_orders.recognized_revenue` | renamed to revenue_recognized |" in lines
    assert any(
        line.startswith("| + | `model.analytics.dim_customers` | **scope expands") for line in lines
    )
    assert any("`revenue-capability` › `july-recognized-revenue`" in line for line in lines)


def test_markdown_plan_without_lock_explains_the_next_step(project_dir: Path) -> None:
    (project_dir / LOCK_FILENAME).unlink()

    result = CliRunner().invoke(
        cli, ["plan", "--format", "markdown", "--project-folder", str(project_dir)]
    )

    assert result.exit_code == 0
    assert "Run `tabletalk lock` and commit the file" in result.output
