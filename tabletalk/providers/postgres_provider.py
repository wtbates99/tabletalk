from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from tabletalk.interfaces import DatabaseProvider


class PostgresProvider(DatabaseProvider):
    def __init__(self, host: str, port: int, dbname: str, user: str, password: str):
        self.host = host
        self.port = port
        self.dbname = dbname
        self.user = user
        self.password = password
        self.connection = psycopg2.connect(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=self.user,
            password=self.password,
        )

    def execute_query(self, sql_query: str) -> List[Dict[str, Any]]:
        cursor = self.connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(sql_query)
        results = cursor.fetchall()
        return [dict(row) for row in results]

    def get_client(self) -> psycopg2.extensions.connection:
        return self.connection

    def get_database_type_map(self) -> Dict[str, str]:
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

        # Primary keys for the whole schema in one query
        cursor.execute(
            """
            SELECT kcu.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema  = kcu.table_schema
            WHERE tc.table_schema = %s
            AND   tc.constraint_type = 'PRIMARY KEY'
            """,
            (schema_name,),
        )
        pk_map: Dict[str, set] = {}
        for row in cursor.fetchall():
            pk_map.setdefault(row[0], set()).add(row[1])

        # Foreign keys for the whole schema in one query
        cursor.execute(
            """
            SELECT
                kcu.table_name   AS fk_table,
                kcu.column_name  AS fk_column,
                ccu.table_name   AS pk_table,
                ccu.column_name  AS pk_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema   = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
                AND tc.table_schema   = ccu.table_schema
            WHERE tc.table_schema = %s
            AND   tc.constraint_type = 'FOREIGN KEY'
            """,
            (schema_name,),
        )
        fk_map: Dict[str, Dict[str, str]] = {}
        for row in cursor.fetchall():
            fk_map.setdefault(row[0], {})[row[1]] = f"{row[2]}.{row[3]}"

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
                )
                """,
                (schema_name, table_name),
            )
            desc_row = cursor.fetchone()
            table_desc = desc_row[0] if desc_row and desc_row[0] else ""

            pks = pk_map.get(table_name, set())
            fks = fk_map.get(table_name, {})

            fields = []
            for col in columns:
                col_name = col[0]
                col_type = col[1].lower()
                mapped = type_map.get(col_type, "S")
                field: Dict[str, Any] = {"n": col_name, "t": mapped}
                if col_name in pks:
                    field["pk"] = True
                if col_name in fks:
                    field["fk"] = fks[col_name]
                fields.append(field)

            compact_tables.append(
                {"t": table_name, "d": table_desc, "f": fields}
            )

        return compact_tables
