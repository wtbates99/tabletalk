import os
import pytest
from psycopg2 import connect
from psycopg2.extras import RealDictCursor

from tabletalk.providers.postgres_provider import PostgresProvider

# Basic test configuration
TEST_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "dbname": "test_db",
    "user": "test",
    "password": "test",
}


@pytest.fixture(scope="function")
def postgres_db():
    """Set up a simple test database"""
    # Create fresh database
    admin_conn = connect(
        host=TEST_CONFIG["host"],
        port=TEST_CONFIG["port"],
        user=TEST_CONFIG["user"],
        password=TEST_CONFIG["password"],
        database="postgres",
    )
    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_CONFIG['dbname']}")
        cur.execute(f"CREATE DATABASE {TEST_CONFIG['dbname']}")
    admin_conn.close()

    # Set up schema and data
    conn = connect(**TEST_CONFIG)
    with conn.cursor() as cur:
        # Create users table
        cur.execute(
            """
            CREATE TABLE users (
                id SERIAL PRIMARY KEY,
                name TEXT,
                age INTEGER
            )
        """
        )
        cur.execute(
            """
            INSERT INTO users (name, age)
            VALUES ('Alice', 30), ('Bob', 25)
        """
        )

        # Create a view
        cur.execute(
            """
            CREATE VIEW adult_users AS
            SELECT name, age FROM users WHERE age >= 18
        """
        )
        conn.commit()
    conn.close()

    yield TEST_CONFIG


@pytest.fixture
def postgres_provider(postgres_db):
    """Create PostgresProvider instance"""
    provider = PostgresProvider(**postgres_db)
    yield provider
    # Close the underlying connection instead of calling close()
    provider.conn.close()


# Simplified tests
def test_basic_query(postgres_provider):
    results = postgres_provider.execute_query("SELECT * FROM users ORDER BY id")
    assert len(results) == 2
    assert results[0]["name"] == "Alice"
    assert results[1]["name"] == "Bob"


def test_table_and_view_schema(postgres_provider):
    """Test that both tables and views are returned by get_compact_tables"""
    schemas = postgres_provider.get_compact_tables()

    # Should return both the table and view
    assert len(schemas) == 2

    # Find table and view in results
    table_schema = next(s for s in schemas if s["t"] == "users")
    view_schema = next(s for s in schemas if s["t"] == "adult_users")

    # Verify table schema
    assert len(table_schema["f"]) == 3
    assert [f["n"] for f in table_schema["f"]] == ["id", "name", "age"]

    # Verify view schema
    assert len(view_schema["f"]) == 2
    assert [f["n"] for f in view_schema["f"]] == ["name", "age"]


def test_view_query(postgres_provider):
    """Test querying the view"""
    results = postgres_provider.execute_query("SELECT * FROM adult_users ORDER BY name")
    assert len(results) == 2
    assert results[0]["name"] == "Alice"
    assert results[0]["age"] == 30
    assert results[1]["name"] == "Bob"
    assert results[1]["age"] == 25
