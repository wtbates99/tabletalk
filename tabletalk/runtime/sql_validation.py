"""Fail-closed SQL AST validation and compiled-agent scope enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from tabletalk.domain import ErrorCode, RuntimeStage, TableTalkError


@dataclass(frozen=True)
class SQLScope:
    relations: tuple[str, ...]
    columns: tuple[tuple[str, tuple[str, ...]], ...]
    approved_joins: tuple[tuple[str, str, str, str], ...] = ()

    @classmethod
    def from_artifact(cls, payload: dict[str, Any]) -> SQLScope:
        relations = payload.get("agent", {}).get("relations", [])
        names = []
        columns = []
        for relation in relations:
            if not isinstance(relation, dict) or not relation.get("name"):
                continue
            name = str(relation["name"])
            names.append(name)
            column_names = tuple(
                sorted(
                    str(column["name"])
                    for column in relation.get("columns", [])
                    if isinstance(column, dict) and column.get("name")
                )
            )
            columns.append((name, column_names))
        approved_joins = []
        relationships = payload.get("agent", {}).get("relationships", [])
        for relationship in relationships:
            if not isinstance(relationship, dict):
                continue
            source = str(relationship.get("source") or "")
            target = str(relationship.get("target") or "")
            if "." not in source or "." not in target:
                continue
            source_relation, source_column = source.rsplit(".", 1)
            target_relation, target_column = target.rsplit(".", 1)
            approved_joins.append(
                (
                    source_relation,
                    source_column,
                    target_relation,
                    target_column,
                )
            )
        return cls(
            tuple(sorted(names)),
            tuple(sorted(columns)),
            tuple(sorted(approved_joins)),
        )


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    relations: tuple[str, ...]
    columns: tuple[str, ...]


def _relation_candidates(table: exp.Table) -> tuple[str, ...]:
    parts = [str(value) for value in (table.catalog, table.db, table.name) if value]
    candidates = {".".join(parts).lower(), table.name.lower()}
    if len(parts) >= 2:
        candidates.add(".".join(parts[-2:]).lower())
    return tuple(sorted(candidate for candidate in candidates if candidate))


def _resolved_column(
    column: exp.Column,
    aliases: dict[str, str],
    allowed_names: dict[str, str],
) -> tuple[str, str] | None:
    qualifier = column.table.lower()
    if not qualifier:
        return None
    relation = aliases.get(qualifier) or allowed_names.get(qualifier)
    if relation is None:
        return None
    return relation.lower(), column.name.lower()


def validate_sql(
    sql: str,
    *,
    dialect: str | None = None,
    scope: SQLScope | None = None,
    max_rows: int | None = None,
) -> ValidatedSQL:
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except ParseError as error:
        raise TableTalkError(
            ErrorCode.SQL_INVALID,
            RuntimeStage.VALIDATION,
            "Generated SQL could not be parsed.",
            details={"dialect": dialect or "default"},
        ) from error
    if len(statements) != 1 or statements[0] is None:
        raise TableTalkError(
            ErrorCode.SQL_INVALID,
            RuntimeStage.VALIDATION,
            "Exactly one SQL statement is required.",
        )
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise TableTalkError(
            ErrorCode.SQL_NOT_READ_ONLY,
            RuntimeStage.VALIDATION,
            "Only read-only query statements may execute.",
        )
    if statement.find(exp.DML) or statement.find(exp.DDL):
        raise TableTalkError(
            ErrorCode.SQL_NOT_READ_ONLY,
            RuntimeStage.VALIDATION,
            "SQL containing write or definition operations may not execute.",
        )
    if max_rows is not None:
        if (
            not isinstance(max_rows, int)
            or isinstance(max_rows, bool)
            or max_rows < 1
        ):
            raise ValueError("max_rows must be a positive integer.")
        limit = statement.args.get("limit")
        if limit is None:
            statement = statement.limit(max_rows)
        else:
            limit_expression = limit.expression
            if not isinstance(limit_expression, exp.Literal) or not limit_expression.is_int:
                raise TableTalkError(
                    ErrorCode.SQL_INVALID,
                    RuntimeStage.VALIDATION,
                    "Query LIMIT must be a fixed integer.",
                )
            if int(limit_expression.this) > max_rows:
                statement = statement.limit(max_rows, copy=False)

    cte_names = {cte.alias_or_name.lower() for cte in statement.ctes}
    allowed_names: dict[str, str] = {}
    allowed_columns: dict[str, set[str]] = {}
    if scope is not None:
        for relation in scope.relations:
            normalized = relation.lower()
            allowed_names[normalized] = relation
            allowed_names[normalized.rsplit(".", 1)[-1]] = relation
        allowed_columns = {
            relation: {column.lower() for column in columns} for relation, columns in scope.columns
        }

    referenced_relations: set[str] = set()
    aliases: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        if table.name.lower() in cte_names:
            continue
        candidates = _relation_candidates(table)
        canonical = next(
            (allowed_names[candidate] for candidate in candidates if candidate in allowed_names),
            None,
        )
        if scope is not None and canonical is None:
            raise TableTalkError(
                ErrorCode.SQL_OUT_OF_SCOPE,
                RuntimeStage.VALIDATION,
                f"SQL references undeclared relation '{table.sql()}'.",
                details={"relation": table.sql()},
            )
        canonical = canonical or table.sql()
        referenced_relations.add(canonical)
        aliases[table.alias_or_name.lower()] = canonical
        aliases[table.name.lower()] = canonical

    if scope is not None:
        approved_edges = {
            frozenset(
                (
                    (left_relation.lower(), left_column.lower()),
                    (right_relation.lower(), right_column.lower()),
                )
            )
            for (
                left_relation,
                left_column,
                right_relation,
                right_column,
            ) in scope.approved_joins
        }
        for join in statement.find_all(exp.Join):
            joined_table = join.this
            if not isinstance(joined_table, exp.Table):
                raise TableTalkError(
                    ErrorCode.SQL_OUT_OF_SCOPE,
                    RuntimeStage.VALIDATION,
                    "Derived or dynamic join targets are not approved.",
                )
            if joined_table.name.lower() in cte_names:
                continue
            on = join.args.get("on")
            if on is None:
                raise TableTalkError(
                    ErrorCode.SQL_OUT_OF_SCOPE,
                    RuntimeStage.VALIDATION,
                    "Cross joins and joins without an approved condition are forbidden.",
                )
            join_edges = set()
            expressions = [on, *on.find_all(exp.EQ)]
            for expression in expressions:
                if not isinstance(expression, exp.EQ):
                    continue
                left = expression.left
                right = expression.right
                if not isinstance(left, exp.Column) or not isinstance(
                    right, exp.Column
                ):
                    continue
                left_column = _resolved_column(left, aliases, allowed_names)
                right_column = _resolved_column(right, aliases, allowed_names)
                if left_column and right_column:
                    join_edges.add(frozenset((left_column, right_column)))
            if not join_edges.intersection(approved_edges):
                raise TableTalkError(
                    ErrorCode.SQL_OUT_OF_SCOPE,
                    RuntimeStage.VALIDATION,
                    "SQL uses a join path that is not approved by the applied Agent.",
                    details={"join": join.sql()},
                )

    referenced_columns: set[str] = set()
    for column in statement.find_all(exp.Column):
        if column.is_star:
            continue
        column_name = column.name.lower()
        qualifier = column.table.lower()
        if qualifier in cte_names:
            continue
        if scope is not None and qualifier:
            resolved_relation = aliases.get(qualifier) or allowed_names.get(qualifier)
            if resolved_relation is not None and column_name not in allowed_columns.get(
                resolved_relation, set()
            ):
                raise TableTalkError(
                    ErrorCode.SQL_OUT_OF_SCOPE,
                    RuntimeStage.VALIDATION,
                    f"SQL references undeclared column '{column.sql()}'.",
                    details={"column": column.sql()},
                )
        elif scope is not None and not cte_names:
            if not any(column_name in values for values in allowed_columns.values()):
                raise TableTalkError(
                    ErrorCode.SQL_OUT_OF_SCOPE,
                    RuntimeStage.VALIDATION,
                    f"SQL references undeclared column '{column.sql()}'.",
                    details={"column": column.sql()},
                )
        referenced_columns.add(column.sql())

    return ValidatedSQL(
        sql=statement.sql(dialect=dialect),
        relations=tuple(sorted(referenced_relations)),
        columns=tuple(sorted(referenced_columns)),
    )
