"""Strict YAML loading and validation for TableTalk eval suites."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tabletalk.evals.models import EvalCase, EvalSuite


class EvalConfigError(ValueError):
    """Raised when an eval suite does not match the versioned schema."""


_EXPECTED_SECTIONS = {"sql", "result", "safety", "answer", "performance"}
_SQL_FIELDS = {
    "must_reference",
    "must_not_reference",
    "must_reference_columns",
    "forbidden_columns",
    "must_include",
    "must_not_include",
}


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvalConfigError(f"{location} must be a mapping")
    return value


def _string_list(value: Any, location: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvalConfigError(f"{location} must be a list of strings")
    return value


def _validate_environment(environment: dict[str, Any]) -> None:
    provider = environment.get("provider")
    if provider is not None and not isinstance(provider, dict):
        raise EvalConfigError("environment.provider must be a mapping")
    fixture = environment.get("fixture")
    if fixture is not None and (not isinstance(fixture, str) or not fixture.strip()):
        raise EvalConfigError("environment.fixture must be a non-empty path string")
    for field in ("pricing", "metric_weights"):
        value = environment.get(field)
        if value is not None and not isinstance(value, dict):
            raise EvalConfigError(f"environment.{field} must be a mapping")


def _validate_expected(expected: dict[str, Any], location: str) -> None:
    unknown_sections = sorted(set(expected) - _EXPECTED_SECTIONS)
    if unknown_sections:
        raise EvalConfigError(
            f"{location} contains unsupported sections: {', '.join(unknown_sections)}"
        )

    sql = expected.get("sql")
    if sql is not None:
        sql = _mapping(sql, f"{location}.sql")
        unknown_sql = sorted(set(sql) - _SQL_FIELDS - {"max_joins"})
        if unknown_sql:
            raise EvalConfigError(
                f"{location}.sql contains unsupported fields: {', '.join(unknown_sql)}"
            )
        for field in _SQL_FIELDS:
            if field in sql:
                _string_list(sql[field], f"{location}.sql.{field}")
        max_joins = sql.get("max_joins")
        if max_joins is not None and (
            not isinstance(max_joins, int) or isinstance(max_joins, bool) or max_joins < 0
        ):
            raise EvalConfigError(f"{location}.sql.max_joins must be a non-negative integer")

    result = expected.get("result")
    if result is not None:
        result = _mapping(result, f"{location}.result")
        result_type = result.get("type")
        if result_type not in {"scalar", "table"}:
            raise EvalConfigError(f"{location}.result.type must be 'scalar' or 'table'")
        reference_sql = result.get("reference_sql")
        if reference_sql is not None and (
            not isinstance(reference_sql, str) or not reference_sql.strip()
        ):
            raise EvalConfigError(f"{location}.result.reference_sql must be non-empty SQL")
        if result_type == "scalar" and "value" not in result and reference_sql is None:
            raise EvalConfigError(f"{location}.result requires either 'value' or 'reference_sql'")
        if result_type == "table" and "rows" not in result and reference_sql is None:
            raise EvalConfigError(f"{location}.result requires either 'rows' or 'reference_sql'")
        if "columns" in result:
            _string_list(result["columns"], f"{location}.result.columns")
        if "rows" in result and (
            not isinstance(result["rows"], list)
            or not all(isinstance(row, dict) for row in result["rows"])
        ):
            raise EvalConfigError(f"{location}.result.rows must be a list of mappings")
        if "comparison" in result:
            comparison = _mapping(result["comparison"], f"{location}.result.comparison")
            row_order = comparison.get("row_order", "ignore")
            if row_order not in {"ignore", "strict"}:
                raise EvalConfigError(
                    f"{location}.result.comparison.row_order must be 'ignore' or 'strict'"
                )

    safety = expected.get("safety")
    if safety is not None:
        safety = _mapping(safety, f"{location}.safety")
        for field in ("forbidden_tables", "forbidden_columns", "forbidden_values"):
            if field in safety:
                _string_list(safety[field], f"{location}.safety.{field}")

    answer = expected.get("answer")
    if answer is not None:
        answer = _mapping(answer, f"{location}.answer")
        if "rubric" in answer:
            raise EvalConfigError(
                f"{location}.answer.rubric is not supported yet; "
                "use must_include and must_not_include for deterministic scoring"
            )
        for field in ("must_include", "must_not_include"):
            if field in answer:
                _string_list(answer[field], f"{location}.answer.{field}")

    performance = expected.get("performance")
    if performance is not None:
        performance = _mapping(performance, f"{location}.performance")
        for field in ("max_latency_ms", "max_tool_calls", "max_cost_usd"):
            value = performance.get(field)
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0
            ):
                raise EvalConfigError(
                    f"{location}.performance.{field} must be a non-negative number"
                )


def _messages(case: dict[str, Any], location: str) -> list[dict[str, str]]:
    input_config = _mapping(case.get("input", {}), f"{location}.input")
    raw_messages: Any
    if "message" in input_config:
        raw_messages = [{"role": "user", "content": input_config["message"]}]
    else:
        raw_messages = input_config.get("messages")

    if not isinstance(raw_messages, list) or not raw_messages:
        raise EvalConfigError(
            f"{location}.input must define a non-empty 'message' or 'messages' list"
        )

    messages: list[dict[str, str]] = []
    for index, raw_message in enumerate(raw_messages):
        message_location = f"{location}.input.messages[{index}]"
        message = _mapping(raw_message, message_location)
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"}:
            raise EvalConfigError(f"{message_location}.role must be 'user' or 'assistant'")
        if not isinstance(content, str) or not content.strip():
            raise EvalConfigError(f"{message_location}.content must be a non-empty string")
        messages.append({"role": role, "content": content.strip()})

    if not any(message["role"] == "user" for message in messages):
        raise EvalConfigError(f"{location}.input.messages must contain at least one user message")
    return messages


def load_eval_suite(path: str | Path) -> EvalSuite:
    """Load a version 1 eval suite from *path* with actionable validation errors."""
    source_path = Path(path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Eval suite not found: {source_path}")

    try:
        with source_path.open() as file:
            raw = yaml.safe_load(file)
    except yaml.YAMLError as exc:
        raise EvalConfigError(f"Invalid YAML in {source_path}: {exc}") from exc

    config = _mapping(raw, "eval suite")
    version = config.get("version")
    if version != 1:
        raise EvalConfigError(f"Unsupported eval version {version!r}; expected version: 1")

    suite_config = _mapping(config.get("suite"), "suite")
    name = suite_config.get("name")
    if not isinstance(name, str) or not name.strip():
        raise EvalConfigError("suite.name must be a non-empty string")

    environment = config.get("environment", {})
    environment = _mapping(environment, "environment")
    _validate_environment(environment)
    default_manifest = suite_config.get("manifest") or environment.get("manifest")

    raw_cases = config.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise EvalConfigError("cases must be a non-empty list")

    cases: list[EvalCase] = []
    seen_names = set()
    for index, raw_case in enumerate(raw_cases):
        location = f"cases[{index}]"
        case = _mapping(raw_case, location)
        case_name = case.get("name")
        if not isinstance(case_name, str) or not case_name.strip():
            raise EvalConfigError(f"{location}.name must be a non-empty string")
        if case_name in seen_names:
            raise EvalConfigError(f"Duplicate eval case name: {case_name}")
        seen_names.add(case_name)

        expected = _mapping(case.get("expected", {}), f"{location}.expected")
        _validate_expected(expected, f"{location}.expected")
        manifest = case.get("manifest") or case.get("conversation") or default_manifest
        if not isinstance(manifest, str) or not manifest.strip():
            raise EvalConfigError(
                f"{location} must define 'manifest' (the 'conversation' alias is also accepted)"
            )

        tags = case.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise EvalConfigError(f"{location}.tags must be a list of strings")

        cases.append(
            EvalCase(
                name=case_name.strip(),
                description=str(case.get("description", "")).strip(),
                manifest=manifest.strip(),
                messages=_messages(case, location),
                expected=expected,
                tags=tags,
            )
        )

    return EvalSuite(
        version=version,
        name=name.strip(),
        description=str(suite_config.get("description", "")).strip(),
        environment=environment,
        cases=cases,
        source_path=source_path,
    )
