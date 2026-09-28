"""Snowflake execution adapter intended for a read-only dbt role."""

from __future__ import annotations

from typing import Any

from tabletalk.interfaces import DatabaseProvider


class SnowflakeProvider(DatabaseProvider):
    def __init__(
        self,
        account: str,
        user: str,
        password: str | None,
        database: str,
        warehouse: str,
        schema: str = "PUBLIC",
        role: str | None = None,
        authenticator: str | None = None,
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
            "database": database,
            "warehouse": warehouse,
            "schema": schema,
        }
        # Password-based auth is the default. Any other authenticator (for example
        # externalbrowser SSO, oauth, or key-pair) is passed through as-is and does
        # not require a stored password.
        if authenticator:
            arguments["authenticator"] = authenticator
        if password:
            arguments["password"] = password
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
