"""``tabletalk plan``: how a dbt change alters what each agent can see and which evals it risks.

The diff is a pure function of two lock snapshots and the eval-case footprints, so it
runs offline, without a warehouse or a model, and is cheap enough for every pull request.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from rich.console import Group, RenderableType
from rich.text import Text
from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError

from tabletalk.evals import EvalError, EvalSuite, render_reference_sql
from tabletalk.manifest import Manifest, ManifestError
from tabletalk.state import (
    EMPTY_DESCRIPTION,
    LOCK_FILENAME,
    AgentState,
    ColumnState,
    LockFile,
    ResourceState,
)

ChangeKind = Literal[
    "agent_added",
    "agent_removed",
    "agent_changed",
    "resource_added",
    "resource_removed",
    "resource_logic_changed",
    "column_added",
    "column_removed",
    "column_renamed",
    "column_type_changed",
    "description_changed",
    "sensitivity_changed",
    "tests_changed",
]
# breaks:  something an answer or eval depends on is gone.
# expands: the agent can now see more; this needs security review.
# changes: behavior may shift; evals that touch it should be rerun.
Impact = Literal["breaks", "expands", "changes"]

_SYMBOL: dict[Impact, tuple[str, str]] = {
    "expands": ("+", "cyan"),
    "breaks": ("-", "red"),
    "changes": ("~", "yellow"),
}
_SUMMARY_LABELS: tuple[tuple[Impact, str], ...] = (
    ("breaks", "breaking"),
    ("expands", "expanding scope"),
    ("changes", "changing behavior"),
)
_AT_RISK_KINDS = {
    "resource_removed",
    "resource_logic_changed",
    "column_removed",
    "column_renamed",
    "column_type_changed",
    "description_changed",
    "sensitivity_changed",
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Change(_Frozen):
    """One difference between the locked and current scope of one agent."""

    agent: str
    kind: ChangeKind
    impact: Impact
    target: str
    detail: str
    resource: str | None = None
    column: str | None = None
    sensitive: bool = False


class CaseFootprint(_Frozen):
    """The dbt resources and columns one eval case depends on."""

    suite: str
    agent: str
    case: str
    models: frozenset[str]
    columns: frozenset[str]


class AffectedCase(_Frozen):
    """An eval case whose dependencies changed."""

    suite: str
    case: str
    agent: str
    reasons: list[str]


class Plan(_Frozen):
    """The full plan: every change and every eval case it puts at risk."""

    changes: list[Change]
    affected_cases: list[AffectedCase]

    @property
    def has_changes(self) -> bool:
        """Return whether any agent's scope differs from the lock."""
        return bool(self.changes)

    def count(self, impact: Impact) -> int:
        """Return how many changes have the given impact."""
        return sum(change.impact == impact for change in self.changes)


def case_footprints(
    suites: Iterable[EvalSuite], manifest: Manifest, project_root: Path
) -> list[CaseFootprint]:
    """Derive each case's dependencies from its expectations and its reference SQL."""
    footprints: list[CaseFootprint] = []
    for suite in suites:
        for case in suite.cases:
            models = set(case.required_models)
            columns = {name.casefold() for name in case.required_columns}
            sql = case.reference_sql
            if case.reference_file:
                path = project_root / case.reference_file
                sql = path.read_text(encoding="utf-8") if path.is_file() else None
            if sql:
                ref_models, ref_columns = _sql_footprint(sql, manifest)
                models |= ref_models
                columns |= ref_columns
            footprints.append(
                CaseFootprint(
                    suite=suite.name,
                    agent=suite.agent,
                    case=case.name,
                    models=frozenset(models),
                    columns=frozenset(columns),
                )
            )
    return footprints


def _sql_footprint(sql: str, manifest: Manifest) -> tuple[set[str], set[str]]:
    """Return the manifest resources and column names a reference query reads."""
    try:
        tree = parse_one(render_reference_sql(sql, manifest))
    except (EvalError, SqlglotError):
        return set(), set()
    models: set[str] = set()
    for table in tree.find_all(exp.Table):
        parts = [part for part in (table.catalog, table.db, table.name) if part]
        try:
            models.add(manifest.resolve_relation(parts).unique_id)
        except ManifestError:
            continue  # A CTE name or a relation outside the manifest.
    columns = {column.name.casefold() for column in tree.find_all(exp.Column) if column.name}
    return models, columns


