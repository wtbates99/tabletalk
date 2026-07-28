from __future__ import annotations

from pathlib import Path

import pytest

from tabletalk.evals.loader import EvalConfigError, load_eval_suite
from tabletalk.evals.metrics import compare_results
from tabletalk.evals.sql_analysis import analyze_sql, identifier_matches


def _suite(path: Path, comparison: str = "scalar") -> Path:
    path.write_text(
        f"""\
kind: EvalSuite
name: sales-regression
agent: sales
fixture:
  type: sqlite
  setup: [fixtures/schema.sql]
cases:
  - name: revenue
    question: What was revenue?
    expect:
      result:
        comparison: {comparison}
        value: 100
"""
    )
    return path


def test_loads_first_class_eval_suite(tmp_path: Path) -> None:
    suite = load_eval_suite(_suite(tmp_path / "suite.yaml"))

    assert suite.version == 2
    assert suite.agent == "sales"
    assert suite.cases[0].messages == [
        {"role": "user", "content": "What was revenue?"}
    ]


def test_rejects_duplicate_case_names(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(
        """\
kind: EvalSuite
name: duplicate
agent: sales
cases:
  - {name: same, question: one}
  - {name: same, question: two}
"""
    )

    with pytest.raises(EvalConfigError, match="Duplicate eval case"):
        load_eval_suite(path)


def test_rejects_legacy_eval_schema(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text("version: 1\nsuite: {name: old}\ncases: []\n")

    with pytest.raises(EvalConfigError, match="kind: EvalSuite"):
        load_eval_suite(path)


def test_scalar_numeric_tolerance() -> None:
    passed, details = compare_results(
        [{"revenue": 100.004}],
        {"type": "scalar", "value": 100.0, "tolerance": 0.01},
    )

    assert passed
    assert details["tolerance"] == 0.01


def test_numeric_tolerance_is_absolute_not_percentage() -> None:
    passed, _ = compare_results(
        [{"revenue": 101.0}],
        {"type": "scalar", "value": 100.0, "tolerance": 0.01},
    )

    assert not passed


def test_table_ignores_order_and_supports_tolerance() -> None:
    passed, _ = compare_results(
        [
            {"region": "west", "revenue": 49.999},
            {"region": "east", "revenue": 100.001},
        ],
        {
            "type": "table",
            "columns": ["region", "revenue"],
            "rows": [
                {"region": "east", "revenue": 100.0},
                {"region": "west", "revenue": 50.0},
            ],
            "comparison": {
                "row_order": "ignore",
                "numeric_tolerance": 0.01,
            },
        },
    )

    assert passed


def test_ordered_rows_detect_position_changes() -> None:
    passed, details = compare_results(
        [{"region": "west"}, {"region": "east"}],
        {
            "type": "table",
            "rows": [{"region": "east"}, {"region": "west"}],
            "comparison": {"row_order": "strict"},
        },
    )

    assert not passed
    assert details["reason"] == "ordered row differs"


def test_keyed_rows_detect_duplicate_keys() -> None:
    passed, details = compare_results(
        [
            {"region": "east", "revenue": 100},
            {"region": "east", "revenue": 50},
        ],
        {
            "type": "table",
            "rows": [
                {"region": "east", "revenue": 100},
                {"region": "west", "revenue": 50},
            ],
            "comparison": {
                "row_order": "ignore",
                "key_columns": ["region"],
            },
        },
    )

    assert not passed
    assert details["reason"] == "agent result contains duplicate keys"


def test_result_shape_checks_columns_and_row_count() -> None:
    passed, details = compare_results(
        [{"region": "east", "revenue": 100}],
        {
            "type": "shape",
            "columns": ["region", "revenue"],
            "row_count": 1,
        },
    )

    assert passed
    assert details["actual_row_count"] == 1


def test_reference_comparison_ignores_query_aliases() -> None:
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


def test_sql_analysis_extracts_qualified_sources_and_joins() -> None:
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


def test_identifier_matching_does_not_expand_scope() -> None:
    assert identifier_matches("analytics.orders", "orders")
    assert not identifier_matches("orders", "analytics.orders")
