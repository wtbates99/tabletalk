"""Tests for eval configuration, execution, metrics, and CI reports."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import pytest

from tabletalk.evals.loader import EvalConfigError, load_eval_suite
from tabletalk.evals.metrics import compare_results
from tabletalk.evals.reporters import json_report, junit_report
from tabletalk.evals.runner import EvalRunner
from tabletalk.evals.sql_analysis import analyze_sql, identifier_matches
from tabletalk.tests.conftest import MockLLMProvider


def _write_suite(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body))
    return path


class TestEvalLoader:
    def test_loads_message_shorthand_and_conversation_alias(self, tmp_path):
        path = _write_suite(
            tmp_path / "suite.yaml",
            """\
            version: 1
            suite:
              name: sales-regression
            environment:
              manifest: sales.txt
            cases:
              - name: revenue
                conversation: ignored-because-case-wins
                input:
                  message: What is revenue?
                expected:
                  result:
                    type: scalar
                    value: 10
            """,
        )
        suite = load_eval_suite(path)
        assert suite.name == "sales-regression"
        assert suite.cases[0].manifest == "ignored-because-case-wins"
        assert suite.cases[0].messages == [{"role": "user", "content": "What is revenue?"}]

    def test_rejects_duplicate_case_names(self, tmp_path):
        path = _write_suite(
            tmp_path / "invalid.yaml",
            """\
            version: 1
            suite:
              name: duplicate
              manifest: sales.txt
            cases:
              - name: same
                input: {message: one}
                expected: {}
              - name: same
                input: {message: two}
                expected: {}
            """,
        )
        with pytest.raises(EvalConfigError, match="Duplicate eval case"):
            load_eval_suite(path)

    def test_rejects_unknown_version(self, tmp_path):
        path = _write_suite(
            tmp_path / "future.yaml",
            """\
            version: 99
            suite: {name: future}
            cases: []
            """,
        )
        with pytest.raises(EvalConfigError, match="Unsupported eval version"):
            load_eval_suite(path)

    def test_rejects_unsupported_rubric_instead_of_silently_passing(self, tmp_path):
        path = _write_suite(
            tmp_path / "rubric.yaml",
            """\
            version: 1
            suite:
              name: rubric
              manifest: sales.txt
            cases:
              - name: answer
                input: {message: Explain revenue}
                expected:
                  answer:
                    rubric: ["Mentions growth"]
            """,
        )
        with pytest.raises(EvalConfigError, match="rubric is not supported yet"):
            load_eval_suite(path)

    def test_rejects_scalar_without_ground_truth(self, tmp_path):
        path = _write_suite(
            tmp_path / "no-ground-truth.yaml",
            """\
            version: 1
            suite:
              name: missing
              manifest: sales.txt
            cases:
              - name: scalar
                input: {message: Count orders}
                expected:
                  result:
                    type: scalar
            """,
        )
        with pytest.raises(EvalConfigError, match="requires either 'value' or 'reference_sql'"):
            load_eval_suite(path)


class TestResultComparison:
    def test_scalar_numeric_tolerance(self):
        passed, details = compare_results(
            [{"revenue": 100.004}],
            {"type": "scalar", "value": 100.0, "tolerance": 0.01},
        )
        assert passed
        assert details["tolerance"] == 0.01

    def test_numeric_tolerance_is_absolute_not_percentage(self):
        passed, _ = compare_results(
            [{"revenue": 101.0}],
            {"type": "scalar", "value": 100.0, "tolerance": 0.01},
        )
        assert not passed

    def test_table_ignores_order_and_supports_tolerance(self):
        actual = [
            {"region": "west", "revenue": 49.999},
            {"region": "east", "revenue": 100.001},
        ]
        expected = {
            "type": "table",
            "columns": ["region", "revenue"],
            "rows": [
                {"region": "east", "revenue": 100.0},
                {"region": "west", "revenue": 50.0},
            ],
            "comparison": {"row_order": "ignore", "numeric_tolerance": 0.01},
        }
        passed, _ = compare_results(actual, expected)
        assert passed

    def test_reports_missing_expected_row(self):
        passed, details = compare_results(
            [{"region": "east"}],
            {
                "type": "table",
                "rows": [{"region": "west"}],
                "comparison": {"row_order": "ignore"},
            },
        )
        assert not passed
        assert details["reason"] == "expected row was not found"

    def test_reference_comparison_ignores_query_aliases(self):
        passed, details = compare_results(
            [{"customer_region": "east", "total_revenue": 100.0}],
            {
                "type": "table",
                "columns": ["region", "revenue"],
                "comparison": {"row_order": "ignore"},
            },
            reference=[{"region": "east", "revenue": 100.0}],
        )
        assert passed
        assert details["actual_column_mapping"] == {
            "region": "customer_region",
            "revenue": "total_revenue",
        }


class TestSQLAnalysis:
    def test_extracts_qualified_tables_columns_and_joins(self):
        analysis = analyze_sql(
            [
                "SELECT o.id, c.name FROM analytics.orders o "
                "JOIN analytics.customers c ON o.customer_id = c.id"
            ]
        )
        assert "analytics.orders" in analysis.tables
        assert "analytics.customers" in analysis.tables
        assert "o.id" in analysis.columns
        assert analysis.join_count == 1

    def test_unqualified_expectation_matches_qualified_identifier(self):
        assert identifier_matches("analytics.orders", "orders")
        assert not identifier_matches("orders", "analytics.orders")


class TestEvalRunner:
    def test_runs_reference_query_and_deterministic_metrics(self, project_with_manifest, tmp_path):
        suite_path = _write_suite(
            tmp_path / "suite.yaml",
            """\
            version: 1
            suite:
              name: ecommerce-regression
              manifest: orders.txt
            cases:
              - name: total-revenue
                input:
                  message: What is total revenue?
                expected:
                  sql:
                    must_reference: [orders]
                    must_not_reference: [employee_sensitive]
                    max_joins: 0
                  result:
                    type: scalar
                    reference_sql: SELECT SUM(total_amount) FROM orders
                    tolerance: 0.001
                  performance:
                    max_tool_calls: 1
            """,
        )
        llm = MockLLMProvider(
            default_response="SELECT SUM(total_amount) AS total_revenue FROM orders"
        )
        with patch("tabletalk.factories.get_llm_provider", return_value=llm):
            result = EvalRunner(
                load_eval_suite(suite_path),
                project_folder=project_with_manifest,
            ).run()

        assert result.passed
        assert result.score == pytest.approx(1.0)
        assert result.passed_count == 1
        case = result.cases[0]
        assert case.trace.generated_sql == ["SELECT SUM(total_amount) AS total_revenue FROM orders"]
        assert {metric.name for metric in case.metrics} == {
            "sql_execution",
            "result_accuracy",
            "sql_structure",
            "safety",
            "performance",
        }

    def test_emits_case_progress_callbacks(self, project_with_manifest, tmp_path):
        suite_path = _write_suite(
            tmp_path / "callbacks.yaml",
            """\
            version: 1
            suite:
              name: callbacks
              manifest: customers.txt
            cases:
              - name: customer-count
                input: {message: Count customers}
                expected:
                  result:
                    type: scalar
                    reference_sql: SELECT COUNT(*) FROM customers
            """,
        )
        started = []
        completed = []
        llm = MockLLMProvider(default_response="SELECT COUNT(*) FROM customers")
        with patch("tabletalk.factories.get_llm_provider", return_value=llm):
            result = EvalRunner(
                load_eval_suite(suite_path),
                project_folder=project_with_manifest,
            ).run(
                on_case_start=lambda case, index, total: started.append((case.name, index, total)),
                on_case_complete=lambda case, index, total: completed.append(
                    (case.case_name, index, total)
                ),
            )

        assert result.passed
        assert started == [("customer-count", 1, 1)]
        assert completed == [("customer-count", 1, 1)]

    def test_execution_error_is_captured_as_a_failed_gate(self, project_with_manifest, tmp_path):
        suite_path = _write_suite(
            tmp_path / "failure.yaml",
            """\
            version: 1
            suite:
              name: broken-query
              manifest: orders.txt
            cases:
              - name: missing-table
                input: {message: Break this}
                expected: {}
            """,
        )
        llm = MockLLMProvider(default_response="SELECT * FROM no_such_table")
        with patch("tabletalk.factories.get_llm_provider", return_value=llm):
            result = EvalRunner(
                load_eval_suite(suite_path),
                project_folder=project_with_manifest,
            ).run()

        assert not result.passed
        assert result.cases[0].trace.error
        assert result.cases[0].metrics[0].name == "sql_execution"
        assert not result.cases[0].metrics[0].passed


class TestEvalReports:
    def test_json_and_junit_reports(self, project_with_manifest, tmp_path):
        suite_path = _write_suite(
            tmp_path / "reports.yaml",
            """\
            version: 1
            suite:
              name: reports
              manifest: customers.txt
            cases:
              - name: one
                input: {message: Count customers}
                expected:
                  result:
                    type: scalar
                    reference_sql: SELECT COUNT(*) FROM customers
            """,
        )
        llm = MockLLMProvider(default_response="SELECT COUNT(*) FROM customers")
        with patch("tabletalk.factories.get_llm_provider", return_value=llm):
            result = EvalRunner(
                load_eval_suite(suite_path),
                project_folder=project_with_manifest,
            ).run()

        parsed_json = json.loads(json_report(result))
        assert parsed_json["passed_count"] == 1
        parsed_xml = ElementTree.fromstring(junit_report(result))
        assert parsed_xml.attrib["tests"] == "1"
        assert parsed_xml.attrib["failures"] == "0"
