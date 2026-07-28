from __future__ import annotations

import pytest

from tabletalk.domain import ErrorCode, TableTalkError
from tabletalk.runtime import SQLScope, validate_sql


@pytest.fixture
def scope() -> SQLScope:
    return SQLScope(
        relations=("main.customers", "main.orders"),
        columns=(
            ("main.customers", ("id", "name")),
            ("main.orders", ("customer_id", "id", "total")),
        ),
        approved_joins=(
            ("main.orders", "customer_id", "main.customers", "id"),
        ),
    )


def test_single_scoped_select_is_valid(scope: SQLScope) -> None:
    result = validate_sql(
        "SELECT c.name, SUM(o.total) FROM customers c "
        "JOIN orders o ON o.customer_id = c.id GROUP BY c.name",
        dialect="sqlite",
        scope=scope,
    )

    assert result.relations == ("main.customers", "main.orders")
    assert "c.name" in result.columns


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers",
        "UPDATE customers SET name = 'x'",
        "INSERT INTO customers VALUES (1, 'x')",
        "DROP TABLE customers",
        "PRAGMA table_info(customers)",
    ],
)
def test_write_and_command_statements_are_rejected(sql: str, scope: SQLScope) -> None:
    with pytest.raises(TableTalkError) as raised:
        validate_sql(sql, dialect="sqlite", scope=scope)

    assert raised.value.code is ErrorCode.SQL_NOT_READ_ONLY


def test_multiple_statements_are_rejected(scope: SQLScope) -> None:
    with pytest.raises(TableTalkError) as raised:
        validate_sql(
            "SELECT * FROM customers; DROP TABLE customers",
            dialect="sqlite",
            scope=scope,
        )

    assert raised.value.code is ErrorCode.SQL_INVALID


def test_undeclared_relation_is_rejected(scope: SQLScope) -> None:
    with pytest.raises(TableTalkError) as raised:
        validate_sql("SELECT * FROM payroll", dialect="sqlite", scope=scope)

    assert raised.value.code is ErrorCode.SQL_OUT_OF_SCOPE
    assert raised.value.details["relation"] == "payroll"


def test_qualified_undeclared_column_is_rejected(scope: SQLScope) -> None:
    with pytest.raises(TableTalkError) as raised:
        validate_sql(
            "SELECT c.social_security_number FROM customers c",
            dialect="sqlite",
            scope=scope,
        )

    assert raised.value.code is ErrorCode.SQL_OUT_OF_SCOPE


def test_cte_over_declared_relation_is_allowed(scope: SQLScope) -> None:
    result = validate_sql(
        "WITH totals AS (SELECT customer_id, SUM(total) AS amount FROM orders "
        "GROUP BY customer_id) SELECT amount FROM totals",
        dialect="sqlite",
        scope=scope,
    )

    assert result.relations == ("main.orders",)


def test_adds_a_policy_row_limit(scope: SQLScope) -> None:
    result = validate_sql(
        "SELECT id FROM customers",
        dialect="sqlite",
        scope=scope,
        max_rows=25,
    )

    assert result.sql == "SELECT id FROM customers LIMIT 25"


def test_reduces_an_excessive_row_limit(scope: SQLScope) -> None:
    result = validate_sql(
        "SELECT id FROM customers LIMIT 1000",
        dialect="sqlite",
        scope=scope,
        max_rows=25,
    )

    assert result.sql == "SELECT id FROM customers LIMIT 25"


def test_rejects_a_dynamic_row_limit(scope: SQLScope) -> None:
    with pytest.raises(TableTalkError) as raised:
        validate_sql(
            "SELECT id FROM customers LIMIT ?",
            dialect="sqlite",
            scope=scope,
            max_rows=25,
        )

    assert raised.value.code is ErrorCode.SQL_INVALID


def test_unapproved_join_path_is_rejected(scope: SQLScope) -> None:
    with pytest.raises(TableTalkError) as raised:
        validate_sql(
            "SELECT c.name, o.total FROM customers AS c "
            "JOIN orders AS o ON o.id = c.id",
            dialect="sqlite",
            scope=scope,
        )

    assert raised.value.code is ErrorCode.SQL_OUT_OF_SCOPE
    assert "join path" in raised.value.message


def test_cross_join_is_rejected(scope: SQLScope) -> None:
    with pytest.raises(TableTalkError) as raised:
        validate_sql(
            "SELECT c.name, o.total FROM customers AS c CROSS JOIN orders AS o",
            dialect="sqlite",
            scope=scope,
        )

    assert raised.value.code is ErrorCode.SQL_OUT_OF_SCOPE
