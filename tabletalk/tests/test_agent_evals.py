from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tabletalk.evals import (
    EvalRunner,
    create_eval_receipt,
    load_eval_suite,
    matching_eval_receipt,
    write_eval_receipt,
)
from tabletalk.interfaces import LLMProvider
from tabletalk.project import Project


class EvalModel(LLMProvider):
    model = "gemma4:31b-cloud"
    base_url = "http://localhost:11434/v1"

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def generate_response(self, prompt: str) -> str:
        raise AssertionError("Agent evals must use structured generation.")

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        del messages, json_schema
        self.calls += 1
        if self.calls % 2:
            return {
                "interpretation": {
                    "intent": "Calculate recognized revenue last month",
                    "metrics": ["revenue"],
                    "dimensions": [],
                    "filters": ["status != cancelled"],
                    "start_date": "2026-06-01",
                    "end_date": "2026-07-01",
                    "timezone": "UTC",
                    "assumptions": ["Last month is the previous calendar month."],
                },
                "plan": [
                    {
                        "operation": "aggregate",
                        "relation": "main.orders",
                        "detail": "Sum recognized revenue for the exact date range",
                    }
                ],
                "sql": (
                    "SELECT SUM(net_revenue) AS revenue FROM main.orders "
                    "WHERE order_date >= '2026-06-01' "
                    "AND order_date < '2026-07-01' "
                    "AND status != 'cancelled'"
                ),
                "ambiguity": None,
            }
        return {
            "direct_answer": "Recognized revenue last month was 150.",
            "calculations": [],
            "claims": [
                {
                    "claim": "Recognized revenue last month was 150.",
                    "evidence_ids": ["row-0"],
                    "calculation_ids": [],
                }
            ],
        }


def write_project(tmp_path: Path) -> tuple[Path, Path]:
    database = tmp_path / "development.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE orders (
          id INTEGER PRIMARY KEY,
          net_revenue NUMERIC NOT NULL,
          order_date DATE NOT NULL,
          status TEXT NOT NULL
        );
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
description: Answers recognized revenue questions.
connection: local
relations:
  include: [main.orders]
semantics:
  metrics:
    revenue:
      expression: main.orders.net_revenue
      aggregation: sum
      time_dimension: main.orders.order_date
  time:
    timezone: UTC
    week_start: monday
    default_dimension: main.orders.order_date
  rules:
    - Exclude cancelled orders.
policies:
  read_only: true
  require_evidence: true
evals:
  - sales-regression
"""
    )
    fixture = tmp_path / "evals" / "fixtures"
    fixture.mkdir(parents=True)
    (fixture / "schema.sql").write_text(
        """
        CREATE TABLE orders (
          id INTEGER PRIMARY KEY,
          net_revenue NUMERIC NOT NULL,
          order_date DATE NOT NULL,
          status TEXT NOT NULL
        );
        """
    )
    (fixture / "data.sql").write_text(
        """
        INSERT INTO orders VALUES
          (1, 100, '2026-06-10', 'complete'),
          (2, 50, '2026-06-20', 'complete'),
          (3, 500, '2026-06-21', 'cancelled'),
          (4, 20, '2026-05-20', 'complete');
        """
    )
    suite = tmp_path / "evals" / "sales.yaml"
    suite.write_text(
        """\
kind: EvalSuite
name: sales-regression
agent: sales
fixture:
  type: sqlite
  setup:
    - fixtures/schema.sql
    - fixtures/data.sql
cases:
  - name: recognized-revenue-last-month
    question: What was revenue last month?
    expected_interpretation:
      metric: revenue
      start_date: "2026-06-01"
      end_date: "2026-07-01"
      timezone: UTC
    expect:
      relations:
        required: [main.orders]
      columns:
        required: [net_revenue, order_date, status]
      reference_sql: |
        SELECT SUM(net_revenue) AS revenue
        FROM orders
        WHERE order_date >= '2026-06-01'
          AND order_date < '2026-07-01'
          AND status != 'cancelled'
      result:
        comparison: scalar
        column: revenue
        absolute_tolerance: 0.01
      answer:
        require_supported_claims: true
        require_evidence: true
        required_disclosures:
          - exact_date_range
          - metric_definition
          - source_relation
      budgets:
        max_latency_ms: 10000
        max_rows: 10
"""
    )
    return tmp_path, suite


def test_first_class_eval_runs_trusted_runtime_against_deterministic_fixture(
    tmp_path: Path,
) -> None:
    project, suite_path = write_project(tmp_path)
    model = EvalModel()

    with patch("tabletalk.factories.get_llm_provider", return_value=model):
        suite = load_eval_suite(suite_path)
        result = EvalRunner(suite, project_folder=str(project)).run()

    assert suite.agent == "sales"
    assert result.passed is True
    assert result.passed_count == 1
    trace = result.cases[0].trace
    assert trace.answer is not None
    assert trace.answer["status"] == "verified"
    assert trace.last_result == [{"revenue": 150}]
    metric_names = {metric.name for metric in result.cases[0].metrics}
    assert "interpretation" in metric_names
    assert "answer_quality" in metric_names
    assert result.metadata["artifact_digest"]
    assert model.calls == 2


def test_first_class_eval_receipt_binds_exact_candidate_artifact(
    tmp_path: Path,
) -> None:
    project, suite_path = write_project(tmp_path)

    with patch(
        "tabletalk.factories.get_llm_provider",
        return_value=EvalModel(),
    ):
        suite = load_eval_suite(suite_path)
        result = EvalRunner(suite, project_folder=str(project)).run()
    receipt = create_eval_receipt(result, suite, project)
    write_eval_receipt(receipt, project)
    digest = str(result.metadata["artifact_digest"])

    assert receipt.receipt.artifact_digests == (("sales", digest),)
    assert matching_eval_receipt(
        project,
        "sales-regression",
        "sales",
        digest,
    ) is not None
    assert matching_eval_receipt(
        project,
        "sales-regression",
        "sales",
        "different",
    ) is None
    serialized = json.loads(receipt.to_json())
    assert serialized["receipt"]["runtime"]["model"] == "gemma4:31b-cloud"


def test_project_evaluate_then_apply_gates_exact_artifact(
    tmp_path: Path,
) -> None:
    project_path, _suite_path = write_project(tmp_path)
    project = Project.load(project_path)
    candidate = project.compile("sales")

    with patch(
        "tabletalk.factories.get_llm_provider",
        return_value=EvalModel(),
    ):
        reports = project.evaluate(candidate)
    applied = project.apply(candidate)
    state = json.loads((project_path / ".tabletalk" / "state.json").read_text())

    assert len(reports) == 1
    assert reports[0].passed is True
    assert applied.artifact_digest == candidate.digest
    assert len(applied.eval_receipts) == 1
    assert state["schema_version"] == 2
    assert state["agents"]["sales"]["artifact_digest"] == candidate.digest
    assert "artifact" not in state["agents"]["sales"]
