"""SQL parsing, scope enforcement, safety checks, and usage derivation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from tabletalk.manifest import Manifest, ManifestError, Node
from tabletalk.traces import Verification


class SQLValidationError(ValueError):
    def __init__(self, message: str, checks: Iterable[Verification] = ()) -> None:
        super().__init__(message)
        self.checks = tuple(checks)


_FORBIDDEN_EXPRESSIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Merge,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
)

_FORBIDDEN_FUNCTIONS = {
    "load_extension",
    "nextval",
    "pg_read_file",
    "pg_ls_dir",
    "read_csv",
    "read_csv_auto",
    "read_json",
    "read_parquet",
    "setval",
    "write_csv",
}


@dataclass(frozen=True)
class ValidatedSQL:
    generated: str
    executed: str
    nodes: tuple[Node, ...]
    columns: tuple[str, ...]
    checks: tuple[Verification, ...]


def validate_sql(
    sql: str,
    manifest: Manifest,
    scope: Iterable[Node],
    *,
    dialect: str,
    max_rows: int,
    allow_sensitive: Iterable[str] = (),
) -> ValidatedSQL:
    checks: list[Verification] = []
    try:
        statements = parse(sql, read=dialect)
    except (ParseError, ValueError) as exc:
        raise SQLValidationError(f"SQL could not be parsed: {exc}") from exc
    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL query statement is required")
    tree = statements[0]
    if not isinstance(tree, (exp.Query, exp.Union, exp.Intersect, exp.Except)):
        raise SQLValidationError("Only a read-only query statement is allowed")
    if any(tree.find(kind) is not None for kind in _FORBIDDEN_EXPRESSIONS):
        raise SQLValidationError("SQL contains a forbidden write or command operation")
    checks.append(Verification("read_only", True))

    for function in tree.find_all(exp.Func):
        function_name = function.name or function.sql_name()
        if function_name.lower() in _FORBIDDEN_FUNCTIONS:
            raise SQLValidationError(f"Function '{function_name}' is forbidden")
    checks.append(Verification("forbidden_functions", True))

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    used: list[Node] = []
    for table in tree.find_all(exp.Table):
        if table.name.lower() in cte_names:
            continue
        parts = [part for part in (table.catalog, table.db, table.name) if part]
        try:
            node = manifest.resolve_relation(parts, scope)
        except ManifestError as exc:
            raise SQLValidationError(str(exc)) from exc
        if node not in used:
            used.append(node)
    if not used:
        raise SQLValidationError("SQL must query at least one manifest-backed relation")
    checks.append(Verification("resources_in_scope", True))
    checks.append(Verification("relations_unambiguous", True))

    if len(used) > 1:
        joins = tuple(tree.find_all(exp.Join))
        if not joins or any(
            join.args.get("on") is None and not join.args.get("using") for join in joins
        ):
            raise SQLValidationError("Every multi-relation query requires explicit join conditions")
    checks.append(Verification("join_conditions", True))

    aliases: dict[str, Node] = {}
    derived_scopes: dict[str, set[str]] = {}
    cte_columns = {
        cte.alias_or_name.lower(): {name.lower() for name in cte.this.named_selects}
        for cte in tree.find_all(exp.CTE)
    }
    for table in tree.find_all(exp.Table):
        if table.name.lower() in cte_names:
            derived_scopes[table.alias_or_name.lower()] = cte_columns.get(table.name.lower(), set())
            continue
        parts = [part for part in (table.catalog, table.db, table.name) if part]
        try:
            node = manifest.resolve_relation(parts, used)
        except ManifestError:
            continue
        aliases[table.alias_or_name.lower()] = node
    for subquery in tree.find_all(exp.Subquery):
        if subquery.alias:
            derived_scopes[subquery.alias.lower()] = {
                name.lower() for name in subquery.this.named_selects
            }

    columns: set[str] = set()
    derived_aliases = {
        expression.alias
        for select in tree.find_all(exp.Select)
        for expression in select.expressions
        if expression.alias and not isinstance(expression, exp.Column)
    }
    for column in tree.find_all(exp.Column):
        name = column.name
        if name == "*":
            continue
        if column.table and column.table.lower() in derived_scopes:
            if name.lower() not in derived_scopes[column.table.lower()]:
                raise SQLValidationError(
                    f"Column '{column.sql()}' does not exist in derived relation '{column.table}'"
                )
            continue
        candidates = (
            [aliases[column.table.lower()]]
            if column.table and column.table.lower() in aliases
            else used
        )
        matches = [
            node for node in candidates if name.lower() in {item.lower() for item in node.columns}
        ]
        if not matches and not column.table and name in derived_aliases:
            continue
        if not matches:
            raise SQLValidationError(
                f"Column '{column.sql()}' does not exist on a selected dbt resource"
            )
        if not column.table and len(matches) > 1:
            raise SQLValidationError(
                f"Unqualified column '{name}' is ambiguous across selected dbt resources"
            )
        columns.add(name)
    checks.append(Verification("columns_exist", True))

    allowed_sensitive = set(allow_sensitive)
    for node in used:
        if node.meta.get("sensitive") is True and node.unique_id not in allowed_sensitive:
            raise SQLValidationError(
                f"Sensitive model '{node.unique_id}' requires explicit permission"
            )
        sensitive_columns = set(node.meta.get("sensitive_columns") or ())
        sensitive_columns.update(
            column.name for column in node.columns.values() if column.meta.get("sensitive") is True
        )
        denied = sensitive_columns.intersection(columns) - allowed_sensitive
        if denied:
            raise SQLValidationError(
                f"Sensitive columns require explicit permission: {', '.join(sorted(denied))}"
            )
    checks.append(Verification("sensitive_access", True))

    executed_tree = tree.copy()
    limit = executed_tree.args.get("limit")
    if limit is None:
        executed_tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    else:
        value = limit.expression
        if isinstance(value, exp.Literal) and value.is_int and int(value.this) > max_rows:
            limit.set("expression", exp.Literal.number(max_rows))
    executed = executed_tree.sql(dialect=dialect)
    checks.append(Verification("row_limit", True, f"maximum {max_rows} rows"))
    return ValidatedSQL(sql, executed, tuple(used), tuple(sorted(columns)), tuple(checks))
