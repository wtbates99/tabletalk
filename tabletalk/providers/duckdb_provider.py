from typing import Any, Dict, List, Optional

from tabletalk.interfaces import DatabaseProvider

_INSTALL_HINT = "Run: uv add 'tabletalk[duckdb]'  or  pip install duckdb"


class DuckDBProvider(DatabaseProvider):
    def __init__(self, database_path: str = ":memory:"):
        """
        Args:
            database_path: Path to .duckdb file, or ':memory:' for an in-memory database.
        """
        try:
            import duckdb
        except ImportError:
            raise ImportError(f"duckdb is not installed. {_INSTALL_HINT}")

        self.database_path = database_path
        self.connection = duckdb.connect(database_path)

    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        result = self.connection.execute(sql_query)
        columns = [desc[0] for desc in result.description] if result.description else []
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def get_client(self) -> Any:
        return self.connection

    def get_database_type_map(self) -> Dict[str, str]:
        return {
            "VARCHAR": "S",
            "TEXT": "S",
            "CHAR": "S",
            "CHARACTER VARYING": "S",
            "BLOB": "BY",
            "BYTEA": "BY",
            "INTEGER": "I",
            "INT": "I",
            "INT4": "I",
            "INT8": "I",
            "INT16": "I",
            "INT32": "I",
            "INT64": "I",
            "SMALLINT": "I",
            "BIGINT": "I",
            "TINYINT": "I",
            "HUGEINT": "I",
            "UINTEGER": "I",
            "UBIGINT": "I",
            "USMALLINT": "I",
            "UTINYINT": "I",
            "FLOAT": "F",
            "FLOAT4": "F",
            "FLOAT8": "F",
            "DOUBLE": "F",
            "REAL": "F",
            "DECIMAL": "N",
            "NUMERIC": "N",
            "BOOLEAN": "B",
            "BOOL": "B",
            "DATE": "D",
            "TIME": "T",
            "TIMESTAMP": "TS",
            "TIMESTAMP WITH TIME ZONE": "TS",
            "TIMESTAMPTZ": "TS",
            "INTERVAL": "IV",
            "JSON": "J",
            "LIST": "A",
            "STRUCT": "J",
            "MAP": "J",
            "UUID": "U",
        }

    def get_compact_tables(
        self, schema_name: str, table_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        type_map = self.get_database_type_map()

        if table_names is None:
            result = self.connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = ?
                AND table_type IN ('BASE TABLE', 'VIEW')
                """,
                [schema_name],
            )
            table_names = [row[0] for row in result.fetchall()]

        compact_tables = []
        for table_name in table_names:
            # PRAGMA table_info gives: cid, name, type, notnull, dflt_value, pk
            pragma_result = self.connection.execute(
                f"PRAGMA table_info('{schema_name}.{table_name}')"
            )
            columns = pragma_result.fetchall()

            # If schema-qualified pragma fails, try unqualified
            if not columns:
                pragma_result = self.connection.execute(
                    f"PRAGMA table_info('{table_name}')"
                )
                columns = pragma_result.fetchall()

            # Collect primary key column indices
            pk_set = {col[1] for col in columns if col[5] > 0}  # pk column is index 5

            # Foreign keys via PRAGMA foreign_key_list
            fk_map: Dict[str, str] = {}
            try:
                fk_result = self.connection.execute(
                    f"PRAGMA foreign_key_list('{table_name}')"
                )
                for fk_row in fk_result.fetchall():
                    # id, seq, table, from, to, on_update, on_delete, match
                    fk_map[fk_row[3]] = f"{fk_row[2]}.{fk_row[4]}"
            except Exception:
                pass

            fields = []
            for col in columns:
                # cid, name, type, notnull, dflt_value, pk
                col_name = col[1]
                col_type = (col[2] or "VARCHAR").upper().split("(")[0].strip()
                mapped = type_map.get(col_type, "S")
                field: Dict[str, Any] = {"n": col_name, "t": mapped}
                if col_name in pk_set:
                    field["pk"] = True
                if col_name in fk_map:
                    field["fk"] = fk_map[col_name]
                fields.append(field)

            compact_tables.append(
                {"t": f"{schema_name}.{table_name}", "d": "", "f": fields}
            )

        return compact_tables
