"""
Tests for tabletalk/providers/duckdb_provider.py

Uses the in-memory and file-based ecommerce_duckdb fixture from conftest.py.
All tests are skipped automatically if duckdb is not installed.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("duckdb", reason="duckdb not installed") is None,
    reason="duckdb not installed",
)


@pytest.fixture
def duckdb_mem():
    """In-memory DuckDB provider seeded with ecommerce tables."""
    pytest.importorskip("duckdb")
    import duckdb
    from tabletalk.providers.duckdb_provider import DuckDBProvider

    conn = duckdb.connect(":memory:")

    conn.execute(
        """
        CREATE TABLE customers (
            id      INTEGER PRIMARY KEY,
            name    VARCHAR NOT NULL,
            email   VARCHAR UNIQUE NOT NULL,
            city    VARCHAR,
            active  BOOLEAN DEFAULT TRUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE products (
            id          INTEGER PRIMARY KEY,
            sku         VARCHAR UNIQUE NOT NULL,
            name        VARCHAR NOT NULL,
            price       DECIMAL(10,2) NOT NULL,
            category_id INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE orders (
            id           INTEGER PRIMARY KEY,
            customer_id  INTEGER NOT NULL REFERENCES customers(id),
            status       VARCHAR DEFAULT 'pending',
            total_amount DECIMAL(10,2) NOT NULL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE order_items (
            id          INTEGER PRIMARY KEY,
            order_id    INTEGER NOT NULL REFERENCES orders(id),
            product_id  INTEGER NOT NULL REFERENCES products(id),
            quantity    INTEGER NOT NULL,
            unit_price  DECIMAL(10,2) NOT NULL
        )
        """
    )

    conn.execute(
        """
        INSERT INTO customers VALUES
            (1, 'Alice Johnson',  'alice@example.com',  'New York',    TRUE),
            (2, 'Bob Smith',      'bob@example.com',    'Los Angeles', TRUE),
            (3, 'Carol Williams', 'carol@example.com',  'Chicago',     TRUE),
            (4, 'Inactive User',  'inactive@example.com', 'Dallas',   FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO products VALUES
            (1, 'ELEC-001', 'Wireless Headphones', 149.99, 1),
            (2, 'ELEC-002', 'USB-C Hub',            49.99, 1),
            (3, 'CLTH-001', 'Organic Cotton Tee',   29.99, 2)
        """
    )
    conn.execute(
        """
        INSERT INTO orders VALUES
            (1, 1, 'delivered', 199.98, '2024-01-10'),
            (2, 2, 'delivered',  49.99, '2024-01-12'),
            (3, 3, 'pending',    29.99, '2024-01-15')
        """
    )
    conn.execute(
        """
        INSERT INTO order_items VALUES
            (1, 1, 1, 1, 149.99),
            (2, 1, 2, 1,  49.99),
            (3, 2, 2, 1,  49.99),
            (4, 3, 3, 1,  29.99)
        """
    )
    conn.close()

    provider = DuckDBProvider(":memory:")
    # Re-use the connection with data loaded via a file is tricky;
    # simpler to create the provider then execute the setup directly.
    provider.connection.execute(
        """
        CREATE TABLE customers (
            id      INTEGER PRIMARY KEY,
            name    VARCHAR NOT NULL,
            email   VARCHAR UNIQUE NOT NULL,
            city    VARCHAR,
            active  BOOLEAN DEFAULT TRUE
        );
        CREATE TABLE products (
            id          INTEGER PRIMARY KEY,
            sku         VARCHAR UNIQUE NOT NULL,
            name        VARCHAR NOT NULL,
            price       DECIMAL(10,2) NOT NULL,
            category_id INTEGER
        );
        CREATE TABLE orders (
            id           INTEGER PRIMARY KEY,
            customer_id  INTEGER NOT NULL REFERENCES customers(id),
            status       VARCHAR DEFAULT 'pending',
            total_amount DECIMAL(10,2) NOT NULL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE order_items (
            id          INTEGER PRIMARY KEY,
            order_id    INTEGER NOT NULL REFERENCES orders(id),
            product_id  INTEGER NOT NULL REFERENCES products(id),
            quantity    INTEGER NOT NULL,
            unit_price  DECIMAL(10,2) NOT NULL
        );
        INSERT INTO customers VALUES
            (1, 'Alice Johnson',  'alice@example.com',  'New York',    TRUE),
            (2, 'Bob Smith',      'bob@example.com',    'Los Angeles', TRUE),
            (3, 'Carol Williams', 'carol@example.com',  'Chicago',     TRUE),
            (4, 'Inactive User',  'inactive@example.com', 'Dallas',   FALSE);
        INSERT INTO products VALUES
            (1, 'ELEC-001', 'Wireless Headphones', 149.99, 1),
            (2, 'ELEC-002', 'USB-C Hub',            49.99, 1),
            (3, 'CLTH-001', 'Organic Cotton Tee',   29.99, 2);
        INSERT INTO orders VALUES
            (1, 1, 'delivered', 199.98, '2024-01-10'),
            (2, 2, 'delivered',  49.99, '2024-01-12'),
            (3, 3, 'pending',    29.99, '2024-01-15');
        INSERT INTO order_items VALUES
            (1, 1, 1, 1, 149.99),
            (2, 1, 2, 1,  49.99),
            (3, 2, 2, 1,  49.99),
            (4, 3, 3, 1,  29.99);
        """
    )
    return provider


# ── execute_query ─────────────────────────────────────────────────────────────

class TestDuckDBExecuteQuery:
    def test_basic_select(self, duckdb_mem):
        results = duckdb_mem.execute_query("SELECT * FROM customers ORDER BY id")
        assert len(results) == 4
        assert results[0]["name"] == "Alice Johnson"

    def test_returns_list_of_dicts(self, duckdb_mem):
        results = duckdb_mem.execute_query("SELECT id, name FROM customers LIMIT 1")
        assert isinstance(results, list)
        assert isinstance(results[0], dict)
        assert "id" in results[0]
        assert "name" in results[0]

    def test_empty_result(self, duckdb_mem):
        results = duckdb_mem.execute_query(
            "SELECT * FROM customers WHERE id = 99999"
        )
        assert results == []

    def test_aggregation(self, duckdb_mem):
        results = duckdb_mem.execute_query(
            "SELECT COUNT(*) AS cnt FROM customers"
        )
        assert results[0]["cnt"] == 4

    def test_join_query(self, duckdb_mem):
        results = duckdb_mem.execute_query(
            """
            SELECT c.name, SUM(o.total_amount) AS total
            FROM customers c
            JOIN orders o ON c.id = o.customer_id
            GROUP BY c.name
            ORDER BY total DESC
            """
        )
        assert len(results) == 3
        assert results[0]["name"] == "Alice Johnson"

    def test_filter_query(self, duckdb_mem):
        results = duckdb_mem.execute_query(
            "SELECT * FROM customers WHERE active = TRUE"
        )
        assert len(results) == 3

    def test_invalid_sql_raises(self, duckdb_mem):
        with pytest.raises(Exception):
            duckdb_mem.execute_query("SELECT * FROM table_that_does_not_exist")

    def test_column_names_preserved(self, duckdb_mem):
        results = duckdb_mem.execute_query(
            "SELECT id AS customer_id, name AS customer_name FROM customers LIMIT 1"
        )
        assert "customer_id" in results[0]
        assert "customer_name" in results[0]


# ── get_client ────────────────────────────────────────────────────────────────

class TestDuckDBGetClient:
    def test_returns_connection(self, duckdb_mem):
        import duckdb

        client = duckdb_mem.get_client()
        assert client is duckdb_mem.connection

    def test_client_is_usable(self, duckdb_mem):
        client = duckdb_mem.get_client()
        result = client.execute("SELECT 1 AS x").fetchall()
        assert result[0][0] == 1


# ── get_database_type_map ─────────────────────────────────────────────────────

class TestDuckDBTypeMap:
    def test_varchar_maps_to_S(self, duckdb_mem):
        assert duckdb_mem.get_database_type_map()["VARCHAR"] == "S"

    def test_integer_maps_to_I(self, duckdb_mem):
        assert duckdb_mem.get_database_type_map()["INTEGER"] == "I"

    def test_float_maps_to_F(self, duckdb_mem):
        assert duckdb_mem.get_database_type_map()["FLOAT"] == "F"

    def test_decimal_maps_to_N(self, duckdb_mem):
        assert duckdb_mem.get_database_type_map()["DECIMAL"] == "N"

    def test_boolean_maps_to_B(self, duckdb_mem):
        assert duckdb_mem.get_database_type_map()["BOOLEAN"] == "B"

    def test_date_maps_to_D(self, duckdb_mem):
        assert duckdb_mem.get_database_type_map()["DATE"] == "D"

    def test_timestamp_maps_to_TS(self, duckdb_mem):
        assert duckdb_mem.get_database_type_map()["TIMESTAMP"] == "TS"

    def test_json_maps_to_J(self, duckdb_mem):
        assert duckdb_mem.get_database_type_map()["JSON"] == "J"

    def test_uuid_maps_to_U(self, duckdb_mem):
        assert duckdb_mem.get_database_type_map()["UUID"] == "U"

    def test_all_values_are_strings(self, duckdb_mem):
        for k, v in duckdb_mem.get_database_type_map().items():
            assert isinstance(v, str), f"{k} maps to non-string: {v!r}"

    def test_bigint_variants(self, duckdb_mem):
        type_map = duckdb_mem.get_database_type_map()
        for t in ["BIGINT", "SMALLINT", "TINYINT", "HUGEINT", "INT64"]:
            assert type_map[t] == "I", f"{t} should map to I"


# ── get_compact_tables ────────────────────────────────────────────────────────

class TestDuckDBGetCompactTables:
    def test_all_tables_returned(self, duckdb_mem):
        tables = duckdb_mem.get_compact_tables("main")
        names = {t["t"].split(".")[-1] for t in tables}
        assert "customers" in names
        assert "products" in names
        assert "orders" in names
        assert "order_items" in names

    def test_specific_tables_only(self, duckdb_mem):
        tables = duckdb_mem.get_compact_tables("main", ["customers", "products"])
        assert len(tables) == 2
        names = {t["t"].split(".")[-1] for t in tables}
        assert names == {"customers", "products"}

    def test_fields_have_required_keys(self, duckdb_mem):
        tables = duckdb_mem.get_compact_tables("main", ["customers"])
        fields = tables[0]["f"]
        for field in fields:
            assert "n" in field
            assert "t" in field

    def test_primary_key_detected(self, duckdb_mem):
        tables = duckdb_mem.get_compact_tables("main", ["customers"])
        fields = {f["n"]: f for f in tables[0]["f"]}
        assert fields["id"].get("pk") is True

    def test_non_pk_field_not_annotated(self, duckdb_mem):
        tables = duckdb_mem.get_compact_tables("main", ["customers"])
        fields = {f["n"]: f for f in tables[0]["f"]}
        assert not fields["name"].get("pk")

    def test_correct_field_count(self, duckdb_mem):
        tables = duckdb_mem.get_compact_tables("main", ["customers"])
        assert len(tables[0]["f"]) == 5  # id, name, email, city, active

    def test_type_codes_are_compact(self, duckdb_mem):
        """All type codes should be short strings (compact notation)."""
        tables = duckdb_mem.get_compact_tables("main", ["customers"])
        for field in tables[0]["f"]:
            assert len(field["t"]) <= 3, f"Type code too long: {field['t']!r}"

    def test_varchar_type_mapped(self, duckdb_mem):
        tables = duckdb_mem.get_compact_tables("main", ["customers"])
        fields = {f["n"]: f for f in tables[0]["f"]}
        assert fields["name"]["t"] == "S"

    def test_boolean_type_mapped(self, duckdb_mem):
        tables = duckdb_mem.get_compact_tables("main", ["customers"])
        fields = {f["n"]: f for f in tables[0]["f"]}
        assert fields["active"]["t"] == "B"

    def test_decimal_type_mapped(self, duckdb_mem):
        tables = duckdb_mem.get_compact_tables("main", ["products"])
        fields = {f["n"]: f for f in tables[0]["f"]}
        assert fields["price"]["t"] == "N"

    def test_table_name_format(self, duckdb_mem):
        """Table name should include schema prefix: 'main.table_name'."""
        tables = duckdb_mem.get_compact_tables("main", ["customers"])
        assert tables[0]["t"] == "main.customers"

    def test_empty_table_list(self, duckdb_mem):
        tables = duckdb_mem.get_compact_tables("main", [])
        assert tables == []

    def test_nonexistent_table_raises(self, duckdb_mem):
        """Requesting a table that doesn't exist raises a CatalogException."""
        with pytest.raises(Exception, match="ghost_table"):
            duckdb_mem.get_compact_tables("main", ["ghost_table"])


# ── File-based DuckDB ─────────────────────────────────────────────────────────

class TestDuckDBFileBased:
    def test_file_based_persistence(self, ecommerce_duckdb):
        """The ecommerce fixture creates a real file-based DuckDB with data."""
        results = ecommerce_duckdb.execute_query("SELECT COUNT(*) AS cnt FROM customers")
        assert results[0]["cnt"] == 5

    def test_file_based_schema_introspection(self, ecommerce_duckdb):
        tables = ecommerce_duckdb.get_compact_tables("main")
        names = {t["t"].split(".")[-1] for t in tables}
        expected = {"customers", "products", "orders", "order_items", "inventory", "campaigns", "campaign_conversions", "categories"}
        assert expected.issubset(names)

    def test_file_based_join(self, ecommerce_duckdb):
        results = ecommerce_duckdb.execute_query(
            """
            SELECT c.name, COUNT(o.id) AS order_count
            FROM customers c
            LEFT JOIN orders o ON c.id = o.customer_id
            GROUP BY c.name
            ORDER BY order_count DESC
            """
        )
        assert len(results) >= 1
        # Alice has 2 orders
        alice = next(r for r in results if r["name"] == "Alice Johnson")
        assert alice["order_count"] == 2
