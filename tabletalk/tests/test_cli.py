"""Contract tests for the focused TableTalk CLI."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml
from click.testing import CliRunner

from tabletalk.cli import cli


def _project(tmp_path: Path) -> Path:
    database = tmp_path / "analytics.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE orders (
          id INTEGER PRIMARY KEY,
          order_date DATE NOT NULL,
          status TEXT NOT NULL,
          recognized_revenue NUMERIC NOT NULL
        );
        INSERT INTO orders VALUES (1, '2026-01-05', 'complete', 100);
        """
    )
    connection.close()
    (tmp_path / "tabletalk.yaml").write_text(
        f"""\
connections:
  local:
    type: sqlite
    path: {database}
    read_only: true
llm:
  provider: ollama
  api_key: ollama
  model: gemma4:31b-cloud
agents: agents
evals: evals
"""
    )
    (tmp_path / "agents").mkdir()
    (tmp_path / "evals").mkdir()
    (tmp_path / "agents" / "sales.yaml").write_text(
        """\
kind: Agent
name: sales
description: Answers governed sales questions.
connection: local
relations:
  include: [main.orders]
semantics:
  metrics:
    recognized_revenue:
      expression: main.orders.recognized_revenue
      aggregation: sum
      time_dimension: main.orders.order_date
  time:
    timezone: UTC
  rules:
    - Exclude cancelled orders.
policies:
  read_only: true
  require_evidence: true
  max_rows: 100
  timeout_seconds: 30
evals: []
"""
    )
    return tmp_path


def test_root_help_exposes_only_the_focused_lifecycle() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    for command in (
        "init",
        "connect",
        "discover",
        "compile",
        "plan",
        "eval",
        "apply",
        "ask",
        "serve",
    ):
        assert command in result.output
    for removed in ("schedule", "promote", "rollback", "watch", "query"):
        assert removed not in result.output


def test_init_creates_immediately_compilable_agent_project(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        initialized = runner.invoke(cli, ["init"])
        compiled = runner.invoke(cli, ["compile", "sales", "--check"])

        assert initialized.exit_code == 0, initialized.output
        assert compiled.exit_code == 0, compiled.output
        assert Path("agents/sales.yaml").is_file()
        assert Path("evals/starter.yaml").is_file()
        config = yaml.safe_load(Path("tabletalk.yaml").read_text())
        assert config["llm"]["model"] == "gemma4:31b-cloud"
        assert config["connections"]["local"]["read_only"] is True


def test_compile_json_contains_exact_artifact_digest(tmp_path: Path) -> None:
    project = _project(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "compile",
            "sales",
            "--project-folder",
            str(project),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload[0]["agent"] == "sales"
    assert payload[0]["artifact_digest"] == payload[0]["artifact"]["digest"]


def test_plan_uses_stable_detailed_exit_code(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "plan",
            "sales",
            "--project-folder",
            str(_project(tmp_path)),
            "--format",
            "json",
            "--detailed-exit-code",
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.output)[0]["changes"] == ["create agent"]


def test_apply_requires_confirmation_and_preserves_state_on_cancel(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["apply", "sales", "--project-folder", str(project)],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "state was not changed" in result.output
    assert not (project / ".tabletalk" / "state.json").exists()


def test_apply_auto_approve_writes_digest_only_state(tmp_path: Path) -> None:
    project = _project(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "apply",
            "sales",
            "--project-folder",
            str(project),
            "--auto-approve",
        ],
    )

    assert result.exit_code == 0, result.output
    state = json.loads((project / ".tabletalk" / "state.json").read_text())
    assert "artifact_digest" in state["agents"]["sales"]
    assert "artifact" not in state["agents"]["sales"]


def test_connections_list_test_and_inspect(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runner = CliRunner()

    listed = runner.invoke(
        cli, ["connections", "list", "--project-folder", str(project)]
    )
    tested = runner.invoke(
        cli, ["connections", "test", "local", "--project-folder", str(project)]
    )
    inspected = runner.invoke(
        cli,
        ["connections", "inspect", "local", "--project-folder", str(project)],
    )

    assert listed.exit_code == 0 and "local" in listed.output
    assert tested.exit_code == 0 and "local" in tested.output
    assert inspected.exit_code == 0
    assert json.loads(inspected.output)["visible_relation_count"] == 1


def test_connect_saves_inline_reference_without_resolved_secret(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "connect",
            "--project-folder",
            str(project),
            "--type",
            "snowflake",
            "--no-test",
        ],
        input=(
            "production\n"
            "acme.us-east-1\n"
            "TABLETALK_SERVICE\n"
            "ANALYTICS\n"
            "TABLETALK_WH\n"
            "TABLETALK_READONLY\n"
            "PUBLIC\n"
            "SNOWFLAKE_PASSWORD\n"
        ),
    )

    assert result.exit_code == 0, result.output
    raw = (project / "tabletalk.yaml").read_text()
    assert "${SNOWFLAKE_PASSWORD}" in raw
    assert "resolved secrets" in result.output


def test_discover_can_write_an_explicit_agent_scope(tmp_path: Path) -> None:
    project = _project(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "discover",
            "--project-folder",
            str(project),
            "--connection",
            "local",
            "--relation",
            "main.orders",
            "--write-agent",
            "orders",
        ],
    )

    assert result.exit_code == 0, result.output
    definition = yaml.safe_load((project / "agents" / "orders.yaml").read_text())
    assert definition["kind"] == "Agent"
    assert definition["relations"]["include"] == ["main.orders"]
    assert definition["policies"]["read_only"] is True


def test_agents_list_and_inspect_use_product_concepts(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runner = CliRunner()

    listed = runner.invoke(
        cli, ["agents", "list", "--project-folder", str(project)]
    )
    inspected = runner.invoke(
        cli,
        ["agents", "inspect", "sales", "--project-folder", str(project)],
    )

    assert listed.exit_code == 0 and "sales" in listed.output
    assert inspected.exit_code == 0
    assert json.loads(inspected.output)["source"]["name"] == "sales"


def test_removed_legacy_command_is_rejected() -> None:
    result = CliRunner().invoke(cli, ["schedule"])

    assert result.exit_code == 2
    assert "No such command" in result.output
