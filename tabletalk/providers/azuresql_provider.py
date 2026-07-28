from typing import Any, Dict, List, Optional

from tabletalk.interfaces import DatabaseProvider

_INSTALL_HINT = "Run: uv add 'tabletalk[azuresql]'  or  pip install pymssql"


class AzureSQLProvider(DatabaseProvider):
    """
    Provider for Azure SQL / SQL Server using pymssql.

    Supports:
    - Azure SQL Database
    - Azure SQL Managed Instance
    - On-premises SQL Server
    """

    def __init__(
        self,
        server: str,
        database: str,
        user: str,
        password: str,
        port: int = 1433,
    ):
        try:
            import pymssql
        except ImportError:
            raise ImportError(f"pymssql is not installed. {_INSTALL_HINT}")

        self.server = server
        self.database = database
        self.connection = pymssql.connect(
            server=server,
            user=user,
            password=password,
            database=database,
            port=str(port),
        )

    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        cursor = self.connection.cursor(as_dict=True)
        cursor.execute(sql_query)
        return list(cursor.fetchall())

    def get_client(self) -> Any:
        return self.connection

    def get_database_type_map(self) -> Dict[str, str]:
        return {
            "char": "S",
            "varchar": "S",
            "nchar": "S",
            "nvarchar": "S",
            "text": "S",
            "ntext": "S",
            "xml": "S",
            "int": "I",
            "bigint": "I",
            "smallint": "I",
            "tinyint": "I",
            "decimal": "N",
            "numeric": "N",
            "money": "N",
            "smallmoney": "N",
            "float": "F",
            "real": "F",
            "bit": "B",
            "date": "D",
            "time": "T",
            "datetime": "DT",
            "datetime2": "DT",
            "datetimeoffset": "TS",
            "smalldatetime": "DT",
            "binary": "BY",
            "varbinary": "BY",
            "image": "BY",
            "uniqueidentifier": "U",
            "json": "J",
            "geography": "G",
            "geometry": "G",
        }

    def get_compact_tables(
        self, schema_name: str, table_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        cursor = self.connection.cursor(as_dict=True)
        type_map = self.get_database_type_map()

        if table_names is None:
            cursor.execute(
                """
                SELECT TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = %s
                AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
                """,
                (schema_name,),
            )
            table_names = [row["TABLE_NAME"] for row in cursor.fetchall()]

        # Primary keys for the whole schema
        pk_map: Dict[str, set] = {}
        cursor.execute(
            """
            SELECT tc.TABLE_NAME, kcu.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
            WHERE tc.TABLE_SCHEMA = %s
            AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            """,
            (schema_name,),
        )
        for row in cursor.fetchall():
            pk_map.setdefault(row["TABLE_NAME"], set()).add(row["COLUMN_NAME"])

        # Foreign keys for the whole schema
        fk_map: Dict[str, Dict[str, str]] = {}
        cursor.execute(
            """
            SELECT
                fk_tbl.TABLE_NAME  AS fk_table,
                fk_col.COLUMN_NAME AS fk_column,
                pk_tbl.TABLE_NAME  AS pk_table,
                pk_col.COLUMN_NAME AS pk_column
            FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
            JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS fk_tbl
                ON rc.CONSTRAINT_NAME = fk_tbl.CONSTRAINT_NAME
            JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS pk_tbl
                ON rc.UNIQUE_CONSTRAINT_NAME = pk_tbl.CONSTRAINT_NAME
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE fk_col
                ON rc.CONSTRAINT_NAME = fk_col.CONSTRAINT_NAME
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE pk_col
                ON rc.UNIQUE_CONSTRAINT_NAME = pk_col.CONSTRAINT_NAME
                AND fk_col.ORDINAL_POSITION = pk_col.ORDINAL_POSITION
            WHERE fk_tbl.TABLE_SCHEMA = %s
            """,
            (schema_name,),
        )
        for row in cursor.fetchall():
            fk_map.setdefault(row["fk_table"], {})[row["fk_column"]] = (
                f"{row['pk_table']}.{row['pk_column']}"
            )

        compact_tables = []
        for table_name in table_names:
            cursor.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
                """,
                (schema_name, table_name),
            )
            columns = cursor.fetchall()

            pks = pk_map.get(table_name, set())
            fks = fk_map.get(table_name, {})

            fields = []
            for col in columns:
                col_name = col["COLUMN_NAME"]
                col_type = col["DATA_TYPE"].lower()
                mapped = type_map.get(col_type, "S")
                field: Dict[str, Any] = {"n": col_name, "t": mapped}
                if col_name in pks:
                    field["pk"] = True
                if col_name in fks:
                    field["fk"] = fks[col_name]
                fields.append(field)

            compact_tables.append(
                {"t": f"{schema_name}.{table_name}", "d": "", "f": fields}
            )

        return compact_tables
