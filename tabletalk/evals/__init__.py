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

from tabletalk.inference import ReplayMissError
from tabletalk.manifest import Manifest
from tabletalk.runtime import AmbiguityError, RejectionError, Runtime
from tabletalk.schemas import EVAL_RESULT_SCHEMA, EVAL_SCHEMA, require_schema_version
from tabletalk.traces import Trace, Usage, Verification, trace_from_dict
from tabletalk.validation import SQLValidationError, validate_sql


class EvalError(ValueError):
    pass


@dataclass(frozen=True)
class BudgetLimits:
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None

    def __post_init__(self) -> None:
        values = {
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost": self.cost,
        }
        token_names = ("prompt_tokens", "completion_tokens", "total_tokens")
        for name in token_names:
            value = values[name]
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise EvalError(f"Eval budget {name} must be an integer")
        for name in ("latency_ms", "cost"):
            value = values[name]
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise EvalError(f"Eval budget {name} must be a finite number")
        if any(value is not None and value < 0 for value in values.values()):
            raise EvalError("Eval budget values must not be negative")

    def checks(self, trace: Trace) -> tuple[Verification, ...]:
        usage = trace.usage
        values = {
            "latency_ms": usage.latency_ms,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": (
                (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
                if usage.prompt_tokens is not None or usage.completion_tokens is not None
                else None
            ),
            "cost": usage.estimated_cost,
        }
        limits = {
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost": self.cost,
        }
        checks = []
        for name, limit in limits.items():
            if limit is None:
                continue
            actual = values[name]
            checks.append(
                Verification(
                    f"budget_{name}",
                    actual is not None and actual <= limit,
                    f"Limit: {limit}. Actual: {actual if actual is not None else 'not recorded'}.",
                )
            )
        return tuple(checks)


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
    reference_file: str | None = None
    result: ResultExpectation = ResultExpectation()
    required_models: tuple[str, ...] = ()
    forbidden_models: tuple[str, ...] = ()
    required_columns: tuple[str, ...] = ()
    forbidden_columns: tuple[str, ...] = ()
    expected_outcome: str = "answer"
    kind: str = "regression"
    protects: str = ""
    reason: str = ""
    source: tuple[dict[str, Any], ...] = ()
    deterministic: bool = False
    warehouse_data: bool = True
    failure: str = ""
    known_failure: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.question.strip():
            raise EvalError("Every eval case requires a non-empty name and question")
        if self.expected_outcome not in {"answer", "ambiguity", "rejection"}:
            raise EvalError("expect.outcome must be one of: answer, ambiguity, rejection")
        if self.kind not in {
            "regression",
            "capability",
            "safety",
            "ambiguity",
            "refusal",
        }:
            raise EvalError(f"Unsupported eval case kind '{self.kind}'")
        ground_truth_count = sum(
            (
                bool(self.reference_sql),
                bool(self.reference_file),
                self.result.rows is not None or self.result.value_set,
            )
        )
        if ground_truth_count > 1:
            raise EvalError(
                "Use reference_sql, reference, or a literal expected result. "
                "Do not combine these fields."
            )

    @property
    def verifies_result(self) -> bool:
        return (
            bool(self.reference_sql or self.reference_file)
            or self.result.rows is not None
            or self.result.value_set
        )


@dataclass(frozen=True)
class EvalSuite:
    name: str
    agent: str
    cases: tuple[EvalCase, ...]
    source_path: Path | None = None
    description: str = ""
    kind: str = "regression"
    trials: int = 1
    budgets: BudgetLimits = BudgetLimits()

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
    usage: Usage | None = None
    inference_fingerprint: str | None = None


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
            "schema_version": EVAL_RESULT_SCHEMA,
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
                    "usage": vars(case.usage) if case.usage else None,
                    "inference_fingerprint": case.inference_fingerprint,
                }
                for case in self.cases
            ],
        }


