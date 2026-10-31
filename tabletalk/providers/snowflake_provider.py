"""Snowflake execution adapter intended for a read-only dbt role."""

from __future__ import annotations

import json
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

    def execute_query(
        self, sql_query: str, *, timeout_seconds: int | None = None, query_tag: str | None = None
    ) -> list[dict[str, Any]]:
        cursor = self.connection.cursor()
        # Session settings persist, so always set both: a query without a tag must
        # not inherit the previous one. The server-side timeout stops warehouse
        # spend when the client gives up; 0 is Snowflake's "no limit".
        cursor.execute(
            "ALTER SESSION SET QUERY_TAG = "
            + _string_literal(query_tag or "")
            + f", STATEMENT_TIMEOUT_IN_SECONDS = {int(timeout_seconds or 0)}"
        )
        cursor.execute(sql_query)
        columns = [column[0] for column in cursor.description] if cursor.description else []
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def estimate_scan_bytes(self, sql_query: str) -> int | None:
        """Return ``GlobalStats.bytesAssigned`` from Snowflake's compile-time plan.

        EXPLAIN compiles without running the query, so it uses no warehouse compute.
        The caller passes a single validated SELECT.
        """
        cursor = self.connection.cursor()
        cursor.execute("EXPLAIN USING JSON " + sql_query)
        row = cursor.fetchone()
        if not row:
            raise ValueError("EXPLAIN returned no plan")
        # The connector types rows as a tuple or, for a DictCursor, a mapping.
        content = next(iter(row.values())) if isinstance(row, dict) else row[0]
        plan = json.loads(content)
        return int(plan["GlobalStats"]["bytesAssigned"])

    def get_client(self) -> Any:
        return self.connection


def _string_literal(value: str) -> str:
    """Quote ``value`` as a Snowflake single-quoted string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
