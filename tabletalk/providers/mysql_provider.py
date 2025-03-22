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
            self.connection: (
                MySQLConnection | PooledMySQLConnection | MySQLConnectionAbstract
            ) = mysql.connector.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                port=self.port,
                database=self.database,
            )
        except Error as e:
            raise Exception(f"Failed to connect to database: {e}")

    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        cursor = self.connection.cursor(dictionary=True)
        cursor.execute(sql_query)
        results = cast(List[Dict[str, Any]], cursor.fetchall())
        cursor.close()
        return results

    def get_client(
        self,
    ) -> MySQLConnection | PooledMySQLConnection | MySQLConnectionAbstract:
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

        type_map = self.get_database_type_map()
        compact_tables: List[Dict[str, Any]] = []

        for table_name in table_names:
            cursor.execute(
                """
                SELECT COLUMN_NAME as column_name,
                       DATA_TYPE as data_type,
                       IS_NULLABLE as is_nullable
                FROM information_schema.columns
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
                """,
                (schema_name, table_name),
            )
            columns = cast(List[Dict[str, str]], cursor.fetchall())

            cursor.execute(
                """
                SELECT TABLE_COMMENT as description, TABLE_TYPE as table_type
                FROM information_schema.tables
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                """,
                (schema_name, table_name),
            )
            description_row = cast(Optional[Dict[str, str]], cursor.fetchone())
            table_description = (
                description_row["description"]
                if description_row and description_row.get("description")
                else ""
            )

            fields: List[Dict[str, str]] = []
            for column in columns:
                col_name = column["column_name"]
                col_type = column["data_type"].lower()
                mapped_type = type_map.get(col_type, "S")
                fields.append({"n": col_name, "t": mapped_type})

            compact_tables.append(
                {
                    "t": table_name,
                    "d": table_description,
                    "f": fields,
                }
            )

        cursor.close()
        return compact_tables
