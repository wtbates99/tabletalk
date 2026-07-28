"""
Snowflake provider tests.

These tests are skipped unless snowflake-connector-python is installed:
    uv add "tabletalk[snowflake]"

They also require a live Snowflake account configured via environment variables:
    SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
    SNOWFLAKE_DATABASE, SNOWFLAKE_WAREHOUSE
"""

import os

import pytest

snowflake = pytest.importorskip(
    "snowflake.connector",
    reason="snowflake-connector-python not installed — pip install tabletalk[snowflake]",
)


@pytest.fixture(scope="module")
def snowflake_provider():
    """Live Snowflake provider — skipped if env vars are not set."""
    account = os.environ.get("SNOWFLAKE_ACCOUNT")
    user = os.environ.get("SNOWFLAKE_USER")
    password = os.environ.get("SNOWFLAKE_PASSWORD")
    database = os.environ.get("SNOWFLAKE_DATABASE")
    warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE")

    if not all([account, user, password, database, warehouse]):
        pytest.skip(
            "Snowflake env vars not set "
            "(SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, "
            "SNOWFLAKE_DATABASE, SNOWFLAKE_WAREHOUSE)"
        )

    from tabletalk.providers.snowflake_provider import SnowflakeProvider

    return SnowflakeProvider(
        account=account,
        user=user,
        password=password,
        database=database,
        warehouse=warehouse,
    )


class TestSnowflakeProvider:
    def test_client_is_connection(self, snowflake_provider):
        client = snowflake_provider.get_client()
        assert client is not None

    def test_execute_simple_query(self, snowflake_provider):
        results = snowflake_provider.execute_query("SELECT 1 AS n")
        assert len(results) == 1
        assert list(results[0].values())[0] == 1

    def test_type_map_completeness(self, snowflake_provider):
        type_map = snowflake_provider.get_database_type_map()
        for key in ("TEXT", "VARCHAR", "NUMBER", "INT", "FLOAT", "BOOLEAN", "DATE", "TIMESTAMP"):
            assert key in type_map, f"Type map missing key: {key}"

    def test_compact_tables_returns_list(self, snowflake_provider):
        schema = os.environ.get("SNOWFLAKE_SCHEMA", "PUBLIC")
        tables = snowflake_provider.get_compact_tables(schema)
        assert isinstance(tables, list)
        for t in tables:
            assert "t" in t
            assert "f" in t
            assert isinstance(t["f"], list)
