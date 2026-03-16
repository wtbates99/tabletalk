"""
Tests for tabletalk/providers/sqlite_provider.py

Uses the ecommerce_sqlite fixture from conftest.py for integration tests
plus a minimal in-memory fixture for unit tests.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tabletalk.providers.sqlite_provider import SQLiteProvider


# ── Minimal fixture (legacy — kept for backward compat with original tests) ───

@pytest.fixture
def sqlite_db_path(tmp_path: Path) -> str:
    """Create a temporary SQLite database with test data."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT,
            age INTEGER,
            created_at TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO users (name, age, created_at) VALUES
            ('Alice', 30, '2024-01-01 10:00:00'),
            ('Bob',   25, '2024-01-02 11:00:00')
        """
    )
    cursor.execute(
        """
        CREATE VIEW adult_users AS
        SELECT name, age FROM users WHERE age >= 18
        """
    )
    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def sqlite_provider(sqlite_db_path: str) -> SQLiteProvider:
    return SQLiteProvider(sqlite_db_path)


# ── Ecommerce fixture ─────────────────────────────────────────────────────────

@pytest.fixture
def ecommerce_provider(ecommerce_sqlite: str) -> SQLiteProvider:
    """SQLiteProvider backed by the full ecommerce schema."""
    return SQLiteProvider(ecommerce_sqlite)


# ── execute_query — basic ──────────────────────────────────────────────────────

class TestExecuteQueryBasic:
    def test_execute_query_table(self, sqlite_provider):
        results = sqlite_provider.execute_query("SELECT * FROM users ORDER BY id")
        assert len(results) == 2
        assert results[0]["name"] == "Alice"
        assert results[0]["age"] == 30
        assert results[1]["name"] == "Bob"
        assert results[1]["age"] == 25

    def test_execute_query_view(self, sqlite_provider):
        results = sqlite_provider.execute_query("SELECT * FROM adult_users ORDER BY age")
        assert len(results) == 2
        assert results[0]["name"] == "Bob"
        assert results[0]["age"] == 25

    def test_returns_list_of_dicts(self, sqlite_provider):
        results = sqlite_provider.execute_query("SELECT * FROM users LIMIT 1")
        assert isinstance(results, list)
        assert isinstance(results[0], dict)

    def test_empty_result(self, sqlite_provider):
        results = sqlite_provider.execute_query(
            "SELECT * FROM users WHERE id = 99999"
        )
        assert results == []

    def test_aggregation(self, sqlite_provider):
        results = sqlite_provider.execute_query("SELECT COUNT(*) AS cnt FROM users")
        assert results[0]["cnt"] == 2

    def test_column_aliases(self, sqlite_provider):
        results = sqlite_provider.execute_query(
            "SELECT name AS user_name, age AS user_age FROM users LIMIT 1"
        )
        assert "user_name" in results[0]
        assert "user_age" in results[0]

    def test_invalid_sql_raises(self, sqlite_provider):
        with pytest.raises(Exception):
            sqlite_provider.execute_query("SELECT * FROM nonexistent_table")


# ── execute_query — ecommerce ─────────────────────────────────────────────────

class TestExecuteQueryEcommerce:
    def test_join_query(self, ecommerce_provider):
        results = ecommerce_provider.execute_query(
            """
            SELECT c.name, COUNT(o.id) AS orders
            FROM customers c
            LEFT JOIN orders o ON c.id = o.customer_id
            GROUP BY c.name
            ORDER BY orders DESC
            """
        )
        assert len(results) >= 1

    def test_revenue_aggregation(self, ecommerce_provider):
        results = ecommerce_provider.execute_query(
            "SELECT SUM(total_amount) AS revenue FROM orders"
        )
        assert results[0]["revenue"] is not None
        assert float(results[0]["revenue"]) > 0

    def test_filter_by_status(self, ecommerce_provider):
        results = ecommerce_provider.execute_query(
            "SELECT * FROM orders WHERE status = 'delivered'"
        )
        assert len(results) == 3

    def test_subquery(self, ecommerce_provider):
        results = ecommerce_provider.execute_query(
            """
            SELECT name FROM customers
            WHERE id IN (SELECT customer_id FROM orders WHERE total_amount > 100)
            ORDER BY name
            """
        )
        assert len(results) >= 1


# ── get_client ────────────────────────────────────────────────────────────────

class TestGetClient:
    def test_returns_connection(self, sqlite_provider):
        client = sqlite_provider.get_client()
        assert isinstance(client, sqlite3.Connection)

    def test_client_is_usable(self, sqlite_provider):
        cursor = sqlite_provider.get_client().cursor()
        cursor.execute("SELECT 1")
        assert cursor.fetchone()[0] == 1


# ── get_database_type_map ─────────────────────────────────────────────────────

class TestGetDatabaseTypeMap:
    def test_text_maps_to_S(self, sqlite_provider):
        assert sqlite_provider.get_database_type_map()["TEXT"] == "S"

    def test_integer_maps_to_I(self, sqlite_provider):
        assert sqlite_provider.get_database_type_map()["INTEGER"] == "I"

    def test_real_maps_to_F(self, sqlite_provider):
        assert sqlite_provider.get_database_type_map()["REAL"] == "F"

    def test_numeric_maps_to_N(self, sqlite_provider):
        assert sqlite_provider.get_database_type_map()["NUMERIC"] == "N"

    def test_blob_maps_to_BY(self, sqlite_provider):
        assert sqlite_provider.get_database_type_map()["BLOB"] == "BY"

    def test_boolean_maps_to_B(self, sqlite_provider):
        assert sqlite_provider.get_database_type_map()["BOOLEAN"] == "B"

    def test_date_maps_to_D(self, sqlite_provider):
        assert sqlite_provider.get_database_type_map()["DATE"] == "D"

    def test_datetime_maps_to_DT(self, sqlite_provider):
        assert sqlite_provider.get_database_type_map()["DATETIME"] == "DT"

    def test_timestamp_maps_to_TS(self, sqlite_provider):
        assert sqlite_provider.get_database_type_map()["TIMESTAMP"] == "TS"

    def test_varchar_maps_to_S(self, sqlite_provider):
        assert sqlite_provider.get_database_type_map()["VARCHAR"] == "S"

    def test_decimal_maps_to_N(self, sqlite_provider):
        assert sqlite_provider.get_database_type_map()["DECIMAL"] == "N"


# ── get_compact_tables — basic ────────────────────────────────────────────────

class TestGetCompactTablesBasic:
    def test_all_tables_returned(self, sqlite_provider):
        results = sqlite_provider.get_compact_tables(schema_name="main")
        assert len(results) == 2

    def test_users_table_found(self, sqlite_provider):
        results = sqlite_provider.get_compact_tables(schema_name="main")
        names = {t["t"] for t in results}
        assert "users" in names

    def test_view_found(self, sqlite_provider):
        results = sqlite_provider.get_compact_tables(schema_name="main")
        names = {t["t"] for t in results}
        assert "adult_users" in names

    def test_users_field_count(self, sqlite_provider):
        results = sqlite_provider.get_compact_tables(schema_name="main")
        users = next(t for t in results if t["t"] == "users")
        assert len(users["f"]) == 4

    def test_users_field_types(self, sqlite_provider):
        results = sqlite_provider.get_compact_tables(schema_name="main")
        users = next(t for t in results if t["t"] == "users")
        fields = {f["n"]: f for f in users["f"]}
        assert fields["id"]["t"] == "I"
        assert fields["name"]["t"] == "S"
        assert fields["age"]["t"] == "I"
        assert fields["created_at"]["t"] == "TS"

    def test_specific_table(self, sqlite_provider):
        results = sqlite_provider.get_compact_tables(schema_name="main", table_names=["users"])
        assert len(results) == 1
        assert results[0]["t"] == "users"

    def test_table_dict_has_required_keys(self, sqlite_provider):
        results = sqlite_provider.get_compact_tables(schema_name="main")
        for t in results:
            assert "t" in t
            assert "d" in t
            assert "f" in t


# ── get_compact_tables — primary keys ─────────────────────────────────────────

class TestCompactTablesPrimaryKeys:
    def test_integer_pk_detected(self, sqlite_provider):
        results = sqlite_provider.get_compact_tables(schema_name="main", table_names=["users"])
        fields = {f["n"]: f for f in results[0]["f"]}
        assert fields["id"].get("pk") is True

    def test_non_pk_not_marked(self, sqlite_provider):
        results = sqlite_provider.get_compact_tables(schema_name="main", table_names=["users"])
        fields = {f["n"]: f for f in results[0]["f"]}
        assert not fields["name"].get("pk")
        assert not fields["age"].get("pk")

    def test_ecommerce_pk_customers(self, ecommerce_provider):
        results = ecommerce_provider.get_compact_tables(schema_name="main", table_names=["customers"])
        fields = {f["n"]: f for f in results[0]["f"]}
        assert fields["id"].get("pk") is True

    def test_ecommerce_pk_orders(self, ecommerce_provider):
        results = ecommerce_provider.get_compact_tables(schema_name="main", table_names=["orders"])
        fields = {f["n"]: f for f in results[0]["f"]}
        assert fields["id"].get("pk") is True


# ── get_compact_tables — foreign keys ─────────────────────────────────────────

class TestCompactTablesForeignKeys:
    def test_fk_detected_on_orders(self, ecommerce_provider):
        """orders.customer_id should have FK annotation pointing to customers.id"""
        results = ecommerce_provider.get_compact_tables(schema_name="main", table_names=["orders"])
        fields = {f["n"]: f for f in results[0]["f"]}
        assert "fk" in fields["customer_id"]
        assert fields["customer_id"]["fk"] == "customers.id"

    def test_fk_detected_on_order_items(self, ecommerce_provider):
        """order_items.order_id should FK to orders.id"""
        results = ecommerce_provider.get_compact_tables(schema_name="main", table_names=["order_items"])
        fields = {f["n"]: f for f in results[0]["f"]}
        assert "fk" in fields["order_id"]
        assert fields["order_id"]["fk"] == "orders.id"

    def test_fk_product_id_on_order_items(self, ecommerce_provider):
        results = ecommerce_provider.get_compact_tables(schema_name="main", table_names=["order_items"])
        fields = {f["n"]: f for f in results[0]["f"]}
        assert "fk" in fields["product_id"]
        assert fields["product_id"]["fk"] == "products.id"

    def test_no_fk_on_customers(self, ecommerce_provider):
        """customers table has no foreign keys"""
        results = ecommerce_provider.get_compact_tables(schema_name="main", table_names=["customers"])
        fks = [f for f in results[0]["f"] if f.get("fk")]
        assert len(fks) == 0

    def test_no_fk_on_products_for_non_fk_columns(self, ecommerce_provider):
        results = ecommerce_provider.get_compact_tables(schema_name="main", table_names=["products"])
        fields = {f["n"]: f for f in results[0]["f"]}
        # sku, name, price should have no FK
        assert not fields["sku"].get("fk")
        assert not fields["name"].get("fk")
        assert not fields["price"].get("fk")


# ── get_compact_tables — ecommerce full schema ────────────────────────────────

class TestCompactTablesEcommerceFullSchema:
    def test_all_eight_tables_present(self, ecommerce_provider):
        tables = ecommerce_provider.get_compact_tables(schema_name="main")
        names = {t["t"] for t in tables}
        expected = {
            "customers", "categories", "products", "inventory",
            "orders", "order_items", "campaigns", "campaign_conversions",
        }
        assert expected == names

    def test_customers_columns(self, ecommerce_provider):
        tables = ecommerce_provider.get_compact_tables(schema_name="main", table_names=["customers"])
        fields = {f["n"] for f in tables[0]["f"]}
        assert {"id", "name", "email", "phone", "city", "country", "created_at", "active"} == fields

    def test_inventory_product_fk(self, ecommerce_provider):
        tables = ecommerce_provider.get_compact_tables(schema_name="main", table_names=["inventory"])
        fields = {f["n"]: f for f in tables[0]["f"]}
        assert fields["product_id"].get("fk") == "products.id"

    def test_campaign_conversions_fks(self, ecommerce_provider):
        tables = ecommerce_provider.get_compact_tables(
            schema_name="main", table_names=["campaign_conversions"]
        )
        fields = {f["n"]: f for f in tables[0]["f"]}
        assert fields["campaign_id"].get("fk") == "campaigns.id"
        assert fields["customer_id"].get("fk") == "customers.id"

    def test_empty_table_name_list(self, ecommerce_provider):
        tables = ecommerce_provider.get_compact_tables(schema_name="main", table_names=[])
        assert tables == []


# ── in-memory SQLite ──────────────────────────────────────────────────────────

class TestSQLiteInMemory:
    def test_in_memory_provider(self):
        provider = SQLiteProvider(":memory:")
        provider.execute_query(
            "CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)"
        )
        provider.execute_query("INSERT INTO t VALUES (1, 'hello')")
        results = provider.execute_query("SELECT * FROM t")
        assert results[0]["val"] == "hello"

    def test_in_memory_schema_introspection(self):
        provider = SQLiteProvider(":memory:")
        provider.execute_query(
            "CREATE TABLE foo (id INTEGER PRIMARY KEY, x REAL, y TEXT)"
        )
        tables = provider.get_compact_tables("main", ["foo"])
        fields = {f["n"]: f for f in tables[0]["f"]}
        assert fields["id"]["t"] == "I"
        assert fields["id"].get("pk") is True
        assert fields["x"]["t"] == "F"
        assert fields["y"]["t"] == "S"
