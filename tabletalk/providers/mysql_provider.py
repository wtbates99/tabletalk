from typing import Any, Dict, List, Optional, cast

import mysql.connector
from mysql.connector import Error, MySQLConnection
from mysql.connector.abstracts import MySQLConnectionAbstract
from mysql.connector.pooling import PooledMySQLConnection

from tabletalk.interfaces import DatabaseProvider


class MySQLProvider(DatabaseProvider):
    def __init__(
        self, host: str, database: str, user: str, password: str, port: int = 3306
    ):
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.port = port
        try:
            self.connection: MySQLConnection | PooledMySQLConnection | MySQLConnectionAbstract = (
                mysql.connector.connect(
                    host=self.host,
                    user=self.user,
                    password=self.password,
                    port=self.port,
                    database=self.database,
                )
            )
        except Error as e:
            raise Exception(f"Failed to connect to database: {e}")

    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        cursor = self.connection.cursor(dictionary=True)
        cursor.execute(sql_query)
        results = cast(List[Dict[str, Any]], cursor.fetchall())
        cursor.close()
        return results

    def get_client(self) -> MySQLConnection | PooledMySQLConnection | MySQLConnectionAbstract:
        return self.connection

    def get_database_type_map(self) -> Dict[str, str]:
        return {
            "varchar": "S",
            "char": "S",
            "text": "S",
            "tinytext": "S",
            "mediumtext": "S",
            "longtext": "S",
            "int": "I",
            "tinyint": "I",
            "smallint": "I",
            "mediumint": "I",
            "bigint": "I",
            "decimal": "N",
            "numeric": "N",
            "float": "F",
            "double": "F",
            "boolean": "B",
            "bool": "B",
            "date": "D",
            "datetime": "DT",
            "timestamp": "TS",
            "time": "T",
            "binary": "BY",
            "varbinary": "BY",
            "blob": "BY",
            "tinyblob": "BY",
            "mediumblob": "BY",
            "longblob": "BY",
            "json": "J",
            "enum": "S",
            "set": "A",
        }

    def get_compact_tables(
        self, schema_name: Optional[str] = None, table_names: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        cursor = self.connection.cursor(dictionary=True)

        if schema_name is None:
            schema_name = self.database

        if table_names is None:
            cursor.execute(
                """
                SELECT TABLE_NAME as table_name
                FROM information_schema.tables
                WHERE TABLE_SCHEMA = %s
                AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
                """,
                (schema_name,),
            )
            results = cast(List[Dict[str, str]], cursor.fetchall())
            if not results:
                raise Exception(f"No tables found in schema '{schema_name}'")
            table_names = [row["table_name"] for row in results]

        # Primary keys for the schema in one query
        cursor.execute(
            """
            SELECT TABLE_NAME, COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE kcu
            JOIN information_schema.TABLE_CONSTRAINTS tc
                ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
                AND kcu.TABLE_SCHEMA   = tc.TABLE_SCHEMA
                AND kcu.TABLE_NAME     = tc.TABLE_NAME
            WHERE kcu.TABLE_SCHEMA = %s
            AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            """,
            (schema_name,),
        )
        pk_map: Dict[str, set] = {}
        for row in cast(List[Dict[str, str]], cursor.fetchall()):
            pk_map.setdefault(row["TABLE_NAME"], set()).add(row["COLUMN_NAME"])

        # Foreign keys for the schema in one query
        cursor.execute(
            """
            SELECT
                kcu.TABLE_NAME      AS fk_table,
                kcu.COLUMN_NAME     AS fk_column,
                kcu.REFERENCED_TABLE_NAME  AS pk_table,
                kcu.REFERENCED_COLUMN_NAME AS pk_column
            FROM information_schema.KEY_COLUMN_USAGE kcu
            WHERE kcu.TABLE_SCHEMA = %s
            AND   kcu.REFERENCED_TABLE_NAME IS NOT NULL
            """,
            (schema_name,),
        )
        fk_map: Dict[str, Dict[str, str]] = {}
        for row in cast(List[Dict[str, str]], cursor.fetchall()):
            fk_map.setdefault(row["fk_table"], {})[row["fk_column"]] = (
                f"{row['pk_table']}.{row['pk_column']}"
            )

        type_map = self.get_database_type_map()
        compact_tables: List[Dict[str, Any]] = []

        for table_name in table_names:
            cursor.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                FROM information_schema.columns
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
                """,
                (schema_name, table_name),
            )
            columns = cast(List[Dict[str, str]], cursor.fetchall())

            cursor.execute(
                """
                SELECT TABLE_COMMENT, TABLE_TYPE
                FROM information_schema.tables
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                """,
                (schema_name, table_name),
            )
            desc_row = cast(Optional[Dict[str, str]], cursor.fetchone())
            table_desc = (
                desc_row["TABLE_COMMENT"]
                if desc_row and desc_row.get("TABLE_COMMENT")
                else ""
            )

            pks = pk_map.get(table_name, set())
            fks = fk_map.get(table_name, {})

            fields: List[Dict[str, Any]] = []
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

            compact_tables.append({"t": table_name, "d": table_desc, "f": fields})

        cursor.close()
        return compact_tables
