"""Deterministic refusal rules for missing, inferred, and conflicting meaning."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tabletalk.agents import ResolvedAgent
from tabletalk.manifest import Node
from tabletalk.semantics import SemanticStatement, SemanticStatus
from tabletalk.traces import Interpretation


class SemanticSupportError(ValueError):
    """Trusted information cannot support a proposed answer."""


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.replace("_", " ").casefold()))


def _mentions(question: str, value: str) -> bool:
    wanted = _words(value)
    return bool(wanted) and wanted <= _words(question)


def _suspected_sensitive(node: Node, column_name: str) -> bool:
    column = node.columns[column_name]
    text = f"{column.name} {column.description}".replace("_", " ").casefold()
    terms = (
        "customer id",
        "email",
        "phone",
        "personally identifiable",
        "sensitive",
        "social security",
        "ssn",
    )
    return column.meta.get("sensitive") is True or any(term in text for term in terms)


@dataclass(frozen=True)
class SemanticGuard:
    agent: ResolvedAgent
    statements: tuple[SemanticStatement, ...] = ()

    def validate(
        self,
        question: str,
        interpretation: Interpretation,
        nodes: tuple[Node, ...],
        columns: tuple[str, ...],
    ) -> None:
        """Reject a proposed answer when trusted meaning is insufficient."""
        context = " ".join(
            (
                question,
                interpretation.intent,
                *interpretation.metrics,
                *interpretation.dimensions,
            )
        )
        question_words = _words(question)
        for measure_term in (
            "amount",
            "cost",
            "margin",
            "quantity",
            "revenue",
            "sales",
        ):
            if measure_term not in question_words:
                continue
            measure_choices = sorted(
                {
                    column.name
                    for node in nodes
                    for column in node.columns.values()
                    if measure_term in _words(column.name)
                }
            )
            if len(measure_choices) > 1 and not any(
                _mentions(question, choice) for choice in measure_choices
            ):
                raise SemanticSupportError(
                    f"The {measure_term} basis is ambiguous. "
                    f"Name one field: {', '.join(measure_choices)}"
                )
        for statement in self.statements:
            if not _mentions(context, statement.id):
                continue
            if statement.status is SemanticStatus.CONFLICTING:
                raise SemanticSupportError(
                    f"Semantic statement '{statement.id}' has conflicting approved meanings"
                )
            if statement.status is SemanticStatus.MISSING:
                raise SemanticSupportError(
                    f"Semantic statement '{statement.id}' has missing business meaning"
                )
            if statement.status is SemanticStatus.INFERRED:
                raise SemanticSupportError(
                    f"Semantic statement '{statement.id}' is inferred and untrusted"
                )

        allowed_sensitive = set(self.agent.source.allow_sensitive)
        selected_columns: list[tuple[Node, str]] = []
        for column_name in columns:
            for node in nodes:
                if column_name in node.columns:
                    selected_columns.append((node, column_name))
        for node, column_name in selected_columns:
            column = node.columns[column_name]
            reference = f"{node.unique_id}.{column_name}"
            trusted_statement = next(
                (
                    statement
                    for statement in self.statements
                    if statement.can_support_answer
                    and (
                        statement.id == column_name
                        or column_name in (statement.expression or "")
                        or statement.time_column == column_name
                    )
                ),
                None,
            )
            if not column.description and trusted_statement is None:
                raise SemanticSupportError(
                    f"Column '{reference}' has no trusted business description"
                )
            if (
                _suspected_sensitive(node, column_name)
                and column_name not in allowed_sensitive
                and reference not in allowed_sensitive
                and node.unique_id not in allowed_sensitive
            ):
                raise SemanticSupportError(
                    f"Column '{reference}' can contain sensitive data and has no approval"
                )

        for node in nodes:
            time_columns = tuple(
                column.name
                for column in node.columns.values()
                if any(term in column.name.casefold() for term in ("_at", "_date", "date_"))
            )
            used_time = tuple(column for column in columns if column in time_columns)
            if len(time_columns) < 2 or not used_time:
                continue
            approved_time = {
                statement.time_column
                for statement in self.statements
                if statement.can_support_answer and statement.time_column
            }
            unexplained = [
                column
                for column in used_time
                if not _mentions(question, column) and column not in approved_time
            ]
            if unexplained:
                time_choices = ", ".join(sorted(time_columns))
                raise SemanticSupportError(
                    f"The time basis is ambiguous. Name one field: {time_choices}"
                )
