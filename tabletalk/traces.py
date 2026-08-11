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
    retry_count: int = 0
    provider_errors: tuple[str, ...] = ()


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
    inference_fingerprint: str | None = None
    cache_hit: bool = False
    semantic_digest: str | None = None
    prompt_version: str | None = None
    runtime_version: str | None = None
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


def trace_from_dict(payload: dict[str, Any]) -> Trace:
    """Load a trace from an immutable inference artifact."""
    interpretation = payload.get("interpretation") or {}
    context = payload.get("dbt_context") or {}
    sql = payload.get("sql") or {}
    result = payload.get("result") or {}
    answer = payload.get("answer") or {}
    usage = payload.get("usage") or {}
    return Trace(
        question=str(payload.get("question") or ""),
        interpretation=Interpretation(
            intent=str(interpretation.get("intent") or ""),
            metrics=tuple(str(item) for item in interpretation.get("metrics") or ()),
            dimensions=tuple(str(item) for item in interpretation.get("dimensions") or ()),
            start_date=(
                str(interpretation["start_date"])
                if interpretation.get("start_date") is not None
                else None
            ),
            end_date=(
                str(interpretation["end_date"])
                if interpretation.get("end_date") is not None
                else None
            ),
            assumptions=tuple(str(item) for item in interpretation.get("assumptions") or ()),
        ),
        dbt_context=DbtContext(
            manifest_digest=str(context.get("manifest_digest") or ""),
            catalog_digest=(
                str(context["catalog_digest"]) if context.get("catalog_digest") else None
            ),
            selected_nodes=tuple(str(item) for item in context.get("selected_nodes") or ()),
            columns=tuple(str(item) for item in context.get("columns") or ()),
            relevant_tests=tuple(str(item) for item in context.get("relevant_tests") or ()),
            test_health={
                str(key): str(value)
                for key, value in (context.get("test_health") or {}).items()
            },
        ),
        sql=SQLTrace(
            generated=str(sql["generated"]) if sql.get("generated") is not None else None,
            executed=str(sql["executed"]) if sql.get("executed") is not None else None,
            dialect=str(sql.get("dialect") or ""),
        ),
        result=ResultTrace(
            tuple(dict(row) for row in result.get("rows") or ()),
            int(result.get("row_count") or 0),
        ),
        answer=Answer(
            text=str(answer.get("text") or ""),
            claims=tuple(
                Claim(
                    text=str(claim.get("text") or ""),
                    evidence=tuple(
                        Evidence(int(item["row"]), str(item["column"]))
                        for item in claim.get("evidence") or ()
                    ),
                )
                for claim in answer.get("claims") or ()
            ),
        ),
        verification=tuple(
            Verification(
                str(item.get("name") or ""),
                bool(item.get("passed")),
                str(item.get("message") or ""),
            )
            for item in payload.get("verification") or ()
        ),
        agent=str(payload.get("agent") or ""),
        agent_digest=str(payload.get("agent_digest") or ""),
        model_identity=str(payload.get("model_identity") or ""),
        warehouse_identity=str(payload.get("warehouse_identity") or ""),
        usage=Usage(
            latency_ms=float(usage.get("latency_ms") or 0),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            estimated_cost=usage.get("estimated_cost"),
            retry_count=int(usage.get("retry_count") or 0),
            provider_errors=tuple(str(item) for item in usage.get("provider_errors") or ()),
        ),
        eval_suite_digest=(
            str(payload["eval_suite_digest"]) if payload.get("eval_suite_digest") else None
        ),
        inference_fingerprint=(
            str(payload["inference_fingerprint"])
            if payload.get("inference_fingerprint")
            else None
        ),
        cache_hit=bool(payload.get("cache_hit", False)),
        semantic_digest=(
            str(payload["semantic_digest"]) if payload.get("semantic_digest") else None
        ),
        prompt_version=(
            str(payload["prompt_version"]) if payload.get("prompt_version") else None
        ),
        runtime_version=(
            str(payload["runtime_version"]) if payload.get("runtime_version") else None
        ),
        created_at=str(payload.get("created_at") or ""),
    )