def compute_plan(
    locked: LockFile, current: LockFile, footprints: Sequence[CaseFootprint] = ()
) -> Plan:
    """Diff two lock snapshots and map every change onto the eval cases it puts at risk."""
    changes: list[Change] = []
    for name in sorted(set(locked.agents) | set(current.agents)):
        before, after = locked.agents.get(name), current.agents.get(name)
        if before is None and after is not None:
            sensitive = _sensitive_columns(after.resources.values())
            changes.append(
                Change(
                    agent=name,
                    kind="agent_added",
                    impact="expands",
                    target=name,
                    detail=_count(len(after.resources), "resource") + _sensitive_note(sensitive),
                    sensitive=bool(sensitive),
                )
            )
        elif before is not None and after is None:
            changes.append(
                Change(
                    agent=name,
                    kind="agent_removed",
                    impact="breaks",
                    target=name,
                    detail="agent definition removed",
                )
            )
        elif before is not None and after is not None:
            changes.extend(_agent_changes(name, before, after))
    return Plan(changes=changes, affected_cases=_affected(changes, footprints))


def _agent_changes(agent: str, before: AgentState, after: AgentState) -> list[Change]:
    changes: list[Change] = []
    if before.agent_digest != after.agent_digest:
        changes.append(
            Change(
                agent=agent,
                kind="agent_changed",
                impact="changes",
                target=agent,
                detail="agent definition changed",
            )
        )
    for unique_id in sorted(set(before.resources) | set(after.resources)):
        old, new = before.resources.get(unique_id), after.resources.get(unique_id)
        if old is None and new is not None:
            sensitive = _sensitive_columns([new])
            changes.append(
                Change(
                    agent=agent,
                    kind="resource_added",
                    impact="expands",
                    target=unique_id,
                    detail="scope expands · "
                    + _count(len(new.columns), "column")
                    + _sensitive_note(sensitive),
                    resource=unique_id,
                    sensitive=bool(sensitive),
                )
            )
        elif old is not None and new is None:
            changes.append(
                Change(
                    agent=agent,
                    kind="resource_removed",
                    impact="breaks",
                    target=unique_id,
                    detail="resource left scope",
                    resource=unique_id,
                )
            )
        elif old is not None and new is not None:
            changes.extend(_resource_changes(agent, unique_id, old, new))
    return changes


def _resource_changes(
    agent: str, unique_id: str, old: ResourceState, new: ResourceState
) -> list[Change]:
    def change(
        kind: ChangeKind,
        impact: Impact,
        detail: str,
        column: str | None = None,
        sensitive: bool = False,
    ) -> Change:
        return Change(
            agent=agent,
            kind=kind,
            impact=impact,
            target=f"{new.name}.{column}" if column else new.name,
            detail=detail,
            resource=unique_id,
            column=column,
            sensitive=sensitive,
        )

    changes: list[Change] = []
    if old.checksum != new.checksum:
        changes.append(change("resource_logic_changed", "changes", "model SQL changed"))
    if old.description_digest != new.description_digest:
        changes.append(change("description_changed", "changes", "description changed"))
    renames = _renames(old.columns, new.columns)
    for previous, renamed in sorted(renames.items()):
        changes.append(change("column_renamed", "breaks", f"renamed to {renamed}", previous))
    for column in sorted(set(old.columns) | set(new.columns)):
        if column in renames or column in renames.values():
            continue
        before, after = old.columns.get(column), new.columns.get(column)
        if before is None and after is not None:
            note = " · sensitive" if after.sensitive else ""
            changes.append(
                change("column_added", "expands", "column added" + note, column, after.sensitive)
            )
        elif before is not None and after is None:
            changes.append(change("column_removed", "breaks", "column removed", column))
        elif before is not None and after is not None:
            if before.data_type != after.data_type:
                changes.append(
                    change(
                        "column_type_changed",
                        "changes",
                        f"type {before.data_type or '?'} → {after.data_type or '?'}",
                        column,
                    )
                )
            if before.description_digest != after.description_digest:
                changes.append(
                    change("description_changed", "changes", "description changed", column)
                )
            if before.sensitive != after.sensitive:
                if after.sensitive:
                    changes.append(
                        change(
                            "sensitivity_changed",
                            "changes",
                            "now sensitive · answers using it will be refused",
                            column,
                            True,
                        )
                    )
                else:
                    changes.append(
                        change(
                            "sensitivity_changed",
                            "expands",
                            "no longer marked sensitive",
                            column,
                        )
                    )
    added, removed = set(new.tests) - set(old.tests), set(old.tests) - set(new.tests)
    if added or removed:
        parts = [f"+{len(added)}" if added else "", f"−{len(removed)}" if removed else ""]
        detail = " ".join(part for part in parts if part) + " dbt tests"
        changes.append(change("tests_changed", "changes", detail))
    return changes


