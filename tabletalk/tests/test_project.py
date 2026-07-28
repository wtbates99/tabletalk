from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from tabletalk import Project
from tabletalk.cli import cli
from tabletalk.domain import ErrorCode, TableTalkError


def project_fixture(tmp_path: Path) -> Project:
    database = tmp_path / "analytics.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            net_revenue NUMERIC NOT NULL,
            order_date DATE NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE customer_sensitive (ssn TEXT NOT NULL);
        """
    )
    connection.close()
    (tmp_path / "tabletalk.yaml").write_text(
        f"""\
connections:
  local:
    type: sqlite
    path: {database}
llm:
  provider: ollama
  api_key: ollama
  model: gemma4:31b-cloud
"""
    )
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "sales.yaml").write_text(
        """\
kind: Agent
name: sales
description: Answers sales questions.
connection: local
relations:
  include: [main.*]
  exclude: [main.customer_sensitive]
semantics:
  metrics:
    revenue:
      expression: main.orders.net_revenue
      aggregation: sum
      time_dimension: main.orders.order_date
      synonyms: [sales]
  time:
    timezone: UTC
    week_start: monday
    default_dimension: main.orders.order_date
  rules:
    - Exclude cancelled orders.
policies:
  read_only: true
  require_evidence: true
evals: []
"""
    )
    return Project.load(tmp_path)


def test_project_compiles_first_class_agent_into_content_addressed_artifact(
    tmp_path: Path,
) -> None:
    project = project_fixture(tmp_path)

    artifact = project.compile("sales")

    path = (
        tmp_path
        / ".tabletalk"
        / "artifacts"
        / "sales"
        / f"{artifact.digest}.json"
    )
    assert path.read_text() == artifact.to_json() + "\n"
    payload = json.loads(path.read_text())
    assert [relation["name"] for relation in payload["agent"]["relations"]] == [
        "main.orders",
        "main.products",
    ]
    serialized = path.read_text()
    assert str(tmp_path / "analytics.db") not in serialized
    assert "api_key" not in serialized


def test_project_compile_is_idempotent(tmp_path: Path) -> None:
    project = project_fixture(tmp_path)

    first = project.compile("sales")
    second = project.compile("sales")

    assert first.digest == second.digest
    artifact_files = list(
        (tmp_path / ".tabletalk" / "artifacts" / "sales").glob("*.json")
    )
    assert len(artifact_files) == 1


def test_project_compilation_normalizes_dbt_metadata_and_fingerprint(
    tmp_path: Path,
) -> None:
    project_fixture(tmp_path)
    target = tmp_path / "dbt" / "target"
    target.mkdir(parents=True)
    manifest = target / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.analytics.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "alias": "orders",
                        "schema": "main",
                        "description": "Curated order facts.",
                        "columns": {
                            "net_revenue": {
                                "name": "net_revenue",
                                "description": "Revenue after returns.",
                            }
                        },
                        "depends_on": {"nodes": ["source.raw.orders"]},
                        "config": {"materialized": "table"},
                        "tags": ["finance"],
                    }
                }
            }
        )
    )
    config_path = tmp_path / "tabletalk.yaml"
    config_path.write_text(
        config_path.read_text()
        + "\ndbt:\n  project_dir: dbt\n  target_dir: target\n"
    )

    first = Project.load(tmp_path).compile("sales")
    orders = next(
        relation for relation in first.agent.relations if relation.name == "main.orders"
    )
    revenue = next(
        column for column in orders.columns if column.name == "net_revenue"
    )
    assert orders.description == "Curated order facts."
    assert revenue.description == "Revenue after returns."
    assert revenue.provenance == "dbt_column_description"
    assert dict(orders.dbt_metadata)["node"] == "model.analytics.orders"

    manifest.write_text(
        manifest.read_text().replace(
            "Revenue after returns.",
            "Recognized revenue after returns and cancellations.",
        )
    )
    second = Project.load(tmp_path).compile("sales")
    assert second.digest != first.digest


def test_project_plan_reports_semantic_changes(tmp_path: Path) -> None:
    project = project_fixture(tmp_path)
    candidate = project.compile("sales")

    new_plan = project.plan(candidate)

    assert new_plan.agent == "sales"
    assert new_plan.applied_digest is None
    assert new_plan.changes == ("create agent",)


def test_apply_writes_digest_only_state_and_retains_previous_artifact(
    tmp_path: Path,
) -> None:
    project = project_fixture(tmp_path)
    first = project.compile("sales")
    applied = project.apply(first)

    state_path = tmp_path / ".tabletalk" / "state.json"
    state = json.loads(state_path.read_text())
    assert state == {
        "schema_version": 2,
        "agents": {
            "sales": {
                "artifact_digest": first.digest,
                "eval_receipts": [],
                "previous_artifact_digest": None,
                "applied_at": applied.applied_at,
            }
        },
    }
    assert "artifact" not in state["agents"]["sales"]

    agent_path = tmp_path / "agents" / "sales.yaml"
    agent_path.write_text(
        agent_path.read_text().replace(
            "Answers sales questions.",
            "Answers governed sales and order questions.",
        )
    )
    second = project.compile("sales")
    second_applied = project.apply(second)
    state = json.loads(state_path.read_text())

    assert second.digest != first.digest
    assert second_applied.previous_artifact_digest == first.digest
    assert state["agents"]["sales"]["previous_artifact_digest"] == first.digest
    assert (
        tmp_path / ".tabletalk" / "artifacts" / "sales" / f"{first.digest}.json"
    ).is_file()


def test_missing_required_eval_leaves_applied_state_unchanged(
    tmp_path: Path,
) -> None:
    project = project_fixture(tmp_path)
    source = tmp_path / "agents" / "sales.yaml"
    source.write_text(source.read_text().replace("evals: []", "evals: [required-suite]"))
    candidate = project.compile("sales")
    state_path = tmp_path / ".tabletalk" / "state.json"
    before = state_path.read_bytes() if state_path.exists() else None

    with pytest.raises(TableTalkError) as raised:
        project.apply(candidate)

    assert raised.value.code is ErrorCode.REQUIRED_EVAL_MISSING
    assert (state_path.read_bytes() if state_path.exists() else None) == before


def test_plan_and_apply_many_handle_removed_agents_atomically(
    tmp_path: Path,
) -> None:
    project = project_fixture(tmp_path)
    sales_source = tmp_path / "agents" / "sales.yaml"
    (tmp_path / "agents" / "inventory.yaml").write_text(
        sales_source.read_text()
        .replace("name: sales", "name: inventory")
        .replace("Answers sales questions.", "Answers inventory questions.")
    )
    initial = project.compile()
    project.apply_many(initial, remove_absent=True)

    (tmp_path / "agents" / "inventory.yaml").unlink()
    remaining = Project.load(tmp_path).compile()
    plans = Project.load(tmp_path).plans(remaining, include_removals=True)

    assert [(item.agent, item.changes) for item in plans] == [
        ("inventory", ("delete agent",)),
        ("sales", ()),
    ]
    Project.load(tmp_path).apply_many(remaining, remove_absent=True)
    state = json.loads((tmp_path / ".tabletalk" / "state.json").read_text())
    assert list(state["agents"]) == ["sales"]


def test_multi_agent_apply_leaves_state_unchanged_when_any_gate_is_missing(
    tmp_path: Path,
) -> None:
    project_fixture(tmp_path)
    sales_source = tmp_path / "agents" / "sales.yaml"
    inventory = sales_source.read_text().replace(
        "name: sales",
        "name: inventory",
    )
    inventory = inventory.replace("evals: []", "evals: [inventory-required]")
    (tmp_path / "agents" / "inventory.yaml").write_text(inventory)
    candidates = Project.load(tmp_path).compile()
    state_path = tmp_path / ".tabletalk" / "state.json"

    with pytest.raises(TableTalkError) as raised:
        Project.load(tmp_path).apply_many(candidates, remove_absent=True)

    assert raised.value.code is ErrorCode.REQUIRED_EVAL_MISSING
    assert not state_path.exists()


def test_public_package_exports_product_concepts() -> None:
    import tabletalk

    assert {
        "Agent",
        "AgentDefinition",
        "Answer",
        "CompiledAgent",
        "CompiledArtifact",
        "EvalReport",
        "EvalSuite",
        "Evidence",
        "Interpretation",
        "Plan",
        "Project",
        "QueryAnswer",
        "SemanticPlan",
        "VerificationStatus",
    } <= set(tabletalk.__all__)


def test_focused_cli_compiles_and_applies_agent_resource(tmp_path: Path) -> None:
    project_fixture(tmp_path)
    runner = CliRunner()

    compiled = runner.invoke(
        cli,
        ["compile", "sales", "--project-folder", str(tmp_path)],
    )
    applied = runner.invoke(
        cli,
        [
            "apply",
            "sales",
            "--project-folder",
            str(tmp_path),
            "--auto-approve",
        ],
    )

    assert compiled.exit_code == 0, compiled.output
    assert "sales" in compiled.output
    assert applied.exit_code == 0, applied.output
    assert "sales applied" in applied.output
    state = json.loads((tmp_path / ".tabletalk" / "state.json").read_text())
    assert state["schema_version"] == 2


def test_focused_cli_plan_has_stable_json_and_detailed_exit_code(
    tmp_path: Path,
) -> None:
    project_fixture(tmp_path)
    runner = CliRunner()

    changed = runner.invoke(
        cli,
        [
            "plan",
            "sales",
            "--project-folder",
            str(tmp_path),
            "--format",
            "json",
            "--detailed-exit-code",
        ],
    )

    assert changed.exit_code == 2
    payload = json.loads(changed.output)
    assert payload[0]["agent"] == "sales"
    assert payload[0]["changes"] == ["create agent"]

    assert (
        runner.invoke(
            cli,
            [
                "apply",
                "sales",
                "--project-folder",
                str(tmp_path),
                "--auto-approve",
            ],
        ).exit_code
        == 0
    )
    unchanged = runner.invoke(
        cli,
        [
            "plan",
            "sales",
            "--project-folder",
            str(tmp_path),
            "--detailed-exit-code",
        ],
    )
    assert unchanged.exit_code == 0
    assert "NO-OP" in unchanged.output
