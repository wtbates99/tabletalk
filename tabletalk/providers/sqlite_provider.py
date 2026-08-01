"""SQLite read-only execution adapter."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from tabletalk.interfaces import DatabaseProvider


class SQLiteProvider(DatabaseProvider):
    def __init__(self, database_path: str, read_only: bool = True) -> None:
        self.database_path = database_path
        self.read_only = read_only
        if read_only and database_path != ":memory:":
            resolved = Path(database_path).resolve()
            self.connection = sqlite3.connect(
                f"file:{resolved.as_posix()}?mode=ro", uri=True, check_same_thread=False
            )
        else:
            self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row

    def execute_query(self, sql_query: str) -> list[dict[str, Any]]:
        cursor = self.connection.execute(sql_query)
        return [dict(row) for row in cursor.fetchall()]

    def get_client(self) -> sqlite3.Connection:
        return self.connection
