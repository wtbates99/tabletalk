import pytest
from typing import Generator, Dict
from contextlib import closing
import uuid
from pytest_postgresql import factories

from tabletalk.providers.postgres_provider import PostgresProvider

# Create a postgresql factory that will create a fresh database for each test session
postgresql_proc = factories.postgresql_proc(
    port=None,  # Let pytest-postgresql choose a random available port
    password="test_password",  # Set a known password for the test database
)
postgresql = factories.postgresql("postgresql_proc")


@pytest.fixture(scope="function")
def postgres_db(postgresql) -> Generator[Dict[str, str], None, None]:
    """Create a temporary PostgreSQL database with test data"""
    with closing(postgresql.cursor()) as cur:
        cur.execute(
            """
            CREATE TABLE users (
                id SERIAL PRIMARY KEY,
                name TEXT,
                age INTEGER,
                created_at TIMESTAMP
            )
        """
        )
        cur.execute(
            """
            INSERT INTO users (name, age, created_at)
            VALUES
                ('Alice', 30, '2024-01-01 10:00:00'),
                ('Bob', 25, '2024-01-02 11:00:00')
        """
        )
        cur.execute(
            """
            CREATE VIEW adult_users AS
            SELECT name, age
            FROM users
            WHERE age >= 18
        """
        )
        postgresql.commit()
    yield postgresql.dsn


@pytest.fixture(scope="function")
def postgres_provider(
    postgres_db: Dict[str, str],
) -> Generator[PostgresProvider, None, None]:
    """Create PostgresProvider instance with connection pooling"""
    provider = PostgresProvider(
        host=postgres_db["host"],
        port=postgres_db["port"],
        dbname=postgres_db["dbname"],
        user=postgres_db["user"],
        password=postgres_db["password"],
        min_connections=1,
        max_connections=5,
    )
    yield provider
    provider.close()


# Tests
def test_execute_query_table(postgres_provider: PostgresProvider) -> None:
    """Test executing query on a table"""
    results = postgres_provider.execute_query("SELECT * FROM users ORDER BY id")
    assert len(results) == 2
    assert results[0]["name"] == "Alice"
    assert results[0]["age"] == 30
    assert results[1]["name"] == "Bob"
    assert results[1]["age"] == 25


def test_execute_query_view(postgres_provider: PostgresProvider) -> None:
    """Test executing query on a view"""
    results = postgres_provider.execute_query("SELECT * FROM adult_users ORDER BY age")
    assert len(results) == 2
    assert results[0]["name"] == "Bob"
    assert results[0]["age"] == 25
    assert results[1]["name"] == "Alice"
    assert results[1]["age"] == 30


def test_get_compact_tables_all(postgres_provider: PostgresProvider) -> None:
    """Test getting schema for all tables and views"""
    results = postgres_provider.get_compact_tables(schema_name="public")
    assert len(results) == 2
    users_table = next(t for t in results if t["t"] == "users")
    assert users_table["t"] == "users"
    assert len(users_table["f"]) == 4
    assert {"n": "id", "t": "I"} in users_table["f"]
    assert {"n": "name", "t": "S"} in users_table["f"]
    assert {"n": "age", "t": "I"} in users_table["f"]
    assert {"n": "created_at", "t": "TS"} in users_table["f"]
    view_table = next(t for t in results if t["t"] == "adult_users")
    assert view_table["t"] == "adult_users"
    assert len(view_table["f"]) == 2
    assert {"n": "name", "t": "S"} in view_table["f"]
    assert {"n": "age", "t": "I"} in view_table["f"]


def test_get_compact_tables_specific(postgres_provider: PostgresProvider) -> None:
    """Test getting schema for specific tables"""
    results = postgres_provider.get_compact_tables(
        schema_name="public", table_names=["users"]
    )
    assert len(results) == 1
    table = results[0]
    assert table["t"] == "users"
    assert len(table["f"]) == 4


def test_get_database_type_map(postgres_provider: PostgresProvider) -> None:
    """Test database type mapping"""
    type_map = postgres_provider.get_database_type_map()
    assert type_map["text"] == "S"
    assert type_map["integer"] == "I"
    assert type_map["timestamp"] == "TS"


def test_database_cleanup(postgres_provider: PostgresProvider) -> None:
    """Verify that database is properly isolated between tests"""
    results = postgres_provider.execute_query("SELECT COUNT(*) as count FROM users")
    assert results[0]["count"] == 2


# Register custom marker for CI/CD
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "postgresql: mark test as requiring postgresql fixture"
    )


@pytest.mark.postgresql
@pytest.mark.parametrize("postgres_db", ["postgresql"], indirect=True)
def test_ci_environment(postgres_provider: PostgresProvider) -> None:
    """Test that runs only in CI with real postgresql fixture"""
    results = postgres_provider.execute_query("SELECT * FROM users")
    assert len(results) == 2
