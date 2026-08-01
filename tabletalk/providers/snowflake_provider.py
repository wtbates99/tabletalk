"""Snowflake execution adapter intended for a read-only dbt role."""

from __future__ import annotations

from typing import Any

from tabletalk.interfaces import DatabaseProvider


class SnowflakeProvider(DatabaseProvider):
    def __init__(
        self,
        account: str,
        user: str,
        password: str,
        database: str,
        warehouse: str,
        schema: str = "PUBLIC",
        role: str | None = None,
    ) -> None:
        try:
            import snowflake.connector
        except ImportError as exc:
            raise ImportError(
                "Install Snowflake support with: uv add 'tabletalk[snowflake]'"
            ) from exc
        arguments: dict[str, Any] = {
            "account": account,
            "user": user,
            "password": password,
            "database": database,
            "warehouse": warehouse,
            "schema": schema,
        }
        if role:
            arguments["role"] = role
        self.connection = snowflake.connector.connect(**arguments)

    def execute_query(self, sql_query: str) -> list[dict[str, Any]]:
        cursor = self.connection.cursor()
        cursor.execute(sql_query)
        columns = [column[0] for column in cursor.description] if cursor.description else []
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_client(self) -> Any:
        return self.connection
