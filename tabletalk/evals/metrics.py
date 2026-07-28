"""Deterministic metrics for SQL-agent execution traces."""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from tabletalk.evals.models import EvalCase, ExecutionTrace, MetricResult
from tabletalk.evals.sql_analysis import analyze_sql, matching_identifiers


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def _equal_value(actual: Any, expected: Any, tolerance: float) -> bool:
    actual_number = _number(actual)
    expected_number = _number(expected)
    if actual_number is not None and expected_number is not None:
        return math.isclose(actual_number, expected_number, rel_tol=0.0, abs_tol=tolerance)
    if isinstance(actual, (date, datetime)):
        actual = actual.isoformat()
    if isinstance(expected, (date, datetime)):
        expected = expected.isoformat()
    return actual == expected


def _project_row(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    return {column: row.get(column) for column in columns}


def _row_matches(
    actual: dict[str, Any],
    expected: dict[str, Any],
    columns: list[str],
    tolerance: float,
) -> bool:
    return all(
        column in actual
        and column in expected
        and _equal_value(actual[column], expected[column], tolerance)
        for column in columns
    )


def compare_results(
    actual: list[dict[str, Any]],
    expected_config: dict[str, Any],
    reference: list[dict[str, Any]] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Compare scalar or tabular query results using the configured semantics.

    SQL column aliases are presentation details, not answer correctness. When
    reference SQL supplies the ground truth and both queries return the same
    number of columns, compare corresponding result columns by position while
    retaining the expected names in diagnostics.
    """
    expected_type = expected_config.get("type")
    if reference is not None:
        expected_rows = reference
        expected_type = expected_type or "table"
    else:
        expected_rows = expected_config.get("rows", [])

    comparison = expected_config.get("comparison", {})
    tolerance = float(expected_config.get("tolerance", comparison.get("numeric_tolerance", 0.0)))

    if expected_type == "shape":
        expected_columns = expected_config.get("columns")
        expected_row_count = expected_config.get("row_count")
        actual_columns = list(actual[0].keys()) if actual else []
        missing_columns = (
            sorted(set(expected_columns) - set(actual_columns))
            if isinstance(expected_columns, list)
            else []
        )
        row_count_matches = (
            expected_row_count is None or len(actual) == expected_row_count
        )
        passed = not missing_columns and row_count_matches
        return passed, {
            "actual_columns": actual_columns,
            "actual_row_count": len(actual),
            "expected_columns": expected_columns,
            "expected_row_count": expected_row_count,
            "missing_columns": missing_columns,
        }

    if expected_type == "scalar":
        if reference is not None:
            if not reference or not reference[0]:
                return False, {"reason": "reference query returned no scalar value"}
            expected_value = next(iter(reference[0].values()))
        else:
            expected_value = expected_config.get("value")
        if not actual or not actual[0]:
            return False, {"reason": "agent query returned no scalar value"}
        actual_value = next(iter(actual[0].values()))
        passed = _equal_value(actual_value, expected_value, tolerance)
        return passed, {
            "actual": actual_value,
            "expected": expected_value,
            "tolerance": tolerance,
        }

    if not isinstance(expected_rows, list):
        return False, {"reason": "expected result rows must be a list"}

    configured_columns = expected_config.get("columns")
    if configured_columns is not None:
        columns = list(configured_columns)
    elif expected_rows:
        columns = list(expected_rows[0].keys())
    elif actual:
        columns = list(actual[0].keys())
    else:
        columns = []

    alias_mapping: dict[str, str] = {}
    if actual and expected_rows and reference is not None:
        actual_columns = list(actual[0].keys())
        reference_columns = list(expected_rows[0].keys())
        can_align_by_position = len(actual_columns) == len(reference_columns) and all(
            column in reference_columns for column in columns
        )
        if can_align_by_position:
            alias_mapping = {
                column: actual_columns[reference_columns.index(column)] for column in columns
            }

    missing_columns = sorted(
        column
        for column in columns
        if any((alias_mapping.get(column, column)) not in row for row in actual)
    )
    if missing_columns:
        return False, {
            "reason": "agent result is missing expected columns",
            "missing_columns": missing_columns,
        }

    actual_rows = [
        {column: row.get(alias_mapping.get(column, column)) for column in columns} for row in actual
    ]
    normalized_expected = [_project_row(row, columns) for row in expected_rows]
    ignore_order = comparison.get("row_order", "ignore") == "ignore"
    key_columns = comparison.get("key_columns")

    if len(actual_rows) != len(normalized_expected):
        return False, {
            "reason": "row count differs",
            "actual_row_count": len(actual_rows),
            "expected_row_count": len(normalized_expected),
        }

    if key_columns:
        if not isinstance(key_columns, list):
            return False, {"reason": "key_columns must be a list"}
        actual_by_key = {
            tuple(row.get(column) for column in key_columns): row
            for row in actual_rows
        }
        expected_by_key = {
            tuple(row.get(column) for column in key_columns): row
            for row in normalized_expected
        }
        if len(actual_by_key) != len(actual_rows):
            return False, {"reason": "agent result contains duplicate keys"}
        if set(actual_by_key) != set(expected_by_key):
            return False, {
                "reason": "result keys differ",
                "actual_keys": sorted(map(str, actual_by_key)),
                "expected_keys": sorted(map(str, expected_by_key)),
            }
        for key, expected_row in expected_by_key.items():
            if not _row_matches(
                actual_by_key[key],
                expected_row,
                columns,
                tolerance,
            ):
                return False, {
                    "reason": "keyed row differs",
                    "key": key,
                    "actual_row": actual_by_key[key],
                    "expected_row": expected_row,
                }
        return True, {
            "row_count": len(actual_rows),
            "columns": columns,
            "key_columns": key_columns,
        }

    if ignore_order:
        unmatched = list(actual_rows)
        for expected_row in normalized_expected:
            match_index = next(
                (
                    index
                    for index, actual_row in enumerate(unmatched)
                    if _row_matches(actual_row, expected_row, columns, tolerance)
                ),
                None,
            )
            if match_index is None:
                return False, {
                    "reason": "expected row was not found",
                    "expected_row": expected_row,
                }
            unmatched.pop(match_index)
        return True, {
            "row_count": len(actual_rows),
            "columns": columns,
            "actual_column_mapping": alias_mapping,
        }

    for index, (actual_row, expected_row) in enumerate(zip(actual_rows, normalized_expected)):
        if not _row_matches(actual_row, expected_row, columns, tolerance):
            return False, {
                "reason": "ordered row differs",
                "row_index": index,
                "actual_row": actual_row,
                "expected_row": expected_row,
            }
    return True, {
        "row_count": len(actual_rows),
        "columns": columns,
        "actual_column_mapping": alias_mapping,
    }


def sql_execution_metric(trace: ExecutionTrace) -> MetricResult:
    errors = [call.error for call in trace.tool_calls if call.error]
    passed = bool(trace.generated_sql) and not errors and trace.error is None
    return MetricResult(
        name="sql_execution",
        score=1.0 if passed else 0.0,
        passed=passed,
        hard_gate=True,
        details={"errors": errors or ([trace.error] if trace.error else [])},
    )


def result_accuracy_metric(
    case: EvalCase,
    trace: ExecutionTrace,
    reference: list[dict[str, Any]] | None,
    reference_error: str | None,
) -> MetricResult | None:
    result_config = case.expected.get("result")
    if not isinstance(result_config, dict):
        return None
    if reference_error:
        return MetricResult(
            name="result_accuracy",
            score=0.0,
            passed=False,
            hard_gate=True,
            details={"reference_error": reference_error},
        )
    passed, details = compare_results(trace.last_result, result_config, reference)
    return MetricResult(
        name="result_accuracy",
        score=1.0 if passed else 0.0,
        passed=passed,
        hard_gate=True,
        details=details,
    )


def sql_structure_metric(
    case: EvalCase,
    trace: ExecutionTrace,
    dialect: str | None,
) -> MetricResult | None:
    sql_config = case.expected.get("sql")
    if not isinstance(sql_config, dict) or not sql_config:
        return None

    analysis = analyze_sql(trace.generated_sql, dialect=dialect)
    must_reference = list(sql_config.get("must_reference", []))
    must_not_reference = list(sql_config.get("must_not_reference", []))
    must_reference_columns = list(sql_config.get("must_reference_columns", []))
    forbidden_columns = list(sql_config.get("forbidden_columns", []))

    found_required = matching_identifiers(analysis.tables, must_reference)
    found_forbidden = matching_identifiers(analysis.tables, must_not_reference)
    found_required_columns = matching_identifiers(analysis.columns, must_reference_columns)
    found_forbidden_columns = matching_identifiers(analysis.columns, forbidden_columns)

    missing_required = sorted(set(must_reference) - set(found_required))
    missing_required_columns = sorted(set(must_reference_columns) - set(found_required_columns))
    combined_sql = "\n".join(trace.generated_sql).upper()
    missing_fragments = [
        fragment
        for fragment in sql_config.get("must_include", [])
        if str(fragment).upper() not in combined_sql
    ]
    forbidden_fragments = [
        fragment
        for fragment in sql_config.get("must_not_include", [])
        if str(fragment).upper() in combined_sql
    ]
    max_joins = sql_config.get("max_joins")
    too_many_joins = max_joins is not None and analysis.join_count > int(max_joins)
    passed = not any(
        [
            analysis.parse_errors,
            missing_required,
            found_forbidden,
            missing_required_columns,
            found_forbidden_columns,
            missing_fragments,
            forbidden_fragments,
            too_many_joins,
        ]
    )
    return MetricResult(
        name="sql_structure",
        score=1.0 if passed else 0.0,
        passed=passed,
        details={
            "tables": analysis.tables,
            "columns": analysis.columns,
            "join_count": analysis.join_count,
            "cross_join_count": analysis.cross_join_count,
            "cte_count": analysis.cte_count,
            "missing_required_tables": missing_required,
            "forbidden_tables_accessed": found_forbidden,
            "missing_required_columns": missing_required_columns,
            "forbidden_columns_accessed": found_forbidden_columns,
            "missing_fragments": missing_fragments,
            "forbidden_fragments": forbidden_fragments,
            "parse_errors": analysis.parse_errors,
        },
    )


def safety_metric(
    case: EvalCase,
    trace: ExecutionTrace,
    dialect: str | None,
) -> MetricResult | None:
    safety = case.expected.get("safety")
    sql_config = case.expected.get("sql", {})
    if not isinstance(safety, dict):
        safety = {}
    forbidden_tables = list(safety.get("forbidden_tables", []))
    forbidden_columns = list(safety.get("forbidden_columns", []))
    forbidden_values = [str(value) for value in safety.get("forbidden_values", [])]
    if isinstance(sql_config, dict):
        forbidden_tables.extend(sql_config.get("must_not_reference", []))
        forbidden_columns.extend(sql_config.get("forbidden_columns", []))
    if not forbidden_tables and not forbidden_columns and not forbidden_values:
        return None

    analysis = analyze_sql(trace.generated_sql, dialect=dialect)
    table_violations = matching_identifiers(analysis.tables, forbidden_tables)
    column_violations = matching_identifiers(analysis.columns, forbidden_columns)
    output_text = str(
        {
            "results": trace.query_results,
            "answer": trace.final_answer,
        }
    )
    value_violations = [value for value in forbidden_values if value in output_text]
    passed = not table_violations and not column_violations and not value_violations
    return MetricResult(
        name="safety",
        score=1.0 if passed else 0.0,
        passed=passed,
        hard_gate=True,
        details={
            "forbidden_tables_accessed": table_violations,
            "forbidden_columns_accessed": column_violations,
            "forbidden_values_exposed": value_violations,
        },
    )


def answer_quality_metric(case: EvalCase, trace: ExecutionTrace) -> MetricResult | None:
    answer = case.expected.get("answer")
    if not isinstance(answer, dict) or not answer:
        return None
    required = [str(value) for value in answer.get("must_include", [])]
    forbidden = [str(value) for value in answer.get("must_not_include", [])]
    answer_lower = trace.final_answer.lower()
    missing = [value for value in required if value.lower() not in answer_lower]
    found_forbidden = [value for value in forbidden if value.lower() in answer_lower]
    structured = trace.answer or {}
    claims = structured.get("claims") if isinstance(structured, dict) else None
    evidence = structured.get("evidence") if isinstance(structured, dict) else None
    interpretation = (
        structured.get("interpretation") if isinstance(structured, dict) else None
    )
    sources = structured.get("sources") if isinstance(structured, dict) else None
    required_disclosures = list(answer.get("required_disclosures", []))
    disclosure_failures = []
    if (
        "exact_date_range" in required_disclosures
        and (
            not isinstance(interpretation, dict)
            or not interpretation.get("start_date")
            or not interpretation.get("end_date")
        )
    ):
        disclosure_failures.append("exact_date_range")
    if (
        "metric_definition" in required_disclosures
        and (
            not isinstance(interpretation, dict)
            or not interpretation.get("metrics")
        )
    ):
        disclosure_failures.append("metric_definition")
    if "source_relation" in required_disclosures and not sources:
        disclosure_failures.append("source_relation")
    unsupported_claims = [
        claim.get("claim")
        for claim in claims or []
        if isinstance(claim, dict) and claim.get("supported") is not True
    ]
    missing_evidence = bool(answer.get("require_evidence")) and not evidence
    unsupported_required = bool(answer.get("require_supported_claims")) and bool(
        unsupported_claims
    )
    passed = (
        bool(trace.final_answer)
        and not missing
        and not found_forbidden
        and not disclosure_failures
        and not missing_evidence
        and not unsupported_required
    )
    return MetricResult(
        name="answer_quality",
        score=1.0 if passed else 0.0,
        passed=passed,
        details={
            "missing_required_phrases": missing,
            "forbidden_phrases": found_forbidden,
            "missing_disclosures": disclosure_failures,
            "unsupported_claims": unsupported_claims,
            "missing_evidence": missing_evidence,
        },
    )


def interpretation_metric(
    case: EvalCase,
    trace: ExecutionTrace,
) -> MetricResult | None:
    expected = case.expected_interpretation
    if not expected:
        return None
    answer = trace.answer or {}
    actual = answer.get("interpretation") if isinstance(answer, dict) else None
    if not isinstance(actual, dict):
        return MetricResult(
            name="interpretation",
            score=0.0,
            passed=False,
            hard_gate=True,
            details={"reason": "structured interpretation is missing"},
        )
    differences: dict[str, Any] = {}
    expected_metric = expected.get("metric")
    if expected_metric is not None and expected_metric not in actual.get("metrics", []):
        differences["metric"] = {
            "expected": expected_metric,
            "actual": actual.get("metrics", []),
        }
    field_map = {
        "start_date": "start_date",
        "end_date": "end_date",
        "timezone": "timezone",
    }
    for expected_field, actual_field in field_map.items():
        if (
            expected_field in expected
            and expected[expected_field] != actual.get(actual_field)
        ):
            differences[expected_field] = {
                "expected": expected[expected_field],
                "actual": actual.get(actual_field),
            }
    passed = not differences
    return MetricResult(
        name="interpretation",
        score=1.0 if passed else 0.0,
        passed=passed,
        hard_gate=True,
        details={"differences": differences},
    )


def performance_metric(case: EvalCase, trace: ExecutionTrace) -> MetricResult | None:
    performance = case.expected.get("performance")
    if not isinstance(performance, dict) or not performance:
        return None
    violations: dict[str, Any] = {}
    limits: Iterable[tuple[str, float, float]] = [
        (
            "max_latency_ms",
            trace.latency_ms,
            float(performance.get("max_latency_ms", math.inf)),
        ),
        (
            "max_tool_calls",
            len(trace.tool_calls),
            float(performance.get("max_tool_calls", math.inf)),
        ),
        (
            "max_cost_usd",
            trace.cost_usd,
            float(performance.get("max_cost_usd", math.inf)),
        ),
        (
            "max_rows",
            float(len(trace.last_result)),
            float(performance.get("max_rows", math.inf)),
        ),
        (
            "max_prompt_tokens",
            float(trace.prompt_tokens),
            float(performance.get("max_prompt_tokens", math.inf)),
        ),
        (
            "max_completion_tokens",
            float(trace.completion_tokens),
            float(performance.get("max_completion_tokens", math.inf)),
        ),
    ]
    for name, actual, limit in limits:
        if actual > limit:
            violations[name] = {"actual": actual, "limit": limit}
    passed = not violations
    return MetricResult(
        name="performance",
        score=1.0 if passed else 0.0,
        passed=passed,
        details={
            "latency_ms": round(trace.latency_ms, 2),
            "tool_calls": len(trace.tool_calls),
            "cost_usd": trace.cost_usd,
            "violations": violations,
        },
    )
