"""One inspectable trace schema shared by live and evaluation runs."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


def _json_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            item.name: _json_value(getattr(value, item.name)) for item in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    return value


@dataclass(frozen=True)
class Interpretation:
    intent: str = ""
    metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    start_date: str | None = None
    end_date: str | None = None
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class DbtContext:
    manifest_digest: str
    catalog_digest: str | None
    selected_nodes: tuple[str, ...]
    columns: tuple[str, ...]
    relevant_tests: tuple[str, ...] = ()
    test_health: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SQLTrace:
    generated: str | None
    executed: str | None
    dialect: str


@dataclass(frozen=True)
class ResultTrace:
    rows: tuple[dict[str, Any], ...]
    row_count: int


@dataclass(frozen=True)
class Evidence:
    row: int
    column: str


@dataclass(frozen=True)
class Claim:
    text: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Answer:
    text: str
    claims: tuple[Claim, ...]


@dataclass(frozen=True)
class Verification:
    name: str
    passed: bool
    message: str = ""


@dataclass(frozen=True)
class Usage:
    latency_ms: float = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    estimated_cost: float | None = None


@dataclass(frozen=True)
class Trace:
    question: str
    interpretation: Interpretation
    dbt_context: DbtContext
    sql: SQLTrace
    result: ResultTrace
    answer: Answer
    verification: tuple[Verification, ...]
    agent: str
    agent_digest: str
    model_identity: str
    warehouse_identity: str
    usage: Usage = Usage()
    eval_suite_digest: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.verification)

    @property
    def correctness_verified(self) -> bool:
        checks = tuple(
            check for check in self.verification if check.name.startswith("correctness_eval:")
        )
        return bool(checks) and all(check.passed for check in checks) and self.passed

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)

    def write(self, directory: str | Path, name: str | None = None) -> Path:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        filename = name or self.created_at.replace(":", "-") + ".json"
        target = root / filename
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")
        return target