def _renames(old: dict[str, ColumnState], new: dict[str, ColumnState]) -> dict[str, str]:
    """Pair removed with added columns that keep their type and documented meaning.

    A pair counts only when the match is one-to-one and the description is non-empty,
    so two undocumented integer columns are never mistaken for a rename.
    """

    def signature(column: ColumnState) -> tuple[str | None, str]:
        return column.data_type, column.description_digest

    removed = {name: old[name] for name in old.keys() - new.keys()}
    added = {name: new[name] for name in new.keys() - old.keys()}
    pairs: dict[str, str] = {}
    for name, column in removed.items():
        if column.description_digest == EMPTY_DESCRIPTION:
            continue
        candidates = [
            other for other, item in added.items() if signature(item) == signature(column)
        ]
        rivals = [other for other, item in removed.items() if signature(item) == signature(column)]
        if len(candidates) == 1 and len(rivals) == 1:
            pairs[name] = candidates[0]
    return pairs


def _affected(changes: Sequence[Change], footprints: Sequence[CaseFootprint]) -> list[AffectedCase]:
    reasons: dict[tuple[str, str, str], list[str]] = {}
    for footprint in footprints:
        for item in changes:
            if item.agent != footprint.agent:
                continue
            hit = item.kind == "agent_removed" or (
                item.kind in _AT_RISK_KINDS and _touches(item, footprint)
            )
            if hit:
                key = (footprint.suite, footprint.case, footprint.agent)
                reasons.setdefault(key, []).append(f"{item.target}: {item.detail}")
    return [
        AffectedCase(suite=suite, case=case, agent=agent, reasons=found)
        for (suite, case, agent), found in sorted(reasons.items())
    ]


def _touches(item: Change, footprint: CaseFootprint) -> bool:
    if item.column is None:
        return item.resource in footprint.models
    in_models = not footprint.models or item.resource in footprint.models
    return in_models and item.column.casefold() in footprint.columns


def _sensitive_columns(resources: Iterable[ResourceState]) -> list[str]:
    return sorted(
        name
        for resource in resources
        for name, column in resource.columns.items()
        if column.sensitive
    )


def _sensitive_note(columns: Sequence[str]) -> str:
    return f" · sensitive: {', '.join(columns)}" if columns else ""


def _count(value: int, noun: str) -> str:
    return f"{value} {noun}{'' if value == 1 else 's'}"


def render_plan(plan: Plan) -> RenderableType:
    """Render the plan as Terraform-style terminal output."""
    if not plan.has_changes:
        return Text.assemble(
            ("No changes. ", "bold green"),
            f"Every agent matches {LOCK_FILENAME}.",
        )
    lines: list[RenderableType] = []
    by_agent: dict[str, list[Change]] = {}
    for item in plan.changes:
        by_agent.setdefault(item.agent, []).append(item)
    width = max(len(item.target) for item in plan.changes) + 2
    for agent, items in by_agent.items():
        lines.append(Text.assemble(("agent ", "dim"), (agent, "bold")))
        for item in items:
            symbol, color = _SYMBOL[item.impact]
            detail_style = "bold magenta" if item.sensitive else "dim"
            lines.append(
                Text.assemble(
                    f"  {symbol} ",
                    (item.target.ljust(width), color),
                    (item.detail, detail_style),
                )
            )
        lines.append(Text(""))
    if plan.affected_cases:
        lines.append(Text("evals at risk", style="bold"))
        for case in plan.affected_cases:
            lines.append(Text.assemble(("  ! ", "red"), f"{case.suite} › {case.case}"))
            for reason in case.reasons:
                lines.append(Text(f"      {reason}", style="dim"))
        lines.append(Text(""))
    summary = ", ".join(
        f"{plan.count(impact)} {label}" for impact, label in _SUMMARY_LABELS if plan.count(impact)
    )
    lines.append(
        Text.assemble(
            ("Plan: ", "bold"),
            f"{summary} across {_count(len(by_agent), 'agent')}. "
            f"{_count(len(plan.affected_cases), 'eval case')} at risk.",
        )
    )
    lines.append(
        Text(f"Review the diff, then run `tabletalk lock` to accept it into {LOCK_FILENAME}.")
    )
    return Group(*lines)
