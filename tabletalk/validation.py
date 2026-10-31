"""SQL parsing, scope enforcement, safety checks, and usage derivation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.scope import Scope, ScopeType, traverse_scope

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
    # For check_result_columns: casefolded sensitive names this query may not return,
    # and, when the outer query projects a star, every name it may return.
    denied_outputs: tuple[str, ...] = ()
    star_outputs: tuple[str, ...] | None = None


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
    scope_nodes = tuple(scope)
    if not scope_nodes:
        raise SQLValidationError("The agent scope is empty, so no relation is allowed")
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

    try:
        scopes = traverse_scope(tree)
    except OptimizeError as exc:
        raise SQLValidationError(f"SQL scopes could not be resolved: {exc}") from exc
    resolver = _Resolver(manifest, scope_nodes, Dialect.get_or_raise(dialect))
    for current in scopes:
        resolver.resolve_relations(current)
    resolver.require_every_table_resolved(tree, scopes)
    used = resolver.nodes
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

    for current in scopes:
        resolver.resolve_columns(current)
    checks.append(Verification("columns_exist", True))

    allowed_sensitive = {item.casefold() for item in allow_sensitive}
    denied_outputs: set[str] = set()
    for node in used:
        if (
            node.meta.get("sensitive") is True
            and node.unique_id.casefold() not in allowed_sensitive
        ):
            raise SQLValidationError(
                f"Sensitive model '{node.unique_id}' requires explicit permission"
            )
        sensitive = {str(name).casefold() for name in node.meta.get("sensitive_columns") or ()}
        sensitive.update(
            column.name.casefold()
            for column in node.columns.values()
            if column.meta.get("sensitive") is True
        )
        sensitive -= allowed_sensitive
        denied_outputs |= sensitive
        denied = sorted(
            name
            for name in resolver.columns.get(node.unique_id, set())
            if name.casefold() in sensitive
        )
        if denied:
            raise SQLValidationError(
                f"Sensitive columns require explicit permission: {', '.join(denied)}"
            )
    checks.append(Verification("sensitive_access", True))
    root = scopes[-1]
    star_outputs = (
        tuple(sorted(name.casefold() for name in resolver.outputs(root)))
        if resolver.projects_star(root)
        else None
    )
    columns = sorted({name for names in resolver.columns.values() for name in names})

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
    return ValidatedSQL(
        sql,
        executed,
        tuple(used),
        tuple(columns),
        tuple(checks),
        denied_outputs=tuple(sorted(denied_outputs)),
        star_outputs=star_outputs,
    )


def check_result_columns(validated: ValidatedSQL, names: Iterable[str]) -> Verification:
    """Backstop on what the warehouse actually returned, before rows reach a model.

    Rejects a result column named like a denied sensitive column, and, for a star
    query, any column dbt does not document (the warehouse may have more columns
    than the manifest knows about).
    """
    for name in names:
        folded = name.casefold()
        if folded in validated.denied_outputs:
            raise SQLValidationError(f"Result column '{name}' is sensitive and was not approved")
        if validated.star_outputs is not None and folded not in validated.star_outputs:
            raise SQLValidationError(
                f"Result column '{name}' is not documented in dbt, so a star query may not "
                "return it"
            )
    return Verification("result_columns", True)


class _Resolver:
    """Resolve relations and columns scope by scope, so names never leak across scopes."""

    def __init__(self, manifest: Manifest, scope: tuple[Node, ...], dialect: Dialect) -> None:
        self.manifest = manifest
        self.scope = scope
        self.dialect = dialect
        self.nodes: list[Node] = []
        self.tables: dict[int, Node] = {}
        self.columns: dict[str, set[str]] = {}

    def _normalize(self, identifier: exp.Identifier | str, quoted: bool = False) -> str:
        if isinstance(identifier, str):
            identifier = exp.Identifier(this=identifier, quoted=quoted)
        return str(self.dialect.normalize_identifier(identifier.copy()).this)

    def _node_columns(self, node: Node) -> dict[str, str]:
        """Map each dbt column's normalized identifier to its dbt spelling."""
        return {self._normalize(name): name for name in node.columns}

    def resolve_relations(self, current: Scope) -> None:
        if current.scope_type == ScopeType.UDTF:
            raise SQLValidationError("Table functions such as UNNEST are not allowed")
        for source in current.sources.values():
            if not isinstance(source, exp.Table):
                continue
            if not isinstance(source.this, exp.Identifier):
                raise SQLValidationError(f"Table function '{source.this.sql()}' is not allowed")
            parts = [part for part in (source.catalog, source.db, source.name) if part]
            try:
                node = self.manifest.resolve_relation(parts, self.scope)
            except ManifestError as exc:
                raise SQLValidationError(str(exc)) from exc
            self.tables[id(source)] = node
            if node not in self.nodes:
                self.nodes.append(node)

    def require_every_table_resolved(self, tree: exp.Expr, scopes: list[Scope]) -> None:
        """Fail closed on any table reference that no scope accounts for."""
        cte_references = {
            id(table)
            for current in scopes
            for table in current.tables
            if isinstance(current.sources.get(table.alias_or_name), Scope)
        }
        for table in tree.find_all(exp.Table):
            if id(table) not in self.tables and id(table) not in cte_references:
                raise SQLValidationError(f"Relation '{table.sql()}' could not be resolved")

    def _source(self, current: Scope, name: str) -> exp.Table | Scope | None:
        """Find a source by alias in this scope, then in enclosing (correlated) scopes."""
        wanted = name.casefold()
        cursor: Scope | None = current
        while cursor is not None:
            for key, source in cursor.sources.items():
                if key.casefold() == wanted and isinstance(source, (exp.Table, Scope)):
                    return source
            cursor = cursor.parent
        return None

    def outputs(self, current: Scope) -> set[str]:
        """Return the normalized column names a scope produces."""
        branches = _set_operation_scopes(current)
        if branches:
            return self.outputs(branches[0])
        select = current.expression
        if not isinstance(select, exp.Select):
            return set()
        names: set[str] = set()
        for projection in select.expressions:
            star = _star(projection)
            if star is not None:
                for source in self._star_sources(current, projection):
                    names |= self._source_outputs(source) - self._excluded(star)
            elif projection.alias_or_name:
                names.add(self._normalize(projection.alias_or_name))
        return names

    def _source_outputs(self, source: exp.Table | Scope) -> set[str]:
        if isinstance(source, Scope):
            return self.outputs(source)
        node = self.tables.get(id(source))
        return set(self._node_columns(node)) if node else set()

    def _star_sources(self, current: Scope, projection: exp.Expression) -> list[exp.Table | Scope]:
        if isinstance(projection, exp.Column) and projection.table:
            source = self._source(current, projection.table)
            if source is None:
                raise SQLValidationError(
                    f"Unknown relation '{projection.table}' in '{projection.sql()}'"
                )
            return [source]
        return [
            source
            for _, source in current.selected_sources.values()
            if isinstance(source, (exp.Table, Scope))
        ] or [
            source[1] if isinstance(source, tuple) else source
            for source in current.sources.values()
            if isinstance(source, (exp.Table, Scope))
        ]

    def _excluded(self, star: exp.Star) -> set[str]:
        return {
            self._normalize(column.this) if isinstance(column, exp.Column) else ""
            for column in star.args.get("except_") or ()
        }

    def projects_star(self, current: Scope) -> bool:
        branches = _set_operation_scopes(current)
        if branches:
            return any(self.projects_star(item) for item in branches)
        select = current.expression
        return isinstance(select, exp.Select) and any(
            _star(projection) is not None for projection in select.expressions
        )

    def _use(self, node: Node, name: str) -> None:
        known = self._node_columns(node)
        if name not in known:
            raise SQLValidationError(
                f"Column '{name}' does not exist on dbt resource '{node.unique_id}'"
            )
        self.columns.setdefault(node.unique_id, set()).add(known[name])

    def resolve_columns(self, current: Scope) -> None:
        select = current.expression
        if isinstance(select, exp.Select):
            for projection in select.expressions:
                star = _star(projection)
                if star is None:
                    continue
                excluded = self._excluded(star)
                for source in self._star_sources(current, projection):
                    if isinstance(source, exp.Table) and id(source) in self.tables:
                        node = self.tables[id(source)]
                        for name in set(self._node_columns(node)) - excluded:
                            self._use(node, name)
        aliases = (
            {self._normalize(item.alias) for item in select.expressions if item.alias}
            if isinstance(select, exp.Select)
            else set()
        )
        # Not ``current.columns``: sqlglot omits unqualified HAVING, QUALIFY, and
        # ORDER BY columns from it, so a sensitive column there would go unchecked.
        for column in current.find_all(exp.Column):
            if isinstance(column.this, exp.Star) or column.arg_key == "except_":
                continue
            name = self._normalize(column.this)
            if column.table:
                owner = self._source(current, column.table)
                if owner is None:
                    raise SQLValidationError(
                        f"Unknown relation '{column.table}' in '{column.sql()}'"
                    )
                self._column_in(owner, name, column)
                continue
            matches = self._unqualified_matches(current, name)
            if not matches:
                if name in aliases:
                    continue
                raise SQLValidationError(
                    f"Column '{column.sql()}' does not exist on a selected dbt resource"
                )
            if len(matches) > 1:
                raise SQLValidationError(
                    f"Unqualified column '{column.name}' is ambiguous across selected dbt resources"
                )
            self._column_in(matches[0], name, column)

    def _unqualified_matches(self, current: Scope, name: str) -> list[exp.Table | Scope]:
        cursor: Scope | None = current
        while cursor is not None:
            matches = [
                source
                for source in cursor.sources.values()
                if isinstance(source, (exp.Table, Scope)) and name in self._source_outputs(source)
            ]
            if matches:
                return matches
            cursor = cursor.parent
        return []

    def _column_in(self, source: exp.Table | Scope, name: str, column: exp.Column) -> None:
        if isinstance(source, Scope):
            if name not in self.outputs(source):
                raise SQLValidationError(
                    f"Column '{column.sql()}' does not exist in derived relation '{column.table}'"
                )
            return
        node = self.tables.get(id(source))
        if node is None:
            raise SQLValidationError(f"Column '{column.sql()}' refers to an unresolved relation")
        self._use(node, name)


def _set_operation_scopes(current: Scope) -> list[Scope]:
    """Return the branch scopes of a UNION/INTERSECT/EXCEPT.

    sqlglot renamed ``Scope.union_scopes`` to ``set_operation_scopes`` in 30.x.
    """
    branches = getattr(current, "set_operation_scopes", None)
    if branches is None:
        branches = getattr(current, "union_scopes", [])
    return list(branches)


def _star(projection: exp.Expression) -> exp.Star | None:
    """Return the star of a ``*`` or ``t.*`` projection, or None."""
    if isinstance(projection, exp.Star):
        return projection
    if isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
        return projection.this
    return None
