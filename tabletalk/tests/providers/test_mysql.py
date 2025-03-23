import os
from typing import Any, Dict, Generator, Union

import mysql.connector
import pytest
from mysql.connector.abstracts import MySQLConnectionAbstract
from mysql.connector.pooling import PooledMySQLConnection

from tabletalk.providers.mysql_provider import MySQLProvider

TEST_CONFIG = {
    "host": os.getenv("MYSQL_TEST_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_TEST_PORT", "3306")),
    "database": os.getenv("MYSQL_TEST_DB", "test"),
    "user": os.getenv("MYSQL_TEST_USER", "root"),
    "password": os.getenv("MYSQL_TEST_PASSWORD", "test"),
}

ConnectionType = Union[PooledMySQLConnection, MySQLConnectionAbstract]


@pytest.fixture(scope="function")
def mysql_db() -> Generator[Dict[str, Any], None, None]:
    """Set up a simple test database"""
    conn: ConnectionType = mysql.connector.connect(
        host=TEST_CONFIG["host"],
        port=TEST_CONFIG["port"],
        user=TEST_CONFIG["user"],
        password=TEST_CONFIG["password"],
    )

    # Handle autocommit based on connection type
    if isinstance(conn, PooledMySQLConnection):
        real_conn = conn.get_connection()
        real_conn.autocommit = True
    else:
        conn.autocommit = True

    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_CONFIG['database']}")
        cur.execute(f"CREATE DATABASE {TEST_CONFIG['database']}")

    conn.close()
    conn = mysql.connector.connect(
        host=TEST_CONFIG["host"],
        port=TEST_CONFIG["port"],
        user=TEST_CONFIG["user"],
        password=TEST_CONFIG["password"],
        database=TEST_CONFIG["database"],
    )

    # Handle autocommit again for the new connection
    if isinstance(conn, PooledMySQLConnection):
        real_conn = conn.get_connection()
        real_conn.autocommit = True
    else:
        conn.autocommit = True

    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE users (
                id INT AUTO_INCREMENT PRIMARY KEY,
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


@pytest.fixture
def mysql_provider(
    mysql_db: Dict[str, Any],
) -> Generator[MySQLProvider, None, None]:
    provider = MySQLProvider(
        host=mysql_db["host"],
        port=mysql_db["port"],
        database=mysql_db["database"],
        user=mysql_db["user"],
        password=mysql_db["password"],
    )
    yield provider
    provider.connection.close()


def test_basic_query(mysql_provider: MySQLProvider) -> None:
    results = mysql_provider.execute_query("SELECT * FROM users ORDER BY id")
    assert len(results) == 2
    assert results[0]["name"] == "Alice"
    assert results[1]["name"] == "Bob"


def test_table_and_view_schema(mysql_provider: MySQLProvider) -> None:
    schemas = mysql_provider.get_compact_tables()
    assert len(schemas) == 2
    table_schema = next(s for s in schemas if s["t"] == "users")
    view_schema = next(s for s in schemas if s["t"] == "adult_users")
    assert len(table_schema["f"]) == 3
    assert [f["n"] for f in table_schema["f"]] == ["id", "name", "age"]
    assert len(view_schema["f"]) == 2
    assert [f["n"] for f in view_schema["f"]] == ["name", "age"]


def test_view_query(mysql_provider: MySQLProvider) -> None:
    results = mysql_provider.execute_query("SELECT * FROM adult_users ORDER BY name")
    assert len(results) == 2
    assert results[0]["name"] == "Alice"
    assert results[0]["age"] == 30
    assert results[1]["name"] == "Bob"
    assert results[1]["age"] == 25
