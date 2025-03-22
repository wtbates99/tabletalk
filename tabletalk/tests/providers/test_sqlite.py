import sqlite3
from pathlib import Path

import pytest

from tabletalk.providers.sqlite_provider import SQLiteProvider


@pytest.fixture
def sqlite_db_path(tmp_path: Path) -> str:
    """Create a temporary SQLite database with test data"""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create a test table
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

    # Insert sample data
    cursor.execute(
        """
        INSERT INTO users (name, age, created_at)
        VALUES
            ('Alice', 30, '2024-01-01 10:00:00'),
            ('Bob', 25, '2024-01-02 11:00:00')
    """
    )

    # Create a test view
    cursor.execute(
        """
        CREATE VIEW adult_users AS
        SELECT name, age
        FROM users
        WHERE age >= 18
    """
    )

    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def sqlite_provider(sqlite_db_path: str) -> SQLiteProvider:
    """Create SQLiteProvider instance"""
    return SQLiteProvider(sqlite_db_path)


def test_execute_query_table(sqlite_provider: SQLiteProvider) -> None:
    """Test executing query on a table"""
    results = sqlite_provider.execute_query("SELECT * FROM users ORDER BY id")
    assert len(results) == 2
    assert results[0]["name"] == "Alice"
    assert results[0]["age"] == 30
    assert results[1]["name"] == "Bob"
    assert results[1]["age"] == 25


def test_execute_query_view(sqlite_provider: SQLiteProvider) -> None:
    """Test executing query on a view"""
    results = sqlite_provider.execute_query("SELECT * FROM adult_users ORDER BY age")
    assert len(results) == 2
    assert results[0]["name"] == "Bob"
    assert results[0]["age"] == 25


def test_get_compact_tables_all(sqlite_provider: SQLiteProvider) -> None:
    """Test getting schema for all tables and views"""
    results = sqlite_provider.get_compact_tables(schema_name="main")

    assert len(results) == 2  # Should find both the table and view

    # Find the users table schema
    users_table = next(t for t in results if t["t"] == "users")
    assert users_table["t"] == "users"
    assert len(users_table["f"]) == 4
    assert {"n": "id", "t": "I"} in users_table["f"]
    assert {"n": "name", "t": "S"} in users_table["f"]
    assert {"n": "age", "t": "I"} in users_table["f"]
    assert {"n": "created_at", "t": "TS"} in users_table["f"]

    # Find the view schema
    view_table = next(t for t in results if t["t"] == "adult_users")
    assert view_table["t"] == "adult_users"
    assert len(view_table["f"]) == 2
    assert {"n": "name", "t": "S"} in view_table["f"]
    assert {"n": "age", "t": "I"} in view_table["f"]


def test_get_compact_tables_specific(sqlite_provider: SQLiteProvider) -> None:
    """Test getting schema for specific tables"""
    results = sqlite_provider.get_compact_tables(
        schema_name="main", table_names=["users"]
    )

    assert len(results) == 1
    table = results[0]
    assert table["t"] == "users"
    assert len(table["f"]) == 4


def test_get_database_type_map(sqlite_provider: SQLiteProvider) -> None:
    """Test database type mapping"""
    type_map = sqlite_provider.get_database_type_map()
    assert type_map["TEXT"] == "S"
    assert type_map["INTEGER"] == "I"
    assert type_map["TIMESTAMP"] == "TS"
