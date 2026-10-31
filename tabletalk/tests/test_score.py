"""tabletalk score: a fixed rubric, golden on the example project, with actionable fixes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tabletalk.cli import cli
from tabletalk.project import Project
from tabletalk.score import Score, badge_json, combine, grade_for, score_agent
from tabletalk.state import LOCK_FILENAME, load_lock
from tabletalk.tests.test_plan import _add_customer_model, _mutate, project_dir

__all__ = ["project_dir"]  # Re-exported fixture.


def _score(root: Path) -> Score:
    project = Project.load(root)
    lock = load_lock(root / LOCK_FILENAME)
    suites = project.eval_suites()
    return combine(
        [
            score_agent(agent, project.manifest, project.semantic_statements(agent), suites, lock)
            for agent in project.agents()
        ]
    )


def _points(score: Score) -> dict[str, float]:
    return {item.name: item.points for item in score.agents[0].criteria}


def test_example_project_scores_b_and_names_what_costs_points(project_dir: Path) -> None:
    score = _score(project_dir)

    assert (score.score, score.grade) == (89, "B")
    assert _points(score) == {
        "Model descriptions": 10,
        "Column descriptions": 18.6,
        "dbt tests": 15,
        "Sensitive data flagged": 10,
        "Semantics resolved": 0,
        "Scope locked": 15,
        "Eval coverage": 20,
    }
    fixes = {item.name: item.fix for item in score.agents[0].criteria if item.fix}
    assert fixes == {
        "Column descriptions": "Describe fct_orders.late_order",
        "Semantics resolved": (
            "Answer with 'tabletalk propose --answer': "
            "Which field defines the date for booked_revenue?"
        ),
    }


def test_an_approved_time_basis_resolves_the_ambiguity(project_dir: Path) -> None:
    knowledge = project_dir / "semantics" / "revenue.yaml"
    text = knowledge.read_text()
    knowledge.write_text(
        text.replace(
            "  recognized_revenue:", "  booked_revenue:\n" + _BOOKED + "  recognized_revenue:"
        )
    )

    assert _points(_score(project_dir))["Semantics resolved"] == 10


_BOOKED = """\
    status: confirmed
    definition: Booked revenue uses the order date.
    time_column: order_date
    approval: apr_example_booked_revenue
    evidence:
      - kind: user_answer
        reference: test
        digest: sha256:0000000000000000000000000000000000000000000000000000000000000000
        locator: /booked_revenue
"""


def test_drift_and_a_missing_lock_lose_the_lock_points(project_dir: Path) -> None:
    _mutate(project_dir, _add_customer_model)
    drifted = _score(project_dir).agents[0].criteria
    (project_dir / LOCK_FILENAME).unlink()
    unlocked = _score(project_dir).agents[0].criteria

    [drift] = [item for item in drifted if item.name == "Scope locked"]
    [missing] = [item for item in unlocked if item.name == "Scope locked"]
    assert (drift.points, drift.detail) == (0, "Scope changed since tabletalk.lock.yaml")
    assert drift.fix == "Review 'tabletalk plan', then run 'tabletalk lock'"
    assert missing.detail == "Not in tabletalk.lock.yaml"


def test_unflagged_sensitive_columns_cost_points_until_flagged(project_dir: Path) -> None:
    _mutate(project_dir, _add_customer_model)
    exposed = _points(_score(project_dir))["Sensitive data flagged"]

    def flag(manifest: dict[str, Any], catalog: dict[str, Any]) -> None:
        for name, column in manifest["nodes"]["model.analytics.dim_customers"]["columns"].items():
            if name in {"customer_id", "email"}:
                column["meta"] = {"sensitive": True}

    _mutate(project_dir, flag)

    assert exposed == 5  # One of two models has unflagged sensitive-looking columns.
    assert _points(_score(project_dir))["Sensitive data flagged"] == 10


def test_missing_evals_name_both_kinds_of_case_to_add(project_dir: Path) -> None:
    for path in (project_dir / "evals").glob("*.yaml"):
        path.unlink()

    [evals] = [
        item for item in _score(project_dir).agents[0].criteria if item.name == "Eval coverage"
    ]

    assert evals.points == 0
    assert evals.fix == (
        "Evals: add a case with reference SQL ('tabletalk eval create'); "
        "add a rejection or ambiguity case"
    )


@pytest.mark.parametrize(
    ("score", "grade"),
    [(100, "A"), (90, "A"), (89, "B"), (80, "B"), (70, "C"), (60, "D"), (59, "F")],
)
def test_grade_boundaries(score: int, grade: str) -> None:
    assert grade_for(score) == grade


def test_cli_writes_a_shields_badge(project_dir: Path, tmp_path: Path) -> None:
    badge = tmp_path / "badges" / "agent-ready.json"

    result = CliRunner().invoke(
        cli, ["score", "--project-folder", str(project_dir), "--badge", str(badge)]
    )

    assert result.exit_code == 0, result.output
    assert "Agent-ready: B · 89/100" in result.output
    assert json.loads(badge.read_text()) == {
        "schemaVersion": 1,
        "label": "agent-ready",
        "message": "B · 89",
        "color": "green",
    }
    assert badge.read_text() == badge_json(_score(project_dir))
