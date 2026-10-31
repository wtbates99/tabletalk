"""Adversarial tests for the SQL guard in tabletalk/validation.py.

Agent scope is the example project's ``group:finance``: only
``model.analytics.fct_orders``. ``fct_orders.customer_id`` is flagged sensitive in
dbt. ``stg_orders`` and the ``raw.orders`` source are out of scope.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from tabletalk.manifest import Manifest, Node
from tabletalk.validation import SQLValidationError, check_result_columns, validate_sql

EXAMPLE = Path(__file__).parents[2] / "examples" / "dbt-analytics"
FCT = "analytics.main.fct_orders"
STG = "analytics.main.stg_orders"

SHADOWING = f"""
select f.order_id, s.order_id as leaked
from {FCT} f
join (with stg_orders as (select 1 as order_id) select order_id from stg_orders) x on 1=1
join {STG} s on s.order_id = f.order_id
"""


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    return Manifest.load(EXAMPLE / "target" / "manifest.json")


@pytest.fixture(scope="module")
def scope(manifest: Manifest) -> tuple[Node, ...]:
    nodes = manifest.select(("group:finance",))
    assert [node.unique_id for node in nodes] == ["model.analytics.fct_orders"]
    return nodes


def _validate(sql: str, manifest: Manifest, scope: tuple[Node, ...], dialect: str = "duckdb"):
    return validate_sql(sql, manifest, scope, dialect=dialect, max_rows=100)


# 1. CTE-name shadowing ------------------------------------------------------------


def test_shadowing_query_really_leaks_an_out_of_scope_row_on_duckdb() -> None:
    """The repro is real: DuckDB executes it and returns stg_orders data."""
    connection = duckdb.connect()
    connection.execute("attach ':memory:' as analytics")
    connection.execute(f"create table {FCT} (order_id integer)")
    connection.execute(f"create table {STG} (order_id integer)")
    connection.execute(f"insert into {FCT} values (7)")
    connection.execute(f"insert into {STG} values (7)")

    assert connection.execute(SHADOWING).fetchall() == [(7, 7)]


def test_a_cte_named_like_an_out_of_scope_table_does_not_hide_the_real_table(
    manifest: Manifest, scope: tuple[Node, ...]
) -> None:
    with pytest.raises(SQLValidationError, match="not a manifest-backed resource in agent scope"):
        _validate(SHADOWING, manifest, scope)


def test_nodes_and_columns_report_every_real_relation_touched(manifest: Manifest) -> None:
    both = manifest.select(("model:fct_orders", "model:stg_orders"))
    validated = validate_sql(
        f"select f.order_id from {FCT} f join "
        f"(with stg_orders as (select order_date from {STG}) select order_date from stg_orders) x "
        "on x.order_date = f.order_date",
        manifest,
        both,
        dialect="duckdb",
        max_rows=10,
    )

    assert {node.unique_id for node in validated.nodes} == {
        "model.analytics.fct_orders",
        "model.analytics.stg_orders",
    }
    assert validated.columns == ("order_date", "order_id")


# 2. Case and quoting of identifiers -----------------------------------------------


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        (f"select customer_id from {FCT}", "duckdb"),
        (f"select CUSTOMER_ID from {FCT}", "duckdb"),
        (f"select Customer_Id from {FCT}", "duckdb"),
        (f'select "CUSTOMER_ID" from {FCT}', "duckdb"),
        (f'select "customer_id" from {FCT}', "duckdb"),
        (f"select f.CUSTOMER_ID from {FCT} f", "duckdb"),
        (f"select CUSTOMER_ID from {FCT}", "snowflake"),
        (f"select customer_id from {FCT}", "snowflake"),
        (f'select "CUSTOMER_ID" from {FCT}', "snowflake"),
    ],
)
def test_sensitive_columns_are_rejected_in_any_case_or_quoting(
    manifest: Manifest, scope: tuple[Node, ...], sql: str, dialect: str
) -> None:
    with pytest.raises(SQLValidationError, match="Sensitive columns require explicit permission"):
        _validate(sql, manifest, scope, dialect)


def test_snowflake_quoted_lowercase_is_a_different_identifier(
    manifest: Manifest, scope: tuple[Node, ...]
) -> None:
    # In Snowflake, "customer_id" (quoted) is not CUSTOMER_ID, so it names no dbt column.
    with pytest.raises(SQLValidationError, match="does not exist"):
        _validate(f'select "customer_id" from {FCT}', manifest, scope, "snowflake")


def test_reported_columns_use_the_dbt_spelling(manifest: Manifest, scope: tuple[Node, ...]) -> None:
    validated = _validate(f"select RECOGNIZED_REVENUE from {FCT}", manifest, scope, "snowflake")

    assert validated.columns == ("recognized_revenue",)


# 3. Star expansion -----------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        f"select * from {FCT}",
        f"select f.* from {FCT} f",
        f"select * from (select * from {FCT}) t",
        f"with x as (select * from {FCT}) select * from x",
        f"select order_id, * from {FCT}",
    ],
)
def test_a_star_over_a_sensitive_column_is_rejected(
    manifest: Manifest, scope: tuple[Node, ...], sql: str
) -> None:
    with pytest.raises(SQLValidationError, match="customer_id"):
        _validate(sql, manifest, scope)


def test_a_star_that_excludes_the_sensitive_column_expands_to_the_rest(
    manifest: Manifest, scope: tuple[Node, ...]
) -> None:
    validated = _validate(f"select * exclude (customer_id) from {FCT}", manifest, scope)

    assert "customer_id" not in validated.columns
    assert "recognized_revenue" in validated.columns


def test_count_star_is_not_a_projection(manifest: Manifest, scope: tuple[Node, ...]) -> None:
    validated = _validate(f"select count(*) as orders from {FCT}", manifest, scope)

    assert validated.columns == ()


# 4. Empty scope ----------------------------------------------------------------------


def test_an_empty_scope_allows_nothing(manifest: Manifest) -> None:
    with pytest.raises(SQLValidationError, match="empty"):
        validate_sql(f"select order_id from {STG}", manifest, (), dialect="duckdb", max_rows=10)


# 5. Result-column backstop ------------------------------------------------------------


def test_backstop_rejects_a_sensitive_result_column(
    manifest: Manifest, scope: tuple[Node, ...]
) -> None:
    validated = _validate(f"select order_id from {FCT}", manifest, scope)

    with pytest.raises(SQLValidationError, match="(?i)customer_id"):
        check_result_columns(validated, ["order_id", "CUSTOMER_ID"])


def test_backstop_rejects_a_star_result_column_dbt_does_not_document(
    manifest: Manifest, scope: tuple[Node, ...]
) -> None:
    validated = _validate(f"select * exclude (customer_id) from {FCT}", manifest, scope)

    with pytest.raises(SQLValidationError, match="ssn"):
        check_result_columns(validated, ["order_id", "ssn"])


def test_backstop_accepts_declared_outputs(manifest: Manifest, scope: tuple[Node, ...]) -> None:
    validated = _validate(
        f"select sum(recognized_revenue) as total, order_date from {FCT} group by 2",
        manifest,
        scope,
    )

    assert check_result_columns(validated, ["total", "ORDER_DATE"]).passed


# 6. Adversarial corpus --------------------------------------------------------------

MUST_REJECT = [
    # Out of scope, direct and indirect.
    f"select order_id from {STG}",
    "select order_id from stg_orders",
    "select order_id from main.stg_orders",
    'select order_id from "analytics"."main"."stg_orders"',
    "select order_id from analytics.main.raw_orders",
    f"select order_id from {FCT} where order_id in (select order_id from {STG})",
    f"select order_id from {FCT} where exists (select 1 from {STG} s where s.order_id = 1)",
    f"select (select max(order_id) from {STG}) as m from {FCT}",
    f"select order_id from {FCT} union all select order_id from {STG}",
    f"select order_id from {FCT} intersect select order_id from {STG}",
    f"select order_id from {FCT} except select order_id from {STG}",
    f"with a as (select order_id from {STG}) select order_id from a",
    f"with a as (with b as (select order_id from {STG}) select * from b) select order_id from a",
    # Shadowing in every position.
    SHADOWING,
    f"with stg_orders as (select 1 as order_id) select o.order_id from {STG} o",
    f"select order_id from (with fct_orders as (select 1 as order_id) select * from fct_orders) "
    f"x join {STG} s on s.order_id = x.order_id",
    f"with stg_orders as (select order_id from {FCT}) select order_id from stg_orders "
    f"union all select order_id from {STG}",
    # Sensitive columns: case, quoting, qualification, functions, predicates.
    f"select CUSTOMER_ID from {FCT}",
    f'select "Customer_Id" from {FCT}',
    f"select upper(customer_id) as c from {FCT}",
    f"select order_id from {FCT} where customer_id = 'C001'",
    f"select count(distinct Customer_ID) as n from {FCT}",
    f"select order_id from {FCT} order by CUSTOMER_ID",
    f"with c as (select CUSTOMER_ID as cid from {FCT}) select cid from c",
    f"select order_id from {FCT} group by order_id having max(customer_id) > ''",
    f"select order_id from {FCT} qualify row_number() over (order by customer_id) = 1",
    f"select order_id, row_number() over (partition by CUSTOMER_ID order by order_id) as r "
    f"from {FCT}",
    # Stars.
    f"select * from {FCT}",
    f"select F.* from {FCT} f",
    f"select * from (select * from {FCT}) t",
    f"select * from {FCT} union all select * from {FCT}",
    # Table functions and file reads.
    "select * from read_text('/etc/passwd')",
    "select * from query_table('stg_orders')",
    "select * from glob('*')",
    "select * from read_csv_auto('x.csv')",
    "select * from generate_series(1, 3)",
    "select * from unnest([1, 2]) t(x)",
    f"select order_id from {FCT} f join read_parquet('x.parquet') p on p.id = f.order_id",
    # Writes, multiple statements, unknown columns.
    f"delete from {FCT}",
    f"select order_id from {FCT}; select 1",
    f"select nope from {FCT}",
    f"select f.nope from {FCT} f",
]

MUST_ACCEPT = [
    f"select order_id from {FCT}",
    f"select ORDER_ID from {FCT}",
    f'select "order_id" from {FCT}',
    "select order_id from fct_orders",
    f"select f.order_id, f.order_date from {FCT} f",
    f"select sum(recognized_revenue) as total from {FCT}",
    f"select count(*) as n from {FCT}",
    f"select order_date, sum(recognized_revenue) as total from {FCT} group by order_date",
    f"select order_id from {FCT} order by order_id",
    f"with a as (select order_id from {FCT}) select order_id from a",
    f"with a as (with b as (select order_id from {FCT}) select order_id from b) "
    "select a.order_id from a",
    f"with stg_orders as (select order_id from {FCT}) select order_id from stg_orders",
    f"select t.total from (select sum(recognized_revenue) as total from {FCT}) t",
    f"select order_id from {FCT} union all select order_id from {FCT}",
    f"select order_id from {FCT} where order_id in (select order_id from {FCT})",
    f"select * exclude (customer_id) from {FCT}",
    f"select order_id, case when order_status = 'cancelled' then 0 else 1 end as live from {FCT}",
    f"select order_date, sum(recognized_revenue) as total from {FCT} group by order_date "
    "having total > 0 order by total",
    f"select order_id, row_number() over (order by order_date) as r from {FCT}",
]


@pytest.mark.parametrize("sql", MUST_REJECT)
def test_must_reject(manifest: Manifest, scope: tuple[Node, ...], sql: str) -> None:
    with pytest.raises(SQLValidationError):
        _validate(sql, manifest, scope)


@pytest.mark.parametrize("sql", MUST_ACCEPT)
def test_must_accept(manifest: Manifest, scope: tuple[Node, ...], sql: str) -> None:
    validated = _validate(sql, manifest, scope)

    assert [node.unique_id for node in validated.nodes] == ["model.analytics.fct_orders"]
    assert "customer_id" not in validated.columns


# 7. The backstop runs in the runtime, before rows reach the model or the trace ------------


def test_runtime_rejects_a_sensitive_result_column_before_the_answer_prompt(
    manifest: Manifest,
) -> None:
    from tabletalk.runtime import RejectionError
    from tabletalk.tests.test_dbt_native import make_runtime

    runtime = make_runtime(manifest)
    # Simulate a warehouse that returns a column the validated SQL never selected.
    runtime.connection.execute = (  # type: ignore[method-assign]
        lambda sql, timeout_seconds, *, query_tag=None: [
            {"recognized_revenue": 184.25, "CUSTOMER_ID": "C001"}
        ]
    )
    llm = runtime.llm
    with pytest.raises(RejectionError, match="(?i)customer_id"):
        runtime.answer("What was recognized revenue in July 2026?")

    assert llm.calls == 1  # type: ignore[attr-defined]  # The SQL call only; no answer call.
