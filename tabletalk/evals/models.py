"""Data models shared by the eval loader, runner, metrics, and reporters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def _json_value(value: Any) -> Any:
    """Convert database and dataclass values into JSON-safe primitives."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@dataclass
class EvalCase:
    """A single agent conversation and its expected behavior."""

    name: str
    messages: list[dict[str, str]]
    expected: dict[str, Any]
    manifest: str | None = None
    description: str = ""
    tags: list[str] = field(default_factory=list)
    expected_interpretation: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalSuite:
    """A versioned collection of eval cases loaded from YAML."""

    name: str
    cases: list[EvalCase]
    source_path: Path
    version: int = 1
    description: str = ""
    environment: dict[str, Any] = field(default_factory=dict)
    agent: str | None = None


@dataclass
class ToolCall:
    """One observable database tool invocation."""

    tool: str
    input: dict[str, Any]
    output: Any | None = None
    error: str | None = None
    latency_ms: float = 0.0


@dataclass
class ExecutionTrace:
    """Everything the runner observed while executing an eval case."""

    final_answer: str = ""
    generated_sql: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    query_results: list[list[dict[str, Any]]] = field(default_factory=list)
    latency_ms: float = 0.0
    generation_ms: float = 0.0
    execution_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    answer: dict[str, Any] | None = None

    @property
    def last_result(self) -> list[dict[str, Any]]:
        return self.query_results[-1] if self.query_results else []


@dataclass
class MetricResult:
    """The score and diagnostic detail for one metric."""

    name: str
    score: float
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    hard_gate: bool = False


@dataclass
class CaseResult:
    """Execution trace and metric results for one case."""

    case_name: str
    passed: bool
    score: float
    trace: ExecutionTrace
    metrics: list[MetricResult]
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


@dataclass
class SuiteResult:
    """Aggregate output from an eval suite run."""

    run_id: str
    suite_name: str
    started_at: str
    completed_at: str
    cases: list[CaseResult]
    score: float
    passed: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def passed_count(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def failed_count(self) -> int:
        return len(self.cases) - self.passed_count

    def to_dict(self) -> dict[str, Any]:
        value = _json_value(asdict(self))
        value["passed_count"] = self.passed_count
        value["failed_count"] = self.failed_count
        return value
