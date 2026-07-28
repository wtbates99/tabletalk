"""Strict loading for first-class execution-based EvalSuite resources."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tabletalk.evals.models import EvalCase, EvalSuite


class EvalConfigError(ValueError):
    """The EvalSuite source does not match the supported schema."""


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvalConfigError(f"{location} must be a mapping")
    return value


def _strings(value: Any, location: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise EvalConfigError(f"{location} must be a list of non-empty strings")
    return value


def _normalize_result(
    raw: Any,
    reference_sql: Any,
    location: str,
) -> dict[str, Any] | None:
    if raw is None and reference_sql is None:
        return None
    result = _mapping(raw or {}, location)
    comparison = result.get("comparison", "table")
    supported = {
        "scalar",
        "table",
        "ordered_rows",
        "unordered_rows",
        "keyed_rows",
        "approximate_numeric_rows",
        "empty",
        "shape",
    }
    if comparison not in supported:
        raise EvalConfigError(
            f"{location}.comparison must be one of: "
            + ", ".join(sorted(supported))
        )
    normalized: dict[str, Any] = {
        "type": (
            "scalar"
            if comparison == "scalar"
            else "shape"
            if comparison == "shape"
            else "table"
        )
    }
    if reference_sql is not None:
        if not isinstance(reference_sql, str) or not reference_sql.strip():
            raise EvalConfigError(f"{location} reference_sql must be non-empty SQL")
        normalized["reference_sql"] = reference_sql
    for field in ("rows", "value", "row_count"):
        if field in result:
            normalized[field] = result[field]
    if "column" in result:
        normalized["columns"] = [result["column"]]
    elif "columns" in result:
        normalized["columns"] = _strings(
            result["columns"],
            f"{location}.columns",
        )
    if "absolute_tolerance" in result:
        tolerance = result["absolute_tolerance"]
        if (
            not isinstance(tolerance, (int, float))
            or isinstance(tolerance, bool)
            or tolerance < 0
        ):
            raise EvalConfigError(
                f"{location}.absolute_tolerance must be non-negative"
            )
        normalized["tolerance"] = tolerance
    comparison_config: dict[str, Any] = {}
    if comparison == "ordered_rows":
        comparison_config["row_order"] = "strict"
    elif normalized["type"] == "table":
        comparison_config["row_order"] = "ignore"
    if comparison == "keyed_rows":
        keys = result.get("keys") or result.get("key_columns")
        comparison_config["key_columns"] = _strings(
            keys,
            f"{location}.keys",
        )
    if "row_order" in result:
        if result["row_order"] not in {"ignore", "strict"}:
            raise EvalConfigError(
                f"{location}.row_order must be ignore or strict"
            )
        comparison_config["row_order"] = result["row_order"]
    if comparison == "empty":
        normalized["rows"] = []
        normalized.pop("reference_sql", None)
    if comparison_config:
        normalized["comparison"] = comparison_config
    if normalized["type"] == "scalar" and not any(
        field in normalized for field in ("value", "reference_sql")
    ):
        raise EvalConfigError(
            f"{location} scalar requires value or reference_sql"
        )
    if normalized["type"] == "table" and not any(
        field in normalized for field in ("rows", "reference_sql")
    ):
        raise EvalConfigError(
            f"{location} table comparison requires rows or reference_sql"
        )
    if normalized["type"] == "shape" and not any(
        field in normalized for field in ("columns", "row_count")
    ):
        raise EvalConfigError(
            f"{location} shape requires columns or row_count"
        )
    return normalized


def _expected(expect: dict[str, Any], location: str) -> dict[str, Any]:
    allowed = {
        "executes",
        "read_only",
        "relations",
        "columns",
        "joins",
        "reference_sql",
        "result",
        "answer",
        "budgets",
    }
    unknown = sorted(set(expect) - allowed)
    if unknown:
        raise EvalConfigError(
            f"{location} contains unsupported fields: {', '.join(unknown)}"
        )
    sql: dict[str, Any] = {}
    relations = _mapping(expect.get("relations", {}), f"{location}.relations")
    columns = _mapping(expect.get("columns", {}), f"{location}.columns")
    for source, destination in (
        ("required", "must_reference"),
        ("forbidden", "must_not_reference"),
    ):
        if source in relations:
            sql[destination] = _strings(
                relations[source],
                f"{location}.relations.{source}",
            )
    for source, destination in (
        ("required", "must_reference_columns"),
        ("forbidden", "forbidden_columns"),
    ):
        if source in columns:
            sql[destination] = _strings(
                columns[source],
                f"{location}.columns.{source}",
            )
    joins = _mapping(expect.get("joins", {}), f"{location}.joins")
    if "max" in joins:
        if not isinstance(joins["max"], int) or joins["max"] < 0:
            raise EvalConfigError(f"{location}.joins.max must be non-negative")
        sql["max_joins"] = joins["max"]
    expected: dict[str, Any] = {}
    if sql:
        expected["sql"] = sql
    result = _normalize_result(
        expect.get("result"),
        expect.get("reference_sql"),
        f"{location}.result",
    )
    if result:
        expected["result"] = result
    answer = expect.get("answer")
    if answer:
        answer = _mapping(answer, f"{location}.answer")
        for field in ("require_supported_claims", "require_evidence"):
            if field in answer and not isinstance(answer[field], bool):
                raise EvalConfigError(f"{location}.answer.{field} must be boolean")
        if "required_disclosures" in answer:
            _strings(
                answer["required_disclosures"],
                f"{location}.answer.required_disclosures",
            )
        expected["answer"] = answer
    budgets = expect.get("budgets")
    if budgets:
        budgets = _mapping(budgets, f"{location}.budgets")
        for field, value in budgets.items():
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
            ):
                raise EvalConfigError(
                    f"{location}.budgets.{field} must be non-negative"
                )
        expected["performance"] = budgets
    return expected


def load_eval_suite(path: str | Path) -> EvalSuite:
    """Load one strict ``kind: EvalSuite`` resource."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Eval suite not found: {source}")
    try:
        config = yaml.safe_load(source.read_text())
    except yaml.YAMLError as error:
        raise EvalConfigError(f"Invalid YAML in {source}: {error}") from error
    config = _mapping(config, "EvalSuite")
    if config.get("kind") != "EvalSuite":
        raise EvalConfigError("Eval resources require kind: EvalSuite")
    allowed = {
        "kind",
        "name",
        "description",
        "agent",
        "fixture",
        "cases",
        "pricing",
        "metric_weights",
    }
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise EvalConfigError(
            "EvalSuite contains unsupported fields: " + ", ".join(unknown)
        )
    name = config.get("name")
    agent = config.get("agent")
    if not isinstance(name, str) or not name.strip():
        raise EvalConfigError("EvalSuite name must be a non-empty string")
    if not isinstance(agent, str) or not agent.strip():
        raise EvalConfigError("EvalSuite agent must be a non-empty string")
    fixture = _mapping(config.get("fixture", {}), "fixture")
    fixture_type = fixture.get("type")
    if fixture_type not in {None, "sqlite", "duckdb"}:
        raise EvalConfigError("fixture.type must be sqlite or duckdb")
    setup = fixture.get("setup") or []
    if not isinstance(setup, list) or not all(
        isinstance(item, str) and item for item in setup
    ):
        raise EvalConfigError("fixture.setup must be a list of SQL paths")
    environment: dict[str, Any] = {
        "fixture_type": fixture_type,
        "fixture_setup": setup,
    }
    if "database" in fixture:
        environment["fixture"] = fixture["database"]
    for field in ("pricing", "metric_weights"):
        value = config.get(field)
        if value is not None:
            environment[field] = _mapping(value, field)
    raw_cases = config.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise EvalConfigError("cases must be a non-empty list")
    cases = []
    names: set[str] = set()
    for index, raw in enumerate(raw_cases):
        location = f"cases[{index}]"
        case = _mapping(raw, location)
        allowed_case = {
            "name",
            "description",
            "question",
            "tags",
            "expected_interpretation",
            "expect",
        }
        unknown_case = sorted(set(case) - allowed_case)
        if unknown_case:
            raise EvalConfigError(
                f"{location} contains unsupported fields: "
                + ", ".join(unknown_case)
            )
        case_name = case.get("name")
        question = case.get("question")
        if not isinstance(case_name, str) or not case_name.strip():
            raise EvalConfigError(f"{location}.name must be a non-empty string")
        if case_name in names:
            raise EvalConfigError(f"Duplicate eval case name: {case_name}")
        names.add(case_name)
        if not isinstance(question, str) or not question.strip():
            raise EvalConfigError(
                f"{location}.question must be a non-empty string"
            )
        tags = case.get("tags") or []
        _strings(tags, f"{location}.tags")
        expected_interpretation = _mapping(
            case.get("expected_interpretation", {}),
            f"{location}.expected_interpretation",
        )
        expect = _mapping(case.get("expect", {}), f"{location}.expect")
        cases.append(
            EvalCase(
                name=case_name.strip(),
                description=str(case.get("description") or "").strip(),
                messages=[{"role": "user", "content": question.strip()}],
                expected=_expected(expect, f"{location}.expect"),
                tags=tags,
                expected_interpretation=expected_interpretation,
            )
        )
    return EvalSuite(
        version=2,
        name=name.strip(),
        description=str(config.get("description") or "").strip(),
        environment=environment,
        cases=cases,
        source_path=source,
        agent=agent.strip(),
    )
