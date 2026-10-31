"""Answer receipts: a portable, versioned record of how one answer was formed.

A receipt is built from a runtime trace and the dbt artifacts that were current when
the question was answered. The JSON form follows ``tabletalk.receipt/v1``. Its JSON
Schema is published at ``docs/spec/receipt-v1.schema.json``. The terminal form is
what ``tabletalk ask`` prints.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from tabletalk.manifest import Manifest, Node
from tabletalk.schemas import content_digest
from tabletalk.semantic_guard import is_sensitive
from tabletalk.traces import Trace

RECEIPT_SCHEMA = "tabletalk.receipt/v1"
RECEIPTS_DIRECTORY = Path(".tabletalk") / "receipts"

# verified:   an approved eval case matched this exact question and passed.
# grounded:   every hard check passed and every claim cites result cells, but no
#             approved eval vouches for the number.
# unverified: a hard check or a matching eval failed.
Trust = Literal["verified", "grounded", "unverified"]


class ReceiptError(ValueError):
    """A receipt could not be found or read."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Evidence(_Frozen):
    """One result cell that supports a claim."""

    row: int
    column: str


class Claim(_Frozen):
    """One factual statement in the answer and the cells that support it."""

    text: str
    evidence: list[Evidence]


class Interpretation(_Frozen):
    """How the agent read the question."""

    intent: str
    metrics: list[str]
    dimensions: list[str]
    start_date: str | None
    end_date: str | None
    assumptions: list[str]


class Query(_Frozen):
    """The SQL the model wrote and the SQL TableTalk executed after validation."""

    dialect: str
    generated: str | None
    executed: str | None


class Result(_Frozen):
    """The bounded evidence rows the answer was formed from."""

    row_count: int
    rows: list[dict[str, JsonValue]]


class ColumnUse(_Frozen):
    """A column the query read, with the description the agent saw."""

    name: str
    description: str
    data_type: str | None
    sensitive: bool


class TestStatus(_Frozen):
    """A dbt test on a used resource. ``status`` is null when run_results.json was absent."""

    unique_id: str
    name: str
    column: str | None
    status: str | None


class Freshness(_Frozen):
    """`dbt source freshness` for an upstream source."""

    source: str
    status: str
    max_loaded_at: str | None
    snapshotted_at: str | None


class Exposure(_Frozen):
    """A dbt exposure, such as a dashboard, that depends on a used resource."""

    unique_id: str
    name: str
    type: str
    owner: str | None
    maturity: str | None
    url: str | None


class Resource(_Frozen):
    """One dbt resource the answer read."""

    unique_id: str
    relation: str
    description: str
    columns: list[ColumnUse]
    tests: list[TestStatus]
    upstream: list[str]
    freshness: list[Freshness]
    exposures: list[Exposure]


class Check(_Frozen):
    """One deterministic verification."""

    name: str
    passed: bool
    detail: str


class Identity(_Frozen):
    """Everything needed to reproduce the run."""

    agent: str
    agent_digest: str
    model: str
    warehouse: str
    manifest_digest: str
    catalog_digest: str | None
    semantic_digest: str | None
    prompt_version: str | None
    runtime_version: str | None


class Replay(_Frozen):
    """Whether the run can be replayed without a model, and how."""

    inference_fingerprint: str | None
    recorded: bool
    command: str | None


class Receipt(_Frozen):
    """A ``tabletalk.receipt/v1`` answer receipt."""

    schema_version: Literal["tabletalk.receipt/v1"] = "tabletalk.receipt/v1"
    run_id: str
    created_at: str
    question: str
    answer: str
    trust: Trust
    trust_reasons: list[str]
    claims: list[Claim]
    interpretation: Interpretation
    query: Query
    result: Result
    resources: list[Resource]
    checks: list[Check]
    identity: Identity
    replay: Replay


def _sha256(digest: str | None) -> str | None:
    """Return a digest in the spec's ``sha256:<hex>`` form."""
    if not digest:
        return None
    return digest if digest.startswith("sha256:") else "sha256:" + digest


def run_id_for(trace: Trace) -> str:
    """Return the stable run ID for a trace: a digest of its full content."""
    return "run_" + content_digest(trace.to_dict()).removeprefix("sha256:")[:16]


def _trust(trace: Trace) -> tuple[Trust, list[str]]:
    hard = [check for check in trace.verification if not check.name.startswith("correctness_eval")]
    evals = [check for check in trace.verification if check.name.startswith("correctness_eval:")]
    failed = [check for check in hard if not check.passed]
    if failed:
        return "unverified", [
            f"{check.name} failed" + (f": {check.message}" if check.message else "")
            for check in failed
        ]
    failed_evals = [check for check in evals if not check.passed]
    if failed_evals:
        return "unverified", [
            f"Approved eval {check.name.split(':', 1)[1]} matched this question and failed: "
            f"{check.message}"
            for check in failed_evals
        ]
    if evals:
        return "verified", [
            f"Approved eval {check.name.split(':', 1)[1]} matched this question and passed."
            for check in evals
        ]
    reasons = [f"All {len(hard)} hard checks passed."]
    if trace.answer.claims and all(claim.evidence for claim in trace.answer.claims):
        reasons.append("Every claim cites result cells.")
    reasons.append("No approved eval case covers this question.")
    return "grounded", reasons


