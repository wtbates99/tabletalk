import sqlite3
from typing import Any, Dict, List, Optional

from tabletalk.interfaces import DatabaseProvider


class SQLiteProvider(DatabaseProvider):
    def __init__(self, database_path: str):
        """
        Initialize SQLite provider with database path.

        Args:
            database_path (str): Path to the SQLite database file
        """
        self.database_path = database_path
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row

    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        """
        Execute a SQL query and return results as a list of dictionaries.

        Args:
            sql_query (str): SQL query to execute

        Returns:
            List[Dict[str, Any]]: Query results
        """
        cursor = self.connection.cursor()
        cursor.execute(sql_query)
        results = cursor.fetchall()
        return [dict(row) for row in results]

    def get_client(self) -> sqlite3.Connection:
        """Return the SQLite connection instance"""
        return self.connection

    def get_database_type_map(self) -> Dict[str, str]:
        """Return the database types mapping for SQLite"""
        return {
            "TEXT": "S",
            "INTEGER": "I",
            "REAL": "F",
            "NUMERIC": "N",
            "BLOB": "BY",
            "BOOLEAN": "B",
            "DATE": "D",
            "DATETIME": "DT",
            "TIMESTAMP": "TS",
            "VARCHAR": "S",
            "CHAR": "S",
            "INT": "I",
            "FLOAT": "F",
            "DOUBLE": "F",
            "DECIMAL": "N",
        }

    def get_compact_tables(
        self, schema_name: str, table_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch table schemas from SQLite database in a compact format.

        Args:
            schema_name (str): Not used in SQLite, but kept for interface compatibility
            table_names (Optional[List[str]]): Specific table names; if None, fetch all tables

        Returns:
            List of table schemas in compact format
        """
        cursor = self.connection.cursor()

        if table_names is None:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            table_names = [row[0] for row in cursor.fetchall()]

        type_map = self.get_database_type_map()
        compact_tables = []

        for table_name in table_names:
            cursor.execute(f"PRAGMA table_info('{table_name}')")
            columns = cursor.fetchall()

            fields = []
            for column in columns:
                col_name = column[1]
                col_type = column[2].upper() if column[2] else "TEXT"

                mapped_type = type_map.get(col_type, "S")
                fields.append({"n": col_name, "t": mapped_type})
                fields.append({"n": col_name, "t": mapped_type})

            compact_tables.append(
                {
                    "t": table_name,
                    "d": "",
                    "f": fields,
                }
            )

        return compact_tables
