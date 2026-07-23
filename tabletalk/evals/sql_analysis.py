"""SQL AST inspection used by deterministic structure and safety metrics."""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import expressions as exp


@dataclass
class SQLAnalysis:
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    join_count: int = 0
    cross_join_count: int = 0
    cte_count: int = 0
    parse_errors: list[str] = field(default_factory=list)


def _qualified_table(table: exp.Table) -> str:
    parts = [table.catalog, table.db, table.name]
    return ".".join(part for part in parts if part).lower()


def _qualified_column(column: exp.Column) -> str:
    parts = [column.catalog, column.db, column.table, column.name]
    return ".".join(part for part in parts if part).lower()


def analyze_sql(statements: list[str], dialect: str | None = None) -> SQLAnalysis:
    """Parse one or more statements and return normalized structural facts."""
    analysis = SQLAnalysis()
    for statement in statements:
        try:
            trees = sqlglot.parse(statement, read=dialect)
        except Exception as exc:
            analysis.parse_errors.append(str(exc))
            continue

        for tree in trees:
            if tree is None:
                continue
            analysis.tables.extend(
                table
                for table in (_qualified_table(node) for node in tree.find_all(exp.Table))
                if table
            )
            analysis.columns.extend(
                column
                for column in (_qualified_column(node) for node in tree.find_all(exp.Column))
                if column
            )
            joins = list(tree.find_all(exp.Join))
            analysis.join_count += len(joins)
            analysis.cross_join_count += sum(
                1 for join in joins if str(join.args.get("kind") or "").upper() == "CROSS"
            )
            analysis.cte_count += len(list(tree.find_all(exp.CTE)))

    analysis.tables = sorted(set(analysis.tables))
    analysis.columns = sorted(set(analysis.columns))
    return analysis


def identifier_matches(actual: str, expected: str) -> bool:
    """Match unqualified expectations against qualified SQL identifiers."""
    actual_normalized = actual.strip('"`[]').lower()
    expected_normalized = expected.strip('"`[]').lower()
    return actual_normalized == expected_normalized or actual_normalized.endswith(
        f".{expected_normalized}"
    )


def matching_identifiers(actual: list[str], expected: list[str]) -> list[str]:
    """Return expected identifiers found in *actual*, preserving expectation spelling."""
    return [
        item
        for item in expected
        if any(identifier_matches(candidate, item) for candidate in actual)
    ]
