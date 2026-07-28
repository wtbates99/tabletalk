"""Execution-based evaluation against exact candidate Agent artifacts."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from tabletalk.compiler import CompiledArtifact
from tabletalk.domain import TableTalkError, to_primitive
from tabletalk.evals.metrics import (
    answer_quality_metric,
    interpretation_metric,
    performance_metric,
    result_accuracy_metric,
    safety_metric,
    sql_execution_metric,
    sql_structure_metric,
)
from tabletalk.evals.models import (
    CaseResult,
    EvalCase,
    EvalSuite,
    ExecutionTrace,
    MetricResult,
    SuiteResult,
    ToolCall,
)
from tabletalk.factories import get_db_provider
from tabletalk.interfaces import QuerySession
from tabletalk.project import Project

_DEFAULT_WEIGHTS = {
    "sql_execution": 0.20,
    "result_accuracy": 0.40,
    "safety": 0.20,
    "sql_structure": 0.10,
    "answer_quality": 0.05,
    "performance": 0.05,
    "interpretation": 0.20,
}


class EvalRunner:
    """Run every case through the same structured runtime as CLI and web."""

    def __init__(
        self,
        suite: EvalSuite,
        project_folder: str = ".",
        session_factory: Callable[[str], QuerySession] = QuerySession,
        candidate: CompiledArtifact | None = None,
    ) -> None:
        if not suite.agent:
            raise ValueError("EvalSuite must identify an Agent.")
        self.suite = suite
        self.project_folder = str(Path(project_folder).expanduser().resolve())
        self.session = session_factory(self.project_folder)
        compiled = candidate or Project.load(self.project_folder).compile(
            suite.agent
        )
        if not isinstance(compiled, CompiledArtifact):
            raise AssertionError("Agent evaluation requires one candidate artifact.")
        if compiled.agent.name != suite.agent:
            raise ValueError(
                f"EvalSuite '{suite.name}' targets '{suite.agent}', not "
                f"candidate '{compiled.agent.name}'."
            )
        self.candidate = compiled
        self._fixture_temporary: tempfile.TemporaryDirectory[str] | None = None
        self._eval_database_type: str | None = None
        self._eval_database_identity: str | None = None
        self._configure_eval_provider()

    def _resolve_suite_path(self, path: str) -> Path:
        expanded = Path(os.path.expandvars(os.path.expanduser(path)))
        if expanded.is_absolute():
            return expanded
        return (self.suite.source_path.parent / expanded).resolve()

    def _configure_eval_provider(self) -> None:
        environment = self.suite.environment
        fixture = environment.get("fixture")
        setup = environment.get("fixture_setup") or []
        fixture_type = environment.get("fixture_type")
        provider_config: dict[str, Any] | None = None
        if setup:
            self._fixture_temporary = tempfile.TemporaryDirectory(
                prefix="tabletalk-eval-"
            )
            suffix = ".duckdb" if fixture_type == "duckdb" else ".db"
            fixture_path = Path(self._fixture_temporary.name) / f"fixture{suffix}"
            scripts = [
                self._resolve_suite_path(str(path)).read_text() for path in setup
            ]
            if fixture_type == "sqlite":
                connection = sqlite3.connect(fixture_path)
                try:
                    for script in scripts:
                        connection.executescript(script)
                    connection.commit()
                finally:
                    connection.close()
            elif fixture_type == "duckdb":
                setup_provider = get_db_provider(
                    {
                        "type": "duckdb",
                        "database_path": str(fixture_path),
                        "read_only": False,
                    }
                )
                connection = setup_provider.get_client()
                try:
                    for script in scripts:
                        connection.execute(script)
                finally:
                    connection.close()
            else:
                raise ValueError(
                    "fixture.setup requires fixture.type sqlite or duckdb"
                )
            provider_config = {
                "type": fixture_type,
                "database_path": str(fixture_path),
                "read_only": True,
            }
        elif fixture:
            fixture_path = self._resolve_suite_path(str(fixture))
            if not fixture_path.is_file():
                raise FileNotFoundError(
                    f"Eval fixture not found: {fixture_path}."
                )
            provider_config = {
                "type": fixture_type,
                "database_path": str(fixture_path),
                "read_only": True,
            }
        if provider_config is not None:
            self.session._db_provider = get_db_provider(provider_config)
            self.session._db_connection_name = self.candidate.agent.connection
            self._eval_database_type = str(provider_config["type"])
            self._eval_database_identity = "eval-fixture"

    def _capture_usage(self, trace: ExecutionTrace) -> None:
        usage = getattr(self.session.llm_provider, "last_usage", {}) or {}
        trace.prompt_tokens += int(usage.get("prompt_tokens", 0))
        trace.completion_tokens += int(usage.get("completion_tokens", 0))

    def _calculate_cost(self, trace: ExecutionTrace) -> float:
        pricing = self.suite.environment.get("pricing", {})
        if not isinstance(pricing, dict):
            return 0.0
        return round(
            trace.prompt_tokens
            * float(pricing.get("input_per_million_tokens", 0))
            / 1_000_000
            + trace.completion_tokens
            * float(pricing.get("output_per_million_tokens", 0))
            / 1_000_000,
            8,
        )

    def _execute_case(self, case: EvalCase) -> ExecutionTrace:
        trace = ExecutionTrace()
        started = time.monotonic()
        question = next(
            message["content"]
            for message in reversed(case.messages)
            if message["role"] == "user"
        )
        try:
            answer = self.session.ask_artifact(
                json.loads(self.candidate.to_json()),
                question,
                database_type_override=self._eval_database_type,
                database_identity_override=self._eval_database_identity,
                dialect_override=self.candidate.agent.dialect,
            )
            trace.answer = to_primitive(answer)
            trace.final_answer = answer.direct_answer or ""
            if answer.sql:
                trace.generated_sql.append(answer.sql)
                trace.tool_calls.append(
                    ToolCall(
                        tool="database.query",
                        input={"sql": answer.sql},
                        output=list(answer.data),
                    )
                )
            trace.query_results.append(list(answer.data))
            self._capture_usage(trace)
        except TableTalkError as error:
            trace.error = f"{error.code.value}: {error.message}"
        except Exception as error:
            trace.error = f"unexpected_failure: {type(error).__name__}"
        finally:
            trace.latency_ms = (time.monotonic() - started) * 1000
            trace.cost_usd = self._calculate_cost(trace)
        return trace

    def _reference_result(
        self,
        case: EvalCase,
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        result = case.expected.get("result")
        reference_sql = (
            result.get("reference_sql") if isinstance(result, dict) else None
        )
        if not reference_sql:
            return None, None
        try:
            return (
                self.session.execute_sql(
                    str(reference_sql),
                    artifact=json.loads(self.candidate.to_json()),
                ),
                None,
            )
        except Exception as error:
            return None, f"{type(error).__name__}: reference query failed"

    def _metric_results(
        self,
        case: EvalCase,
        trace: ExecutionTrace,
        reference: list[dict[str, Any]] | None,
        reference_error: str | None,
    ) -> list[MetricResult]:
        optional = [
            result_accuracy_metric(case, trace, reference, reference_error),
            sql_structure_metric(case, trace, self.candidate.agent.dialect),
            safety_metric(case, trace, self.candidate.agent.dialect),
            answer_quality_metric(case, trace),
            interpretation_metric(case, trace),
            performance_metric(case, trace),
        ]
        return [
            sql_execution_metric(trace),
            *(metric for metric in optional if metric is not None),
        ]

    def _case_score(self, metrics: list[MetricResult]) -> float:
        configured = self.suite.environment.get("metric_weights", {})
        weights = {
            **_DEFAULT_WEIGHTS,
            **(configured if isinstance(configured, dict) else {}),
        }
        total = sum(float(weights.get(metric.name, 1)) for metric in metrics)
        if not total:
            return 0.0
        return sum(
            metric.score * float(weights.get(metric.name, 1))
            for metric in metrics
        ) / total

    def run(
        self,
        on_case_start: Callable[[EvalCase, int, int], None] | None = None,
        on_case_complete: Callable[[CaseResult, int, int], None] | None = None,
    ) -> SuiteResult:
        started = datetime.now(timezone.utc)
        results = []
        total = len(self.suite.cases)
        for index, case in enumerate(self.suite.cases, start=1):
            if on_case_start:
                on_case_start(case, index, total)
            trace = self._execute_case(case)
            reference, reference_error = self._reference_result(case)
            metrics = self._metric_results(
                case,
                trace,
                reference,
                reference_error,
            )
            result = CaseResult(
                case_name=case.name,
                description=case.description,
                tags=case.tags,
                passed=all(metric.passed for metric in metrics),
                score=self._case_score(metrics),
                trace=trace,
                metrics=metrics,
            )
            results.append(result)
            if on_case_complete:
                on_case_complete(result, index, total)
        completed = datetime.now(timezone.utc)
        return SuiteResult(
            run_id=str(uuid4()),
            suite_name=self.suite.name,
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
            cases=results,
            score=(
                sum(result.score for result in results) / len(results)
                if results
                else 0
            ),
            passed=all(result.passed for result in results),
            metadata={
                "llm_provider": self.session.config.get("llm", {}).get(
                    "provider"
                ),
                "model": getattr(self.session.llm_provider, "model", None),
                "agent": self.suite.agent,
                "artifact_digest": self.candidate.digest,
                "suite_path": str(self.suite.source_path),
            },
        )