def _ancestors(manifest: Manifest, node: Node) -> list[str]:
    seen: list[str] = []
    pending = list(node.parents)
    while pending:
        parent = pending.pop(0)
        if parent in seen or parent not in manifest.nodes:
            continue
        seen.append(parent)
        pending.extend(manifest.nodes[parent].parents)
    return seen


def _resource(
    manifest: Manifest, node: Node, used: set[str], inherited: Mapping[str, str]
) -> Resource:
    upstream = _ancestors(manifest, node)
    columns = [
        ColumnUse(
            name=name,
            description=column.description or inherited.get(f"{node.unique_id}.{name}", ""),
            data_type=column.physical_type or column.data_type,
            sensitive=is_sensitive(node, name),
        )
        for name, column in node.columns.items()
        if name.casefold() in used
    ]
    return Resource(
        unique_id=node.unique_id,
        relation=node.relation_name or f"{node.schema}.{node.alias}".strip("."),
        description=node.description,
        columns=columns,
        tests=[
            TestStatus(
                unique_id=test.unique_id,
                name=test.name,
                column=test.column_name,
                status=test.status,
            )
            for test in node.tests  # All of them: grain tests matter to any aggregate.
        ],
        upstream=upstream,
        freshness=[
            Freshness(
                source=item.unique_id,
                status=item.status,
                max_loaded_at=item.max_loaded_at,
                snapshotted_at=item.snapshotted_at,
            )
            for source in upstream
            if (item := manifest.freshness.get(source)) is not None
        ],
        exposures=[
            Exposure(
                unique_id=exposure.unique_id,
                name=exposure.label or exposure.name,
                type=exposure.exposure_type,
                owner=exposure.owner,
                maturity=exposure.maturity,
                url=exposure.url,
            )
            for exposure in manifest.exposures
            if node.unique_id in exposure.parents
        ],
    )


def build_receipt(
    trace: Trace,
    manifest: Manifest,
    *,
    recorded: bool = False,
    inherited_descriptions: Mapping[str, str] | None = None,
) -> Receipt:
    """Build a receipt from a trace and the dbt artifacts current when it was answered."""
    payload = trace.to_dict()
    run_id = run_id_for(trace)
    used = {name.casefold() for name in trace.dbt_context.columns}
    trust, reasons = _trust(trace)
    return Receipt(
        run_id=run_id,
        created_at=trace.created_at,
        question=trace.question,
        answer=trace.answer.text,
        trust=trust,
        trust_reasons=reasons,
        claims=[
            Claim(
                text=claim.text,
                evidence=[Evidence(row=item.row, column=item.column) for item in claim.evidence],
            )
            for claim in trace.answer.claims
        ],
        interpretation=Interpretation(
            intent=trace.interpretation.intent,
            metrics=list(trace.interpretation.metrics),
            dimensions=list(trace.interpretation.dimensions),
            start_date=trace.interpretation.start_date,
            end_date=trace.interpretation.end_date,
            assumptions=list(trace.interpretation.assumptions),
        ),
        query=Query(
            dialect=trace.sql.dialect, generated=trace.sql.generated, executed=trace.sql.executed
        ),
        result=Result(row_count=trace.result.row_count, rows=payload["result"]["rows"]),
        resources=[
            _resource(manifest, manifest.nodes[unique_id], used, inherited_descriptions or {})
            for unique_id in trace.dbt_context.selected_nodes
            if unique_id in manifest.nodes
        ],
        checks=[
            Check(name=check.name, passed=check.passed, detail=check.message)
            for check in trace.verification
        ],
        identity=Identity(
            agent=trace.agent,
            agent_digest=_sha256(trace.agent_digest) or "",
            model=trace.model_identity,
            warehouse=trace.warehouse_identity,
            manifest_digest=_sha256(trace.dbt_context.manifest_digest) or "",
            catalog_digest=_sha256(trace.dbt_context.catalog_digest),
            semantic_digest=_sha256(trace.semantic_digest),
            prompt_version=trace.prompt_version,
            runtime_version=trace.runtime_version,
        ),
        replay=Replay(
            inference_fingerprint=_sha256(trace.inference_fingerprint),
            recorded=recorded,
            command=f"tabletalk receipt replay {run_id}" if recorded else None,
        ),
    )


