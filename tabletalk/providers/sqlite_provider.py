import sqlite3
from typing import Any, Dict, List, Optional

from tabletalk.interfaces import DatabaseProvider


class SQLiteProvider(DatabaseProvider):
    def __init__(self, database_path: str):
        self.database_path = database_path
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row

    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        cursor = self.connection.cursor()
        cursor.execute(sql_query)
        results = cursor.fetchall()
        return [dict(row) for row in results]

    def get_client(self) -> sqlite3.Connection:
        return self.connection

    def get_database_type_map(self) -> Dict[str, str]:
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
        cursor = self.connection.cursor()

        if table_names is None:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
            )
            table_names = [row[0] for row in cursor.fetchall()]

        type_map = self.get_database_type_map()
        compact_tables = []

        for table_name in table_names:
            # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
            cursor.execute(f"PRAGMA table_info('{table_name}')")
            columns = cursor.fetchall()

            # pk column is nonzero for primary key columns (value = position in PK)
            pk_set = {col[1] for col in columns if col[5] > 0}

            # Foreign keys via PRAGMA foreign_key_list
            fk_map: Dict[str, str] = {}
            cursor.execute(f"PRAGMA foreign_key_list('{table_name}')")
            for fk_row in cursor.fetchall():
                # id, seq, table, from, to, on_update, on_delete, match
                fk_map[fk_row[3]] = f"{fk_row[2]}.{fk_row[4]}"

            fields = []
            for col in columns:
                col_name = col[1]
                col_type = (col[2] or "TEXT").upper().split("(")[0].strip()
                mapped = type_map.get(col_type, "S")
                field: Dict[str, Any] = {"n": col_name, "t": mapped}
                if col_name in pk_set:
                    field["pk"] = True
                if col_name in fk_map:
                    field["fk"] = fk_map[col_name]
                fields.append(field)

            compact_tables.append({"t": table_name, "d": "", "f": fields})

        return compact_tables