def suite_result_from_dict(payload: dict[str, Any]) -> SuiteResult:
    require_schema_version(
        payload,
        EVAL_RESULT_SCHEMA,
        resource="eval result",
        editable=False,
    )
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise EvalError("Eval result cases must be a list")
    cases = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise EvalError("Each eval result case must be an object")
        raw_checks = raw_case.get("checks") or ()
        raw_trace = raw_case.get("trace")
        cases.append(
            CaseResult(
                name=str(raw_case.get("name") or ""),
                passed=bool(raw_case.get("passed")),
                checks=tuple(
                    Verification(
                        str(item.get("name") or ""),
                        bool(item.get("passed")),
                        str(item.get("message") or ""),
                    )
                    for item in raw_checks
                    if isinstance(item, dict)
                ),
                trace=trace_from_dict(raw_trace) if isinstance(raw_trace, dict) else None,
                error=str(raw_case["error"]) if raw_case.get("error") else None,
                usage=(
                    Usage(
                        **{
                            **dict(raw_case["usage"]),
                            "provider_errors": tuple(
                                raw_case["usage"].get("provider_errors") or ()
                            ),
                        }
                    )
                    if isinstance(raw_case.get("usage"), dict)
                    else None
                ),
                inference_fingerprint=(
                    str(raw_case["inference_fingerprint"])
                    if raw_case.get("inference_fingerprint")
                    else None
                ),
            )
        )
    return SuiteResult(
        name=str(payload.get("name") or ""),
        agent=str(payload.get("agent") or ""),
        cases=tuple(cases),
        suite_digest=str(payload.get("suite_digest") or ""),
        trial=int(payload.get("trial") or 1),
        trials=int(payload.get("trials") or 1),
    )


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
    require_schema_version(payload, EVAL_SCHEMA, resource=str(source))
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
        source_evidence = raw.get("source") or []
        if not isinstance(source_evidence, list) or not all(
            isinstance(item, dict) for item in source_evidence
        ):
            raise EvalError(f"cases[{index}].source must be a list of mappings")
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
                reference_file=str(expect["reference"]) if expect.get("reference") else None,
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
                kind=str(raw.get("kind") or "regression"),
                protects=str(raw.get("protects") or ""),
                reason=str(raw.get("reason") or ""),
                source=tuple(dict(item) for item in source_evidence),
                deterministic=bool(raw.get("deterministic", False)),
                warehouse_data=bool(raw.get("warehouse_data", True)),
                failure=str(raw.get("failure") or ""),
                known_failure=bool(raw.get("known_failure", False)),
            )
        )
    description = payload.get("description") or ""
    kind = payload.get("kind") or "regression"
    trials = payload.get("trials", 1)
    budgets_raw = _mapping(payload.get("budgets"), "budgets")
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
        BudgetLimits(
            latency_ms=budgets_raw.get("latency_ms"),
            prompt_tokens=budgets_raw.get("prompt_tokens"),
            completion_tokens=budgets_raw.get("completion_tokens"),
            total_tokens=budgets_raw.get("total_tokens"),
            cost=budgets_raw.get("cost"),
        ),
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

    def run(self, case_name: str | None = None, *, trial: int = 1) -> SuiteResult:
        cases = [case for case in self.suite.cases if case_name is None or case.name == case_name]
        if not cases:
            raise EvalError(f"Eval case '{case_name}' was not found")
        results = tuple(self._run_case(case, trial) for case in cases)
        return SuiteResult(
            self.suite.name,
            self.suite.agent,
            results,
            self.suite.digest,
            trial=trial,
            trials=self.suite.trials,
        )

    def _run_case(self, case: EvalCase, trial: int) -> CaseResult:
        try:
            trace = self.runtime.answer(case.question, trial=trial)
        except ReplayMissError:
            raise  # No recording is not an agent failure. Callers report it separately.
        except Exception as exc:
            expected_exception = (
                case.expected_outcome == "ambiguity" and isinstance(exc, AmbiguityError)
            ) or (
                case.expected_outcome == "rejection"
                and isinstance(exc, (RejectionError, SQLValidationError))
            )
            check = Verification("expected_outcome", expected_exception, str(exc))
            return CaseResult(
                case.name,
                check.passed,
                (check,),
                error=str(exc),
                usage=getattr(exc, "usage", None),
                inference_fingerprint=getattr(exc, "inference_fingerprint", None),
            )
        return self.evaluate_trace(case, trace)

    def evaluate_trace(self, case: EvalCase, trace: Trace) -> CaseResult:
        """Apply deterministic expectations to an already executed live trace."""
        expected_failure = case.expected_outcome in {"ambiguity", "rejection"}
        expected_message = (
            "Expected a clarification request"
            if case.expected_outcome == "ambiguity"
            else "Expected rejection"
        )
        checks: list[Verification] = [
            Verification(
                "expected_outcome",
                not expected_failure,
                "Agent answered" if not expected_failure else expected_message,
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
                    ". ".join(
                        f"{check.name}: {check.message or 'failed'}"
                        for check in trace.verification
                        if not check.passed
                    ),
                ),
            ]
        )
        expected_rows = case.result.rows or ()
        reference_sql = case.reference_sql
        if case.reference_file:
            try:
                if self.suite.source_path is None:
                    raise EvalError("A reference file requires a saved eval suite")
                project_root = self.suite.source_path.parent.parent.resolve()
                reference_path = (project_root / case.reference_file).resolve()
                reference_path.relative_to(project_root)
                reference_sql = reference_path.read_text()
            except (OSError, ValueError) as exc:
                checks.append(Verification("reference_query", False, str(exc)))
        if reference_sql:
            try:
                sql = render_reference_sql(reference_sql, self.runtime.manifest)
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
        if reference_sql or case.result.rows is not None or case.result.value_set:
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
        checks.extend(self.suite.budgets.checks(trace))
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
        return f"Expected scalar {expected_value!r}. Got {actual_value!r}."
    if len(actual) != len(expected):
        return f"Expected {len(expected)} rows. Got {len(actual)} rows."
    if config.comparison == "keyed":
        expected_by_key = {tuple(row.get(name) for name in config.keys): row for row in expected}
        actual_by_key = {tuple(row.get(name) for name in config.keys): row for row in actual}
        missing = sorted(set(expected_by_key) - set(actual_by_key), key=repr)
        unexpected = sorted(set(actual_by_key) - set(expected_by_key), key=repr)
        if missing or unexpected:
            return f"Missing keys: {missing[:5]}. Unexpected keys: {unexpected[:5]}."
        for key in expected_by_key:
            if not _rows_equal((actual_by_key[key],), (expected_by_key[key],), config.tolerance):
                return f"Key {key}: expected {expected_by_key[key]!r}. Got {actual_by_key[key]!r}."
    for index, (actual_row, expected_row) in enumerate(zip(actual, expected)):
        if not _rows_equal((actual_row,), (expected_row,), config.tolerance):
            return f"Row {index}: expected {expected_row!r}. Got {actual_row!r}."
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
    return bool(left == right)


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
    "BudgetLimits",
    "EvalRunner",
    "EvalSuite",
    "SuiteResult",
    "load_eval_suite",
    "render_reference_sql",
    "suite_result_from_dict",
]
