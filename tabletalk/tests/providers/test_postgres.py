import os
from typing import Any, Dict, Generator

import pytest
from psycopg2 import connect

from tabletalk.providers.postgres_provider import PostgresProvider

TEST_CONFIG = {
    "host": os.getenv("POSTGRES_TEST_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_TEST_PORT", 5432)),
    "dbname": os.getenv("POSTGRES_TEST_DB", "test_db"),
    "user": os.getenv("POSTGRES_TEST_USER", "test"),
    "password": os.getenv("POSTGRES_TEST_PASSWORD", "test"),
}


@pytest.fixture(scope="function")
def postgres_db() -> Generator[Dict[str, Any], None, None]:
    """Set up a simple test database"""
    # Create fresh database
    admin_conn = connect(
        host=TEST_CONFIG["host"],
        port=TEST_CONFIG["port"],
        user=TEST_CONFIG["user"],
        password=TEST_CONFIG["password"],
        dbname="postgres",
    )
    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_CONFIG['dbname']}")
        cur.execute(f"CREATE DATABASE {TEST_CONFIG['dbname']}")
    admin_conn.close()

    # Set up schema and data
    conn = connect(
        host=TEST_CONFIG["host"],
        port=TEST_CONFIG["port"],
        dbname=TEST_CONFIG["dbname"],
        user=TEST_CONFIG["user"],
        password=TEST_CONFIG["password"],
    )
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
        cur.execute(
            """
            CREATE VIEW adult_users AS
            SELECT name, age FROM users WHERE age >= 18
        """
        )
        conn.commit()
    conn.close()

    yield TEST_CONFIG


# Rest of the code remains unchanged
@pytest.fixture
def postgres_provider(
    postgres_db: Dict[str, Any],
) -> Generator[PostgresProvider, None, None]:
    provider = PostgresProvider(
        host=postgres_db["host"],
        port=postgres_db["port"],
        dbname=postgres_db["dbname"],
        user=postgres_db["user"],
        password=postgres_db["password"],
    )
    yield provider
    provider.connection.close()


def test_basic_query(postgres_provider: PostgresProvider) -> None:
    results = postgres_provider.execute_query("SELECT * FROM users ORDER BY id")
    assert len(results) == 2
    assert results[0]["name"] == "Alice"
    assert results[1]["name"] == "Bob"


def test_table_and_view_schema(postgres_provider: PostgresProvider) -> None:
    schemas = postgres_provider.get_compact_tables()
    assert len(schemas) == 2
    table_schema = next(s for s in schemas if s["t"] == "users")
    view_schema = next(s for s in schemas if s["t"] == "adult_users")
    assert len(table_schema["f"]) == 3
    assert [f["n"] for f in table_schema["f"]] == ["id", "name", "age"]
    assert len(view_schema["f"]) == 2
    assert [f["n"] for f in view_schema["f"]] == ["name", "age"]


def test_view_query(postgres_provider: PostgresProvider) -> None:
    results = postgres_provider.execute_query("SELECT * FROM adult_users ORDER BY name")
    assert len(results) == 2
    assert results[0]["name"] == "Alice"
    assert results[0]["age"] == 30
    assert results[1]["name"] == "Bob"
    assert results[1]["age"] == 25
