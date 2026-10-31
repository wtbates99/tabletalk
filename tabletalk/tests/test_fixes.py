"""tabletalk fix: certain dbt YAML fixes as a patch that git applies cleanly."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tabletalk.fixes import plan_fixes
from tabletalk.project import Project
from tabletalk.tests.test_plan import FCT, project_dir

__all__ = ["project_dir"]  # Re-exported fixture.

SENSITIVE = "        config:\n          meta:\n            sensitive: true\n"
BOOKED = (
    "      - name: booked_date\n        description: Date on which the customer placed the order.\n"
)


def _break(root: Path) -> str:
    """Drop fct_orders.customer_id's flag and booked_date's description; return the original."""
    schema = root / "models" / "schema.yml"
    original = schema.read_text()
    head, fct = original.split("  - name: fct_orders", 1)
    fct = fct.replace(SENSITIVE, "", 1).replace(BOOKED, "      - name: booked_date\n", 1)
    schema.write_text(head + "  - name: fct_orders" + fct)
    return original


def _sync_manifest(root: Path) -> None:
    """Mirror _break in manifest.json, as 'dbt parse' would, for tests without dbt."""
    path = root / "target" / "manifest.json"
    manifest = json.loads(path.read_text())
    columns = manifest["nodes"][FCT]["columns"]
    for key in ("meta", "config"):
        columns["customer_id"].pop(key, None)
    columns["customer_id"]["config"] = {"meta": {}}
    columns["booked_date"]["description"] = ""
    path.write_text(json.dumps(manifest))


def test_clean_example_needs_no_fixes(project_dir: Path) -> None:
    project = Project.load(project_dir)

    result = plan_fixes(project.agents(), project.manifest, project.dbt_project_dir)

    assert (result.fixes, result.manual, result.patch) == ([], [], "")


def test_flags_and_inherited_descriptions_become_one_patch(project_dir: Path) -> None:
    _break(project_dir)
    _sync_manifest(project_dir)
    project = Project.load(project_dir)

    result = plan_fixes(project.agents(), project.manifest, project.dbt_project_dir)

    assert [(item.kind, item.column) for item in result.fixes] == [
        ("flag_sensitive", "customer_id"),
        ("inherit_description", "booked_date"),
    ]
    assert result.manual == []
    assert "+        description: Source booking date.\n" in result.patch
    assert "+            sensitive: true\n" in result.patch
    assert result.patch.startswith("--- a/models/schema.yml\n+++ b/models/schema.yml\n")


def test_disagreeing_upstream_descriptions_are_never_guessed(project_dir: Path) -> None:
    # stg_orders and raw.orders describe order_date differently.
    path = project_dir / "target" / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["nodes"][FCT]["columns"]["order_date"]["description"] = ""
    path.write_text(json.dumps(manifest))
    project = Project.load(project_dir)

    result = plan_fixes(project.agents(), project.manifest, project.dbt_project_dir)

    assert all(item.column != "order_date" for item in result.fixes + result.manual)


def test_a_column_missing_from_yaml_is_left_to_a_human(project_dir: Path) -> None:
    _break(project_dir)
    _sync_manifest(project_dir)
    schema = project_dir / "models" / "schema.yml"
    schema.write_text(
        schema.read_text().replace("      - name: customer_id\n", "      - name: cust\n")
    )
    project = Project.load(project_dir)

    result = plan_fixes(project.agents(), project.manifest, project.dbt_project_dir)

    [manual] = result.manual
    assert (manual.column, manual.reason) == (
        "customer_id",
        "customer_id is not listed under fct_orders in models/schema.yml",
    )
    assert [item.column for item in result.fixes] == ["booked_date"]


def test_an_existing_config_block_is_left_to_a_human(project_dir: Path) -> None:
    _break(project_dir)
    _sync_manifest(project_dir)
    schema = project_dir / "models" / "schema.yml"
    schema.write_text(
        schema.read_text().replace(
            "      - name: customer_id\n",
            "      - name: customer_id\n        config:\n          tags: [pii]\n",
        )
    )
    project = Project.load(project_dir)

    [manual] = plan_fixes(project.agents(), project.manifest, project.dbt_project_dir).manual

    assert "already has a config or meta block" in manual.reason


@pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt-duckdb is not installed")
def test_patch_applies_with_git_and_dbt_accepts_the_result(project_dir: Path) -> None:
    original = _break(project_dir)

    def run(*command: str) -> str:
        done = subprocess.run(command, cwd=project_dir, capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    run("dbt", "deps", "--profiles-dir", ".")
    run("dbt", "parse", "--profiles-dir", ".")
    run("tabletalk", "fix", "--write", "fix.patch")
    run("git", "init", "-q", ".")
    run("git", "apply", "fix.patch")
    run("dbt", "parse", "--profiles-dir", ".")

    expected = original.replace(
        BOOKED, "      - name: booked_date\n        description: Source booking date.\n", 1
    )
    assert (project_dir / "models" / "schema.yml").read_text() == expected
    assert "No certain dbt fixes" in run("tabletalk", "fix")
