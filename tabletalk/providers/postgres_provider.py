from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from tabletalk.interfaces import DatabaseProvider


class PostgresProvider(DatabaseProvider):
    def __init__(self, host: str, database: str, user: str, password: str):
        """
        Initialize PostgreSQL provider with connection string.

        Args:
            host (str): PostgreSQL host
            database (str): PostgreSQL database name
            user (str): PostgreSQL user
            password (str): PostgreSQL password
        """
        self.host = host
        self.database = database
        self.user = user
        self.password = password
        self.connection = psycopg2.connect(
            host=self.host,
            database=self.database,
            user=self.user,
            password=self.password,
        )

    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        """
        Execute a SQL query and return results as a list of dictionaries.

        Args:
            sql_query (str): SQL query to execute

        Returns:
            List[Dict[str, Any]]: Query results
        """
        cursor = self.connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(sql_query)
        results = cursor.fetchall()
        return [dict(row) for row in results]

    def get_client(self) -> psycopg2.extensions.connection:
        """Return the PostgreSQL connection instance"""
        return self.connection

    def get_database_type_map(self) -> Dict[str, str]:
        """Return the database types mapping for PostgreSQL"""
        return {
            "character varying": "S",
            "varchar": "S",
            "character": "S",
            "char": "S",
            "text": "S",
            "integer": "I",
            "smallint": "I",
            "bigint": "I",
            "decimal": "N",
            "numeric": "N",
            "real": "F",
            "double precision": "F",
            "float": "F",
            "boolean": "B",
            "date": "D",
            "timestamp": "DT",
            "timestamp with time zone": "TS",
            "timestamp without time zone": "DT",
            "time": "T",
            "bytea": "BY",
            "json": "J",
            "jsonb": "J",
            "uuid": "U",
            "array": "A",
        }

    def get_compact_tables(
        self, schema_name: str = "public", table_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch table and view schemas from PostgreSQL database in a compact format.

        Args:
            schema_name (str): PostgreSQL schema name (default: 'public')
            table_names (Optional[List[str]]): Specific table/view names; if None, fetch all

        Returns:
            List of table/view schemas in compact format
        """
        cursor = self.connection.cursor()

        if table_names is None:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s
                AND table_type IN ('BASE TABLE', 'VIEW')
                """,
                (schema_name,),
            )
            table_names = [row[0] for row in cursor.fetchall()]

        type_map = self.get_database_type_map()
        compact_tables = []

        for table_name in table_names:
            cursor.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema_name, table_name),
            )
            columns = cursor.fetchall()

            cursor.execute(
                """
                SELECT obj_description(
                    (quote_ident(%s) || '.' || quote_ident(%s))::regclass::oid
                ) as description
                """,
                (schema_name, table_name),
            )
            description_row = cursor.fetchone()
            table_description = (
                description_row[0] if description_row and description_row[0] else ""
            )

            fields = []
            for column in columns:
                col_name = column[0]
                col_type = column[1].lower()

                mapped_type = type_map.get(col_type, "S")
                fields.append({"n": col_name, "t": mapped_type})

            compact_tables.append(
                {
                    "t": table_name,
                    "d": table_description,
                    "f": fields,
                }
            )

        print(compact_tables)
        return compact_tables
