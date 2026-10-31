"""Agent-readiness score: a fixed, explainable rubric over dbt metadata, lock, and evals.

Every point comes from a deterministic check that someone can fix. The rubric is
documented in docs/score.md. Change it only together with that page.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from rich.console import Group
from rich.table import Table
from rich.text import Text

from tabletalk.agents import Agent
from tabletalk.evals import EvalSuite
from tabletalk.manifest import Manifest
from tabletalk.semantic_analysis import FindingCode, analyze_semantics
from tabletalk.semantics import SemanticStatement, SemanticStatus
from tabletalk.state import LockFile, snapshot

Grade = Literal["A", "B", "C", "D", "F"]
_FIX_EXAMPLES = 3
_APPROVED = {SemanticStatus.TRUSTED, SemanticStatus.CONFIRMED}


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Criterion(_Model):
    """One rubric line: points earned, the most available, and how to earn the rest."""

    name: str
    points: float
    max_points: int
    detail: str
    fix: str | None = None


class AgentScore(_Model):
    agent: str
    score: int
    grade: Grade
    criteria: list[Criterion]


class Score(_Model):
    """The project score is the mean of its agents' scores."""

    score: int
    grade: Grade
    agents: list[AgentScore]


def grade_for(score: int) -> Grade:
    """Map 0–100 to a letter: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, otherwise F."""
    for floor, letter in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if score >= floor:
            return letter  # type: ignore[return-value]  # Literal narrowed by the table.
    return "F"


def _share(earned: int, total: int, max_points: int) -> float:
    return round(max_points * (earned / total if total else 1.0), 1)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _examples(items: Sequence[str]) -> str:
    shown = ", ".join(items[:_FIX_EXAMPLES])
    return shown + (
        f", and {len(items) - _FIX_EXAMPLES} more" if len(items) > _FIX_EXAMPLES else ""
    )


def score_agent(
    agent: Agent,
    manifest: Manifest,
    statements: Sequence[SemanticStatement],
    suites: Sequence[EvalSuite],
    lock: LockFile | None,
) -> AgentScore:
    """Score one agent's scope. ``statements`` are the agent's approved knowledge."""
    nodes = sorted(agent.resolve(manifest).nodes, key=lambda node: node.unique_id)
    analysis = analyze_semantics(manifest, nodes, statements)
    short = {node.unique_id: node.name for node in nodes}
    criteria: list[Criterion] = []

    undescribed = [short[node.unique_id] for node in nodes if not node.description]
    criteria.append(
        Criterion(
            name="Model descriptions",
            points=_share(len(nodes) - len(undescribed), len(nodes), 10),
            max_points=10,
            detail=f"{len(nodes) - len(undescribed)}/{len(nodes)} models described",
            fix=f"Describe {_examples(undescribed)} in schema.yml" if undescribed else None,
        )
    )

    columns = [(node, column) for node in nodes for column in node.columns.values()]
    bare = [f"{node.name}.{column.name}" for node, column in columns if not column.description]
    criteria.append(
        Criterion(
            name="Column descriptions",
            points=_share(len(columns) - len(bare), len(columns), 20),
            max_points=20,
            detail=f"{len(columns) - len(bare)}/{len(columns)} columns described",
            fix=f"Describe {_examples(bare)}" if bare else None,
        )
    )

    untested = [short[node.unique_id] for node in nodes if not node.tests]
    criteria.append(
        Criterion(
            name="dbt tests",
            points=_share(len(nodes) - len(untested), len(nodes), 15),
            max_points=15,
            detail=f"{len(nodes) - len(untested)}/{len(nodes)} models tested",
            fix=f"Add tests to {_examples(untested)}" if untested else None,
        )
    )

    suspected = [
        gap.id.removeprefix("sensitive:")
        for gap in analysis.gaps
        if gap.code is FindingCode.SUSPECTED_SENSITIVE_DATA
    ]
    exposed = {
        node.unique_id
        for node in nodes
        for reference in suspected
        if reference == node.unique_id or reference.startswith(node.unique_id + ".")
    }
    criteria.append(
        Criterion(
            name="Sensitive data flagged",
            points=_share(len(nodes) - len(exposed), len(nodes), 10),
            max_points=10,
            detail=(
                f"{len(suspected)} column{'' if len(suspected) == 1 else 's'} look sensitive "
                "without meta.sensitive"
                if suspected
                else "No unflagged sensitive-looking columns"
            ),
            fix=(
                "Run 'tabletalk fix' to flag "
                + _examples([reference.split(".", 2)[-1] for reference in suspected])
                if suspected
                else None
            ),
        )
    )

    resolved_time = {
        statement.id
        for statement in statements
        if statement.status in _APPROVED and statement.time_column
    }
    ambiguous = [
        gap
        for gap in analysis.gaps
        if gap.code is FindingCode.CONFLICTING_STATEMENT
        or (
            gap.code is FindingCode.AMBIGUOUS_TIME_BASIS
            and gap.id.rsplit(".", 1)[-1] not in resolved_time
        )
    ]
    candidates = [
        gap
        for gap in analysis.gaps
        if gap.code in {FindingCode.AMBIGUOUS_TIME_BASIS, FindingCode.CONFLICTING_STATEMENT}
    ]
    criteria.append(
        Criterion(
            name="Semantics resolved",
            points=_share(len(candidates) - len(ambiguous), len(candidates), 10),
            max_points=10,
            detail=f"{len(candidates) - len(ambiguous)}/{len(candidates)} ambiguities resolved",
            fix=(
                "Answer with 'tabletalk propose --answer': "
                + _examples([gap.question or gap.message for gap in ambiguous])
                if ambiguous
                else None
            ),
        )
    )

    current = snapshot((agent,), manifest).agents[agent.name]
    locked = lock.agents.get(agent.name) if lock else None
    lock_detail = (
        "Scope matches tabletalk.lock.yaml"
        if locked == current
        else "Scope changed since tabletalk.lock.yaml"
        if locked
        else "Not in tabletalk.lock.yaml"
    )
    criteria.append(
        Criterion(
            name="Scope locked",
            points=15.0 if locked == current else 0.0,
            max_points=15,
            detail=lock_detail,
            fix=None if locked == current else "Review 'tabletalk plan', then run 'tabletalk lock'",
        )
    )

    cases = [case for suite in suites if suite.agent == agent.name for case in suite.cases]
    verified = sum(case.expected_outcome == "answer" and case.verifies_result for case in cases)
    boundaries = sum(case.expected_outcome != "answer" for case in cases)
    eval_fixes = []
    if not verified:
        eval_fixes.append("add a case with reference SQL ('tabletalk eval create')")
    if not boundaries:
        eval_fixes.append("add a rejection or ambiguity case")
    criteria.append(
        Criterion(
            name="Eval coverage",
            points=(15.0 if verified else 0.0) + (5.0 if boundaries else 0.0),
            max_points=20,
            detail=_count(verified, "result-checked case")
            + ", "
            + _count(boundaries, "boundary case"),
            fix=("Evals: " + "; ".join(eval_fixes)) if eval_fixes else None,
        )
    )

    score = round(sum(item.points for item in criteria))
    return AgentScore(agent=agent.name, score=score, grade=grade_for(score), criteria=criteria)


