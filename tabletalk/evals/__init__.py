"""Execution-based eval resources using the exact live Runtime.answer path."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from tabletalk.manifest import Manifest
from tabletalk.runtime import RejectionError, Runtime
from tabletalk.traces import Trace, Verification
from tabletalk.validation import SQLValidationError, validate_sql


class EvalError(ValueError):
    pass


@dataclass(frozen=True)
class ResultExpectation:
    comparison: str = "unordered"
    tolerance: float = 0.0
    value: Any = None
    rows: tuple[dict[str, Any], ...] | None = None
    keys: tuple[str, ...] = ()
    row_count: int | None = None
    columns: tuple[str, ...] = ()
    value_set: bool = False
    allow_extra_columns: bool = False

    def __post_init__(self) -> None:
        if self.value is not None and not self.value_set:
            object.__setattr__(self, "value_set", True)
        if self.comparison not in {
            "scalar",
            "ordered",
            "ordered_values",
            "unordered",
            "keyed",
        }:
            raise EvalError(f"Unsupported result comparison '{self.comparison}'")
        if self.tolerance < 0 or not math.isfinite(self.tolerance):
            raise EvalError("Result tolerance must be a finite non-negative number")
        if self.row_count is not None and (
            not isinstance(self.row_count, int)
            or isinstance(self.row_count, bool)
            or self.row_count < 0
        ):
            raise EvalError("result.row_count must be a non-negative integer")
        if self.comparison == "keyed" and not self.keys:
            raise EvalError("keyed result comparison requires keys")


@dataclass(frozen=True)
class EvalCase:
    name: str
    question: str
    reference_sql: str | None = None
    result: ResultExpectation = ResultExpectation()
    required_models: tuple[str, ...] = ()
    forbidden_models: tuple[str, ...] = ()
    required_columns: tuple[str, ...] = ()
    forbidden_columns: tuple[str, ...] = ()
    expected_outcome: str = "answer"

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.question.strip():
            raise EvalError("Every eval case requires a non-empty name and question")
        if self.expected_outcome not in {"answer", "ambiguity", "rejection"}:
            raise EvalError("expect.outcome must be one of: answer, ambiguity, rejection")
        if self.reference_sql and (self.result.rows is not None or self.result.value_set):
            raise EvalError("Use reference_sql or a literal expected result, not both")

    @property
    def verifies_result(self) -> bool:
        return bool(self.reference_sql) or self.result.rows is not None or self.result.value_set


@dataclass(frozen=True)
class EvalSuite:
    name: str
    agent: str
    cases: tuple[EvalCase, ...]
    source_path: Path | None = None
    description: str = ""
    kind: str = "regression"
    trials: int = 1

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.agent.strip():
            raise EvalError("Eval suite requires non-empty name and agent fields")
        if not self.cases:
            raise EvalError("Eval suite must contain at least one case")
        if self.kind not in {"regression", "capability"}:
            raise EvalError("Eval suite kind must be regression or capability")
        if (
            not isinstance(self.trials, int)
            or isinstance(self.trials, bool)
            or not 1 <= self.trials <= 20
        ):
            raise EvalError("Eval suite trials must be an integer from 1 through 20")
        names = [case.name for case in self.cases]
        if len(names) != len(set(names)):
            raise EvalError("Eval case names must be unique within a suite")

    @property
    def digest(self) -> str:
        raw = self.source_path.read_bytes() if self.source_path else repr(self).encode()
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class CaseResult:
    name: str
    passed: bool
    checks: tuple[Verification, ...]
    trace: Trace | None = None
    error: str | None = None


@dataclass(frozen=True)
class SuiteResult:
    name: str
    agent: str
    cases: tuple[CaseResult, ...]
    suite_digest: str
    trial: int = 1
    trials: int = 1

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "agent": self.agent,
            "passed": self.passed,
            "suite_digest": self.suite_digest,
            "trial": self.trial,
            "trials": self.trials,
            "cases": [
                {
                    "name": case.name,
                    "passed": case.passed,
                    "error": case.error,
                    "checks": [vars(check) for check in case.checks],
                    "trace": case.trace.to_dict() if case.trace else None,
                }
                for case in self.cases
            ],
        }


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvalError(f"{field_name} must be a list of strings")
    return tuple(value)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise EvalError(f"{field_name} must be a mapping")
    return value


def load_eval_suite(path: str | Path) -> EvalSuite:
    source = Path(path).resolve()
    try:
        payload = yaml.safe_load(source.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise EvalError(f"Could not load eval suite {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvalError("Eval suite must be a YAML mapping")
    if "metrics" in payload or "fixtures" in payload:
        raise EvalError(f"Eval suite {source.name} uses the removed judge/fixture format")
    name = payload.get("name")
    agent = payload.get("agent")
    if not isinstance(name, str) or not isinstance(agent, str):
        raise EvalError("Eval suite requires string name and agent fields")
    cases: list[EvalCase] = []
    for index, raw in enumerate(payload.get("cases") or ()):
        if not isinstance(raw, dict):
            raise EvalError(f"cases[{index}] must be a mapping")
        expect = _mapping(raw.get("expect"), f"cases[{index}].expect")
        result_raw = _mapping(expect.get("result"), f"cases[{index}].expect.result")
        models = _mapping(expect.get("models"), f"cases[{index}].expect.models")
        columns = _mapping(expect.get("columns"), f"cases[{index}].expect.columns")
        literal_rows = result_raw.get("rows") if "rows" in result_raw else None
        if literal_rows is not None and (
            not isinstance(literal_rows, list)
            or not all(isinstance(row, dict) for row in literal_rows)
        ):
            raise EvalError(f"cases[{index}].expect.result.rows must be a list of mappings")
        cases.append(
            EvalCase(
                name=str(raw.get("name") or f"case-{index + 1}"),
                question=str(raw.get("question") or ""),
                reference_sql=str(expect["reference_sql"]) if expect.get("reference_sql") else None,
                result=ResultExpectation(
                    comparison=str(result_raw.get("comparison") or "unordered"),
                    tolerance=float(result_raw.get("tolerance") or 0),
                    value=result_raw.get("value"),
                    value_set="value" in result_raw,
                    rows=(
                        tuple(dict(row) for row in literal_rows)
                        if literal_rows is not None
                        else None
                    ),
                    keys=_strings(result_raw.get("keys"), "result.keys"),
                    row_count=result_raw.get("row_count"),
                    columns=_strings(result_raw.get("columns"), "result.columns"),
                    allow_extra_columns=bool(result_raw.get("allow_extra_columns", False)),
                ),
                required_models=_strings(models.get("required"), "models.required"),
                forbidden_models=_strings(models.get("forbidden"), "models.forbidden"),
                required_columns=_strings(columns.get("required"), "columns.required"),
                forbidden_columns=_strings(columns.get("forbidden"), "columns.forbidden"),
                expected_outcome=str(expect.get("outcome") or "answer"),
            )
        )
    description = payload.get("description") or ""
    kind = payload.get("kind") or "regression"
    trials = payload.get("trials", 1)
    if not isinstance(description, str) or not isinstance(kind, str):
        raise EvalError("Eval suite description and kind must be strings")
    return EvalSuite(
        name,
        agent,
        tuple(cases),
        source,
        description.strip(),
        kind.strip(),
        trials,
    )


def render_reference_sql(sql: str, manifest: Manifest) -> str:
    pattern = re.compile(r"\{\{\s*ref\(['\"]([^'\"]+)['\"]\)\s*\}\}")

    def replace_ref(match: re.Match[str]) -> str:
        nodes = tuple(node for node in manifest.queryable_nodes if node.name == match.group(1))
        if len(nodes) != 1:
            raise EvalError(f"ref('{match.group(1)}') is ambiguous")
        node = nodes[0]
        return node.relation_name or f"{node.schema}.{node.alias}".strip(".")

    if re.search(r"\{\{", pattern.sub("", sql)):
        raise EvalError("Only dbt ref() is supported in reference_sql")
    return pattern.sub(replace_ref, sql)


class EvalRunner:
    def __init__(self, suite: EvalSuite, runtime: Runtime) -> None:
        self.suite = suite
        self.runtime = runtime

    def run(self, case_name: str | None = None) -> SuiteResult:
        cases = [case for case in self.suite.cases if case_name is None or case.name == case_name]
        if not cases:
            raise EvalError(f"Eval case '{case_name}' was not found")
        results = tuple(self._run_case(case) for case in cases)
        return SuiteResult(self.suite.name, self.suite.agent, results, self.suite.digest)

    def _run_case(self, case: EvalCase) -> CaseResult:
        try:
            trace = self.runtime.answer(case.question)
        except Exception as exc:
            expected_exception = (
                case.expected_outcome == "ambiguity" and isinstance(exc, RejectionError)
            ) or (
                case.expected_outcome == "rejection"
                and isinstance(exc, (RejectionError, SQLValidationError))
            )
            check = Verification("expected_outcome", expected_exception, str(exc))
            return CaseResult(case.name, check.passed, (check,), error=str(exc))
        return self.evaluate_trace(case, trace)

    def evaluate_trace(self, case: EvalCase, trace: Trace) -> CaseResult:
        """Apply deterministic expectations to an already executed live trace."""
        expected_failure = case.expected_outcome in {"ambiguity", "rejection"}
        checks: list[Verification] = [
            Verification(
                "expected_outcome",
                not expected_failure,
                "Agent answered" if not expected_failure else "Expected rejection",
            )
        ]
        selected = set(trace.dbt_context.selected_nodes)
        used_columns = set(trace.dbt_context.columns)
        checks.extend(
            [
                Verification(
                    "required_models",
                    set(case.required_models) <= selected,
                    _difference(case.required_models, selected),
                ),
                Verification(
                    "forbidden_models",
                    not (set(case.forbidden_models) & selected),
                    _intersection(case.forbidden_models, selected),
                ),
                Verification(
                    "required_columns",
                    set(case.required_columns) <= used_columns,
                    _difference(case.required_columns, used_columns),
                ),
                Verification(
                    "forbidden_columns",
                    not (set(case.forbidden_columns) & used_columns),
                    _intersection(case.forbidden_columns, used_columns),
                ),
                Verification(
                    "supported_claims",
                    trace.passed,
                    "; ".join(
                        f"{check.name}: {check.message or 'failed'}"
                        for check in trace.verification
                        if not check.passed
                    ),
                ),
            ]
        )
        expected_rows = case.result.rows or ()
        if case.reference_sql:
            try:
                sql = render_reference_sql(case.reference_sql, self.runtime.manifest)
                validated_reference = validate_sql(
                    sql,
                    self.runtime.manifest,
                    self.runtime.agent.nodes,
                    dialect=self.runtime.connection.dialect,
                    max_rows=self.runtime.agent.source.max_rows,
                    allow_sensitive=self.runtime.agent.source.allow_sensitive,
                )
                expected_rows = self.runtime.connection.execute(
                    validated_reference.executed,
                    self.runtime.agent.source.timeout_seconds,
                )
                checks.append(Verification("reference_query", True))
            except Exception as exc:
                checks.append(Verification("reference_query", False, str(exc)))
        if case.reference_sql or case.result.rows is not None or case.result.value_set:
            checks.append(_compare_result(trace.result.rows, expected_rows, case.result))
        if case.result.row_count is not None:
            checks.append(
                Verification(
                    "row_count",
                    len(trace.result.rows) == case.result.row_count,
                    f"expected {case.result.row_count}, got {len(trace.result.rows)}",
                )
            )
        if case.result.columns:
            actual_columns = set(trace.result.rows[0]) if trace.result.rows else set()
            checks.append(
                Verification(
                    "shape",
                    set(case.result.columns) == actual_columns,
                    f"expected {case.result.columns}, got {tuple(actual_columns)}",
                )
            )
        trace = replace(trace, eval_suite_digest=self.suite.digest)
        return CaseResult(case.name, all(check.passed for check in checks), tuple(checks), trace)


def _difference(required: Iterable[str], actual: set[str]) -> str:
    return "missing: " + ", ".join(sorted(set(required) - actual)) if set(required) - actual else ""


def _intersection(forbidden: Iterable[str], actual: set[str]) -> str:
    return (
        "forbidden: " + ", ".join(sorted(set(forbidden) & actual))
        if set(forbidden) & actual
        else ""
    )


def _compare_result(
    actual: tuple[dict[str, Any], ...],
    expected: Iterable[dict[str, Any]],
    config: ResultExpectation,
) -> Verification:
    expected_tuple = tuple(dict(row) for row in expected)
    if config.comparison == "scalar":
        actual_value = next(iter(actual[0].values()), None) if len(actual) == 1 else None
        expected_value = config.value
        if expected_value is None and len(expected_tuple) == 1:
            expected_value = next(iter(expected_tuple[0].values()), None)
        passed = _equal(actual_value, expected_value, config.tolerance)
    elif config.comparison == "ordered":
        passed = _rows_equal(
            actual,
            expected_tuple,
            config.tolerance,
            allow_extra_columns=config.allow_extra_columns,
        )
    elif config.comparison == "ordered_values":
        passed = len(actual) == len(expected_tuple) and all(
            len(actual_row) == len(expected_row)
            and all(
                _equal(actual_value, expected_value, config.tolerance)
                for actual_value, expected_value in zip(actual_row.values(), expected_row.values())
            )
            for actual_row, expected_row in zip(actual, expected_tuple)
        )
    elif config.comparison == "unordered":
        passed = _unordered_equal(
            actual,
            expected_tuple,
            config.tolerance,
            allow_extra_columns=config.allow_extra_columns,
        )
    elif config.comparison == "keyed":
        if not config.keys:
            raise EvalError("keyed result comparison requires keys")

        def key(row: dict[str, Any]) -> tuple[Any, ...]:
            return tuple(row.get(item) for item in config.keys)

        passed = _rows_equal(
            tuple(sorted(actual, key=key)),
            tuple(sorted(expected_tuple, key=key)),
            config.tolerance,
            allow_extra_columns=config.allow_extra_columns,
        )
    else:
        raise EvalError(f"Unsupported result comparison '{config.comparison}'")
    return Verification(
        "result_match",
        passed,
        "" if passed else _result_difference(actual, expected_tuple, config),
    )


def _result_difference(
    actual: tuple[dict[str, Any], ...],
    expected: tuple[dict[str, Any], ...],
    config: ResultExpectation,
) -> str:
    if config.comparison == "scalar":
        actual_value = next(iter(actual[0].values()), None) if len(actual) == 1 else None
        expected_value = config.value
        if expected_value is None and len(expected) == 1:
            expected_value = next(iter(expected[0].values()), None)
        return f"expected scalar {expected_value!r}; got {actual_value!r}"
    if len(actual) != len(expected):
        return f"expected {len(expected)} rows; got {len(actual)} rows"
    if config.comparison == "keyed":
        expected_by_key = {tuple(row.get(name) for name in config.keys): row for row in expected}
        actual_by_key = {tuple(row.get(name) for name in config.keys): row for row in actual}
        missing = sorted(set(expected_by_key) - set(actual_by_key), key=repr)
        unexpected = sorted(set(actual_by_key) - set(expected_by_key), key=repr)
        if missing or unexpected:
            return f"missing keys {missing[:5]}; unexpected keys {unexpected[:5]}"
        for key in expected_by_key:
            if not _rows_equal((actual_by_key[key],), (expected_by_key[key],), config.tolerance):
                return f"key {key}: expected {expected_by_key[key]!r}; got {actual_by_key[key]!r}"
    for index, (actual_row, expected_row) in enumerate(zip(actual, expected)):
        if not _rows_equal((actual_row,), (expected_row,), config.tolerance):
            return f"row {index}: expected {expected_row!r}; got {actual_row!r}"
    return "result rows differ"


def _equal(left: Any, right: Any, tolerance: float) -> bool:
    if (
        isinstance(left, (int, float, Decimal))
        and not isinstance(left, bool)
        and isinstance(right, (int, float, Decimal))
        and not isinstance(right, bool)
    ):
        return math.isclose(float(left), float(right), abs_tol=tolerance, rel_tol=0)
    if isinstance(left, (date, datetime)) or isinstance(right, (date, datetime)):
        return str(left) == str(right)
    return left == right


def _rows_equal(
    left: tuple[dict[str, Any], ...],
    right: tuple[dict[str, Any], ...],
    tolerance: float,
    *,
    allow_extra_columns: bool = False,
) -> bool:
    return len(left) == len(right) and all(
        (set(b) <= set(a) if allow_extra_columns else set(a) == set(b))
        and all(_equal(a[key], b[key], tolerance) for key in b)
        for a, b in zip(left, right)
    )


def _unordered_equal(
    left: tuple[dict[str, Any], ...],
    right: tuple[dict[str, Any], ...],
    tolerance: float,
    *,
    allow_extra_columns: bool = False,
) -> bool:
    remaining = list(right)
    for row in left:
        match = next(
            (
                index
                for index, candidate in enumerate(remaining)
                if _rows_equal(
                    (row,),
                    (candidate,),
                    tolerance,
                    allow_extra_columns=allow_extra_columns,
                )
            ),
            None,
        )
        if match is None:
            return False
        remaining.pop(match)
    return not remaining


__all__ = [
    "EvalCase",
    "EvalRunner",
    "EvalSuite",
    "SuiteResult",
    "load_eval_suite",
    "render_reference_sql",
]
