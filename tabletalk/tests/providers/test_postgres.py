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
        conn.commit()
    conn.close()

    yield TEST_CONFIG


@pytest.fixture
def postgres_provider(postgres_db):
    """Create PostgresProvider instance"""
    provider = PostgresProvider(**postgres_db)
    yield provider
    provider.close()


# Simplified tests
def test_basic_query(postgres_provider):
    results = postgres_provider.execute_query("SELECT * FROM users ORDER BY id")
    assert len(results) == 2
    assert results[0]["name"] == "Alice"
    assert results[1]["name"] == "Bob"


def test_table_schema(postgres_provider):
    schemas = postgres_provider.get_compact_tables()
    assert len(schemas) == 1
    assert schemas[0]["t"] == "users"
    assert len(schemas[0]["f"]) == 3
