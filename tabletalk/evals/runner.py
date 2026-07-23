"""Eval-suite execution over the existing TableTalk QuerySession."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from tabletalk.evals.metrics import (
    answer_quality_metric,
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

_DIALECTS = {
    "azuresql": "tsql",
    "bigquery": "bigquery",
    "duckdb": "duckdb",
    "mysql": "mysql",
    "postgres": "postgres",
    "snowflake": "snowflake",
    "sqlite": "sqlite",
}

_DEFAULT_WEIGHTS = {
    "sql_execution": 0.20,
    "result_accuracy": 0.40,
    "safety": 0.20,
    "sql_structure": 0.10,
    "answer_quality": 0.05,
    "performance": 0.05,
}


class EvalRunner:
    """Run deterministic TableTalk eval cases and capture full traces."""

    def __init__(
        self,
        suite: EvalSuite,
        project_folder: str = ".",
        session_factory: Callable[[str], QuerySession] = QuerySession,
    ):
        self.suite = suite
        self.project_folder = str(Path(project_folder).expanduser().resolve())
        self.session_factory = session_factory
        self.session = session_factory(self.project_folder)
        self._configure_eval_provider()

    def _resolve_suite_path(self, path: str) -> Path:
        expanded = Path(os.path.expandvars(os.path.expanduser(path)))
        if expanded.is_absolute():
            return expanded
        return (self.suite.source_path.parent / expanded).resolve()

    def _configure_eval_provider(self) -> None:
        """Override the project database with a deterministic fixture when requested."""
        environment = self.suite.environment
        provider_config = environment.get("provider")
        fixture = environment.get("fixture")
        if provider_config is None and fixture:
            fixture_path = self._resolve_suite_path(str(fixture))
            if not fixture_path.is_file():
                raise FileNotFoundError(
                    f"Eval fixture not found: {fixture_path}. "
                    "Build or download the fixture before running this suite."
                )
            provider_config = {
                "type": environment.get("fixture_type", "duckdb"),
                "database_path": str(fixture_path),
            }
        elif isinstance(provider_config, dict):
            provider_config = dict(provider_config)
            database_path = provider_config.get("database_path")
            if isinstance(database_path, str) and database_path != ":memory:":
                provider_config["database_path"] = str(self._resolve_suite_path(database_path))

        if provider_config:
            self.session._db_provider = get_db_provider(provider_config)
            self.session._db_loaded = True

    def _dialect(self) -> str | None:
        environment_dialect = self.suite.environment.get("dialect")
        if environment_dialect:
            return str(environment_dialect)
        provider = self.session.get_db_provider()
        provider_type = self.suite.environment.get("fixture_type")
        if not provider_type:
            environment_provider = self.suite.environment.get("provider")
            if isinstance(environment_provider, dict):
                provider_type = environment_provider.get("type")
        if not provider_type:
            provider_type = self.session.config.get("provider", {}).get("type")
        if provider is None:
            return None
        return _DIALECTS.get(str(provider_type))

    def _load_manifest(self, manifest: str) -> str:
        possible_path = self._resolve_suite_path(manifest)
        if possible_path.is_file():
            return possible_path.read_text()
        manifest_name = manifest if manifest.endswith(".txt") else f"{manifest}.txt"
        return self.session.load_manifest(manifest_name)

    def _capture_usage(self, trace: ExecutionTrace) -> None:
        usage = getattr(self.session.llm_provider, "last_usage", {}) or {}
        trace.prompt_tokens += int(usage.get("prompt_tokens", 0))
        trace.completion_tokens += int(usage.get("completion_tokens", 0))

    def _calculate_cost(self, trace: ExecutionTrace) -> float:
        pricing = self.suite.environment.get("pricing", {})
        if not isinstance(pricing, dict):
            return 0.0
        input_rate = float(pricing.get("input_per_million_tokens", 0.0))
        output_rate = float(pricing.get("output_per_million_tokens", 0.0))
        return round(
            trace.prompt_tokens * input_rate / 1_000_000
            + trace.completion_tokens * output_rate / 1_000_000,
            8,
        )

    def _execute_case(self, case: EvalCase) -> ExecutionTrace:
        trace = ExecutionTrace()
        started = time.monotonic()
        history: list[dict[str, str]] = []
        last_question = ""

        try:
            manifest = self._load_manifest(case.manifest or "")
            for message in case.messages:
                if message["role"] == "assistant":
                    history.append(message)
                    continue

                question = message["content"]
                last_question = question
                generation_started = time.monotonic()
                raw_sql = "".join(
                    self.session.generate_sql_conversational(manifest, question, history)
                )
                trace.generation_ms += (time.monotonic() - generation_started) * 1000
                sql = self.session._clean_sql(raw_sql)
                trace.generated_sql.append(sql)
                self._capture_usage(trace)

                call = ToolCall(tool="database.query", input={"sql": sql})
                execution_started = time.monotonic()
                try:
                    result = self.session.execute_sql(sql)
                    call.output = result
                    trace.query_results.append(result)
                except Exception as exc:
                    call.error = str(exc)
                    trace.error = str(exc)
                finally:
                    call.latency_ms = (time.monotonic() - execution_started) * 1000
                    trace.execution_ms += call.latency_ms
                    trace.tool_calls.append(call)

                history.extend(
                    [
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": sql},
                    ]
                )
                history = history[-self.session.max_conv_messages :]
                if call.error:
                    break

            answer_config = case.expected.get("answer")
            generate_answer = bool(answer_config) or bool(
                self.suite.environment.get("generate_answer", False)
            )
            if (
                generate_answer
                and trace.error is None
                and trace.generated_sql
                and trace.query_results
            ):
                trace.final_answer = "".join(
                    self.session.explain_results_stream(
                        last_question,
                        trace.generated_sql[-1],
                        trace.last_result,
                    )
                ).strip()
                self._capture_usage(trace)
        except Exception as exc:
            trace.error = str(exc)
        finally:
            trace.latency_ms = (time.monotonic() - started) * 1000
            trace.cost_usd = self._calculate_cost(trace)

        return trace

    def _reference_result(self, case: EvalCase) -> tuple[list[dict[str, Any]] | None, str | None]:
        result_config = case.expected.get("result")
        if not isinstance(result_config, dict):
            return None, None
        reference_sql = result_config.get("reference_sql")
        if not reference_sql:
            return None, None
        try:
            return self.session.execute_sql(str(reference_sql)), None
        except Exception as exc:
            return None, str(exc)

    def _metric_results(
        self,
        case: EvalCase,
        trace: ExecutionTrace,
        reference: list[dict[str, Any]] | None,
        reference_error: str | None,
    ) -> list[MetricResult]:
        dialect = self._dialect()
        optional_metrics = [
            result_accuracy_metric(case, trace, reference, reference_error),
            sql_structure_metric(case, trace, dialect),
            safety_metric(case, trace, dialect),
            answer_quality_metric(case, trace),
            performance_metric(case, trace),
        ]
        return [sql_execution_metric(trace), *[metric for metric in optional_metrics if metric]]

    def _case_score(self, metrics: list[MetricResult]) -> float:
        custom_weights = self.suite.environment.get("metric_weights", {})
        weights = {
            **_DEFAULT_WEIGHTS,
            **(custom_weights if isinstance(custom_weights, dict) else {}),
        }
        total_weight = sum(float(weights.get(metric.name, 1.0)) for metric in metrics)
        if total_weight == 0:
            return 0.0
        return (
            sum(metric.score * float(weights.get(metric.name, 1.0)) for metric in metrics)
            / total_weight
        )

    def run(self) -> SuiteResult:
        """Execute every case in suite order and return an aggregate result."""
        started_at = datetime.now(timezone.utc)
        case_results: list[CaseResult] = []
        for case in self.suite.cases:
            trace = self._execute_case(case)
            reference, reference_error = self._reference_result(case)
            metrics = self._metric_results(case, trace, reference, reference_error)
            score = self._case_score(metrics)
            case_results.append(
                CaseResult(
                    case_name=case.name,
                    description=case.description,
                    tags=case.tags,
                    passed=all(metric.passed for metric in metrics),
                    score=score,
                    trace=trace,
                    metrics=metrics,
                )
            )

        score = (
            sum(case.score for case in case_results) / len(case_results) if case_results else 0.0
        )
        completed_at = datetime.now(timezone.utc)
        model = getattr(self.session.llm_provider, "model", None)
        return SuiteResult(
            run_id=str(uuid4()),
            suite_name=self.suite.name,
            started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
            cases=case_results,
            score=score,
            passed=all(case.passed for case in case_results),
            metadata={
                "llm_provider": self.session.config.get("llm", {}).get("provider"),
                "model": model,
                "project_folder": self.project_folder,
                "suite_path": str(self.suite.source_path),
            },
        )