def combine(agents: Sequence[AgentScore]) -> Score:
    """Average agent scores into the project score."""
    score = round(sum(item.score for item in agents) / len(agents)) if agents else 0
    return Score(score=score, grade=grade_for(score), agents=list(agents))


_COLORS: dict[Grade, str] = {
    "A": "brightgreen",
    "B": "green",
    "C": "yellow",
    "D": "orange",
    "F": "red",
}


def badge_json(score: Score) -> str:
    """Return a shields.io endpoint document for the project score."""
    return (
        json.dumps(
            {
                "schemaVersion": 1,
                "label": "agent-ready",
                "message": f"{score.grade} · {score.score}",
                "color": _COLORS[score.grade],
            },
            indent=2,
        )
        + "\n"
    )


def write_badge(path: Path, score: Score) -> None:
    """Write the shields.io endpoint document to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(badge_json(score))


def render_score(score: Score) -> Group:
    """Render the score, each agent's rubric, and the fixes worth the most points."""
    parts: list[Table | Text] = [
        Text.assemble(
            ("Agent-ready: ", "bold"), (f"{score.grade} · {score.score}/100", "bold cyan")
        )
    ]
    for agent in score.agents:
        table = Table(title=f"{agent.agent}  {agent.grade} · {agent.score}", title_justify="left")
        table.add_column("Criterion")
        table.add_column("Points", justify="right")
        table.add_column("Detail")
        for item in agent.criteria:
            full = item.points == item.max_points
            style = "green" if full else "yellow" if item.points else "red"
            table.add_row(
                item.name, Text(f"{item.points:g}/{item.max_points}", style=style), item.detail
            )
        parts.append(table)
        fixes = sorted(
            (item for item in agent.criteria if item.fix),
            key=lambda item: item.points - item.max_points,
        )
        for item in fixes:
            parts.append(Text(f"  +{item.max_points - item.points:g}  {item.fix}"))
    return Group(*parts)
