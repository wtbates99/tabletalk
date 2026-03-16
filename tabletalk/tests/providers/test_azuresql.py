"""
Azure SQL / SQL Server provider tests.

These tests are skipped unless pymssql is installed:
    uv add "tabletalk[azuresql]"

They also require a live Azure SQL instance configured via environment variables:
    AZURESQL_SERVER, AZURESQL_DATABASE, AZURESQL_USER, AZURESQL_PASSWORD
"""

import os

import pytest

pymssql = pytest.importorskip(
    "pymssql",
    reason="pymssql not installed — pip install tabletalk[azuresql]",
)


@pytest.fixture(scope="module")
def azuresql_provider():
    """Live Azure SQL provider — skipped if env vars are not set."""
    server = os.environ.get("AZURESQL_SERVER")
    database = os.environ.get("AZURESQL_DATABASE")
    user = os.environ.get("AZURESQL_USER")
    password = os.environ.get("AZURESQL_PASSWORD")

    if not all([server, database, user, password]):
        pytest.skip(
            "Azure SQL env vars not set "
            "(AZURESQL_SERVER, AZURESQL_DATABASE, AZURESQL_USER, AZURESQL_PASSWORD)"
        )

    from tabletalk.providers.azuresql_provider import AzureSQLProvider

    return AzureSQLProvider(server=server, database=database, user=user, password=password)


class TestAzureSQLProvider:
    def test_client_is_connection(self, azuresql_provider):
        client = azuresql_provider.get_client()
        assert client is not None

    def test_execute_simple_query(self, azuresql_provider):
        results = azuresql_provider.execute_query("SELECT 1 AS n")
        assert len(results) == 1
        assert results[0]["n"] == 1

    def test_type_map_completeness(self, azuresql_provider):
        type_map = azuresql_provider.get_database_type_map()
        for key in ("varchar", "int", "decimal", "float", "bit", "date", "datetime"):
            assert key in type_map, f"Type map missing key: {key}"

    def test_compact_tables_returns_list(self, azuresql_provider):
        schema = os.environ.get("AZURESQL_SCHEMA", "dbo")
        tables = azuresql_provider.get_compact_tables(schema)
        assert isinstance(tables, list)
        for t in tables:
            assert "t" in t
            assert "f" in t
