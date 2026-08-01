"""DuckDB read-only execution adapter."""

from __future__ import annotations

from typing import Any

from tabletalk.interfaces import DatabaseProvider


class DuckDBProvider(DatabaseProvider):
    def __init__(self, database_path: str = ":memory:", read_only: bool = False) -> None:
        try:
            import duckdb
        except ImportError as exc:
            raise ImportError("Install DuckDB with: uv add 'tabletalk[duckdb]'") from exc
        self.database_path = database_path
        self.read_only = read_only
        self.connection = duckdb.connect(database_path, read_only=read_only)

    def execute_query(self, sql_query: str) -> list[dict[str, Any]]:
        result = self.connection.execute(sql_query)
        columns = [column[0] for column in result.description] if result.description else []
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def get_client(self) -> Any:
        return self.connection
