from typing import Any, Dict, List, Optional

from tabletalk.interfaces import DatabaseProvider

_INSTALL_HINT = "Run: uv add 'tabletalk[snowflake]'  or  pip install snowflake-connector-python"


class SnowflakeProvider(DatabaseProvider):
    def __init__(
        self,
        account: str,
        user: str,
        password: str,
        database: str,
        warehouse: str,
        schema: str = "PUBLIC",
        role: Optional[str] = None,
    ):
        try:
            import snowflake.connector
        except ImportError:
            raise ImportError(
                f"snowflake-connector-python is not installed. {_INSTALL_HINT}"
            )

        connect_kwargs: Dict[str, Any] = {
            "account": account,
            "user": user,
            "password": password,
            "database": database,
            "warehouse": warehouse,
            "schema": schema,
        }
        if role:
            connect_kwargs["role"] = role

        self.database = database
        self.schema = schema
        self.connection = snowflake.connector.connect(**connect_kwargs)

    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        cursor = self.connection.cursor()
        cursor.execute(sql_query)
        columns = [col[0] for col in cursor.description] if cursor.description else []
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_client(self) -> Any:
        return self.connection

    def get_database_type_map(self) -> Dict[str, str]:
        return {
            "TEXT": "S",
            "VARCHAR": "S",
            "STRING": "S",
            "CHAR": "S",
            "CHARACTER": "S",
            "NUMBER": "N",
            "DECIMAL": "N",
            "NUMERIC": "N",
            "INT": "I",
            "INTEGER": "I",
            "BIGINT": "I",
            "SMALLINT": "I",
            "TINYINT": "I",
            "BYTEINT": "I",
            "FLOAT": "F",
            "FLOAT4": "F",
            "FLOAT8": "F",
            "DOUBLE": "F",
            "REAL": "F",
            "BOOLEAN": "B",
            "DATE": "D",
            "TIME": "T",
            "DATETIME": "DT",
            "TIMESTAMP": "TS",
            "TIMESTAMP_LTZ": "TS",
            "TIMESTAMP_NTZ": "DT",
            "TIMESTAMP_TZ": "TS",
            "VARIANT": "J",
            "OBJECT": "J",
            "ARRAY": "A",
            "BINARY": "BY",
            "VARBINARY": "BY",
            "GEOGRAPHY": "G",
            "GEOMETRY": "G",
        }

    def get_compact_tables(
        self, schema_name: str, table_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        cursor = self.connection.cursor()
        type_map = self.get_database_type_map()

        if table_names is None:
            cursor.execute(
                """
                SELECT TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = %s
                AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
                """,
                (schema_name.upper(),),
            )
            table_names = [row[0] for row in cursor.fetchall()]

        # Fetch primary keys for the whole schema at once
        pk_cols: Dict[str, set] = {}
        try:
            cursor.execute(f"SHOW PRIMARY KEYS IN SCHEMA {schema_name}")
            for row in cursor.fetchall():
                # columns: (created_on, database_name, schema_name, table_name,
                #            column_name, key_sequence, constraint_name, rely, comment)
                tbl = row[3].upper()
                col = row[4].upper()
                pk_cols.setdefault(tbl, set()).add(col)
        except Exception:
            pass  # Non-fatal; some roles may not have SHOW privilege

        # Fetch foreign keys for the whole schema at once
        fk_map: Dict[str, Dict[str, str]] = {}
        try:
            cursor.execute(f"SHOW IMPORTED KEYS IN SCHEMA {schema_name}")
            for row in cursor.fetchall():
                # pk_database, pk_schema, pk_table, pk_column,
                # fk_database, fk_schema, fk_table, fk_column, ...
                fk_tbl = row[6].upper()
                fk_col = row[7].upper()
                pk_tbl = row[2].upper()
                pk_col = row[3].upper()
                fk_map.setdefault(fk_tbl, {})[fk_col] = f"{pk_tbl}.{pk_col}"
        except Exception:
            pass

        compact_tables = []
        for table_name in table_names:
            cursor.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COMMENT
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
                """,
                (schema_name.upper(), table_name.upper()),
            )
            columns = cursor.fetchall()

            # Table comment
            cursor.execute(
                """
                SELECT COMMENT
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                """,
                (schema_name.upper(), table_name.upper()),
            )
            desc_row = cursor.fetchone()
            table_desc = desc_row[0] if desc_row and desc_row[0] else ""

            tbl_upper = table_name.upper()
            pks = pk_cols.get(tbl_upper, set())
            fks = fk_map.get(tbl_upper, {})

            fields = []
            for col in columns:
                col_name, col_type, _, col_comment = col[0], col[1], col[2], col[3]
                mapped = type_map.get(col_type.upper(), "S")
                field: Dict[str, Any] = {"n": col_name, "t": mapped}
                if col_name.upper() in pks:
                    field["pk"] = True
                if col_name.upper() in fks:
                    field["fk"] = fks[col_name.upper()]
                if col_comment:
                    field["d"] = col_comment
                fields.append(field)

            compact_tables.append(
                {"t": f"{schema_name}.{table_name}", "d": table_desc, "f": fields}
            )

        return compact_tables