def receipt_json_schema() -> dict[str, JsonValue]:
    """Return the JSON Schema of ``tabletalk.receipt/v1``."""
    schema: dict[str, JsonValue] = Receipt.model_json_schema()
    schema["$id"] = (
        "https://github.com/wtbates99/tabletalk/blob/main/docs/spec/receipt-v1.schema.json"
    )
    schema["title"] = RECEIPT_SCHEMA
    return schema


def write_receipt(directory: Path, receipt: Receipt) -> Path:
    """Write a receipt as ``<directory>/<run_id>.json`` and return the path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{receipt.run_id}.json"
    path.write_text(receipt.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_receipt(directory: Path, run_id: str) -> Receipt:
    """Load a receipt by run ID or by a unique run ID prefix."""
    matches = sorted(directory.glob(f"{run_id}*.json")) if directory.is_dir() else []
    if not matches:
        raise ReceiptError(f"No receipt found for '{run_id}' in {directory}")
    if len(matches) > 1:
        names = ", ".join(path.stem for path in matches[:5])
        raise ReceiptError(f"'{run_id}' matches several receipts: {names}")
    try:
        return Receipt.model_validate_json(matches[0].read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ReceiptError(f"{matches[0]} is not a valid {RECEIPT_SCHEMA} receipt: {exc}") from exc


_TRUST_STYLE: dict[Trust, str] = {
    "verified": "green",
    "grounded": "cyan",
    "unverified": "yellow",
}


def _short(unique_id: str) -> str:
    """Return ``fct_orders`` for a model and ``raw.orders`` for a source."""
    parts = unique_id.split(".")
    return ".".join(parts[-2:]) if parts[0] == "source" else parts[-1]


def _cell(value: JsonValue) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def render_receipt(receipt: Receipt) -> RenderableType:
    """Render the receipt for a terminal."""
    style = _TRUST_STYLE[receipt.trust]
    parts: list[RenderableType] = [
        Panel(
            receipt.answer or "No answer",
            title=f"[bold]{receipt.trust.upper()}[/bold] · {receipt.identity.agent}",
            subtitle=receipt.run_id,
            border_style=style,
        ),
        Text("How this answer was formed", style="bold"),
    ]
    facts = Table.grid(padding=(0, 2))
    facts.add_column(style="dim", no_wrap=True)
    facts.add_column()
    facts.add_row("Question", receipt.question)
    facts.add_row("Trust", Text(" ".join(receipt.trust_reasons), style=style))
    interpretation = receipt.interpretation
    if interpretation.start_date or interpretation.end_date:
        facts.add_row(
            "Window",
            f"{interpretation.start_date or 'open'} → {interpretation.end_date or 'open'}",
        )
    if interpretation.assumptions:
        facts.add_row("Assumptions", "; ".join(interpretation.assumptions))
    for resource in receipt.resources:
        facts.add_row(
            "Resource", Text.assemble((resource.unique_id, "bold"), f"  {resource.relation}")
        )
        for column in resource.columns:
            note = " · sensitive" if column.sensitive else ""
            facts.add_row("", f"{column.name}: {column.description or '(no description)'}{note}")
        if resource.tests:
            ran = [test for test in resource.tests if test.status is not None]
            if ran:
                summary = ", ".join(f"{test.name} {test.status}" for test in ran)
            else:
                summary = f"{len(resource.tests)} defined, not run (no run_results.json)"
            facts.add_row("", f"dbt tests: {summary}")
        if resource.upstream:
            facts.add_row("", "upstream: " + " ← ".join(_short(item) for item in resource.upstream))
        for item in resource.freshness:
            loaded = f", loaded {item.max_loaded_at}" if item.max_loaded_at else ""
            facts.add_row("", f"freshness: {_short(item.source)} {item.status}{loaded}")
        for exposure in resource.exposures:
            owner = f", {exposure.owner}" if exposure.owner else ""
            facts.add_row("", f"feeds: {exposure.name} ({exposure.type}{owner})")
    parts.append(facts)
    if receipt.query.executed:
        parts.append(
            Panel(Syntax(receipt.query.executed, "sql", word_wrap=True), title="Executed SQL")
        )
    if receipt.result.rows:
        rows = Table(show_header=True, header_style="bold")
        columns = list(receipt.result.rows[0])
        for name in columns:
            rows.add_column(name)
        for row in receipt.result.rows[:10]:
            rows.add_row(*(_cell(row.get(name)) for name in columns))
        parts.append(rows)
    failed = [check for check in receipt.checks if not check.passed]
    checks = Text.assemble(
        ("Checks  ", "dim"),
        f"{len(receipt.checks) - len(failed)}/{len(receipt.checks)} passed",
    )
    parts.append(checks)
    for check in failed:
        parts.append(Text(f"  ✗ {check.name}: {check.detail}", style="red"))
    replay = (
        receipt.replay.command
        if receipt.replay.command
        else "not recorded · set inference.cache_policy: normal in tabletalk.yaml"
    )
    parts.append(Text.assemble(("Replay  ", "dim"), replay))
    return Group(*parts)
